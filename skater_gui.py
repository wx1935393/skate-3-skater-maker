#!/usr/bin/env python3
"""
skater_gui.py - GUI for Skate 3 (PS3) SKATER.P saves.
Keep this file in the same folder as skater_tool.py. Run: python skater_gui.py
(Thumbnails need Pillow:  pip install pillow)

What works: CRC check/fix, gear-slot overview, LOGO id swap (graphics),
thumbnail viewer, hex patching, and Compare (finds which bytes changed
between two saves, labeled by gear slot) - used to discover color / body-part
fields. Slots tab deletes gear/body slots (size fields + CRC repaired).
Colours tab: pick any gear colour, optional GLOW (neon) mode, one-click presets.
"""
import io, os, re, shutil, struct, sys
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, filedialog, messagebox, colorchooser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import skater_tool as st

try:
    from PIL import Image
except Exception:
    Image = None
try:
    from PIL import ImageTk
except Exception:
    ImageTk = None


def to_photo(im):
    """PIL image -> Tk image. Works even if PIL.ImageTk is unavailable (uses PNG data)."""
    if ImageTk is not None:
        try: return ImageTk.PhotoImage(im)
        except Exception: pass
    buf = io.BytesIO(); im.save(buf, "PNG")
    return tk.PhotoImage(data=buf.getvalue())


def slot_label(d, off):
    """Describe which region/gear slot a byte offset belongs to."""
    if off < 0x29e9:
        return "header/settings"
    bl = st.blocks(d)
    for i, b in enumerate(bl):
        end = bl[i + 1] if i + 1 < len(bl) else st.CRC_END
        if b <= off < end:
            best = None
            for n in st.GEAR_NAMES:
                for m in re.finditer(re.escape(n), bytes(d[b:min(end, b + 0x1800)])):
                    p = b + m.start()
                    if p <= off and (best is None or p > best[0]):
                        best = (p, n.decode())
            tag = f"block {i+1}"
            if off > b + 0x1800:
                return tag + " (after gear list: thumbnail/other)"
            return tag + (f" / {best[1]}" if best else " / preamble")
    return "padding/other"


def diff_ranges(x, y):
    n = min(len(x), len(y))
    out, i = [], 0
    while i < n:
        if x[i] != y[i]:
            j = i
            while j < n and (x[j] != y[j] or (j + 1 < n and x[j + 1] != y[j + 1])) and j - i < 64:
                j += 1
            out.append((i, j))
            i = j
        else:
            i += 1
    return out


# ====================================================================== look & feel
BG, PANEL, PANEL2, EDGE = "#1b1c20", "#23252a", "#2c2f36", "#383b43"
FG, DIM = "#e2e4e9", "#8a8f9a"
ACCENT, OK, BAD = "#4c8dff", "#5cc16d", "#e5534b"
UI, MONO = "Segoe UI", "Consolas"

def _h2t(h):
    h = h.lstrip("#"); return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))

def _lerp(c1, c2, t):
    return "#%02x%02x%02x" % tuple(round(a + (b - a) * t) for a, b in zip(c1, c2))

def _pick_font(root, wanted, fallback):
    try:
        have = set(tkfont.families(root))
    except Exception:
        return fallback
    for n in wanted:
        if n in have: return n
    return fallback

def setup_theme(root):
    """Flat dark theme for ttk + classic tk widgets."""
    global UI, MONO
    UI = _pick_font(root, ["Inter", "Segoe UI", "SF Pro Text", "Noto Sans", "Ubuntu", "DejaVu Sans"], "TkDefaultFont")
    MONO = _pick_font(root, ["JetBrains Mono", "Cascadia Mono", "Consolas", "Menlo", "DejaVu Sans Mono"], "TkFixedFont")
    root.configure(bg=BG)
    s = ttk.Style(root)
    try: s.theme_use("clam")
    except Exception: pass
    s.configure(".", background=BG, foreground=FG, fieldbackground=PANEL2, bordercolor=EDGE, lightcolor=BG, darkcolor=BG,
                troughcolor=PANEL, focuscolor=BG, font=(UI, 10))
    s.configure("TFrame", background=BG)
    s.configure("TLabel", background=BG, foreground=FG)
    s.configure("Dim.TLabel", background=BG, foreground=DIM)
    s.configure("Title.TLabel", background=BG, foreground=FG, font=(UI, 12, "bold"))
    s.configure("TButton", background=PANEL2, foreground=FG, bordercolor=EDGE, padding=(12, 5), relief="flat")
    s.map("TButton", background=[("pressed", EDGE), ("active", "#363a42")], bordercolor=[("focus", ACCENT)])
    s.configure("Accent.TButton", background=ACCENT, foreground="#ffffff", bordercolor=ACCENT)
    s.map("Accent.TButton", background=[("pressed", "#3a74d8"), ("active", "#6aa0ff")], foreground=[("active", "#ffffff")])
    s.configure("TCheckbutton", background=BG, foreground=FG, indicatorcolor=PANEL2, indicatorbackground=PANEL2)
    s.map("TCheckbutton", indicatorcolor=[("selected", ACCENT)], indicatorbackground=[("selected", ACCENT)], background=[("active", BG)])
    s.configure("TEntry", fieldbackground=PANEL2, foreground=FG, insertcolor=FG, bordercolor=EDGE, padding=4)
    s.map("TEntry", bordercolor=[("focus", ACCENT)])
    s.configure("TCombobox", fieldbackground=PANEL2, background=PANEL2, foreground=FG, arrowcolor=DIM, bordercolor=EDGE,
                selectbackground=PANEL2, selectforeground=FG, padding=4)
    s.map("TCombobox", fieldbackground=[("readonly", PANEL2)], foreground=[("readonly", FG)], bordercolor=[("focus", ACCENT)])
    s.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(0, 0, 0, 0))
    s.configure("TNotebook.Tab", background=BG, foreground=DIM, padding=(14, 7), borderwidth=0)
    s.map("TNotebook.Tab", background=[("selected", PANEL2), ("active", PANEL)], foreground=[("selected", FG), ("active", FG)])
    s.configure("Vertical.TScrollbar", background=PANEL2, troughcolor=BG, arrowcolor=DIM, bordercolor=BG)
    s.configure("Horizontal.TScale", background=ACCENT, troughcolor=PANEL2, bordercolor=EDGE)
    s.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=FG, rowheight=26, borderwidth=0, font=(MONO, 10))
    s.configure("Treeview.Heading", background=PANEL2, foreground=DIM, relief="flat", padding=(8, 5), font=(UI, 9))
    s.map("Treeview", background=[("selected", ACCENT)], foreground=[("selected", "#ffffff")])
    s.map("Treeview.Heading", background=[("active", PANEL2)])
    for pat, val in (("*Listbox.background", PANEL), ("*Listbox.foreground", FG), ("*Listbox.selectBackground", ACCENT),
                     ("*Listbox.selectForeground", "#ffffff"), ("*Listbox.highlightThickness", 0), ("*Listbox.borderWidth", 0),
                     ("*Listbox.relief", "flat"), ("*Text.background", PANEL), ("*Text.foreground", FG),
                     ("*Text.insertBackground", FG), ("*Text.selectBackground", ACCENT), ("*Text.highlightThickness", 0),
                     ("*Text.borderWidth", 0), ("*Text.relief", "flat"), ("*Label.background", BG), ("*Label.foreground", FG),
                     ("*TCombobox*Listbox.background", PANEL2), ("*TCombobox*Listbox.foreground", FG),
                     ("*TCombobox*Listbox.selectBackground", ACCENT), ("*TCombobox*Listbox.selectForeground", "#ffffff")):
        root.option_add(pat, val)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        setup_theme(self)
        self.title("SKATER.P editor")
        self.geometry("1060x760"); self.minsize(940, 640)
        self.d = None
        self.path = None
        self.imgs = []; self._chips = []; self._toast_job = None

        bar = ttk.Frame(self); bar.pack(fill="x", padx=14, pady=(12, 10))
        ttk.Label(bar, text="SKATER.P", style="Title.TLabel").pack(side="left")
        ttk.Label(bar, text="Skate 3 save editor", style="Dim.TLabel").pack(side="left", padx=(8, 22))
        ttk.Button(bar, text="Open...", command=self.open).pack(side="left")
        ttk.Button(bar, text="Save + fix CRC...", style="Accent.TButton", command=self.save).pack(side="left", padx=8)
        self.status = tk.Label(bar, text="No file loaded", fg=DIM, bg=BG, font=(UI, 10)); self.status.pack(side="right")
        self.bind("<Control-o>", lambda e: self.open()); self.bind("<Control-s>", lambda e: self.save())
        tk.Frame(self, height=1, bg=EDGE).pack(fill="x")

        self.nb = ttk.Notebook(self); self.nb.pack(fill="both", expand=True, padx=14, pady=(10, 14))
        self.t_over = self._text_tab("Overview")
        self._col_tab(); self._slot_tab(); self._gfx_tab(); self._logo_tab(); self._patch_tab(); self._cmp_tab()
        self.toast_lbl = tk.Label(self, text="", bg="#34373f", fg=FG, font=(UI, 10), padx=14, pady=8)

    def toast(self, msg, bad=False):
        self.toast_lbl.config(text=msg, bg=BAD if bad else "#34373f", fg="#ffffff" if bad else FG)
        self.toast_lbl.place(relx=1.0, rely=1.0, x=-20, y=-20, anchor="se"); self.toast_lbl.lift()
        if self._toast_job: self.after_cancel(self._toast_job)
        self._toast_job = self.after(3000, self.toast_lbl.place_forget)

    # ---------- helpers
    def _text_tab(self, name):
        f = ttk.Frame(self.nb); self.nb.add(f, text=name)
        t = tk.Text(f, wrap="none", font=(MONO, 10)); t.pack(fill="both", expand=True)
        return t

    def need(self):
        if self.d is None:
            messagebox.showinfo("No file", "Open a SKATER.P first."); return False
        return True

    def refresh(self):
        d = self.d
        s, c = st.stored_crc(d), st.calc_crc(d)
        ok = s == c
        self.status.config(text=f"\u25cf  {os.path.basename(self.path)}  \u00b7  CRC {'OK' if ok else 'MISMATCH (fixed on save)'}", fg=OK if ok else BAD)
        t = self.t_over; t.delete("1.0", "end")
        t.insert("end", f"size {len(d):#x}\nCRC stored {s:08x} computed {c:08x}\n")
        t.insert("end", f"payload length @0x1c: {struct.unpack('>I', d[0x1c:0x20])[0]:#x}\n\n")
        for i, b in enumerate(st.blocks(d)):
            seg = bytes(d[b:b + 0x1800])
            names = [n.decode() for n in st.GEAR_NAMES if n in seg]
            t.insert("end", f"cas_db block {i+1} @ {b:#x}: {', '.join(names)}\n")
        self.fill_slots(); self.fill_cols(); self.fill_gfx(); self.fill_logos()

    # ---------- file ops
    def open(self):
        p = filedialog.askopenfilename(title="Open SKATER.P")
        if not p: return
        self.d = st.load(p); self.path = p; self.refresh()

    def save(self):
        if not self.need(): return
        p = filedialog.asksaveasfilename(initialfile=os.path.basename(self.path), title="Save as")
        if not p: return
        if os.path.abspath(p) == os.path.abspath(self.path) and not os.path.exists(p + ".bak"):
            shutil.copy2(p, p + ".bak")
        st.fix_crc(self.d); st.save(self.d, p)
        self.path = p; self.refresh()
        self.toast("Saved with fixed CRC  \u2192  " + os.path.basename(p))


    # ---------- slots (delete gear/body slots)
    def _slot_tab(self):
        f = ttk.Frame(self.nb); self.nb.add(f, text="Slots")
        ttk.Label(f, style="Dim.TLabel", wraplength=780, text="Deleting a slot removes it from the chosen block and repairs every size field "
                  "and the CRC (this is what the modded save does). Block 1 is the one that changed in the modded save. "
                  "Keep backups - the game may reject a slot it needs.").pack(padx=6, pady=6, anchor="w")
        self.slb = tk.Listbox(f, height=14, font=(MONO, 10), exportselection=False); self.slb.pack(fill="x", padx=6)
        ttk.Button(f, text="Delete selected slot", command=self.del_slot).pack(padx=6, pady=6, anchor="w")
        self.slot_keys = []

    def fill_slots(self):
        self.slb.delete(0, "end"); self.slot_keys = []
        for bi, sl in enumerate(st.list_slots(self.d), 1):
            for n, s, e in (sl or []):
                self.slb.insert("end", f"block {bi}   {n:<12} {e-s} bytes"); self.slot_keys.append((bi, n))

    def del_slot(self):
        if not self.need(): return
        sel = self.slb.curselection()
        if not sel: messagebox.showwarning("Select", "Pick a slot first."); return
        bi, n = self.slot_keys[sel[0]]
        if not messagebox.askyesno("Delete slot", f"Delete {n} from block {bi}?"): return
        try: st.delete_slot(self.d, bi, n)
        except Exception as ex: messagebox.showerror("Failed", str(ex)); return
        bad = st.check_sizes(self.d)
        self.refresh()
        self.toast(f"Removed {n} \u2014 now SAVE" + ("" if not bad else f"  (warning: {bad})"), bool(bad))


    # ---------- colours (tint table)
    PRESETS = [("cyan", "00ffff"), ("magenta", "ff00ff"), ("lime", "00ff00"), ("yellow", "ffff00"), ("red", "ff0000"),
               ("blue", "0000ff"), ("orange", "ff8000"), ("white", "ffffff"), ("black", "000000")]

    def _col_tab(self):
        f = self.col_frame = ttk.Frame(self.nb); self.nb.add(f, text="Colours")
        ttk.Label(f, style="Dim.TLabel", wraplength=980, text="Select a gear entry, then choose a colour or a preset. Block 1 is the skater shown in game. "
                  "Glow pushes the colour into HDR (bright channels far above white, dark channels negative). "
                  "Entries marked ~ are identified by position only.").pack(padx=2, pady=(12, 8), anchor="w")
        top = ttk.Frame(f); top.pack(fill="x", padx=2)
        ttk.Label(top, text="Block").pack(side="left"); self.col_block = tk.StringVar(value="1")
        cb = ttk.Combobox(top, textvariable=self.col_block, values=["1", "2", "3", "4", "5"], width=3, state="readonly"); cb.pack(side="left", padx=6)
        cb.bind("<<ComboboxSelected>>", lambda e: self.refresh_views())
        self.col_all = tk.BooleanVar(value=False); ttk.Checkbutton(top, text="Apply to all blocks", variable=self.col_all).pack(side="left", padx=16)

        mid = ttk.Frame(f); mid.pack(fill="both", expand=True, padx=2, pady=10)
        self.cl = ttk.Treeview(mid, columns=("slot", "a", "b"), show="tree headings", height=13, selectmode="browse")
        self.cl.heading("#0", text=""); self.cl.column("#0", width=56, stretch=False)
        for k, t, w in (("slot", "Slot", 150), ("a", "A (main)", 230), ("b", "B", 230)):
            self.cl.heading(k, text=t, anchor="w"); self.cl.column(k, width=w, anchor="w")
        self.cl.pack(side="left", fill="both", expand=True)
        self.cl.bind("<<TreeviewSelect>>", lambda e: self.col_show())
        side = ttk.Frame(mid); side.pack(side="left", padx=(14, 0), anchor="n")
        self.col_sw = tk.Canvas(side, width=240, height=170, bg=PANEL, highlightthickness=0); self.col_sw.pack()
        self.col_info = tk.Label(side, text="", bg=BG, fg=DIM, font=(MONO, 9), justify="left"); self.col_info.pack(anchor="w", pady=6)

        r = ttk.Frame(f); r.pack(fill="x", padx=2, pady=(0, 6))
        self.col_A = tk.BooleanVar(value=True); self.col_B = tk.BooleanVar(value=False)
        ttk.Checkbutton(r, text="Set A", variable=self.col_A).pack(side="left"); ttk.Checkbutton(r, text="Set B", variable=self.col_B).pack(side="left", padx=8)
        ttk.Label(r, text="Brightness").pack(side="left", padx=(14, 0)); self.col_mul = tk.StringVar(value="1.0")
        ttk.Entry(r, textvariable=self.col_mul, width=5).pack(side="left", padx=6)
        self.col_glow = tk.BooleanVar(value=False)
        ttk.Checkbutton(r, text="Glow", variable=self.col_glow).pack(side="left", padx=(18, 6))
        self.col_str = tk.DoubleVar(value=st.NEON_STRENGTH)
        ttk.Scale(r, from_=4, to=40, variable=self.col_str, length=120, command=lambda v: self.col_strl.config(text=f"{float(v):.0f}")).pack(side="left")
        self.col_strl = tk.Label(r, text=f"{st.NEON_STRENGTH:.0f}", bg=BG, fg=DIM, width=3, font=(MONO, 10)); self.col_strl.pack(side="left")
        ttk.Button(r, text="Choose colour...", style="Accent.TButton", command=self.col_pick).pack(side="right")

        pr = ttk.Frame(f); pr.pack(fill="x", padx=2, pady=(2, 8))
        ttk.Label(pr, text="Presets", style="Dim.TLabel").pack(side="left", padx=(0, 10))
        for nm, hx in self.PRESETS:
            tk.Button(pr, bg="#" + hx, activebackground="#" + hx, width=3, height=1, relief="flat", bd=0, cursor="hand2",
                      highlightthickness=1, highlightbackground=EDGE, highlightcolor=FG,
                      command=lambda h=hx: self.apply_color(_h2t(h))).pack(side="left", padx=3)
        self.col_keys = []

    @staticmethod
    def _hex(c):
        return "#%02x%02x%02x" % tuple(max(0, min(255, round(x * 255))) for x in c)

    def _col_row(self):
        sel = self.cl.selection()
        try: return int(sel[0]) if sel else None
        except (ValueError, TypeError): return None

    def refresh_views(self):
        self.fill_cols()

    def fill_cols(self):
        prev = self._col_row()
        for i in self.cl.get_children(): self.cl.delete(i)
        self.col_keys = []; self._chips = []
        try: es = st.tint_entries(self.d, int(self.col_block.get()))
        except Exception as ex: self.cl.insert("", "end", iid="x", values=(f"cannot read colour table: {ex}", "", "")); return
        f = lambda v: "(" + ", ".join(f"{x:6.2f}" for x in v) + ")"
        for n, e in enumerate(es):
            chip = tk.PhotoImage(width=30, height=14); chip.put(self._hex(e["A"]), to=(0, 0, 30, 14)); self._chips.append(chip)
            self.cl.insert("", "end", iid=str(n), image=chip, values=((e["slot"] or "~" + e["guess"]), f(e["A"]), f(e["B"])))
            self.col_keys.append(e["idx"])
        if prev is not None and prev < len(es): self.cl.selection_set(str(prev))

    def col_show(self):
        row = self._col_row()
        if row is None: return
        e = st.tint_entries(self.d, int(self.col_block.get()))[self.col_keys[row]]
        c = self.col_sw; c.delete("all")
        a, b = self._hex(e["A"]), self._hex(e["B"])
        if max(e["A"]) > 1.2:      # HDR value: draw a soft halo
            c.create_rectangle(14, 14, 226, 114, outline=_lerp(_h2t(PANEL), _h2t(a), 0.5), width=10)
        c.create_rectangle(24, 24, 216, 104, fill=a, outline=EDGE, width=1)
        c.create_rectangle(24, 126, 216, 154, fill=b, outline=EDGE, width=1)
        c.create_text(24, 8, text="A", anchor="w", fill=DIM, font=(UI, 8)); c.create_text(24, 116, text="B", anchor="w", fill=DIM, font=(UI, 8))
        self.col_info.config(text=f"A  {e['A'][0]:.2f}, {e['A'][1]:.2f}, {e['A'][2]:.2f}\nB  {e['B'][0]:.2f}, {e['B'][1]:.2f}, {e['B'][2]:.2f}")

    def col_pick(self):
        if not self.need(): return
        if self._col_row() is None: self.toast("Select a gear entry first", True); return
        rgb, hx = colorchooser.askcolor(title="Choose colour")
        if rgb: self.apply_color(tuple(int(x) for x in rgb))

    def apply_color(self, rgb):
        if not self.need(): return
        row = self._col_row()
        if row is None: self.toast("Select a gear entry first", True); return
        try: mul = float(self.col_mul.get())
        except ValueError: messagebox.showerror("Bad value", "Brightness must be a number."); return
        if self.col_glow.get(): c = st.neon_rgb("%02x%02x%02x" % rgb, float(self.col_str.get()))
        else: c = tuple(x / 255 * mul for x in rgb)
        idx = self.col_keys[row]
        blocks = range(1, len(st.blocks(self.d)) + 1) if self.col_all.get() else [int(self.col_block.get())]
        done = 0
        for bi in blocks:
            try: st.set_tint(self.d, bi, idx, A=c if self.col_A.get() else None, B=c if self.col_B.get() else None); done += 1
            except Exception: pass
        self.refresh(); self.cl.selection_set(str(row)); self.col_show()
        self.toast(f"Colour set in {done} block(s)" + (" (glow)" if self.col_glow.get() else "") + ". Remember to save.")

    # ---------- embedded custom graphics
    def _gfx_tab(self):
        f = ttk.Frame(self.nb); self.nb.add(f, text="Graphics")
        ttk.Label(f, style="Dim.TLabel", wraplength=780, text="Each custom graphic is stored inside the save as a 256x128 picture with transparency. "
                  "Replace one with your own image. Mode: stretch = force 256x128 (distorts), fit = keep proportions with see-through bars, fill = keep proportions and crop. "
                  "Pillow is required (pip install pillow).").pack(padx=6, pady=6, anchor="w")
        self.gl = tk.Listbox(f, height=6, font=(MONO, 10), exportselection=False); self.gl.pack(fill="x", padx=6)
        r = ttk.Frame(f); r.pack(padx=6, pady=6, anchor="w")
        ttk.Button(r, text="Export selected as PNG...", command=self.gfx_export).pack(side="left")
        ttk.Button(r, text="Replace with image...", command=self.gfx_replace).pack(side="left", padx=4)
        ttk.Button(r, text="Fill solid colour (RRGGBB)", command=self.gfx_fill).pack(side="left")
        self.gfx_fit = tk.StringVar(value="fit")
        ttk.Combobox(r, textvariable=self.gfx_fit, values=["stretch", "fit", "fill"], width=8, state="readonly").pack(side="left", padx=4)
        self.gfx_aspect = tk.StringVar(value="2.5"); self.gfx_tone = tk.StringVar(value="8"); self.gfx_sharp = tk.StringVar(value="0")
        for lbl, var in (("aspect", self.gfx_aspect), ("tone", self.gfx_tone), ("sharpen", self.gfx_sharp)):
            ttk.Label(r, text=lbl).pack(side="left", padx=(6, 0)); ttk.Entry(r, textvariable=var, width=4).pack(side="left")
        self.gfx_col = tk.StringVar(value="ff0000"); ttk.Entry(r, textvariable=self.gfx_col, width=9).pack(side="left", padx=4)
        self.gfx_prev = ttk.Label(f); self.gfx_prev.pack(padx=6, pady=6, anchor="w"); self.gfx_img = None

    def fill_gfx(self):
        self.gl.delete(0, "end")
        for i, s in st.find_graphics(self.d): self.gl.insert("end", f"{i}   @ {s:#x}")
        self.gl.bind("<<ListboxSelect>>", lambda e: self.gfx_show())

    def _gfx_sel(self):
        if not self.need(): return None
        sel = self.gl.curselection()
        if not sel: messagebox.showwarning("Select", "Pick a graphic first."); return None
        return self.gl.get(sel[0]).split()[0]

    def _need_pil(self):
        if Image is None:
            messagebox.showerror("Pillow missing", "Pillow is not installed:  pip install pillow"); return False
        return True

    def gfx_show(self):
        if Image is None: return
        gid = self._gfx_sel()
        if not gid: return
        s = [s for i, s in st.find_graphics(self.d) if i == gid][0]
        w, h, px = st.decode_graphic(self.d, s)
        im = Image.frombytes("RGBA", (w, h), px); bg = Image.new("RGBA", (w, h), (190, 190, 190, 255)); bg.alpha_composite(im)
        self.gfx_img = to_photo(bg.convert("RGB")); self.gfx_prev.config(image=self.gfx_img)

    def gfx_export(self):
        if not self._need_pil(): return
        gid = self._gfx_sel()
        if not gid: return
        p = filedialog.asksaveasfilename(defaultextension=".png", initialfile=f"graphic_{gid}.png")
        if not p: return
        try:
            s = [s for i, s in st.find_graphics(self.d) if i == gid][0]
            w, h, px = st.decode_graphic(self.d, s); Image.frombytes("RGBA", (w, h), px).save(p)
        except Exception as ex: messagebox.showerror("Export failed", repr(ex)); return
        self.toast("Exported " + os.path.basename(p))

    def gfx_replace(self):
        if not self._need_pil(): return
        gid = self._gfx_sel()
        if not gid: return
        p = filedialog.askopenfilename(title="Image to use", filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp *.gif"), ("All", "*.*")])
        if not p: return
        try: st.put_graphic(self.d, gid, st.encode_graphic(Image.open(p), self.gfx_fit.get(), float(self.gfx_aspect.get()), 0, float(self.gfx_sharp.get()), float(self.gfx_tone.get())))
        except Exception as ex: messagebox.showerror("Failed", repr(ex)); return
        self.refresh(); self.toast("Graphic replaced \u2014 now SAVE")

    def gfx_fill(self):
        gid = self._gfx_sel()
        if not gid: return
        h = self.gfx_col.get().strip().lstrip("#")
        try: st.fill_graphic(self.d, gid, int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), int(h[6:8], 16) if len(h) == 8 else 255)
        except Exception as ex: messagebox.showerror("Bad colour", str(ex)); return
        self.refresh()

    # ---------- logos
    def _logo_tab(self):
        f = ttk.Frame(self.nb); self.nb.add(f, text="Logo IDs")
        self.lb = tk.Listbox(f, height=10, font=(MONO, 10)); self.lb.pack(fill="x", padx=6, pady=6)
        r = ttk.Frame(f); r.pack(fill="x", padx=6)
        ttk.Label(r, text="Selected id -> new 8-digit id:").pack(side="left")
        self.new_logo = tk.StringVar(); ttk.Entry(r, textvariable=self.new_logo, width=12).pack(side="left", padx=4)
        ttk.Button(r, text="Replace everywhere", command=self.replace_logo).pack(side="left")
        ttk.Label(f, style="Dim.TLabel", wraplength=760, text="Graphics are referenced by an 8-digit id (LOGO/.../<id>.psg). "
                  "The new id must exist in the game's content/cache for the graphic to show.").pack(padx=6, pady=8, anchor="w")

    def fill_logos(self):
        self.lb.delete(0, "end")
        for m in re.finditer(rb"LOGO/(\d{4})/(\d{7})/(\d{8})", bytes(self.d)):
            self.lb.insert("end", f"{m.start():#08x}   {m.group(3).decode()}")

    def replace_logo(self):
        if not self.need(): return
        sel = self.lb.curselection(); new = self.new_logo.get().strip()
        if not sel or not re.fullmatch(r"\d{8}", new):
            messagebox.showwarning("Check input", "Select an id and type exactly 8 digits."); return
        old = self.lb.get(sel[0]).split()[-1].encode()
        data = bytes(self.d).replace(old, new.encode()); self.d[:] = data
        st.fix_crc(self.d); self.refresh()

    # ---------- hex patch
    def _patch_tab(self):
        f = ttk.Frame(self.nb); self.nb.add(f, text="Hex patch")
        r = ttk.Frame(f); r.pack(padx=6, pady=10, anchor="w")
        self.p_off = tk.StringVar(); self.p_hex = tk.StringVar()
        ttk.Label(r, text="Offset (e.g. 0x122e):").grid(row=0, column=0, sticky="w")
        ttk.Entry(r, textvariable=self.p_off, width=14).grid(row=0, column=1, padx=4)
        ttk.Label(r, text="New bytes (hex, e.g. 01):").grid(row=1, column=0, sticky="w")
        ttk.Entry(r, textvariable=self.p_hex, width=30).grid(row=1, column=1, padx=4, pady=4)
        ttk.Button(r, text="Apply", command=self.patch).grid(row=2, column=1, sticky="w")
        self.p_out = ttk.Label(f, text=""); self.p_out.pack(padx=6, anchor="w")

    def patch(self):
        if not self.need(): return
        try:
            off = int(self.p_off.get(), 0); val = bytes.fromhex(self.p_hex.get())
        except ValueError:
            messagebox.showerror("Bad input", "Offset or hex is invalid."); return
        if off < st.CRC_START or off + len(val) > st.CRC_END:
            messagebox.showerror("Out of range", "Offset is outside the checksummed range."); return
        old = bytes(self.d[off:off + len(val)]).hex()
        self.d[off:off + len(val)] = val; st.fix_crc(self.d)
        self.p_out.config(text=f"{off:#x}: {old} -> {val.hex()}  (label: {slot_label(self.d, off)})")
        self.refresh()

    # ---------- compare
    def _cmp_tab(self):
        f = ttk.Frame(self.nb); self.nb.add(f, text="Compare")
        ttk.Label(f, style="Dim.TLabel", wraplength=780, text="Load a skater, then compare against another save that differs by ONE thing "
                  "(e.g. only the shirt color). The changed bytes show where that setting is stored.").pack(padx=6, pady=6, anchor="w")
        ttk.Button(f, text="Compare with another save...", command=self.compare).pack(padx=6, anchor="w")
        self.cmp_t = tk.Text(f, font=(MONO, 10), wrap="none"); self.cmp_t.pack(fill="both", expand=True, padx=6, pady=6)

    def compare(self):
        if not self.need(): return
        p = filedialog.askopenfilename(title="Other save")
        if not p: return
        o = st.load(p); t = self.cmp_t; t.delete("1.0", "end")
        if len(o) != len(self.d): t.insert("end", "sizes differ\n")
        rs = diff_ranges(self.d, o)
        t.insert("end", f"{len(rs)} changed region(s)\n\n")
        for a, b in rs[:300]:
            t.insert("end", f"{a:#08x}-{b:#08x}  [{slot_label(self.d, a)}]\n   this : {bytes(self.d[a:b]).hex(' ')}\n   other: {bytes(o[a:b]).hex(' ')}\n")
        if len(rs) > 300: t.insert("end", "...truncated\n")


if __name__ == "__main__":
    App().mainloop()
