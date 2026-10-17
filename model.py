"""
model.py
--------
Simulation logic for the Traffic Signal Optimizer.

Key concepts:
  - Intersection : one crossroads with parameters (lanes, throughput, demand)
  - Road         : connects two intersections, has its OWN length and speed
                   Roads are BIDIRECTIONAL — cars flow both ways simultaneously
  - Network      : sparse grid; intersections AND roads are placed manually
  - Simulator    : runs the discrete-time simulation

Flow:
  1. User places intersections on the grid (click nodes)
  2. User connects them with roads (click two intersections)
  3. Each road has its own length_m and speed_ms → travel_time is computed
  4. External entry roads are auto-created for every exposed edge
  5. Simulation propagates cars through the network with travel-time delay
"""

import math, random

DEFAULT_SPEED_MS    = 60 * 1000 / 3600   # 60 km/h in m/s
DEFAULT_ROAD_LENGTH = 400                 # metres
DEFAULT_DEMAND_CPH  = 180                 # cars/hour on external entry roads


# ─────────────────────────────────────────────
#  ROAD  (bidirectional, per-road length+speed)
# ─────────────────────────────────────────────

class Road:
    """
    A bidirectional road segment between two intersections (or from outside).

    Each road has its OWN length_m and speed_ms — set independently when
    the user draws the road.

    Cars travel in BOTH directions simultaneously.
    Two independent transit buffers (_transit_fwd, _transit_bwd) track cars
    going in each direction. "forward" = from the lower grid key to the higher.

    travel_time = length_m / speed_ms  (seconds)
    """

    def __init__(self, length_m=DEFAULT_ROAD_LENGTH,
                 speed_ms=DEFAULT_SPEED_MS,
                 demand_cph=DEFAULT_DEMAND_CPH):
        self.length_m   = float(length_m)
        self.speed_ms   = float(speed_ms)
        self.demand_cph = float(demand_cph)   # for external entry roads only

        # Separate in-transit buffers for each direction
        self._transit_fwd = []   # (arrive_at_time, num_cars)
        self._transit_bwd = []

    @property
    def travel_time(self):
        """Seconds to drive this road end-to-end."""
        return self.length_m / self.speed_ms

    @property
    def speed_kmh(self):
        return self.speed_ms * 3.6

    @property
    def arrival_rate(self):
        """Cars/second — only used for external entry roads."""
        return self.demand_cph / 3600.0

    def reset(self):
        self._transit_fwd = []
        self._transit_bwd = []

    def release(self, num_cars, current_time, forward=True):
        """Release cars onto this road. They arrive after travel_time seconds."""
        if num_cars > 0:
            entry = (current_time + self.travel_time, num_cars)
            (self._transit_fwd if forward else self._transit_bwd).append(entry)

    def collect_arrivals(self, current_time, forward=True):
        """Collect cars that have finished travelling in the given direction."""
        buf = self._transit_fwd if forward else self._transit_bwd
        arrived, still = 0.0, []
        for arrive_at, cars in buf:
            (arrived := arrived + cars) if current_time >= arrive_at \
                else still.append((arrive_at, cars))
        # Rewrite using simple loop (walrus not available in all Python 3.9)
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


# ─────────────────────────────────────────────
#  INTERSECTION
# ─────────────────────────────────────────────

class Intersection:
    """
    One signalized crossroads.

    Parameters (editable by user):
      lanes[d]        — number of lanes per direction [N,S,E,W]
      arrival_rate[d] — cached cars/sec per direction (auto-computed from roads)

    Phase 0 = NS green.  Phase 1 = EW green.
    Directions: N=0  S=1  E=2  W=3
    """

    SAT_FLOW = 0.5   # cars/second/lane at saturation (= 1800 cars/hr/lane)

    def __init__(self, row, col):
        self.row, self.col = row, col
        self.lanes        = [2, 2, 2, 2]   # 2 lanes per direction by default
        self.green_times  = [30.0, 30.0]
        self.yellow_time  = 3.0
        self.current_phase = 0
        self.phase_timer   = 0.0
        self.in_yellow     = False
        self.queue        = [0.0]*4
        self.arrival_rate = [0.0]*4        # updated each tick by Network
        self.cars_passed  = 0.0
        self.queue_sum    = 0.0
        self.ticks        = 0

    # ── Webster ──────────────────────────────

    def flow_ratios(self):
        s_ns = self.SAT_FLOW * max(self.lanes[0], self.lanes[1])
        s_ew = self.SAT_FLOW * max(self.lanes[2], self.lanes[3])
        y1 = (self.arrival_rate[0]+self.arrival_rate[1]) / s_ns if s_ns else 0
        y2 = (self.arrival_rate[2]+self.arrival_rate[3]) / s_ew if s_ew else 0
        return y1, y2

    def compute_webster(self):
        L = 6.0
        y1, y2 = self.flow_ratios()
        Y = min(y1+y2, 0.95)
        C = max(20.0, min((1.5*L+5)/(1-Y), 120.0))
        eff = C - L
        g1, g2 = (eff/2, eff/2) if Y==0 else (eff*y1/Y, eff*y2/Y)
        return C, max(5.0, min(g1, C-10)), max(5.0, min(g2, C-10))

    def apply_webster(self):
        _, g1, g2 = self.compute_webster()
        self.green_times = [g1, g2]

    def apply_uniform(self, green=30.0):
        self.green_times = [green, green]

    # ── Tick ─────────────────────────────────

    def tick(self, dt, incoming):
        """
        incoming: [N,S,E,W] cars arriving this tick.
        Returns departed: [N,S,E,W] cars that left (for road propagation).
        """
        for d in range(4):
            self.queue[d] += _poisson(incoming[d])

        departed = [0.0]*4
        if not self.in_yellow:
            for d in ([0,1] if self.current_phase==0 else [2,3]):
                leaving = min(self.queue[d], self.SAT_FLOW*self.lanes[d]*dt)
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
        return (not self.in_yellow) and d in ([0,1] if self.current_phase==0 else [2,3])

    def is_yellow(self, d):
        return self.in_yellow and d in ([0,1] if self.current_phase==0 else [2,3])

    def reset(self):
        self.queue = [0.0]*4
        self.arrival_rate = [0.0]*4
        self.cars_passed = self.queue_sum = 0.0
        self.ticks = 0
        self.current_phase = 0
        self.phase_timer = 0.0
        self.in_yellow = False

    def avg_queue(self):
        return self.queue_sum / self.ticks if self.ticks else 0.0

    def throughput_per_hour(self, sim_sec):
        return (self.cars_passed / sim_sec)*3600 if sim_sec else 0


# ─────────────────────────────────────────────
#  NETWORK
# ─────────────────────────────────────────────

class Network:
    """
    Sparse grid. Both intersections AND roads are placed manually.

    Intersections: placed by clicking grid nodes
    Roads        : placed by clicking two existing intersections
                   Each road has its OWN length_m and speed_ms
    Entry roads  : auto-created for every exposed edge (outside traffic)

    Roads are NOT automatically created when intersections are placed —
    the user must explicitly draw them.
    """

    def __init__(self, rows=6, cols=6):
        self.rows = rows
        self.cols = cols
        self.grid        = {}   # {(r,c): Intersection}
        self.roads       = {}   # {canonical_key: Road}  — user-drawn roads
        self.entry_roads = {}   # {((r,c), direction): Road} — auto external

        self.default_length_m   = DEFAULT_ROAD_LENGTH
        self.default_speed_ms   = DEFAULT_SPEED_MS
        self.default_demand_cph = DEFAULT_DEMAND_CPH

    # ── Intersections ─────────────────────────

    def add_intersection(self, r, c):
        if (r, c) not in self.grid:
            self.grid[(r,c)] = Intersection(r, c)
            self._rebuild_entry_roads(r, c)

    def remove_intersection(self, r, c):
        if (r,c) not in self.grid:
            return
        del self.grid[(r,c)]
        # Remove all roads touching this cell
        self.roads = {k:v for k,v in self.roads.items() if (r,c) not in k}
        self.entry_roads = {k:v for k,v in self.entry_roads.items() if k[0]!=(r,c)}
        # Rebuild entry roads for neighbours that lost a connection
        for nr,nc in self._adj(r,c):
            if (nr,nc) in self.grid:
                self._rebuild_entry_roads(nr, nc)

    def toggle_intersection(self, r, c):
        if (r,c) in self.grid:
            self.remove_intersection(r, c)
        else:
            self.add_intersection(r, c)

    # ── Roads (manually drawn) ────────────────

    def add_road(self, r1, c1, r2, c2, length_m=None, speed_ms=None, demand_cph=None):
        """
        Draw a road between two existing intersections.
        The two cells must be adjacent (horizontally or vertically).
        Each road has its own length and speed.
        """
        if (r1,c1) not in self.grid or (r2,c2) not in self.grid:
            return False   # both intersections must exist
        if not self._are_adjacent(r1,c1,r2,c2):
            return False   # only adjacent cells can be connected
        key = _road_key(r1,c1,r2,c2)
        self.roads[key] = Road(
            length_m   = length_m   or self.default_length_m,
            speed_ms   = speed_ms   or self.default_speed_ms,
            demand_cph = demand_cph or self.default_demand_cph,
        )
        # This direction is now internal — remove external entry roads for both ends
        self._rebuild_entry_roads(r1, c1)
        self._rebuild_entry_roads(r2, c2)
        return True

    def remove_road(self, r1, c1, r2, c2):
        key = _road_key(r1,c1,r2,c2)
        if key in self.roads:
            del self.roads[key]
            self._rebuild_entry_roads(r1, c1)
            self._rebuild_entry_roads(r2, c2)

    def has_road(self, r1, c1, r2, c2):
        return _road_key(r1,c1,r2,c2) in self.roads

    def _are_adjacent(self, r1, c1, r2, c2):
        return abs(r1-r2) + abs(c1-c2) == 1

    # ── Entry roads (auto) ────────────────────

    def _rebuild_entry_roads(self, r, c):
        """
        For each direction at (r,c): if there is no user-drawn internal road
        in that direction, create an external entry road.
        If there IS an internal road, remove any external entry road.
        """
        nbr = {0:(r-1,c), 1:(r+1,c), 2:(r,c+1), 3:(r,c-1)}
        for d, (nr,nc) in nbr.items():
            ek  = ((r,c), d)
            rk  = _road_key(r,c,nr,nc)
            has_internal = rk in self.roads
            if has_internal:
                self.entry_roads.pop(ek, None)
            else:
                if ek not in self.entry_roads:
                    self.entry_roads[ek] = Road(
                        self.default_length_m,
                        self.default_speed_ms,
                        self.default_demand_cph,
                    )

    # ── Accessors ─────────────────────────────

    def get(self, r, c):
        return self.grid.get((r,c))

    def all(self):
        return list(self.grid.values())

    def is_empty(self):
        return len(self.grid) == 0

    def get_road(self, r1, c1, r2, c2):
        return self.roads.get(_road_key(r1,c1,r2,c2))

    def get_entry_road(self, r, c, d):
        return self.entry_roads.get(((r,c), d))

    def _adj(self, r, c):
        return [(r-1,c),(r+1,c),(r,c-1),(r,c+1)]

    # ── Simulation ────────────────────────────

    def _incoming(self, r, c, t, dt):
        """Cars arriving at (r,c) this tick from each direction."""
        # direction d means cars arriving FROM that direction
        # N(0): cars came from above (r-1,c) travelling south → forward=(r-1,c)<(r,c)
        src = {0:(r-1,c), 1:(r+1,c), 2:(r,c+1), 3:(r,c-1)}
        inc = [0.0]*4
        for d, (nr,nc) in src.items():
            rk = _road_key(r,c,nr,nc)
            ek = ((r,c), d)
            if rk in self.roads:
                fwd = (nr,nc) < (r,c)
                inc[d] = self.roads[rk].collect_arrivals(t, forward=fwd)
            elif ek in self.entry_roads:
                inc[d] = self.entry_roads[ek].arrival_rate * dt
        return inc

    def _update_arrival_rates(self):
        """Sync each intersection's cached arrival_rate from its roads."""
        for (r,c), inter in self.grid.items():
            src = {0:(r-1,c), 1:(r+1,c), 2:(r,c+1), 3:(r,c-1)}
            for d, (nr,nc) in src.items():
                rk = _road_key(r,c,nr,nc)
                ek = ((r,c), d)
                if rk in self.roads:
                    up = self.grid.get((nr,nc))
                    inter.arrival_rate[d] = (up.cars_passed/up.ticks
                                             if up and up.ticks else
                                             self.roads[rk].arrival_rate)
                elif ek in self.entry_roads:
                    inter.arrival_rate[d] = self.entry_roads[ek].arrival_rate
                else:
                    inter.arrival_rate[d] = 0.0

    def tick_all(self, dt=1.0, current_time=0.0):
        inc_map = {(r,c): self._incoming(r,c,current_time,dt)
                   for (r,c) in self.grid}

        dn = {0:lambda r,c:(r-1,c), 1:lambda r,c:(r+1,c),
              2:lambda r,c:(r,c+1), 3:lambda r,c:(r,c-1)}

        for (r,c), inter in self.grid.items():
            departed = inter.tick(dt, inc_map[(r,c)])
            for d, cars in enumerate(departed):
                if cars > 0:
                    nr,nc = dn[d](r,c)
                    rk    = _road_key(r,c,nr,nc)
                    if rk in self.roads:
                        fwd = (r,c) < (nr,nc)
                        self.roads[rk].release(cars, current_time, forward=fwd)

        self._update_arrival_rates()

    def reset_all(self):
        for i in self.all(): i.reset()
        for r in self.roads.values(): r.reset()
        for r in self.entry_roads.values(): r.reset()

    def apply_webster_all(self):
        self._update_arrival_rates()
        for i in self.all(): i.apply_webster()

    def apply_uniform_all(self, green=30.0):
        for i in self.all(): i.apply_uniform(green)

    def total_cars(self):
        return sum(i.cars_passed for i in self.all())

    def avg_queue(self):
        lst = self.all()
        return sum(i.avg_queue() for i in lst)/len(lst) if lst else 0


# ─────────────────────────────────────────────
#  SIMULATOR
# ─────────────────────────────────────────────

class SimResult:
    def __init__(self, name):
        self.name = name
        self.throughput_hr = self.avg_queue = self.total_cars = 0.0
        self.history_cars = []
        self.history_queue = []
        self.per_intersection = {}


class Simulator:
    def __init__(self, network):
        self.network = network

    def run(self, scenario="webster", duration=3600, progress_cb=None):
        (self.network.apply_webster_all() if scenario=="webster"
         else self.network.apply_uniform_all(30.0))
        self.network.reset_all()
        result = SimResult("Webster Optimized" if scenario=="webster"
                           else "Uniform (30s green)")
        record_every = max(1, duration//300)
        for t in range(1, duration+1):
            self.network.tick_all(1.0, float(t))
            if t % record_every == 0:
                result.history_cars.append((t, self.network.total_cars()))
                result.history_queue.append((t, self.network.avg_queue()))
            if progress_cb and t%50==0:
                progress_cb(t/duration)
        result.total_cars    = self.network.total_cars()
        result.throughput_hr = result.total_cars/duration*3600
        result.avg_queue     = self.network.avg_queue()
        for inter in self.network.all():
            result.per_intersection[(inter.row,inter.col)] = {
                "green_ns": inter.green_times[0],
                "green_ew": inter.green_times[1],
                "tph":      inter.throughput_per_hour(duration),
                "queue":    inter.avg_queue(),
            }
        return result

    def run_comparison(self, duration=3600, progress_cb=None):
        def cb1(p): progress_cb and progress_cb(p*.5)
        def cb2(p): progress_cb and progress_cb(.5+p*.5)
        return self.run("uniform",duration,cb1), self.run("webster",duration,cb2)


# ─────────────────────────────────────────────
#  UTILITIES
# ─────────────────────────────────────────────

def _road_key(r1,c1,r2,c2):
    return (min((r1,c1),(r2,c2)), max((r1,c1),(r2,c2)))

def _poisson(lam):
    if lam<=0: return 0
    if lam>30: return max(0,int(random.gauss(lam,math.sqrt(lam))))
    L,k,p = math.exp(-lam),0,1.0
    while p>L:
        p*=random.random(); k+=1
    return k-1

def improvement(u, w):
    tg = (w.throughput_hr-u.throughput_hr)/u.throughput_hr*100 if u.throughput_hr else 0
    qr = (u.avg_queue-w.avg_queue)/u.avg_queue*100 if u.avg_queue else 0
    return tg, qr
