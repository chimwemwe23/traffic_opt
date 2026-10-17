Program to compute the optimal phases of green light intervals at a traffic intersection. **Using Webster's formula** to calculate the optimal amount of effective green time, taking into account the flow ratios of every lane. Seeing how Webster's formula provides a more effective way of allocating green time than, say, a uniform distribution (30s for every direction).

## Features
- **Webster's formula** - stimulates green phase durations based on real traffic demand
- **Discrete-time simulation** - simulates 1 hour of traffic second-bysecond with Poisson-distributed random arrivals
- **Side-by-side comparison** - watch the traffic lights change in real time
- **Results charts** - cumulative throughput and queue length over time (via matplotlib)

## How it Works
Each intersection has two phases:
- **Phase 0** - North-South road has green
- **Phase 1** - East-West road has green

Webster's formula calculates the optimal cycle length and splits green time **proportionally to traffic demand.**

```
C = (1.5L + 5) / (1- Y)
gi = (C - L) * (yi / Y)
```

Where 'C' is the cycle length, 'L' is total lost time, 'Y = y1 + y2' is the sum of flow ratios, and 'gi' is the green time for each phase.

## Requirements
- Python 3.9+
- tkinter
- matplotlib

## Installation and Running
# Clone the repository
git clone https://github.com/chimwemwe23/traffic_signal_optimisation.git

cd traffic_signal_optimisation

# Install the dependency
pip install matplotlib

# Run
python main.py

---
## Usage
1. **Click any intersection** on the grid to select it
2. **Edit arrival rates** (cars/second per direction) in the right panel
3. Use **presets** - Light / Medium / Heavy / Asymmetric - for quick setup
5. Hit **"Live Preview"** to wach the animated simulation
6. Hit **"Run Comparison"** to stimulate 1 hour and see the results

---

## Project Structure

```
traffic-signal-optimizer
├── main.py       # Entry point and main app window
├── model.py      # Intersection, Network, Simulator, Webster formula
└── gui.py        # NetworkCanvas, InputPanel, ResultsWindow
```

| File | Responsibility |
|---|---|
| `model.py` | All simulation logic. Contains `Intersection` (one crossroads), `Network` (the grid), `Simulator` (runs the clock), and Webster's formula. |
| `gui.py` | All visual code. `NetworkCanvas` draws the animated road grid. `InputPanel` handles editing. `ResultsWindow` shows comparison charts. |
| `main.py` | Wires model and GUI together. Handles button events, runs simulations on background threads so the GUI stays responsive. |

---

## License

MIT
