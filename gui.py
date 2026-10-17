"""
gui.py
------
All GUI code.

Interaction model:
  LEFT-CLICK  empty grid node  → place intersection
  LEFT-CLICK  existing intersection → remove it (if no roads attached)
  LEFT-CLICK  intersection (road mode) → start/complete drawing a road
  RIGHT-CLICK intersection → select for editing in panel

Modes (toolbar toggle):
  PLACE mode  — left-click places/removes intersections
  ROAD mode   — left-click first intersection, then second → road dialog pops up
"""

import tkinter as tk
from tkinter import ttk, simpledialog, messagebox

try:
    import matplotlib; matplotlib.use("TkAgg")
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    HAS_MPL = True
except ImportError:
    HAS_MPL = False

from model import (Intersection, Network, Road, improvement,
                   DEFAULT_ROAD_LENGTH, DEFAULT_SPEED_MS, DEFAULT_DEMAND_CPH)

BG, BG2, BG3 = "#1a1a2e", "#16213e", "#0f3460"
GREEN, RED, YELLOW = "#00e676", "#ff1744", "#ffea00"
BLUE, GOLD, FG, FG_DIM = "#29b6f6", "#ffd740", "#e0e0e0", "#aaaaaa"
ORANGE, PURPLE = "#ff9800", "#ce93d8"


# ─────────────────────────────────────────────
#  ROAD DIALOG  — pops up when user draws a road
# ─────────────────────────────────────────────

class RoadDialog(tk.Toplevel):
    """
    Small popup asking for road length and speed when the user
    connects two intersections.
    """
    def __init__(self, parent, cell_a, cell_b,
                 default_length=DEFAULT_ROAD_LENGTH,
                 default_speed=60):
        super().__init__(parent)
        self.title("New Road Properties")
        self.configure(bg=BG2)
        self.resizable(False, False)
        self.result = None   # set to (length_m, speed_ms) on confirm

        self.grab_set()   # modal

        tk.Label(self, text=f"Road: {cell_a} ↔ {cell_b}",
                 bg=BG2, fg=GOLD, font=("Arial",11,"bold")).pack(pady=(14,6), padx=20)

        frame = tk.Frame(self, bg=BG2); frame.pack(padx=20, pady=4)

        # Length
        tk.Label(frame, text="Length (metres):", bg=BG2, fg=FG,
                 font=("Arial",10), anchor="w", width=18).grid(row=0,column=0,pady=4)
        self._len = tk.IntVar(value=int(default_length))
        tk.Entry(frame, textvariable=self._len, width=10,
                 bg=BG3, fg=FG, insertbackground="white",
                 font=("Arial",10)).grid(row=0,column=1,padx=6)

        # Speed
        tk.Label(frame, text="Speed (km/h):", bg=BG2, fg=FG,
                 font=("Arial",10), anchor="w", width=18).grid(row=1,column=0,pady=4)
        self._spd = tk.IntVar(value=int(default_speed))
        tk.Entry(frame, textvariable=self._spd, width=10,
                 bg=BG3, fg=FG, insertbackground="white",
                 font=("Arial",10)).grid(row=1,column=1,padx=6)

        # Travel time preview (updates live)
        self._tt_lbl = tk.Label(self, text="", bg=BG2, fg=GOLD,
                                font=("Arial",9))
        self._tt_lbl.pack()
        self._len.trace_add("write", lambda *_: self._update_tt())
        self._spd.trace_add("write", lambda *_: self._update_tt())
        self._update_tt()

        # Buttons
        bf = tk.Frame(self, bg=BG2); bf.pack(pady=12, padx=20, fill="x")
        tk.Button(bf, text="Add Road", command=self._confirm,
                  bg="#1565c0", fg="white", relief="flat",
                  font=("Arial",10,"bold"), padx=10, pady=5,
                  activebackground="#1976d2").pack(side="left", expand=True, fill="x", padx=(0,4))
        tk.Button(bf, text="Cancel", command=self.destroy,
                  bg=BG3, fg=FG, relief="flat",
                  font=("Arial",10), padx=10, pady=5).pack(side="left", expand=True, fill="x")

        # Centre on parent
        self.update_idletasks()
        px, py = parent.winfo_rootx(), parent.winfo_rooty()
        pw, ph = parent.winfo_width(), parent.winfo_height()
        w, h   = self.winfo_width(), self.winfo_height()
        self.geometry(f"+{px+pw//2-w//2}+{py+ph//2-h//2}")

    def _update_tt(self):
        try:
            length = max(1, self._len.get())
            speed  = max(1, self._spd.get())
            tt     = length / (speed * 1000/3600)
            self._tt_lbl.config(text=f"→ Travel time: {tt:.1f} seconds")
        except tk.TclError:
            pass

    def _confirm(self):
        try:
            length = max(10, int(self._len.get()))
            speed  = max(1,  int(self._spd.get()))
            self.result = (float(length), speed*1000/3600)
            self.destroy()
        except (ValueError, tk.TclError):
            messagebox.showerror("Invalid input", "Please enter valid numbers.",
                                 parent=self)


# ─────────────────────────────────────────────
#  NETWORK CANVAS
# ─────────────────────────────────────────────

class NetworkCanvas(tk.Canvas):
    """
    Draws the grid. Two interaction modes controlled by the toolbar.

    PLACE mode (default):
      Left-click empty dot  → place intersection
      Left-click intersection → remove it (only if no user roads attached)

    ROAD mode:
      Left-click intersection A → highlights it (first endpoint)
      Left-click intersection B → opens RoadDialog, adds road on confirm
      Left-click same intersection twice → cancels

    Right-click (either mode):
      Select intersection for editing in the input panel
    """

    R_INTER = 20
    R_LIGHT = 6
    MODE_PLACE = "place"
    MODE_ROAD  = "road"

    def __init__(self, parent, network: Network, on_select=None, **kw):
        super().__init__(parent, bg=BG, highlightthickness=0, **kw)
        self.network    = network
        self.on_select  = on_select
        self.selected   = None      # (r,c) selected for editing
        self.mode       = self.MODE_PLACE
        self._road_start = None     # first intersection picked in road mode
        self.margin     = 55

        self.bind("<Configure>", lambda e: self.draw())
        self.bind("<Button-1>",  self._left_click)
        self.bind("<Button-3>",  self._right_click)

    # ── Layout ───────────────────────────────

    def _cell(self):
        w,h = self.winfo_width(), self.winfo_height()
        cw = (w-2*self.margin)/max(self.network.cols,1)
        ch = (h-2*self.margin)/max(self.network.rows,1)
        return cw, ch

    def _pos(self, r, c):
        cw,ch = self._cell()
        return self.margin+c*cw+cw/2, self.margin+r*ch+ch/2

    def _nearest_cell(self, x, y):
        cw,ch = self._cell()
        c = round((x-self.margin-cw/2)/cw)
        r = round((y-self.margin-ch/2)/ch)
        return (max(0,min(r,self.network.rows-1)),
                max(0,min(c,self.network.cols-1)))

    def _nearest_intersection(self, x, y):
        """Return (r,c) of the closest existing intersection to pixel, or None."""
        best, best_d = None, 9999
        for (r,c) in self.network.grid:
            ix,iy = self._pos(r,c)
            d = ((x-ix)**2+(y-iy)**2)**0.5
            if d < best_d:
                best_d, best = d, (r,c)
        return best if best and best_d < self.R_INTER*2.5 else None

    # ── Events ───────────────────────────────

    def _left_click(self, event):
        if self.mode == self.MODE_PLACE:
            self._place_mode_click(event)
        else:
            self._road_mode_click(event)

    def _place_mode_click(self, event):
        r,c = self._nearest_cell(event.x, event.y)
        if (r,c) in self.network.grid:
            # Only remove if no user-drawn roads are attached
            has_road = any((r,c) in k for k in self.network.roads)
            if has_road:
                messagebox.showinfo("Cannot remove",
                    "Remove connected roads first before deleting this intersection.",
                    parent=self.winfo_toplevel())
                return
            self.network.remove_intersection(r,c)
            if self.selected == (r,c):
                self.selected = None
                if self.on_select: self.on_select(None,None)
        else:
            self.network.add_intersection(r,c)
        self.draw()

    def _road_mode_click(self, event):
        inter = self._nearest_intersection(event.x, event.y)
        if inter is None:
            return   # clicked empty space in road mode — ignore

        if self._road_start is None:
            # First click — pick starting intersection
            self._road_start = inter
            self.draw()
        elif self._road_start == inter:
            # Clicked same intersection — cancel
            self._road_start = None
            self.draw()
        else:
            # Second click — try to draw road
            r1,c1 = self._road_start
            r2,c2 = inter
            self._road_start = None

            if not self.network._are_adjacent(r1,c1,r2,c2):
                messagebox.showinfo("Not adjacent",
                    "Roads can only connect adjacent intersections "
                    "(horizontally or vertically next to each other).",
                    parent=self.winfo_toplevel())
                self.draw()
                return

            if self.network.has_road(r1,c1,r2,c2):
                # Road exists — ask if user wants to remove it
                if messagebox.askyesno("Remove road?",
                    f"A road already exists between {(r1,c1)} and {(r2,c2)}.\n"
                    "Do you want to remove it?",
                    parent=self.winfo_toplevel()):
                    self.network.remove_road(r1,c1,r2,c2)
                self.draw()
                return

            # Open road properties dialog
            dlg = RoadDialog(self.winfo_toplevel(), (r1,c1), (r2,c2))
            self.winfo_toplevel().wait_window(dlg)
            if dlg.result:
                length_m, speed_ms = dlg.result
                self.network.add_road(r1,c1,r2,c2,
                                      length_m=length_m, speed_ms=speed_ms)
            self.draw()

    def _right_click(self, event):
        inter = self._nearest_intersection(event.x, event.y)
        if inter:
            self.selected = inter
            self.draw()
            if self.on_select: self.on_select(*inter)

    # ── Drawing ───────────────────────────────

    def draw(self):
        self.delete("all")
        if self.winfo_width() < 10: return
        self._draw_grid_hints()
        self._draw_roads()
        self._draw_intersections()
        self._draw_labels()
        self._draw_legend()

    def _draw_grid_hints(self):
        for r in range(self.network.rows):
            for c in range(self.network.cols):
                if (r,c) not in self.network.grid:
                    ix,iy = self._pos(r,c)
                    self.create_oval(ix-3,iy-3,ix+3,iy+3,
                                     fill="#2a2a4a", outline="#3a3a6a")

    def _draw_roads(self):
        ri = self.R_INTER

        # Internal roads (user-drawn) — solid grey lines with road info
        for (a,b), road in self.network.roads.items():
            ax,ay = self._pos(*a)
            bx,by = self._pos(*b)
            self.create_line(ax,ay,bx,by, fill="#666", width=6)
            # Road label: length and speed
            mx,my = (ax+bx)/2, (ay+by)/2
            tt = road.travel_time
            spd = road.speed_ms*3.6
            self.create_text(mx,my-10,
                             text=f"{road.length_m:.0f}m | {spd:.0f}km/h | {tt:.0f}s",
                             fill=PURPLE, font=("Arial",8,"bold"))
            # Bidirectional arrows
            self._draw_arrows(ax,ay,bx,by)

        # External entry road stubs — orange dashed
        cw,ch = self._cell()
        stub = min(cw,ch)*0.22
        for ((r,c),d), road in self.network.entry_roads.items():
            ix,iy = self._pos(r,c)
            offsets = {
                0:(ix,iy-ri-stub,ix,iy-ri),
                1:(ix,iy+ri,ix,iy+ri+stub),
                2:(ix+ri,iy,ix+ri+stub,iy),
                3:(ix-ri-stub,iy,ix-ri,iy),
            }
            self.create_line(*offsets[d], fill=ORANGE, width=3,
                             dash=(4,3), capstyle=tk.ROUND)

    def _draw_arrows(self, ax, ay, bx, by):
        """Draw small bidirectional arrows along a road."""
        import math
        dx, dy = bx-ax, by-ay
        dist   = max((dx**2+dy**2)**0.5, 1)
        ux, uy = dx/dist, dy/dist   # unit vector A→B
        px, py = -uy, ux            # perpendicular

        offset = 5   # pixels to offset each arrow from centre line
        arr_len = 10

        for t, direction in [(0.35, 1), (0.65, -1)]:
            mx = ax + dx*t + px*offset*direction
            my = ay + dy*t + py*offset*direction
            # Arrow tip going A→B
            tip_x = mx + ux*arr_len*direction
            tip_y = my + uy*arr_len*direction
            self.create_line(mx, my, tip_x, tip_y,
                             fill=BLUE, width=2, arrow=tk.LAST,
                             arrowshape=(6,8,3))

    def _draw_intersections(self):
        ri,rl = self.R_INTER, self.R_LIGHT
        for (r,c), inter in self.network.grid.items():
            ix,iy = self._pos(r,c)

            # Road-mode highlight (first endpoint selected)
            if self._road_start == (r,c):
                self.create_oval(ix-ri-8,iy-ri-8,ix+ri+8,iy+ri+8,
                                 fill="", outline=BLUE, width=3, dash=(4,2))

            # Selection ring
            if self.selected == (r,c):
                self.create_oval(ix-ri-5,iy-ri-5,ix+ri+5,iy+ri+5,
                                 fill="", outline=GOLD, width=3)

            # Intersection circle
            self.create_oval(ix-ri,iy-ri,ix+ri,iy+ri, fill=FG, outline="#888")

            # Traffic light dots
            for d,(lx,ly) in enumerate([
                (ix,iy-ri-rl-3),(ix,iy+ri+rl+3),
                (ix+ri+rl+3,iy),(ix-ri-rl-3,iy),
            ]):
                col = (YELLOW if inter.is_yellow(d) else
                       GREEN  if inter.is_green(d)  else RED)
                self.create_oval(lx-rl,ly-rl,lx+rl,ly+rl, fill=col, outline="")

            # Queue bars
            bl = ri*1.3
            for d,(bx,by,ddx,ddy) in enumerate([
                (ix-ri-10,iy-ri,0,-bl),(ix+ri+6,iy+ri,0,bl),
                (ix+ri,iy-ri-10,bl,0),(ix-ri,iy+ri+6,-bl,0),
            ]):
                frac = min(inter.queue[d],20)/20
                if frac>0.01:
                    self.create_line(bx,by,bx+ddx*frac,by+ddy*frac,
                                     fill=BLUE,width=4,capstyle=tk.ROUND)

            # Label
            self.create_text(ix,iy, text=f"{r},{c}",
                             fill="#333", font=("Arial",7,"bold"))

    def _draw_labels(self):
        for c in range(self.network.cols):
            ix,_ = self._pos(0,c)
            self.create_text(ix,self.margin-20,text=f"Col {c}",
                             fill=FG_DIM,font=("Arial",9))
        for r in range(self.network.rows):
            _,iy = self._pos(r,0)
            self.create_text(self.margin-22,iy,text=f"Row {r}",
                             fill=FG_DIM,font=("Arial",9))

    def _draw_legend(self):
        y = 12
        items = [
            (ORANGE, "-- external entry", True),
            ("#666", "— user road (bidirectional)", False),
            (BLUE,   "→ direction of travel", False),
        ]
        x = 10
        for color, text, dashed in items:
            dash = (4,3) if dashed else ()
            self.create_line(x,y,x+22,y, fill=color, width=2, dash=dash)
            self.create_text(x+26,y, text=text, fill=color,
                             font=("Arial",8), anchor="w")
            x += 160

        mode_color = BLUE if self.mode==self.MODE_ROAD else GREEN
        mode_text  = ("ROAD MODE — click two intersections to draw a road"
                      if self.mode==self.MODE_ROAD
                      else "PLACE MODE — click grid to add/remove intersections")
        self.create_text(10, 28, text=mode_text, fill=mode_color,
                         font=("Arial",8,"bold"), anchor="w")


# ─────────────────────────────────────────────
#  INPUT PANEL
# ─────────────────────────────────────────────

class InputPanel(tk.Frame):
    """
    Right panel. Edits:
      - Lanes per direction for selected intersection
      - Road length, speed, demand for each of its connected roads
      - Webster preview
    """

    DIR_NAMES = ["North","South","East","West"]

    def __init__(self, parent, on_change=None, **kw):
        super().__init__(parent, bg=BG2, **kw)
        self.on_change = on_change
        self.inter:   Intersection = None
        self.network: Network      = None
        self._lane_vars   = []
        self._len_vars    = []
        self._spd_vars    = []
        self._demand_vars = []
        self._tt_labels   = []
        self._road_frames = []
        self._build()

    def _build(self):
        def lbl(text, size=10, bold=False, color=FG):
            return tk.Label(self, text=text, bg=BG2, fg=color,
                            font=("Arial",size,"bold" if bold else "normal"))

        lbl("Intersection Editor",12,bold=True,color=GOLD).pack(pady=(12,4))
        self._id_lbl = lbl("Right-click an intersection", color=FG_DIM)
        self._id_lbl.pack()
        ttk.Separator(self).pack(fill="x",padx=8,pady=6)

        # Lanes
        lbl("Lanes per Direction",bold=True,color=BLUE).pack(anchor="w",padx=10)
        for name in self.DIR_NAMES:
            row = tk.Frame(self,bg=BG2); row.pack(fill="x",padx=14,pady=2)
            tk.Label(row,text=f"{name}:",width=8,anchor="w",
                     bg=BG2,fg=FG,font=("Arial",10)).pack(side="left")
            var = tk.IntVar(value=2)
            self._lane_vars.append(var)
            tk.Spinbox(row,from_=1,to=6,textvariable=var,width=3,
                       bg=BG3,fg=FG,buttonbackground=BG3,
                       font=("Arial",10),command=self._changed).pack(side="left")

        ttk.Separator(self).pack(fill="x",padx=8,pady=6)

        # Road properties per direction
        lbl("Connected Roads",bold=True,color=BLUE).pack(anchor="w",padx=10)
        lbl("Length & speed are per-road — set when drawing.",
            size=8,color=FG_DIM).pack(anchor="w",padx=14)

        for i,name in enumerate(self.DIR_NAMES):
            f = tk.LabelFrame(self,text=f"{name}",bg=BG2,fg=BLUE,
                              font=("Arial",9,"bold"))
            f.pack(fill="x",padx=10,pady=3)
            self._road_frames.append(f)

            r1 = tk.Frame(f,bg=BG2); r1.pack(fill="x",padx=6,pady=2)
            tk.Label(r1,text="Length:",width=9,anchor="w",
                     bg=BG2,fg=FG,font=("Arial",9)).pack(side="left")
            lv = tk.IntVar(value=DEFAULT_ROAD_LENGTH)
            self._len_vars.append(lv)
            e = tk.Entry(r1,textvariable=lv,width=6,
                         bg=BG3,fg=FG,insertbackground="white",font=("Arial",9))
            e.pack(side="left")
            e.bind("<FocusOut>",self._changed); e.bind("<Return>",self._changed)
            tk.Label(r1,text="m",bg=BG2,fg=FG_DIM,font=("Arial",9)).pack(side="left",padx=2)
            ttl = tk.Label(r1,text="",bg=BG2,fg=GOLD,font=("Arial",8))
            ttl.pack(side="left",padx=6)
            self._tt_labels.append(ttl)

            r2 = tk.Frame(f,bg=BG2); r2.pack(fill="x",padx=6,pady=2)
            tk.Label(r2,text="Speed:",width=9,anchor="w",
                     bg=BG2,fg=FG,font=("Arial",9)).pack(side="left")
            sv = tk.IntVar(value=60)
            self._spd_vars.append(sv)
            e2 = tk.Entry(r2,textvariable=sv,width=6,
                          bg=BG3,fg=FG,insertbackground="white",font=("Arial",9))
            e2.pack(side="left")
            e2.bind("<FocusOut>",self._changed); e2.bind("<Return>",self._changed)
            tk.Label(r2,text="km/h",bg=BG2,fg=FG_DIM,font=("Arial",9)).pack(side="left",padx=2)

            r3 = tk.Frame(f,bg=BG2); r3.pack(fill="x",padx=6,pady=2)
            tk.Label(r3,text="Demand:",width=9,anchor="w",
                     bg=BG2,fg=FG,font=("Arial",9)).pack(side="left")
            dv = tk.DoubleVar(value=DEFAULT_DEMAND_CPH)
            self._demand_vars.append(dv)
            e3 = tk.Entry(r3,textvariable=dv,width=6,
                          bg=BG3,fg=FG,insertbackground="white",font=("Arial",9))
            e3.pack(side="left")
            e3.bind("<FocusOut>",self._changed); e3.bind("<Return>",self._changed)
            tk.Label(r3,text="cars/hr (entry only)",bg=BG2,fg=FG_DIM,
                     font=("Arial",8)).pack(side="left",padx=2)

        ttk.Separator(self).pack(fill="x",padx=8,pady=6)

        lbl("Webster Preview",bold=True,color=BLUE).pack(anchor="w",padx=10)
        self._preview = tk.Text(self,height=6,width=26,bg="#0a1628",fg=GREEN,
                                font=("Courier",9),state="disabled",relief="flat")
        self._preview.pack(padx=10,pady=4,fill="x")

    def load(self, inter: Intersection):
        self.inter = inter
        self._id_lbl.config(text=f"Editing: ({inter.row},{inter.col})")
        for i,v in enumerate(self._lane_vars):
            v.set(inter.lanes[i])
        if self.network:
            r,c = inter.row,inter.col
            nbr = {0:(r-1,c),1:(r+1,c),2:(r,c+1),3:(r,c-1)}
            for d,(nr,nc) in nbr.items():
                road = (self.network.get_road(r,c,nr,nc)
                        or self.network.get_entry_road(r,c,d))
                kind = ("internal" if self.network.get_road(r,c,nr,nc)
                        else "entry" if self.network.get_entry_road(r,c,d)
                        else "none")
                self._road_frames[d].config(text=f"{self.DIR_NAMES[d]} [{kind}]")
                if road:
                    self._len_vars[d].set(int(road.length_m))
                    self._spd_vars[d].set(int(road.speed_ms*3.6))
                    self._demand_vars[d].set(road.demand_cph)
                    self._tt_labels[d].config(text=f"→ {road.travel_time:.0f}s")
                else:
                    self._tt_labels[d].config(text="→ no road")
        self._update_preview()

    def _changed(self, _=None):
        if not self.inter or not self.network: return
        try:
            for i,v in enumerate(self._lane_vars):
                self.inter.lanes[i] = max(1,min(int(v.get()),6))
            r,c = self.inter.row,self.inter.col
            nbr = {0:(r-1,c),1:(r+1,c),2:(r,c+1),3:(r,c-1)}
            for d,(nr,nc) in nbr.items():
                road = (self.network.get_road(r,c,nr,nc)
                        or self.network.get_entry_road(r,c,d))
                if road:
                    road.length_m   = max(10, int(self._len_vars[d].get()))
                    road.speed_ms   = max(1,  int(self._spd_vars[d].get())) * 1000/3600
                    road.demand_cph = max(0,  float(self._demand_vars[d].get()))
                    self._tt_labels[d].config(text=f"→ {road.travel_time:.0f}s")
        except (ValueError,tk.TclError): pass
        self._update_preview()
        if self.on_change: self.on_change()

    def _update_preview(self):
        if not self.inter: return
        C,g1,g2 = self.inter.compute_webster()
        y1,y2   = self.inter.flow_ratios()
        sat = (y1+y2)>=0.95
        txt = (f"Cycle   : {C:.1f} s\n"
               f"NS green: {g1:.1f} s\n"
               f"EW green: {g2:.1f} s\n"
               f"y_NS    : {y1:.3f}\n"
               f"y_EW    : {y2:.3f}\n"
               f"Status  : {'OVER-SAT' if sat else 'OK'}")
        self._preview.config(state="normal")
        self._preview.delete("1.0","end")
        self._preview.insert("1.0",txt)
        self._preview.config(state="disabled")

    def clear(self):
        self.inter = None
        self._id_lbl.config(text="Right-click an intersection")
        self._preview.config(state="normal")
        self._preview.delete("1.0","end")
        self._preview.config(state="disabled")


# ─────────────────────────────────────────────
#  RESULTS WINDOW
# ─────────────────────────────────────────────

class ResultsWindow(tk.Toplevel):
    def __init__(self,parent,uniform,webster):
        super().__init__(parent)
        self.title("Results — Uniform vs Webster")
        self.configure(bg=BG); self.geometry("860x640")
        self.u,self.w = uniform,webster
        self._build()

    def _build(self):
        tk.Label(self,text="Simulation Comparison Results",
                 bg=BG,fg=GOLD,font=("Arial",14,"bold")).pack(pady=(12,4))
        tg,qr = improvement(self.u,self.w)
        bar = tk.Frame(self,bg=BG3,pady=8); bar.pack(fill="x",padx=12,pady=6)
        for label,value,color in [
            ("Uniform Throughput",f"{self.u.throughput_hr:.0f} cars/hr",FG),
            ("Webster Throughput",f"{self.w.throughput_hr:.0f} cars/hr",FG),
            ("Throughput Gain",   f"{tg:+.1f}%", GREEN if tg>=0 else RED),
            ("Queue Reduction",   f"{qr:+.1f}%", GREEN if qr>=0 else RED),
        ]:
            col=tk.Frame(bar,bg=BG3); col.pack(side="left",expand=True)
            tk.Label(col,text=label,bg=BG3,fg=FG_DIM,font=("Arial",9)).pack()
            tk.Label(col,text=value,bg=BG3,fg=color,font=("Arial",13,"bold")).pack()

        nb=ttk.Notebook(self); nb.pack(fill="both",expand=True,padx=12,pady=6)
        for title,ud,wd,yl in [
            ("Throughput",self.u.history_cars, self.w.history_cars, "Cars"),
            ("Queue",     self.u.history_queue,self.w.history_queue,"Avg queue"),
        ]:
            t=tk.Frame(nb,bg=BG); nb.add(t,text=title)
            if HAS_MPL: self._chart(t,ud,wd,title,yl)
            else: tk.Label(t,text="pip install matplotlib",bg=BG,fg=RED).pack(expand=True)
        t3=tk.Frame(nb,bg=BG); nb.add(t3,text="Per Intersection"); self._table(t3)
        t4=tk.Frame(nb,bg=BG); nb.add(t4,text="Summary");          self._text(t4)

    def _chart(self,parent,ud,wd,title,yl):
        fig=Figure(figsize=(7.5,3.4),dpi=96,facecolor=BG)
        ax=fig.add_subplot(111); ax.set_facecolor(BG3)
        if ud: ax.plot([t/3600 for t,_ in ud],[v for _,v in ud],color="#ff6b6b",lw=2,label="Uniform (30s)")
        if wd: ax.plot([t/3600 for t,_ in wd],[v for _,v in wd],color=GREEN,lw=2,label="Webster")
        ax.set_xlabel("Time (hours)",color=FG_DIM); ax.set_ylabel(yl,color=FG_DIM)
        ax.set_title(title,color=GOLD); ax.tick_params(colors=FG_DIM)
        for s in ax.spines.values(): s.set_color("#555")
        ax.spines["top"].set_visible(False); ax.spines["right"].set_visible(False)
        ax.legend(facecolor=BG2,labelcolor=FG); ax.grid(True,alpha=0.15,color="#555")
        fig.tight_layout(pad=1.5)
        FigureCanvasTkAgg(fig,master=parent).get_tk_widget().pack(fill="both",expand=True,padx=4,pady=4)

    def _table(self,parent):
        headers=["Intersection","NS Green","EW Green","TPH Uniform","TPH Webster","Gain"]
        hf=tk.Frame(parent,bg=BG3); hf.pack(fill="x",padx=6,pady=(6,0))
        for i,h in enumerate(headers):
            tk.Label(hf,text=h,bg=BG3,fg=GOLD,font=("Arial",9,"bold"),
                     width=13,wraplength=90,anchor="center").grid(row=0,column=i,padx=2,pady=4)
        outer=tk.Frame(parent,bg=BG); outer.pack(fill="both",expand=True,padx=6,pady=4)
        cv=tk.Canvas(outer,bg=BG,highlightthickness=0)
        sb=ttk.Scrollbar(outer,orient="vertical",command=cv.yview)
        body=tk.Frame(cv,bg=BG)
        body.bind("<Configure>",lambda e:cv.configure(scrollregion=cv.bbox("all")))
        cv.create_window((0,0),window=body,anchor="nw")
        cv.configure(yscrollcommand=sb.set)
        cv.pack(side="left",fill="both",expand=True); sb.pack(side="right",fill="y")
        for ri,key in enumerate(sorted(self.w.per_intersection)):
            wi=self.w.per_intersection[key]; ui=self.u.per_intersection.get(key,{})
            gain=((wi["tph"]-ui.get("tph",0))/ui["tph"]*100) if ui.get("tph") else 0
            bg_row="#1e2d4a" if ri%2==0 else BG2
            for ci,val in enumerate([
                f"({key[0]},{key[1]})",f"{wi['green_ns']:.1f}s",f"{wi['green_ew']:.1f}s",
                f"{ui.get('tph',0):.0f}",f"{wi['tph']:.0f}",f"{gain:+.1f}%"
            ]):
                color=(GREEN if gain>=0 else RED) if ci==5 else FG
                tk.Label(body,text=val,bg=bg_row,fg=color,
                         font=("Arial",9),width=13,anchor="center"
                         ).grid(row=ri,column=ci,padx=2,pady=2,sticky="ew")

    def _text(self,parent):
        tg,qr=improvement(self.u,self.w)
        txt=("="*44+"\n    TRAFFIC SIGNAL OPTIMIZATION RESULTS\n"+"="*44+"\n\n"
             f"UNIFORM (30s)\n  Throughput : {self.u.throughput_hr:.0f} cars/hr\n"
             f"  Avg queue  : {self.u.avg_queue:.2f}\n\n"
             f"WEBSTER\n  Throughput : {self.w.throughput_hr:.0f} cars/hr\n"
             f"  Avg queue  : {self.w.avg_queue:.2f}\n\n"
             f"IMPROVEMENT\n  Throughput : {tg:+.1f}%\n  Queue      : {qr:+.1f}%\n"
             +"="*44)
        box=tk.Text(parent,bg="#0a1628",fg=GREEN,font=("Courier",10),wrap="word",relief="flat")
        box.pack(fill="both",expand=True,padx=10,pady=10)
        box.insert("1.0",txt); box.config(state="disabled")
