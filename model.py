"""
model.py
--------
All simulation logic for the Traffic Signal Optimizer.

Concepts
  * Sparse grid: intersections exist only where the user placed them.
  * Roads are bidirectional and connect ADJACENT intersections.
  * Every side of an intersection without a road gets an external ENTRY road
    that injects Poisson traffic (and absorbs cars leaving the network).

Direction convention (used everywhere):
  d = 0 North, 1 South, 2 East, 3 West  -> the side a car ARRIVES from.
  A car that arrives from side d drives straight through and leaves through
  the opposite side OPP[d].

Control scenarios
  "uniform"     equal green (30 s) for both phases
  "webster"     Webster cycle and green split from the expected demand
  "green_wave"  the same Webster splits + travel-time offsets (section 2.3)

Time-varying demand
  Network.demand_multiplier scales all entry demands (lambda_i(t) = m(t) * lambda_i0).
  Simulator.run_daily() runs 24 h with the profile DAILY_PROFILE and either a
  fixed plan or a plan recalculated every update_interval seconds.
"""

import math
import random

DEFAULT_SPEED_MS = 60 * 1000 / 3600   # 60 km/h in m/s (about 16.67)
DEFAULT_ROAD_LENGTH = 400             # metres
DEFAULT_DEMAND_CPH = 180              # entry demand, cars/hour

DELTA = {0: (-1, 0), 1: (1, 0), 2: (0, 1), 3: (0, -1)}   # neighbour on side d
OPP = {0: 1, 1: 0, 2: 3, 3: 2}                           # opposite side

# ---- signal-timing constants (Webster) -----------------------------------
YELLOW_TIME = 4.0          # s   (lost time L = 2 * yellow for two phases)
MIN_CYCLE, MAX_CYCLE = 40.0, 120.0
MIN_GREEN, MAX_GREEN = 20.0, 120.0

# ---- synthetic 24-hour demand profile: (from_hour, to_hour, multiplier, name)
DAILY_PROFILE = [
    (0, 6, 0.3, "Night"),
    (6, 9, 1.5, "Morning peak"),
    (9, 16, 0.7, "Daytime lull"),
    (16, 19, 1.6, "Evening peak"),
    (19, 24, 0.5, "Evening decrease"),
]


def demand_multiplier(t_sec, profile=DAILY_PROFILE):
    """m(t) for simulation time t (seconds); wraps around every 24 h."""
    hour = (t_sec / 3600.0) % 24
    for start, end, m, _ in profile:
        if start <= hour < end:
            return m
    return 1.0


# ---------------------------------------------------------------------------
# Road
# ---------------------------------------------------------------------------
class Road:
    """
    A bidirectional road segment. Cars travel both ways at once; each
    direction has its own in-transit buffer.

    forward=True  : from the lower-keyed cell to the higher-keyed cell
    forward=False : the opposite way
    """

    def __init__(self, length_m=DEFAULT_ROAD_LENGTH,
                 speed_ms=DEFAULT_SPEED_MS,
                 demand_cph=DEFAULT_DEMAND_CPH):
        self.length_m = length_m
        self.speed_ms = speed_ms
        self.demand_cph = demand_cph          # used by ENTRY roads only
        self._transit_fwd = []
        self._transit_bwd = []

    @property
    def travel_time(self):
        return self.length_m / self.speed_ms

    @property
    def arrival_rate(self):
        """Entry demand in cars/second (before the demand multiplier)."""
        return self.demand_cph / 3600.0

    @property
    def cars_in_transit(self):
        return (sum(n for _, n in self._transit_fwd)
                + sum(n for _, n in self._transit_bwd))

    def reset(self):
        self._transit_fwd = []
        self._transit_bwd = []

    def release(self, num_cars, current_time, forward=True):
        if num_cars > 0:
            buf = self._transit_fwd if forward else self._transit_bwd
            buf.append((current_time + self.travel_time, num_cars))

    def collect_arrivals(self, current_time, forward=True):
        buf = self._transit_fwd if forward else self._transit_bwd
        arrived, still = 0.0, []
        for arrive_at, cars in buf:
            if current_time >= arrive_at:
                arrived += cars
            else:
                still.append((arrive_at, cars))
        if forward:
            self._transit_fwd = still
        else:
            self._transit_bwd = still
        return arrived


# ---------------------------------------------------------------------------
# Intersection
# ---------------------------------------------------------------------------
class Intersection:
    """
    One signalised crossroads.
    Phase 0 = North-South green, phase 1 = East-West green.
    Real cycle = green0 + yellow + green1 + yellow.
    """

    SAT_FLOW = 0.5          # cars / second / lane at saturation (1800 veh/h)

    def __init__(self, row, col):
        self.row = row
        self.col = col
        self.lanes = [2, 2, 2, 2]               # [N, S, E, W]
        self.green_times = [30.0, 30.0]
        self.yellow_time = YELLOW_TIME
        self.offset = 0.0                       # green-wave offset (s)
        self.current_phase = 0
        self.phase_timer = 0.0
        self.in_yellow = False
        self.queue = [0.0] * 4
        self.arrival_rate = [0.0] * 4           # expected cars/s per approach
        self.cars_passed = 0.0
        self.queue_sum = 0.0
        self.ticks = 0

    # ---- timing ----------------------------------------------------------
    def lost_time(self):
        return 2 * self.yellow_time

    def cycle_length(self):
        return sum(self.green_times) + self.lost_time()

    def flow_ratios(self):
        s_ns = self.SAT_FLOW * max(self.lanes[0], self.lanes[1])
        s_ew = self.SAT_FLOW * max(self.lanes[2], self.lanes[3])
        y1 = (self.arrival_rate[0] + self.arrival_rate[1]) / s_ns if s_ns else 0
        y2 = (self.arrival_rate[2] + self.arrival_rate[3]) / s_ew if s_ew else 0
        return y1, y2

    def compute_webster(self):
        """Returns (actual cycle, green_NS, green_EW)."""
        L = self.lost_time()
        y1, y2 = self.flow_ratios()
        Y = min(y1 + y2, 0.95)
        C = max(MIN_CYCLE, min((1.5 * L + 5) / (1 - Y), MAX_CYCLE))
        eff = C - L
        tot = y1 + y2
        g1, g2 = (eff / 2, eff / 2) if tot == 0 else (eff * y1 / tot, eff * y2 / tot)
        g1 = max(MIN_GREEN, min(g1, MAX_GREEN))
        g2 = max(MIN_GREEN, min(g2, MAX_GREEN))
        return g1 + g2 + L, g1, g2

    def apply_webster(self):
        _, g1, g2 = self.compute_webster()
        self.green_times = [g1, g2]

    def apply_uniform(self, green=30.0):
        self.green_times = [green, green]

    # ---- green-wave offset ----------------------------------------------
    def set_cycle_position(self, pos):
        """Put the signal at position `pos` seconds into its cycle."""
        y = self.yellow_time
        g0, g1 = self.green_times
        pos %= (g0 + y + g1 + y)
        self.in_yellow = False
        if pos < g0:
            self.current_phase, self.phase_timer = 0, pos
        elif pos < g0 + y:
            self.current_phase, self.in_yellow, self.phase_timer = 0, True, pos - g0
        elif pos < g0 + y + g1:
            self.current_phase, self.phase_timer = 1, pos - g0 - y
        else:
            self.current_phase, self.in_yellow, self.phase_timer = 1, True, pos - g0 - y - g1

    def apply_offset(self, delta):
        """phi(t) = (t - delta) mod C : NS green starts delta seconds late."""
        self.offset = delta
        self.set_cycle_position(-delta)

    # ---- one second of simulation ---------------------------------------
    def tick(self, dt, incoming):
        """
        incoming : [N, S, E, W] cars joining each queue this tick.
        Returns  : [N, S, E, W] cars discharged this tick.
        """
        for d in range(4):
            self.queue[d] += incoming[d]

        departed = [0.0] * 4
        if not self.in_yellow:
            for d in ([0, 1] if self.current_phase == 0 else [2, 3]):
                leaving = min(self.queue[d], self.SAT_FLOW * self.lanes[d] * dt)
                self.queue[d] -= leaving
                self.cars_passed += leaving
                departed[d] = leaving

        self.queue_sum += sum(self.queue)
        self.ticks += 1
        self.phase_timer += dt

        if not self.in_yellow:
            if self.phase_timer >= self.green_times[self.current_phase]:
                self.in_yellow, self.phase_timer = True, 0.0
        else:
            if self.phase_timer >= self.yellow_time:
                self.in_yellow = False
                self.current_phase = 1 - self.current_phase
                self.phase_timer = 0.0
        return departed

    def is_green(self, d):
        return (not self.in_yellow) and d in ([0, 1] if self.current_phase == 0 else [2, 3])

    def is_yellow(self, d):
        return self.in_yellow and d in ([0, 1] if self.current_phase == 0 else [2, 3])

    def reset(self):
        self.queue = [0.0] * 4
        self.arrival_rate = [0.0] * 4
        self.cars_passed = 0.0
        self.queue_sum = 0.0
        self.ticks = 0
        self.offset = 0.0
        self.current_phase = 0
        self.phase_timer = 0.0
        self.in_yellow = False

    def total_queue(self):
        return sum(self.queue)

    def avg_queue(self):
        return self.queue_sum / self.ticks if self.ticks else 0.0

    def throughput_per_hour(self, sim_sec):
        return (self.cars_passed / sim_sec) * 3600 if sim_sec else 0


# ---------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------
class Network:
    """Sparse grid of intersections + manually drawn roads."""

    def __init__(self, rows=6, cols=6):
        self.rows = rows
        self.cols = cols
        self.grid = {}            # {(r, c): Intersection}
        self.roads = {}           # {canonical_key: Road}    internal roads
        self.entry_roads = {}     # {((r, c), side): Road}   external entries
        self.default_length_m = DEFAULT_ROAD_LENGTH
        self.default_speed_ms = DEFAULT_SPEED_MS
        self.default_demand_cph = DEFAULT_DEMAND_CPH
        self.demand_multiplier = 1.0     # m(t), scales every entry demand
        self.entered = 0.0               # cars that entered the network
        self.exited = 0.0                # cars that left the network

    # ---- convenience builders -------------------------------------------
    @classmethod
    def build_grid(cls, rows, cols, length_m=DEFAULT_ROAD_LENGTH,
                   speed_ms=DEFAULT_SPEED_MS):
        """Full rows x cols grid with every neighbouring pair connected."""
        net = cls(rows, cols)
        for r in range(rows):
            for c in range(cols):
                net.add_intersection(r, c)
        for r in range(rows):
            for c in range(cols):
                if c + 1 < cols:
                    net.add_road(r, c, r, c + 1, length_m, speed_ms)
                if r + 1 < rows:
                    net.add_road(r, c, r + 1, c, length_m, speed_ms)
        return net

    def set_entry_demand(self, ns_cph, ew_cph):
        """Demand (cars/hour) of EVERY entry road: N/S sides get ns, E/W get ew."""
        for (_, d), road in self.entry_roads.items():
            road.demand_cph = ns_cph if d in (0, 1) else ew_cph
        self._update_arrival_rates()

    # ---- placing / removing intersections -------------------------------
    def add_intersection(self, r, c):
        if (r, c) not in self.grid:
            self.grid[(r, c)] = Intersection(r, c)
            self._refresh_entries(r, c)
            self._update_arrival_rates()

    def remove_intersection(self, r, c):
        if (r, c) not in self.grid:
            return
        del self.grid[(r, c)]
        self.roads = {k: v for k, v in self.roads.items() if (r, c) not in k}
        self.entry_roads = {k: v for k, v in self.entry_roads.items()
                            if k[0] != (r, c)}
        for nr, nc in self._adj(r, c):
            self._refresh_entries(nr, nc)
        self._update_arrival_rates()

    def toggle(self, r, c):
        if (r, c) in self.grid:
            self.remove_intersection(r, c)
        else:
            self.add_intersection(r, c)

    # ---- roads -----------------------------------------------------------
    def _are_adjacent(self, r1, c1, r2, c2):
        return abs(r1 - r2) + abs(c1 - c2) == 1

    def has_road(self, r1, c1, r2, c2):
        return _road_key(r1, c1, r2, c2) in self.roads

    def add_road(self, r1, c1, r2, c2, length_m=None, speed_ms=None):
        if not self._are_adjacent(r1, c1, r2, c2):
            return None
        if (r1, c1) not in self.grid or (r2, c2) not in self.grid:
            return None
        road = Road(length_m if length_m is not None else self.default_length_m,
                    speed_ms if speed_ms is not None else self.default_speed_ms,
                    0.0)
        self.roads[_road_key(r1, c1, r2, c2)] = road
        self._refresh_entries(r1, c1)
        self._refresh_entries(r2, c2)
        self._update_arrival_rates()
        return road

    def remove_road(self, r1, c1, r2, c2):
        self.roads.pop(_road_key(r1, c1, r2, c2), None)
        self._refresh_entries(r1, c1)
        self._refresh_entries(r2, c2)
        self._update_arrival_rates()

    def _refresh_entries(self, r, c):
        """Every side WITHOUT an internal road gets an external entry road."""
        if (r, c) not in self.grid:
            return
        for d, (dr, dc) in DELTA.items():
            ek = ((r, c), d)
            if _road_key(r, c, r + dr, c + dc) in self.roads:
                self.entry_roads.pop(ek, None)
            elif ek not in self.entry_roads:
                self.entry_roads[ek] = Road(self.default_length_m,
                                            self.default_speed_ms,
                                            self.default_demand_cph)

    # ---- accessors -------------------------------------------------------
    def get(self, r, c):
        return self.grid.get((r, c))

    def all(self):
        return list(self.grid.values())

    def is_empty(self):
        return len(self.grid) == 0

    def get_road(self, r1, c1, r2, c2):
        return self.roads.get(_road_key(r1, c1, r2, c2))

    def get_entry_road(self, r, c, direction):
        return self.entry_roads.get(((r, c), direction))

    def _adj(self, r, c):
        return [(r + dr, c + dc) for dr, dc in DELTA.values()]

    # ---- demand estimate used by Webster ----------------------------------
    def _expected_rate(self, r, c, d):
        """
        Expected flow (cars/s) on approach d of (r, c). Cars drive straight, so
        it equals the demand of the entry road at the far end of the straight
        line of roads behind that approach (times the demand multiplier).
        """
        dr, dc = DELTA[d]
        cr, cc = r, c
        while _road_key(cr, cc, cr + dr, cc + dc) in self.roads \
                and (cr + dr, cc + dc) in self.grid:
            cr, cc = cr + dr, cc + dc
        entry = self.entry_roads.get(((cr, cc), d))
        return entry.arrival_rate * self.demand_multiplier if entry else 0.0

    def _update_arrival_rates(self):
        for (r, c), inter in self.grid.items():
            for d in range(4):
                cap = Intersection.SAT_FLOW * inter.lanes[d]
                inter.arrival_rate[d] = min(self._expected_rate(r, c, d), cap)

    # ---- simulation ------------------------------------------------------
    def _incoming(self, r, c, t, dt):
        """Cars joining each queue of (r, c) this tick."""
        inc = [0.0] * 4
        for d, (dr, dc) in DELTA.items():
            nr, nc = r + dr, c + dc
            road = self.roads.get(_road_key(r, c, nr, nc))
            if road:
                inc[d] = road.collect_arrivals(t, forward=(nr, nc) < (r, c))
            else:
                entry = self.entry_roads.get(((r, c), d))
                if entry:
                    n = _poisson(entry.arrival_rate * self.demand_multiplier * dt)
                    inc[d] = n
                    self.entered += n
        return inc

    def tick_all(self, dt=1.0, current_time=0.0):
        cells = list(self.grid.items())      # copy: GUI thread may edit grid
        inc_map = {rc: self._incoming(rc[0], rc[1], current_time, dt)
                   for rc, _ in cells}

        for (r, c), inter in cells:
            departed = inter.tick(dt, inc_map[(r, c)])
            for d, cars in enumerate(departed):
                if cars > 0:
                    dr, dc = DELTA[OPP[d]]            # drive straight through
                    nr, nc = r + dr, c + dc
                    road = self.roads.get(_road_key(r, c, nr, nc))
                    if road:
                        road.release(cars, current_time,
                                     forward=(r, c) < (nr, nc))
                    else:                             # leaves the network
                        self.exited += cars
        if int(current_time) % 10 == 0:
            self._update_arrival_rates()

    def reset_all(self):
        for i in self.all():
            i.reset()
        for r in self.roads.values():
            r.reset()
        for r in self.entry_roads.values():
            r.reset()
        self.entered = 0.0
        self.exited = 0.0
        self._update_arrival_rates()

    # ---- control plans ---------------------------------------------------
    def apply_webster_all(self):
        self._update_arrival_rates()
        for i in self.all():
            i.apply_webster()

    def apply_uniform_all(self, green=30.0):
        for i in self.all():
            i.apply_uniform(green)

    def compute_offsets(self, corridor="NS", cycle=None):
        """
        Green-wave offsets  delta_i = (sum of travel times from the reference
        intersection) mod C.  Reference = first intersection of each line
        (smallest row for 'NS' columns, smallest column for 'EW' rows);
        progression direction = increasing index (southbound / eastbound).
        """
        if not self.grid:
            return {}
        C = cycle or self.all()[0].cycle_length()
        lines = {}
        for (r, c) in self.grid:
            lines.setdefault(c if corridor == "NS" else r, []).append((r, c))
        offsets = {}
        for cells in lines.values():
            cells.sort()
            T, prev = 0.0, None
            for cell in cells:
                if prev is not None:
                    road = self.roads.get(_road_key(prev[0], prev[1], cell[0], cell[1]))
                    T = T + road.travel_time if road else 0.0
                offsets[cell] = T % C
                prev = cell
        return offsets

    def apply_green_wave(self, corridor="NS"):
        """
        Keep the Webster green splits, give all signals one common cycle
        (the longest Webster cycle; splits are scaled proportionally) and
        shift each signal by its travel-time offset.
        Call AFTER apply_webster_all().  Returns (cycle, offsets).
        """
        inters = self.all()
        if not inters:
            return 0.0, {}
        cyc = max(i.cycle_length() for i in inters)
        for i in inters:
            eff_old, eff_new = sum(i.green_times), cyc - i.lost_time()
            k = eff_new / eff_old if eff_old else 1.0
            i.green_times = [g * k for g in i.green_times]
        offsets = self.compute_offsets(corridor, cyc)
        for pos, delta in offsets.items():
            self.grid[pos].apply_offset(delta)
        return cyc, offsets

    # ---- statistics ------------------------------------------------------
    def total_cars(self):
        return sum(i.cars_passed for i in self.all())

    def total_queue(self):
        return sum(i.total_queue() for i in self.all())

    def cars_in_transit(self):
        return sum(r.cars_in_transit for r in self.roads.values())

    def avg_queue(self):
        lst = self.all()
        return sum(i.avg_queue() for i in lst) / len(lst) if lst else 0


# ---------------------------------------------------------------------------
# Simulator
# ---------------------------------------------------------------------------
class SimResult:
    def __init__(self, name):
        self.name = name
        self.scenario = ""
        self.seed = None
        self.throughput_hr = 0.0     # discharges at all intersections / hour
        self.exit_hr = 0.0           # cars leaving the network / hour
        self.avg_queue = 0.0         # mean over intersections of mean queue
        self.avg_delay_s = 0.0       # queue-seconds per discharged car (Little)
        self.total_cars = 0.0
        self.history_cars = []
        self.history_queue = []
        self.per_intersection = {}


class DailyResult:
    """Per-second series of a 24 h run."""

    def __init__(self, mode, seed):
        self.mode, self.seed = mode, seed
        self.cars = []        # cumulative discharges at every tick
        self.exited = []      # cumulative cars that left the network
        self.queue = []       # mean queue per intersection at every tick
        self.plan_log = []    # (t, cycle, green_NS, green_EW, multiplier)

    def period_stats(self, profile=DAILY_PROFILE):
        rows = []
        for a, b, m, name in profile:
            i0, i1 = int(a * 3600), int(b * 3600)
            hours = b - a
            cars0 = self.cars[i0 - 1] if i0 > 0 else 0.0
            ex0 = self.exited[i0 - 1] if i0 > 0 else 0.0
            q = self.queue[i0:i1]
            rows.append({
                "period": name, "hours": f"{a:02d}:00-{b:02d}:00", "multiplier": m,
                "throughput_hr": (self.cars[i1 - 1] - cars0) / hours,
                "exit_hr": (self.exited[i1 - 1] - ex0) / hours,
                "mean_queue": sum(q) / len(q),
            })
        return rows


class Simulator:
    def __init__(self, network):
        self.network = network

    # ---- 1-hour style runs ----------------------------------------------
    def run(self, scenario="webster", duration=3600, progress_cb=None,
            seed=None, corridor="NS"):
        """scenario: 'uniform' | 'webster' | 'green_wave' | 'webster_random'."""
        if seed is not None:
            random.seed(seed)
        net = self.network
        net.demand_multiplier = 1.0
        net.reset_all()
        if scenario == "uniform":
            net.apply_uniform_all(30.0)
            label = "Uniform (30s green)"
        elif scenario == "webster":
            net.apply_webster_all()
            label = "Webster Optimized"
        elif scenario == "green_wave":
            net.apply_webster_all()
            net.apply_green_wave(corridor)
            label = "Webster + Green Wave"
        elif scenario == "webster_random":
            # diagnostic: Webster splits, but every signal starts at a random
            # point of its cycle (own RNG, so the arrival stream is unchanged)
            net.apply_webster_all()
            rng = random.Random(10007 * (seed or 0) + 17)
            for i in net.all():
                i.set_cycle_position(rng.uniform(0, i.cycle_length()))
            label = "Webster, random phases"
        else:
            raise ValueError(f"unknown scenario {scenario!r}")

        result = SimResult(label)
        result.scenario, result.seed = scenario, seed
        record_every = max(1, duration // 300)

        for t in range(1, duration + 1):
            net.tick_all(1.0, float(t))
            if t % record_every == 0:
                result.history_cars.append((t, net.total_cars()))
                result.history_queue.append((t, net.avg_queue()))
            if progress_cb and t % 50 == 0:
                progress_cb(t / duration)

        self._finish(result, duration)
        return result

    def _finish(self, result, duration):
        net = self.network
        result.total_cars = net.total_cars()
        result.throughput_hr = result.total_cars / duration * 3600
        result.exit_hr = net.exited / duration * 3600
        result.avg_queue = net.avg_queue()
        qsum = sum(i.queue_sum for i in net.all())
        result.avg_delay_s = qsum / result.total_cars if result.total_cars else 0.0
        for inter in net.all():
            result.per_intersection[(inter.row, inter.col)] = {
                "green_ns": inter.green_times[0],
                "green_ew": inter.green_times[1],
                "tph": inter.throughput_per_hour(duration),
                "queue": inter.avg_queue(),
            }

    def run_comparison(self, duration=3600, progress_cb=None, seed=None):
        def cb1(p):
            if progress_cb: progress_cb(p * .5)
        def cb2(p):
            if progress_cb: progress_cb(.5 + p * .5)
        return (self.run("uniform", duration, cb1, seed),
                self.run("webster", duration, cb2, seed))

    def run_three(self, duration=3600, seed=None, corridor="NS"):
        """Uniform, Webster and Webster+green-wave with the same arrivals."""
        return [self.run(s, duration, seed=seed, corridor=corridor)
                for s in ("uniform", "webster", "green_wave")]

    # ---- 24-hour run -----------------------------------------------------
    def run_daily(self, mode="adaptive", duration=86400, update_interval=900,
                  seed=None, profile=DAILY_PROFILE, progress_cb=None):
        """
        mode 'uniform'  : 30 s / 30 s all day
        mode 'fixed'    : Webster plan from the BASELINE demand (m = 1), never changed
        mode 'adaptive' : Webster plan recalculated every update_interval s
                          from the current demand m(t) * baseline
        """
        if seed is not None:
            random.seed(seed)
        net = self.network
        net.demand_multiplier = 1.0
        net.reset_all()
        if mode == "uniform":
            net.apply_uniform_all(30.0)
        elif mode == "fixed":
            net.apply_webster_all()
        elif mode != "adaptive":
            raise ValueError(f"unknown mode {mode!r}")

        res = DailyResult(mode, seed)
        probe = net.all()[0]
        n = len(net.grid)
        for t in range(1, duration + 1):
            m = demand_multiplier(t - 1, profile)
            net.demand_multiplier = m
            if mode == "adaptive" and (t - 1) % update_interval == 0:
                net.apply_webster_all()
                res.plan_log.append((t - 1, probe.cycle_length(),
                                     probe.green_times[0], probe.green_times[1], m))
            net.tick_all(1.0, float(t))
            res.cars.append(net.total_cars())
            res.exited.append(net.exited)
            res.queue.append(net.total_queue() / n)
            if progress_cb and t % 3600 == 0:
                progress_cb(t / duration)
        net.demand_multiplier = 1.0
        return res


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------
def _road_key(r1, c1, r2, c2):
    """Canonical sorted key for a road between two cells."""
    return (min((r1, c1), (r2, c2)), max((r1, c1), (r2, c2)))


def _poisson(lam):
    if lam <= 0:
        return 0
    if lam > 30:
        return max(0, int(random.gauss(lam, math.sqrt(lam))))
    L, k, p = math.exp(-lam), 0, 1.0
    while p > L:
        p *= random.random()
        k += 1
    return k - 1


def improvement(u, w):
    """(throughput gain %, queue reduction %) of w relative to baseline u."""
    tg = (w.throughput_hr - u.throughput_hr) / u.throughput_hr * 100 if u.throughput_hr else 0
    qr = (u.avg_queue - w.avg_queue) / u.avg_queue * 100 if u.avg_queue else 0
    return tg, qr
