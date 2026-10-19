"""
experiments.py
  exp1  three-way comparison   uniform / Webster / Webster + green wave
  exp2  sensitivity analysis   NS:EW demand ratio 1, 1.5, 2, 3, 4
  exp3  24-hour demand         uniform / fixed Webster plan / adaptive (15 min)
  test  self-tests             conservation of cars, offset sign, demand profile

Usage
  python experiments.py test
  python experiments.py exp1 exp2          # 1-hour experiments
  python experiments.py exp3 --seeds 3     # 24-hour experiment (slowest)
  python experiments.py all --seeds 5

All strategies use the SAME random seeds, so they see identical arrivals.
"""

import argparse
import csv
import math
import os
import sys
import time

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from model import Network, Simulator, DAILY_PROFILE, demand_multiplier

#experiment parameters
ROWS, COLS = 2, 3
ROAD_LENGTH_M = 400
SPEED_KMH = 60
BASE_NS, BASE_EW = 500, 120          # cars/hour on every N/S and E/W entry road
DURATION = 3600
RATIOS = [1.0, 1.5, 2.0, 3.0, 4.0]   # NS : EW (EW fixed at 120)
UPDATE_INTERVAL = 900                # 15 min
SCENARIOS = ["uniform", "webster", "green_wave", "webster_random"]
LABELS = {"uniform": "Uniform (30 s)", "webster": "Webster",
          "green_wave": "Webster + green wave",
          "webster_random": "Webster, random phases*",
          "fixed": "Fixed Webster plan", "adaptive": "Adaptive (15 min)"}
COLORS = {"uniform": "#d9534f", "webster": "#2e7d32", "green_wave": "#1565c0",
          "webster_random": "#9e9e9e",
          "fixed": "#ef6c00", "adaptive": "#2e7d32"}


def make_net(ns, ew):
    net = Network.build_grid(ROWS, COLS, ROAD_LENGTH_M, SPEED_KMH * 1000 / 3600)
    net.set_entry_demand(ns, ew)
    return net


def mean(x):
    return sum(x) / len(x)


def std(x):
    if len(x) < 2:
        return 0.0
    m = mean(x)
    return math.sqrt(sum((v - m) ** 2 for v in x) / (len(x) - 1))


def pct(new, base, lower_is_better=False):
    if not base:
        return float("nan")
    return ((base - new) if lower_is_better else (new - base)) / base * 100


def write_csv(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    print(f"  wrote {path}")


def run_scenarios(ns, ew, seeds, duration=DURATION):
    """{scenario: [SimResult per seed]} on a fresh network, common seeds."""
    out = {s: [] for s in SCENARIOS}
    for seed in seeds:
        net = make_net(ns, ew)
        sim = Simulator(net)
        for s in SCENARIOS:
            out[s].append(sim.run(s, duration, seed=seed))
    return out


# Experiment 1 - three-way comparison
def exp1(seeds, out):
    print(f"\n== Experiment 1: three-way comparison  (NS={BASE_NS}, EW={BASE_EW} cars/h per entry road, "
          f"{len(seeds)} seeds, {DURATION}s) ==")
    res = run_scenarios(BASE_NS, BASE_EW, seeds)
    base = res["uniform"]

    rows, summary = [], {}
    for s in SCENARIOS:
        r = res[s]
        thr = [x.throughput_hr for x in r]
        ext = [x.exit_hr for x in r]
        que = [x.avg_queue for x in r]
        dly = [x.avg_delay_s for x in r]
        # paired improvement per seed (same arrivals in every scenario)
        it = [pct(a.throughput_hr, b.throughput_hr) for a, b in zip(r, base)]
        iq = [pct(a.avg_queue, b.avg_queue, True) for a, b in zip(r, base)]
        summary[s] = dict(thr=thr, ext=ext, que=que, dly=dly, it=it, iq=iq)
        rows.append([LABELS[s], f"{mean(thr):.1f}", f"{std(thr):.1f}",
                     f"{mean(ext):.1f}", f"{std(ext):.1f}",
                     f"{mean(que):.3f}", f"{std(que):.3f}",
                     f"{mean(dly):.2f}", f"{std(dly):.2f}",
                     f"{mean(it):+.2f}", f"{std(it):.2f}",
                     f"{mean(iq):+.2f}", f"{std(iq):.2f}"])
    header = ["scenario", "throughput_mean", "throughput_std", "exit_mean", "exit_std",
              "queue_mean", "queue_std", "delay_s_mean", "delay_s_std",
              "throughput_gain_%_mean", "throughput_gain_%_std",
              "queue_reduction_%_mean", "queue_reduction_%_std"]
    write_csv(os.path.join(out, "exp1_comparison.csv"), header, rows)

    # paired comparison (same seed = same arrivals): does coordination itself help?
    pair_rows = []
    for other in ("webster", "webster_random"):
        dq = [pct(a.avg_queue, b.avg_queue, True) for a, b in zip(res["green_wave"], res[other])]
        dd = [pct(a.avg_delay_s, b.avg_delay_s, True) for a, b in zip(res["green_wave"], res[other])]
        wins = sum(1 for v in dq if v > 0)
        pair_rows.append([f"green_wave vs {other}", f"{mean(dq):+.2f}", f"{std(dq):.2f}",
                          f"{mean(dd):+.2f}", f"{std(dd):.2f}", f"{wins}/{len(dq)}"])
        print(f"  paired: green wave vs {LABELS[other]:<24} queue {mean(dq):+6.2f}% (std {std(dq):.2f}), "
              f"delay {mean(dd):+6.2f}%, better in {wins}/{len(dq)} seeds")
    write_csv(os.path.join(out, "exp1_paired_green_wave.csv"),
              ["comparison", "queue_reduction_%_mean", "queue_reduction_%_std",
               "delay_reduction_%_mean", "delay_reduction_%_std", "seeds_where_better"], pair_rows)

    # a look at the plans that were used
    net = make_net(BASE_NS, BASE_EW)
    sim = Simulator(net)
    sim.run("webster", 10, seed=1)
    i = net.get(0, 0)
    print(f"  Webster plan (all signals): green NS={i.green_times[0]:.1f}s  EW={i.green_times[1]:.1f}s  "
          f"cycle={i.cycle_length():.1f}s  y={tuple(round(v, 3) for v in i.flow_ratios())}")
    sim.run("green_wave", 10, seed=1)
    offs = {k: round(v.offset, 1) for k, v in net.grid.items()}
    print(f"  Green-wave offsets (NS corridor, per intersection): {offs}")

    print(f"\n  {'scenario':<22}{'throughput':>14}{'exit/h':>12}{'queue':>10}{'delay,s':>10}{'thr gain':>11}{'queue red.':>12}")
    for s in SCENARIOS:
        d = summary[s]
        print(f"  {LABELS[s]:<22}{mean(d['thr']):>14.1f}{mean(d['ext']):>12.1f}{mean(d['que']):>10.3f}"
              f"{mean(d['dly']):>10.2f}{mean(d['it']):>+10.2f}%{mean(d['iq']):>+11.2f}%")

    for key, ylabel, fname, title in [
            ("thr", "Throughput (discharges / hour)", "fig2_throughput.png", "Mean throughput"),
            ("que", "Mean queue per intersection (cars)", "fig3_queue.png", "Mean queue length")]:
        fig, ax = plt.subplots(figsize=(6.4, 4.2))
        vals = [mean(summary[s][key]) for s in SCENARIOS]
        errs = [std(summary[s][key]) for s in SCENARIOS]
        bars = ax.bar([LABELS[s] for s in SCENARIOS], vals, yerr=errs, capsize=5,
                      color=[COLORS[s] for s in SCENARIOS])
        for b, v in zip(bars, vals):
            ax.text(b.get_x() + b.get_width() / 2, v, f"{v:.1f}" if key == "thr" else f"{v:.2f}",
                    ha="center", va="bottom", fontsize=9)
        ax.set_ylabel(ylabel)
        ax.set_title(f"{title} ({len(seeds)} seeds, mean ± std)")
        ax.grid(axis="y", alpha=0.3)
        ax.tick_params(axis="x", labelsize=8)
        fig.tight_layout(rect=(0, 0.04, 1, 1))
        fig.text(0.01, 0.01, "* diagnostic: Webster splits, but every signal starts at a random point of its cycle", fontsize=7, color="#555")
        fig.savefig(os.path.join(out, fname), dpi=160)
        plt.close(fig)
        print(f"  wrote {os.path.join(out, fname)}")


# Experiment 2 - sensitivity to directional asymmetry

def exp2(seeds, out):
    print(f"\n== Experiment 2: sensitivity (EW={BASE_EW}, NS = R x {BASE_EW}, {len(seeds)} seeds, {DURATION}s) ==")
    rows, curves = [], {"webster": [], "green_wave": [], "webster_random": []}
    for R in RATIOS:
        ns = BASE_EW * R
        res = run_scenarios(ns, BASE_EW, seeds)
        u = res["uniform"]
        row = [R, ns, BASE_EW, f"{mean([x.throughput_hr for x in u]):.1f}"]
        for s in ("webster", "green_wave", "webster_random"):
            r = res[s]
            it = [pct(a.throughput_hr, b.throughput_hr) for a, b in zip(r, u)]
            iq = [pct(a.avg_queue, b.avg_queue, True) for a, b in zip(r, u)]
            row += [f"{mean([x.throughput_hr for x in r]):.1f}",
                    f"{mean(it):+.2f}", f"{std(it):.2f}",
                    f"{mean(iq):+.2f}", f"{std(iq):.2f}"]
            curves[s].append((R, mean(it), std(it), mean(iq), std(iq)))
        row += [f"{mean([x.avg_queue for x in u]):.3f}"]
        row += [f"{mean([x.exit_hr for x in u]):.1f}"]
        for s in ("webster", "green_wave", "webster_random"):
            ie = [pct(a.exit_hr, b.exit_hr) for a, b in zip(res[s], u)]
            row += [f"{mean([x.exit_hr for x in res[s]]):.1f}", f"{mean(ie):+.2f}", f"{std(ie):.2f}"]
        rows.append(row)
        print(f"  R={R:<4} uniform thr={mean([x.throughput_hr for x in u]):8.1f} | "
              f"Webster: thr {curves['webster'][-1][1]:+6.2f}%  queue {curves['webster'][-1][3]:+7.2f}% | "
              f"Green wave: thr {curves['green_wave'][-1][1]:+6.2f}%  queue {curves['green_wave'][-1][3]:+7.2f}% | "
              f"random phases: queue {curves['webster_random'][-1][3]:+7.2f}%")
    header = ["R", "NS_cph", "EW_cph", "uniform_throughput",
              "webster_throughput", "webster_thr_gain_%", "webster_thr_gain_std",
              "webster_queue_red_%", "webster_queue_red_std",
              "gw_throughput", "gw_thr_gain_%", "gw_thr_gain_std",
              "gw_queue_red_%", "gw_queue_red_std",
              "wrandom_throughput", "wrandom_thr_gain_%", "wrandom_thr_gain_std",
              "wrandom_queue_red_%", "wrandom_queue_red_std", "uniform_queue",
              "uniform_exit", "webster_exit", "webster_exit_gain_%", "webster_exit_gain_std",
              "gw_exit", "gw_exit_gain_%", "gw_exit_gain_std",
              "wrandom_exit", "wrandom_exit_gain_%", "wrandom_exit_gain_std"]
    write_csv(os.path.join(out, "exp2_sensitivity.csv"), header, rows)

    fig, axs = plt.subplots(1, 2, figsize=(11, 4.2))
    for ax, (idx, ylabel, ttl) in zip(axs, [(1, "Throughput improvement over uniform, %", "Throughput"),
                                           (3, "Queue reduction over uniform, %", "Queue length")]):
        for s in ("webster", "green_wave", "webster_random"):
            xs = [c[0] for c in curves[s]]
            ys = [c[idx] for c in curves[s]]
            es = [c[idx + 1] for c in curves[s]]
            ax.errorbar(xs, ys, yerr=es, marker="o", capsize=4, label=LABELS[s], color=COLORS[s],
                        ls="--" if s == "webster_random" else "-")
        ax.axhline(0, color="#777", lw=0.8)
        ax.set_xlabel("Demand asymmetry R = NS / EW")
        ax.set_ylabel(ylabel)
        ax.set_title(ttl)
        ax.set_xticks(RATIOS)
        ax.grid(alpha=0.3)
        ax.legend()
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fig.text(0.01, 0.01, "* diagnostic: Webster splits, but every signal starts at a random point of its cycle", fontsize=7, color="#555")
    path = os.path.join(out, "fig4_sensitivity.png")
    fig.savefig(path, dpi=160)
    plt.close(fig)
    print(f"  wrote {path}")


# Experiment 3 - 24-hour demand
def exp3(seeds, out):
    print(f"\n== Experiment 3: 24 h demand profile (NS={BASE_NS}, EW={BASE_EW} x m(t); {len(seeds)} seeds; "
          f"adaptive update every {UPDATE_INTERVAL//60} min) ==")
    modes = ["uniform", "fixed", "adaptive"]
    runs = {m: [] for m in modes}
    t0 = time.time()
    for seed in seeds:
        for m in modes:
            net = make_net(BASE_NS, BASE_EW)
            runs[m].append(Simulator(net).run_daily(m, update_interval=UPDATE_INTERVAL, seed=seed))
        print(f"  seed {seed} done ({time.time()-t0:.0f}s)")

    # table 5
    rows = []
    stats = {m: [r.period_stats() for r in runs[m]] for m in modes}
    print(f"\n  {'period':<18}{'m':>5}  " + "  ".join(f"{LABELS[m][:14]:>14} thr/queue" for m in modes))
    for k, (a, b, mult, name) in enumerate(DAILY_PROFILE):
        row = [name, f"{a:02d}:00-{b:02d}:00", mult]
        line = f"  {name:<18}{mult:>5}  "
        for m in modes:
            thr = mean([s[k]["throughput_hr"] for s in stats[m]])
            que = mean([s[k]["mean_queue"] for s in stats[m]])
            row += [f"{thr:.1f}", f"{que:.3f}", f"{std([s[k]['mean_queue'] for s in stats[m]]):.3f}"]
            line += f"{thr:>14.1f}/{que:<8.3f}"
        for m in modes:
            row.append(f"{mean([s[k]['exit_hr'] for s in stats[m]]):.1f}")
        rows.append(row)
        print(line)
    header = ["period", "hours", "multiplier"]
    for m in modes:
        header += [f"{m}_throughput", f"{m}_mean_queue", f"{m}_queue_std_over_seeds"]
    header += [f"{m}_exit_throughput" for m in modes]
    write_csv(os.path.join(out, "exp3_daily_periods.csv"), header, rows)

    # whole-day totals
    tot_rows = []
    for m in modes:
        thr = [r.cars[-1] / 24 for r in runs[m]]
        que = [mean(r.queue) for r in runs[m]]
        ext = [r.exited[-1] / 24 for r in runs[m]]
        tot_rows.append([LABELS[m], f"{mean(thr):.1f}", f"{mean(que):.3f}", f"{mean(ext):.1f}"])
        print(f"  whole day  {LABELS[m]:<20} throughput {mean(thr):8.1f}/h   mean queue {mean(que):.3f}")
    write_csv(os.path.join(out, "exp3_daily_totals.csv"),
              ["strategy", "throughput_per_hour", "mean_queue", "exit_throughput_per_hour"], tot_rows)

    # adaptive plan log
    log = runs["adaptive"][0].plan_log
    write_csv(os.path.join(out, "exp3_adaptive_plan_log.csv"),
              ["t_s", "cycle_s", "green_NS_s", "green_EW_s", "multiplier"],
              [[t, f"{c:.1f}", f"{g1:.1f}", f"{g2:.1f}", m] for t, c, g1, g2, m in log])

    # fig 5 : demand multiplier
    hrs = [i / 60 for i in range(24 * 60)]
    fig, ax = plt.subplots(figsize=(8, 3.4))
    ax.step(hrs, [demand_multiplier(h * 3600) for h in hrs], where="post", color="#1565c0")
    ax.set_xlabel("Time of day, h"); ax.set_ylabel("Demand multiplier m(t)")
    ax.set_title("Synthetic 24-hour demand profile")
    ax.set_xlim(0, 24); ax.set_xticks(range(0, 25, 3)); ax.grid(alpha=0.3)
    fig.tight_layout(); fig.savefig(os.path.join(out, "fig5_demand_profile.png"), dpi=160); plt.close(fig)

    # fig 6 : green times / cycle of the adaptive plan
    ts = [t / 3600 for t, *_ in log]
    fig, ax = plt.subplots(figsize=(8, 3.8))
    ax.step(ts, [c for _, c, *_ in log], where="post", label="Cycle", color="#333")
    ax.step(ts, [g for _, _, g, _, _ in log], where="post", label="Green NS", color="#1565c0")
    ax.step(ts, [g for _, _, _, g, _ in log], where="post", label="Green EW", color="#ef6c00")
    ax.set_xlabel("Time of day, h"); ax.set_ylabel("Seconds")
    ax.set_title("Adaptive Webster plan (recalculated every 15 min)")
    ax.set_xlim(0, 24); ax.set_xticks(range(0, 25, 3)); ax.grid(alpha=0.3); ax.legend()
    fig.tight_layout(); fig.savefig(os.path.join(out, "fig6_adaptive_plan.png"), dpi=160); plt.close(fig)

    # fig 7 : hourly mean queue
    fig, ax = plt.subplots(figsize=(8, 3.8))
    for m in modes:
        hourly = [mean([mean(r.queue[h * 3600:(h + 1) * 3600]) for r in runs[m]]) for h in range(24)]
        ax.plot([h + 0.5 for h in range(24)], hourly, marker="o", ms=3,
                label=LABELS[m], color=COLORS[m])
    ax.set_xlabel("Time of day, h"); ax.set_ylabel("Mean queue per intersection (cars)")
    ax.set_title(f"Queue length over the day (hourly mean, {len(seeds)} seeds)")
    ax.set_xlim(0, 24); ax.set_xticks(range(0, 25, 3)); ax.grid(alpha=0.3); ax.legend()
    fig.tight_layout(); fig.savefig(os.path.join(out, "fig7_daily_queue.png"), dpi=160); plt.close(fig)
    print(f"  wrote fig5_demand_profile.png, fig6_adaptive_plan.png, fig7_daily_queue.png in {out}")

# Self-tests (report section 3.3 checklist)

def selftest():
    ok_all = True

    def check(name, cond, info=""):
        nonlocal ok_all
        ok_all &= bool(cond)
        print(f"  [{'PASS' if cond else 'FAIL'}] {name} {info}")

    print("\n== Self-tests ==")

    # 1. demand profile
    expect = {0: .3, 5.9: .3, 6: 1.5, 8.99: 1.5, 9: .7, 15.9: .7, 16: 1.6, 18.9: 1.6, 19: .5, 23.9: .5, 24: .3, 30: 1.5}
    check("demand multiplier follows the profile",
          all(abs(demand_multiplier(h * 3600) - m) < 1e-9 for h, m in expect.items()))

    # 2. Webster hand calculation, baseline demand
    net = make_net(BASE_NS, BASE_EW)
    i = net.get(0, 0)
    lam_ns = 2 * BASE_NS / 3600
    lam_ew = 2 * BASE_EW / 3600
    y1, y2 = lam_ns / 1.0, lam_ew / 1.0          # saturation 2 lanes x 0.5 = 1 car/s
    L = 8.0
    C = max(40, min((1.5 * L + 5) / (1 - (y1 + y2)), 120))
    g1 = max(20, (C - L) * y1 / (y1 + y2)); g2 = max(20, (C - L) * y2 / (y1 + y2))
    _, a, b = i.compute_webster()
    check("Webster green times match the hand calculation",
          abs(a - g1) < 1e-6 and abs(b - g2) < 1e-6, f"(NS {a:.2f}s / EW {b:.2f}s)")

    # 3. conservation of cars:  entered = exited + queued + on the road
    for s in SCENARIOS:
        net = make_net(BASE_NS * 2, BASE_EW * 2)
        Simulator(net).run(s, 1800, seed=7)
        lhs = net.entered
        rhs = net.exited + net.total_queue() + net.cars_in_transit()
        check(f"cars are conserved ({s})", abs(lhs - rhs) < 1e-6 * max(lhs, 1),
              f"(entered {lhs:.0f} = exited {net.exited:.0f} + queued {net.total_queue():.1f} + in transit {net.cars_in_transit():.1f})")

    # 4. green wave: offset sign / checks of section 3.3
    net = make_net(0, 0)                          # no random traffic: pure signal check
    sim = Simulator(net)
    net.demand_multiplier = 1.0
    net.reset_all(); net.apply_webster_all()
    cyc, offs = net.apply_green_wave("NS")
    travel = ROAD_LENGTH_M / (SPEED_KMH / 3.6)
    check("reference intersections have offset 0",
          all(offs[(0, c)] == 0 for c in range(COLS)))
    check("offset of the next intersection = travel time (400 m / 60 km/h)",
          all(abs(offs[(1, c)] - travel % cyc) < 1e-9 for c in range(COLS)),
          f"({offs[(1, 0)]:.2f}s, travel {travel:.2f}s)")
    check("offsets lie in [0, C)", all(0 <= v < cyc for v in offs.values()), f"(C={cyc:.1f}s)")
    check("coordinated plan keeps the Webster splits (equal cycles -> unchanged greens)",
          abs(net.get(0, 0).green_times[0] - net.get(0, 0).compute_webster()[1]) < 1e-9)
    # step the clock and find when NS turns green at (0,0) and (1,0)
    starts = {(0, 0): [], (1, 0): []}
    prev = {k: net.get(*k).is_green(0) for k in starts}
    for t in range(1, int(4 * cyc) + 1):
        net.tick_all(1.0, float(t))
        for k in starts:
            now = net.get(*k).is_green(0)
            if now and not prev[k]:
                starts[k].append(t)
            prev[k] = now
    diffs = [(b - a) % cyc for a, b in zip(starts[(0, 0)], starts[(1, 0)])]
    check("NS green at the next intersection starts one travel time later",
          diffs and all(abs(d - travel) <= 1.01 for d in diffs),
          f"(green starts {starts[(0, 0)][:3]} vs {starts[(1, 0)][:3]}, shift {diffs[:3]})")

    # 5. same seed -> same arrivals for every scenario
    net = make_net(BASE_NS, BASE_EW)
    sim = Simulator(net)
    ent = [(sim.run(s, 600, seed=3), net.entered)[1] for s in SCENARIOS]
    check("identical random arrivals in all scenarios for one seed",
          max(ent) - min(ent) < 1e-9, f"(entered {ent[0]:.0f})")

    print("\n  ALL TESTS PASSED" if ok_all else "\n  SOME TESTS FAILED")
    return ok_all


# ===========================================================================
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("what", nargs="+", choices=["test", "exp1", "exp2", "exp3", "all"])
    ap.add_argument("--seeds", type=int, default=5, help="number of random seeds (default 5)")
    ap.add_argument("--seeds-daily", type=int, default=None,
                    help="seeds for the 24 h experiment (default: min(--seeds, 3))")
    ap.add_argument("--out", default="results", help="output folder (default: results)")
    ap.add_argument("--ns", type=float, default=None, help="baseline NS demand per entry road, cars/h (default 500)")
    ap.add_argument("--ew", type=float, default=None, help="baseline EW demand per entry road, cars/h (default 120)")
    a = ap.parse_args()
    global BASE_NS, BASE_EW
    if a.ns is not None: BASE_NS = a.ns
    if a.ew is not None: BASE_EW = a.ew

    os.makedirs(a.out, exist_ok=True)
    todo = set(a.what)
    if "all" in todo:
        todo |= {"test", "exp1", "exp2", "exp3"}
    seeds = list(range(1, a.seeds + 1))
    seeds_d = list(range(1, (a.seeds_daily or min(a.seeds, 3)) + 1))

    ok = True
    if "test" in todo:
        ok = selftest()
    if "exp1" in todo:
        exp1(seeds, a.out)
    if "exp2" in todo:
        exp2(seeds, a.out)
    if "exp3" in todo:
        exp3(seeds_d, a.out)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
