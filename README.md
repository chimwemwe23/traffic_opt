# Traffic Signal Optimisation
 
Simulation of traffic-signal timing on a small road network. Three strategies are compared:
 
| Scenario | Green times | Offsets between signals |
|---|---|---|
| **Uniform** | 30 s / 30 s | none |
| **Webster** | cycle and split from Webster's formula | none |
| **Webster + green wave** | the same Webster split (one common cycle) | travel-time offsets, `delta_i = (sum of travel times) mod C` |
 
The model also supports a **24-hour demand profile** (`m(t)` = 0.3 / 1.5 / 0.7 / 1.6 / 0.5) and
an **adaptive plan** that recalculates Webster's timings every 15 minutes.
 
## Files
 
| File | What it does |
|---|---|
| `model.py` | All simulation logic: `Road`, `Intersection`, `Network`, `Simulator`, Webster, green-wave offsets, demand profile |
| `experiments.py` | Runs the three experiments + self-tests without the GUI, writes CSV tables and PNG charts |
| `gui.py`, `main.py` | Tkinter GUI (unchanged; it still compares uniform vs Webster) |
| `results/` | Output for the report setup (NS 500, EW 120 cars/h per entry road) |
| `results_heavy_demand/` | Same, with NS 800, EW 200 (the adaptive plan actually changes at the peaks) |
 
## Run
 
```
pip install matplotlib
python main.py                       # GUI: place intersections, draw roads, run comparison
 
python experiments.py test           # self-tests (cars conserved, offset sign, demand profile ...)
python experiments.py exp1 exp2      # comparison + sensitivity (1-hour runs, ~1 min)
python experiments.py exp3           # 24-hour experiment (~50 s per seed)
python experiments.py all --seeds 5 --out results
python experiments.py exp1 exp3 --ns 800 --ew 200 --out results_heavy_demand
```
 
All strategies use the **same random seeds**, so they see identical car arrivals.
 
## Model assumptions 
 
* Direction `d` = side a car arrives from (N, S, E, W). Cars drive straight through.
* Demand is given **per entry road**: "NS 500" means 500 cars/h on every N and every S entry road.
* Saturation flow 0.5 cars/s per lane, 2 lanes -> 1 car/s per direction.
* Yellow 4 s, lost time `L = 2 x yellow = 8 s`; real cycle = green_NS + green_EW + 2 x yellow.
* Webster: `C = (1.5 L + 5)/(1 - Y)` clamped to [40, 120] s; greens clamped to [20, 120] s.
  Uniform scenario: 30 s green + 4 s yellow per phase -> 68 s cycle.
* Flow ratio `y` = (sum of both opposing approaches) / saturation flow of one direction.
* Webster uses the **expected demand** of each approach (entry demand of the road behind it, times `m(t)`).
* Green wave: one common cycle (the longest Webster cycle, splits scaled proportionally), reference
  intersection = first one of each line, progression southbound (NS corridor) or eastbound (EW).
* Indicators: throughput (discharges per hour over all intersections, and cars leaving the network),
  mean queue per intersection, average delay (queue-seconds per discharged car, Little's law).
