"""Always-on-top Lucky / Super Lucky pity counter for Aniimo.

Never touches the game process: actions come from global hotkeys (RegisterHotKey), from the
capture cards seen on screen (cards.Watcher) and from the game's sound, which Windows hands over
(detect.Listener), all through the same queue.
Run with: pythonw overlay.py  (or the exe built by build_exe.bat)
"""
import ctypes, json, os, queue, sys, threading, time
import tkinter as tk
from ctypes import wintypes

import pity

# your files (counters, settings) sit next to the script, or next to the exe once built
HERE = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
EVENTS = os.path.join(DATA, "events.jsonl")
WINDOW = os.path.join(DATA, "window.json")
BANNER = os.path.join(DATA, "window_floraison.json")
READINGS = os.path.join(DATA, "floraison.json")
CONFIG = os.path.join(HERE, "config.json")
DEFAULT_CONFIG = {"server": "EU", "reset_pity_weekly": True, "opacity": 0.9, "show_lucky": True, "show_super": True,
                  "sound_detection": True, "sound_catch": False, "screen_cards": True, "floraison": True,
                  "sound_threshold": 0.6,
                  "hotkeys": {"catch": "F7", "lucky": "F8", "super": "F9", "undo": "F6"}}

BG, FG, DIM, TRACK = "#16161d", "#ecebf2", "#8d8aa0", "#2a2935"
WARN, OK = "#ff6b6b", "#7ee08a"
PRISM = "#8fd8ff"
# logo text, fill, 3D shadow, frame colour
STYLE = {"lucky": ("LUCKY!", "#ffe27a", "#a8711a", "#f2c14e"),
         "super": ("SUPER LUCKY!", "#ff8ad8", "#4a6cff", "#c78bff")}
FLASH = {"catch": {"lucky": OK, "super": OK}, "lucky": {"lucky": STYLE["lucky"][1], "super": OK},
         "super": {"super": STYLE["super"][1]}, "undo": {"lucky": DIM, "super": DIM},
         "set": {"lucky": OK, "super": OK}}
SOUND_KIND = {"lucky": "lucky", "super": "super", "ball_success": "catch"}  # sons/<name>.wav -> event

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
user32.GetAncestor.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND] + [ctypes.c_int] * 4 + [wintypes.UINT]
user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
user32.MessageBoxW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]
MOD = {"CTRL": 2, "ALT": 1, "SHIFT": 4, "WIN": 8}
NAMED_VK = {"ADD": 0x6B, "SUBTRACT": 0x6D, "MULTIPLY": 0x6A, "DIVIDE": 0x6F, "DECIMAL": 0x6E,
            "INSERT": 0x2D, "DELETE": 0x2E, "HOME": 0x24, "END": 0x23, "PAGEUP": 0x21,
            "PAGEDOWN": 0x22, "PAUSE": 0x13, "SCROLLLOCK": 0x91}
ACTIONS = [("catch", "Capture oubliée"), ("lucky", "Lucky oublié"), ("super", "Super Lucky oublié"),
           ("undo", "Annuler la dernière action")]
SERVERS = [("EU", "Europe"), ("NA", "Amérique"), ("ASIA", "Asie")]


def parse_hotkey(text):
    """"CTRL+F7", "NUMPAD5", "ADD"... -> (modifiers, virtual key)."""
    mods, vk = 0, None
    for part in text.upper().replace(" ", "").split("+"):
        if part in MOD:
            mods |= MOD[part]
        elif part in NAMED_VK:
            vk = NAMED_VK[part]
        elif part.startswith("NUMPAD") and part[6:].isdigit() and int(part[6:]) <= 9:
            vk = 0x60 + int(part[6:])
        elif part.startswith("F") and part[1:].isdigit() and 1 <= int(part[1:]) <= 24:
            vk = 0x6F + int(part[1:])
        elif len(part) == 1 and part.isalnum():
            vk = ord(part)
        else:
            raise ValueError(f"touche inconnue « {part} »")
    if vk is None:
        raise ValueError("il manque la touche")
    return mods, vk


def key_name(vk, ctrl=False, alt=False, shift=False):
    """Text for a key pressed in the settings ("CTRL+F7", "NUMPAD5"), the reverse of parse_hotkey."""
    names = {v: k for k, v in NAMED_VK.items()}
    if 0x70 <= vk <= 0x87:
        key = f"F{vk - 0x6F}"
    elif 0x60 <= vk <= 0x69:
        key = f"NUMPAD{vk - 0x60}"
    elif vk in names:
        key = names[vk]
    elif 0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A:
        key = chr(vk)
    else:
        return None
    return "+".join([m for m, on in (("CTRL", ctrl), ("ALT", alt), ("SHIFT", shift)) if on] + [key])


def listen_hotkeys(binds, out):
    """Register {action: "F7"} system-wide; post ("do", action) or ("err", text) to out.

    Runs until the thread gets WM_QUIT (Hotkeys.stop), then frees its keys. Empty key: none."""
    actions = {}
    for i, (action, text) in enumerate(binds.items(), 1):
        if not text:
            continue
        try:
            mods, vk = parse_hotkey(text)
        except ValueError as e:
            out.put(("err", f"Raccourci {text} : {e}"))
            continue
        if user32.RegisterHotKey(None, i, mods | 0x4000, vk):  # MOD_NOREPEAT
            actions[i] = action
        else:
            out.put(("err", f"Raccourci {text} déjà pris par une autre appli"))
    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        if msg.message == 0x0312 and msg.wParam in actions:  # WM_HOTKEY
            out.put(("do", actions[msg.wParam]))
    for i in actions:
        user32.UnregisterHotKey(None, i)


class Hotkeys:
    """The global hotkeys, held by a thread of their own (RegisterHotKey ties them to the thread
    that registers them): restarted when the settings change a key, paused while one is typed."""

    def __init__(self, out):
        self.out, self.thread, self.tid = out, None, None

    def start(self, binds):
        self.stop()
        ready = threading.Event()

        def run():
            msg = wintypes.MSG()
            user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)  # gives the thread its message queue
            self.tid = kernel32.GetCurrentThreadId()
            ready.set()
            listen_hotkeys(binds, self.out)

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()
        ready.wait(2)

    def stop(self):
        if self.thread and self.thread.is_alive():
            user32.PostThreadMessageW(self.tid, 0x0012, 0, 0)  # WM_QUIT
            self.thread.join(2)
        self.thread = None


class Log:
    """Event file kept in time order; appends when possible, rewrites otherwise."""

    def __init__(self, path):
        self.path, self.events = path, []
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                for line in f:
                    try:
                        self.events.append(json.loads(line))
                    except ValueError:
                        pass  # half-written line from a crash

    def add(self, e):
        """Insert in time order: a sound can be recognised after a later key press."""
        i = len(self.events)
        while i and self.events[i - 1]["ts"] > e["ts"]:
            i -= 1
        self.events.insert(i, e)
        if i < len(self.events) - 1:
            self.save()
        else:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(e) + "\n")

    def remove(self, e):
        self.events = [x for x in self.events if x is not e]
        self.save()

    def undo(self):
        if not self.events:
            return None
        e = self.events.pop()
        self.save()
        return e

    def save(self):
        with open(self.path + ".tmp", "w", encoding="utf-8") as f:
            f.writelines(json.dumps(x) + "\n" for x in self.events)
        os.replace(self.path + ".tmp", self.path)


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)


def pct(x):
    return f"{x * 100:.2f}".rstrip("0").rstrip(".").replace(".", ",") + " %"


def duration(sec):
    d, h, m = int(sec // 86400), int(sec % 86400 // 3600), int(sec % 3600 // 60)
    return f"{d} j {h} h" if d else f"{h} h {m:02d}"


def odds_line(name, o):
    if o.pity >= o.cap:
        return f"{name} : au-delà de la garantie, compteur décalé ?"
    if o.left == 1:
        return f"{name} : {pct(o.p)} par capture · la prochaine est garantie !"
    return (f"{name} : {pct(o.p)} par capture · garanti dans {o.left} · ~{o.expected:.0f} en moyenne · "
            f"{o.had_it * 100:.0f} % l'auraient déjà eu")


def progress(o):
    """Share of the way to the guarantee, e.g. '68 %' or '0,4 %'."""
    v = min(o.pity / o.cap, 1) * 100
    return (f"{v:.0f}" if v >= 10 or v == 0 else f"{v:.1f}".replace(".", ",")) + " %"


class Bar:
    """Left: catches since Monday, 'catches since the last strike / guarantee', strikes since Monday.
    Right: strike logo and how far the guarantee is."""

    def __init__(self, parent, kind, px):
        self.kind, self.px = kind, px
        self.w, self.h = px(300), px(80)
        self.c = tk.Canvas(parent, width=self.w, height=self.h, bg=BG, highlightthickness=0)

    def pack(self):
        self.c.pack(padx=self.px(6), pady=(self.px(6), 0))

    def show(self, o, catches, number_color=None):
        c, px, w, h = self.c, self.px, self.w, self.h
        logo, fill, shadow, frame = STYLE[self.kind]
        name = "Lucky" if self.kind == "lucky" else "Super Lucky"
        c.delete("all")
        c.create_rectangle(px(1), px(1), w - px(1), h - px(1), outline=frame, width=px(2))
        c.create_text(px(14), px(14), text=f"{catches} capture{'s' if catches > 1 else ''}", anchor="w",
                      fill=DIM, font=("Segoe UI", 9))
        base = px(56)
        count = c.create_text(px(14), base, text=str(o.pity), anchor="sw",
                              fill=number_color or FG, font=("Segoe UI Semibold", 24))
        c.create_text(c.bbox(count)[2] + px(4), base - px(6), text=f"/ {o.cap}", anchor="sw", fill=DIM,
                      font=("Segoe UI Semibold", 13))
        c.create_text(px(14), px(64), text=f"{o.done} {name} depuis lundi", anchor="w", fill=DIM,
                      font=("Segoe UI", 9))
        x, y = w - px(12), px(32)
        c.create_text(x + px(1), y + px(2), text=logo, anchor="e", fill=shadow, font=("Segoe UI Black", 13))
        c.create_text(x, y, text=logo, anchor="e", fill=fill, font=("Segoe UI Black", 13))
        c.create_text(x, px(52), text=f"{progress(o)} de la pity", anchor="e", fill=FG, font=("Segoe UI", 9))
        # progress towards the guarantee along the bottom edge
        x0, x1, y1 = px(4), w - px(4), h - px(4)
        c.create_rectangle(x0, y1 - px(3), x1, y1, fill=TRACK, width=0)
        color = WARN if o.pity >= o.cap else OK if o.left == 1 else frame
        c.create_rectangle(x0, y1 - px(3), x0 + (x1 - x0) * min(o.pity / o.cap, 1), y1, fill=color, width=0)


def number(n):
    return f"{n:,}".replace(",", "\u202f")


class Banner:
    """Top-of-screen strip: prismatic energy of the area last read in the Enrichir menu, what is
    missing to Full, and how many Lucky / Super Lucky that makes. Lucky and Super Lucky counted
    since that reading are added as an estimate (≈) until the menu is read again."""

    def __init__(self, overlay, px):
        import floraison
        self.fl, self.overlay, self.px = floraison, overlay, px
        self.win = load_json(BANNER, {"x": None, "y": 8, "locked": False, "shown": True})
        self.data = load_json(READINGS, {"readings": {}, "last": None})
        self.top = tk.Toplevel(overlay.root)
        self.top.overrideredirect(True)
        self.top.attributes("-topmost", True)
        self.top.attributes("-alpha", overlay.cfg["opacity"])
        self.top.configure(bg=BG)
        self.w, self.h = px(640), px(62)
        self.c = tk.Canvas(self.top, width=self.w, height=self.h, bg=BG, highlightthickness=0)
        self.c.pack()
        x = self.win["x"] if self.win["x"] is not None else (self.top.winfo_screenwidth() - self.w) // 2
        self.top.geometry(f"+{x}+{self.win['y']}")
        self.locked = tk.BooleanVar(value=self.win["locked"])
        self.shown = tk.BooleanVar(value=self.win["shown"])
        self.drag = None
        self.top.bind("<ButtonPress-1>", self.drag_start)
        self.top.bind("<B1-Motion>", self.drag_move)
        self.top.bind("<ButtonRelease-1>", lambda e: self.save_window())
        self.top.bind("<Button-3>", self.popup)
        self.menu = tk.Menu(self.top, tearoff=0)
        self.set_shown(save=False)

    def drag_start(self, e):
        self.drag = None if self.locked.get() else (e.x_root - self.top.winfo_x(), e.y_root - self.top.winfo_y())

    def drag_move(self, e):
        if self.drag:
            self.top.geometry(f"+{e.x_root - self.drag[0]}+{e.y_root - self.drag[1]}")

    def save_window(self):
        self.win.update(x=self.top.winfo_x(), y=self.top.winfo_y(), locked=self.locked.get(), shown=self.shown.get())
        save_json(BANNER, self.win)

    def set_shown(self, save=True):
        if self.shown.get():
            self.top.deiconify()
            self.redraw()
        else:
            self.top.withdraw()
        if save:
            self.save_window()

    def popup(self, e):
        m = self.menu
        m.delete(0, "end")
        for zone, r in sorted(self.data["readings"].items(), key=lambda kv: -kv[1]["ts"]):
            n = self.fl.needs(r["energy"])
            m.add_command(label=f"{zone} : {self.fl.STAGES[r['stage']][0]} · {number(r['energy'])} pts · "
                                f"≈ {n['lucky']} Lucky ou {n['super']} Super",
                          command=lambda z=zone: self.choose(z))
        if self.data["readings"]:
            m.add_separator()
        m.add_command(label="Oublier les lectures", command=self.forget)
        m.add_command(label="Réglages…", command=self.overlay.settings)
        m.add_checkbutton(label="Verrouiller la position", variable=self.locked, command=self.save_window)
        m.tk_popup(e.x_root, e.y_root)

    def choose(self, zone):
        self.data["last"] = zone
        save_json(READINGS, self.data)
        self.redraw()

    def forget(self):
        self.data = {"readings": {}, "last": None}
        save_json(READINGS, self.data)
        self.redraw()

    def on_reading(self, r):
        zone = r.pop("zone") or "Zone ?"
        self.data["readings"][zone] = r
        self.data["last"] = zone
        save_json(READINGS, self.data)
        self.redraw()

    def estimate(self, r):
        """Energy read, plus the Lucky / Super Lucky counted since (average gains)."""
        strikes = [e["k"] for e in self.overlay.log.events if e["ts"] > r["ts"] and e["k"] in ("lucky", "super")]
        lucky, sup = strikes.count("lucky"), strikes.count("super")
        gain = lucky * self.fl.LUCKY_MEAN + sup * self.fl.SUPER_GAIN
        return min(round(r["energy"] + gain), self.fl.FULL), lucky, sup

    def redraw(self):
        if not self.shown.get():
            return
        c, px, w, h, fl = self.c, self.px, self.w, self.h, self.fl
        c.delete("all")
        c.create_rectangle(px(1), px(1), w - px(1), h - px(1), outline=PRISM, width=px(2))
        zone = self.data.get("last")
        r = self.data["readings"].get(zone)
        if not r:
            c.create_text(px(14), h // 2 - px(2), anchor="w", fill=DIM, font=("Segoe UI", 10),
                          text="FLORAISON · ouvre le menu Enrichir d'une zone pour lire son énergie prismatique")
            return
        energy, lucky, sup = self.estimate(r)
        approx = lucky or sup
        stage = max(i for i, (_, need) in enumerate(fl.STAGES) if energy >= need)
        n = fl.needs(energy)
        c.create_text(px(14), px(12), anchor="w", fill=DIM, font=("Segoe UI", 9),
                      text=f"FLORAISON · {zone} · {fl.STAGES[stage][0]}")
        main = c.create_text(px(14), px(48), anchor="sw", fill=FG, font=("Segoe UI Semibold", 18),
                             text=f"{'≈ ' if approx else ''}{number(energy)}")
        c.create_text(c.bbox(main)[2] + px(4), px(44), anchor="sw", fill=DIM, font=("Segoe UI Semibold", 11),
                      text=f"/ {number(fl.FULL)}")
        if n["missing"] == 0:
            c.create_text(w - px(14), px(30), anchor="e", fill=OK, font=("Segoe UI Black", 13),
                          text="PLEIN · Prismana garanti")
        else:
            c.create_text(px(250), px(12), anchor="w", fill=FG, font=("Segoe UI", 9),
                          text=f"il manque {number(n['missing'])} points")
            line = c.create_text(px(250), px(46), anchor="sw", fill=STYLE["lucky"][1], font=("Segoe UI Black", 13),
                                 text=f"≈ {n['lucky']} Lucky")
            x = c.bbox(line)[2] + px(5)
            more = c.create_text(x, px(44), anchor="sw", fill=DIM, font=("Segoe UI", 9),
                                 text=f"({n['lucky_best']} à {n['lucky_worst']})")
            x = c.bbox(more)[2] + px(10)
            c.create_text(x, px(46), anchor="sw", fill=STYLE["super"][1], font=("Segoe UI Black", 13),
                          text=f"ou {n['super']} Super Lucky")
            if approx:
                since = " + ".join(t for t in (f"{lucky} Lucky" if lucky else "", f"{sup} Super" if sup else "") if t)
                c.create_text(w - px(14), px(12), anchor="e", fill=DIM, font=("Segoe UI", 8),
                              text=f"estimé : {since} depuis la lecture")
        # energy along the bottom edge, with a tick at each state's threshold
        x0, x1, y1 = px(4), w - px(4), h - px(4)
        c.create_rectangle(x0, y1 - px(4), x1, y1, fill=TRACK, width=0)
        c.create_rectangle(x0, y1 - px(4), x0 + (x1 - x0) * energy / fl.FULL, y1,
                           fill=OK if n["missing"] == 0 else PRISM, width=0)
        for _, need in fl.STAGES[1:-1]:
            x = x0 + (x1 - x0) * need / fl.FULL
            c.create_line(x, y1 - px(8), x, y1, fill=DIM)


class Settings:
    """What to show, the correction keys and the server; every change applies at once and is saved."""

    def __init__(self, overlay):
        self.o, cfg = overlay, overlay.cfg
        if getattr(overlay, "settings_window", None) and overlay.settings_window.winfo_exists():
            overlay.settings_window.lift()
            return
        d = self.d = overlay.settings_window = tk.Toplevel(overlay.root)
        d.title("Réglages du compteur")
        d.attributes("-topmost", True)
        d.resizable(False, False)
        pad = {"padx": 10, "pady": 3}

        show = tk.LabelFrame(d, text="Afficher", padx=8, pady=4)
        show.pack(fill="x", padx=10, pady=(10, 4))
        self.flags = {}
        for key, label in (("show_lucky", "Cadre Lucky"), ("show_super", "Cadre Super Lucky")):
            v = self.flags[key] = tk.BooleanVar(value=cfg[key])
            tk.Checkbutton(show, text=label, variable=v, command=lambda k=key: self.set_flag(k)).pack(anchor="w")
        if overlay.banner:
            tk.Checkbutton(show, text="Bandeau Floraison (énergie prismatique)", variable=overlay.banner.shown,
                           command=lambda: self.set_flag("banner")).pack(anchor="w")
        row = tk.Frame(show)
        row.pack(anchor="w", pady=(4, 0))
        tk.Label(row, text="Opacité").pack(side="left")
        self.opacity = tk.Scale(row, from_=40, to=100, orient="horizontal", length=160, showvalue=True,
                                command=lambda v: self.set_opacity(int(v)))
        self.opacity.set(round(cfg["opacity"] * 100))
        self.opacity.pack(side="left", padx=6)
        tk.Label(row, text="%").pack(side="left")

        keys = tk.LabelFrame(d, text="Touches de correction", padx=8, pady=4)
        keys.pack(fill="x", padx=10, pady=4)
        self.buttons = {}
        for r, (action, label) in enumerate(ACTIONS):
            tk.Label(keys, text=label, anchor="w").grid(row=r, column=0, sticky="w", pady=2)
            b = self.buttons[action] = tk.Button(keys, width=24, command=lambda a=action: self.capture(a))
            b.grid(row=r, column=1, padx=6, pady=2)
            tk.Button(keys, text="Aucune", command=lambda a=action: self.set_key(a, "")).grid(row=r, column=2)
        self.note = tk.Label(keys, justify="left", anchor="w", wraplength=420, text=(
            "Clique sur une touche, puis appuie sur la nouvelle (Échap : garder l'ancienne). Le jeu utilise "
            "F5, F7, F8 et F9 : tant que le compteur les garde, le jeu ne les reçoit plus. Le pavé "
            "numérique ou Ctrl + une touche évitent ça."))
        self.note.grid(row=len(ACTIONS), column=0, columnspan=3, sticky="w", pady=(6, 2))
        self.refresh_keys()

        server = tk.LabelFrame(d, text="Serveur (heure du reset du lundi)", padx=8, pady=4)
        server.pack(fill="x", padx=10, pady=4)
        self.server = tk.StringVar(value=cfg["server"])
        for code, label in SERVERS:
            tk.Radiobutton(server, text=label, value=code, variable=self.server,
                           command=self.set_server).pack(side="left", **pad)

        tk.Button(d, text="Fermer", width=12, command=self.close).pack(pady=(4, 10))
        d.protocol("WM_DELETE_WINDOW", self.close)
        d.bind("<KeyPress>", self.key_pressed)
        self.capturing = None
        d.focus_force()

    def save(self):
        save_json(CONFIG, self.o.cfg)

    def visible_count(self):
        banner = self.o.banner.shown.get() if self.o.banner else False
        return self.flags["show_lucky"].get() + self.flags["show_super"].get() + banner

    def set_flag(self, key):
        if self.visible_count() == 0:  # nothing left to right-click on to come back here
            if key == "banner":
                self.o.banner.shown.set(True)
            else:
                self.flags[key].set(True)
            self.note.config(text="Garde au moins un élément affiché : c'est par son clic droit qu'on revient ici.")
            return
        if key == "banner":
            self.o.banner.set_shown()
            return
        self.o.cfg[key] = self.flags[key].get()
        self.o.layout()
        self.save()

    def set_opacity(self, percent):
        self.o.cfg["opacity"] = percent / 100
        self.o.root.attributes("-alpha", percent / 100)
        if self.o.banner:
            self.o.banner.top.attributes("-alpha", percent / 100)
        self.save()

    def set_server(self):
        self.o.cfg["server"] = self.server.get()
        self.o.rebuild()
        self.save()

    def refresh_keys(self):
        for action, b in self.buttons.items():
            b.config(text=self.o.cfg["hotkeys"].get(action) or "aucune", relief="raised")

    def capture(self, action):
        """Wait for the next key; the hotkeys are paused, or pressing F7 would count a catch."""
        if self.o.hotkeys:
            self.o.hotkeys.stop()
        self.refresh_keys()
        self.capturing = action
        self.buttons[action].config(text="appuie sur une touche…", relief="sunken")
        self.d.focus_force()

    def key_pressed(self, e):
        if not self.capturing:
            return
        action = self.capturing
        if e.keycode in (0x10, 0x11, 0x12, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5):  # a modifier alone: wait
            return
        if e.keycode == 0x1B:  # Escape
            self.set_key(action, self.o.cfg["hotkeys"].get(action, ""))
            return
        down = lambda vk: bool(user32.GetKeyState(vk) & 0x8000)
        ctrl, alt, shift = down(0x11), down(0x12), down(0x10)
        text = key_name(e.keycode, ctrl, alt, shift)
        if text is None:
            self.note.config(text="Cette touche ne peut pas servir : prends une touche F, du pavé numérique, "
                                  "ou une lettre avec Ctrl ou Alt.")
            return "break"
        if text.split("+")[-1].isalnum() and len(text.split("+")[-1]) == 1 and not (ctrl or alt):
            self.note.config(text=f"{text} seule bloquerait cette touche dans toutes les applis (le chat aussi) : "
                                  "ajoute Ctrl ou Alt.")
            return "break"
        self.set_key(action, text)
        return "break"

    def set_key(self, action, text):
        """Keep the key if no other action uses it and Windows lets us have it, then restart the hotkeys."""
        self.capturing = None
        keys = dict(self.o.cfg["hotkeys"])
        other = [label for a, label in ACTIONS if a != action and text and keys.get(a) == text]
        if other:
            self.note.config(text=f"{text} sert déjà à « {other[0]} ».")
        elif text and not self.available(text):
            self.note.config(text=f"{text} est déjà prise par une autre appli.")
        else:
            keys[action] = text
            self.o.cfg["hotkeys"] = keys
            self.save()
            self.note.config(text=f"« {dict(ACTIONS)[action]} » : {text or 'aucune touche'}.")
        self.restart_hotkeys()
        self.refresh_keys()

    @staticmethod
    def available(text):
        mods, vk = parse_hotkey(text)
        if not user32.RegisterHotKey(None, 0xBFFF, mods | 0x4000, vk):
            return False
        user32.UnregisterHotKey(None, 0xBFFF)
        return True

    def restart_hotkeys(self):
        if self.o.hotkeys:
            self.o.clear_errors("Raccourci")
            self.o.hotkeys.start(self.o.cfg["hotkeys"])

    def close(self):
        if self.capturing:
            self.capturing = None
            self.restart_hotkeys()
        self.d.destroy()


class Overlay:
    def __init__(self, root, cfg, log, inbox):
        self.root, self.cfg, self.log, self.inbox = root, cfg, log, inbox
        self.watchers = []       # cards.Watcher / floraison.Watcher, to know which parts of the screen they read
        self.errors, self.highlight, self.drag = [], {}, None
        self.history = []        # how to revert each action of this session, for F6
        self.tokens = []         # (deadline, "key" | "sound"): the next F7 / automatic catch is already counted
        self.win = load_json(WINDOW, {"x": 40, "y": 40, "locked": False})
        scale = root.winfo_fpixels("1i") / 96
        px = lambda n: round(n * scale)

        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.attributes("-alpha", cfg["opacity"])
        root.configure(bg=BG)
        root.geometry(f"+{self.win['x']}+{self.win['y']}")

        self.hotkeys = None      # Hotkeys, restarted by the settings when a key changes
        self.bars = {kind: Bar(root, kind, px) for kind in ("lucky", "super")}
        self.status = tk.Label(root, bg=BG, fg=WARN, font=("Segoe UI", 8), anchor="w")  # packed on error
        self.spacer = tk.Frame(root, bg=BG, height=px(6))
        self.layout()

        self.menu = tk.Menu(root, tearoff=0)
        self.locked = tk.BooleanVar(value=self.win["locked"])
        # bindings on the toplevel also fire for every widget inside it
        root.bind("<ButtonPress-1>", self.drag_start)
        root.bind("<B1-Motion>", self.drag_move)
        root.bind("<ButtonRelease-1>", lambda e: self.save_window())
        root.bind("<Button-3>", self.popup)

        self.banner = Banner(self, px) if cfg["floraison"] else None
        self.rebuild()
        self.tick()
        self.poll()
        self.keep_on_top()

    def popup(self, e):
        """Right-click menu: current stats on top, then actions."""
        m, s = self.menu, self.state
        lucky, sup = pity.odds(s)
        m.delete(0, "end")
        for line in (odds_line("Lucky", lucky), odds_line("Super Lucky", sup),
                     f"Semaine : {s.catches_week} captures · {s.lucky_week} Lucky · {s.super_week} Super",
                     f"Reset dans {duration(s.week + 7 * 86400 - time.time())}"):
            m.add_command(label=line, state="disabled")
        m.add_separator()
        keys = self.cfg["hotkeys"]
        m.add_command(label="+1 capture", accelerator=keys["catch"], command=lambda: self.do("catch"))
        m.add_command(label="Lucky !", accelerator=keys["lucky"], command=lambda: self.do("lucky"))
        m.add_command(label="Super Lucky !", accelerator=keys["super"], command=lambda: self.do("super"))
        m.add_command(label="Annuler la dernière action", accelerator=keys["undo"], command=lambda: self.do("undo"))
        m.add_separator()
        m.add_command(label="Régler les compteurs…", command=self.edit)
        m.add_command(label="Réglages…", command=self.settings)
        m.add_checkbutton(label="Verrouiller la position", variable=self.locked, command=self.save_window)
        m.add_command(label="Ouvrir le dossier", command=lambda: os.startfile(HERE))
        m.add_separator()
        m.add_command(label="Quitter", command=self.root.destroy)
        m.tk_popup(e.x_root, e.y_root)

    def drag_start(self, e):
        self.drag = None if self.locked.get() else (e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y())

    def drag_move(self, e):
        if self.drag:
            self.root.geometry(f"+{e.x_root - self.drag[0]}+{e.y_root - self.drag[1]}")

    def save_window(self):
        self.win.update(x=self.root.winfo_x(), y=self.root.winfo_y(), locked=self.locked.get())
        save_json(WINDOW, self.win)

    def layout(self):
        """Pack the boxes the settings show, Lucky above Super Lucky; hide the window if none."""
        for widget in [b.c for b in self.bars.values()] + [self.status, self.spacer]:
            widget.pack_forget()
        shown = [kind for kind in ("lucky", "super") if self.cfg[f"show_{kind}"]]
        for kind in shown:
            self.bars[kind].pack()
        if self.errors:
            self.status.pack(fill="x", padx=8)
        self.spacer.pack()
        if shown:
            self.root.deiconify()
        else:
            self.root.withdraw()

    def settings(self):
        Settings(self)

    def rebuild(self):
        self.state = pity.build(self.log.events, time.time(), self.cfg["server"], self.cfg["reset_pity_weekly"])
        self.redraw()

    def redraw(self):
        for kind, o in zip(("lucky", "super"), pity.odds(self.state)):
            if kind in self.bars:
                self.bars[kind].show(o, self.state.catches_week, self.highlight.get(kind))
        if self.banner:
            self.banner.redraw()  # Lucky counted since the last reading change its estimate

    def add(self, e):
        self.log.add(e)
        self.history.append(lambda: self.log.remove(e))

    def take_token(self, use):
        """Use up a pending 'this Aniimo is already counted' token for an F7 ("key") or an automatic catch."""
        now = time.time()
        self.tokens = [tok for tok in self.tokens if tok[0] >= now]
        for tok in self.tokens:
            if tok[1] == use:
                self.tokens.remove(tok)
                return True
        return False

    def do(self, action):
        now, flash = time.time(), action
        if action == "undo":
            if self.history:
                self.history.pop()()
            elif not self.log.undo():
                return
        elif action == "catch" and self.take_token("key"):
            flash = "undo"  # this Aniimo was already counted automatically
        elif action in ("lucky", "super") and any(
                e["k"] == action and e.get("auto") and now - 15 <= e["ts"] for e in self.log.events[-50:]):
            flash = "undo"  # pressed out of habit after the sound was recognised
        else:
            self.add({"ts": round(now, 1), "k": action})
            if action == "catch":
                self.tokens.append((now + 4, "sound"))  # its card or success sound may still be coming
        # the changed numbers light up briefly as feedback for hotkeys pressed in game
        self.show_flash(flash)

    def heard(self, kind, t):
        """Seen or heard at time t: a catch (card or sound), or a Lucky / Super Lucky on an Aniimo caught."""
        now = time.time()
        if kind == "catch":
            if self.take_token("sound"):
                return  # already counted: by an F7, or as the Aniimo of a Lucky
            self.add({"ts": round(t, 1), "k": "catch", "auto": True})
            self.tokens.append((now + 4, "key"))  # in case F7 is pressed for it too
            self.show_flash("catch")
            return
        recent = [e for e in self.log.events[-50:] if abs(e["ts"] - t) <= 10]
        if any(e["k"] == kind and abs(e["ts"] - t) <= 5 for e in recent):
            return  # already entered by hand
        # a catch counted right around the strike is its Aniimo (its F7 may come a few seconds later);
        # earlier ones are previous Aniimo when catching fast. Its card or success sound comes ~2.3 s
        # after the strike sound, so an automatic catch before it belongs to another Aniimo
        near = [e for e in recent if e["k"] == "catch" and t - (0.5 if e.get("auto") else 2) <= e["ts"] <= t + 5]
        if near:
            e = min(reversed(near), key=lambda e: abs(e["ts"] - t))  # ties: the latest
            e.update(k=kind, auto=True)
            self.log.save()
            self.history.append(lambda: (e.update(k="catch"), e.pop("auto", None), self.log.save()))
        else:
            self.add({"ts": round(t, 1), "k": kind, "auto": True})
            # its Aniimo is counted here: absorb its F7 and its card / success sound (~2.3 s later)
            self.tokens += [(now + 8, "key"), (now + 5, "sound")]
        self.show_flash(kind)

    def show_flash(self, action):
        self.highlight = FLASH[action]
        self.rebuild()
        self.root.after(700, self.clear_highlight)

    def clear_highlight(self):
        self.highlight = {}
        self.redraw()

    def tick(self):
        if pity.week_start(time.time(), self.cfg["server"]) != self.state.week:
            self.rebuild()
        self.root.after(5000, self.tick)

    def poll(self):
        try:
            while True:
                msg = self.inbox.get_nowait()
                if msg[0] == "do":
                    self.do(msg[1])
                elif msg[0] == "heard":
                    self.heard(SOUND_KIND[msg[1]], msg[2])
                elif msg[0] == "cards":  # capture cards or a Culbuton notification seen on screen
                    for _ in range(msg[2]):
                        self.heard("catch", msg[1])
                elif msg[0] == "floraison":  # the Enrichir menu was read
                    if self.banner:
                        self.banner.on_reading(msg[1])
                else:
                    self.show_error(msg[1])
        except queue.Empty:
            pass
        self.root.after(50, self.poll)

    def show_error(self, text):
        if text not in self.errors:
            self.errors.append(text)
        self.status.config(text=" · ".join(self.errors))
        self.layout()

    def clear_errors(self, prefix):
        self.errors = [e for e in self.errors if not e.startswith(prefix)]
        self.status.config(text=" · ".join(self.errors))
        self.layout()

    def keep_on_top(self):
        zones = [z for w in self.watchers for z in w.zones]
        for window in (self.root, self.banner.top if self.banner else None):
            if window is None or not window.winfo_viewable():
                continue
            # borderless games can push themselves above other topmost windows
            hwnd = user32.GetAncestor(window.winfo_id(), 2)  # GA_ROOT
            user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x13)  # HWND_TOPMOST, NOSIZE|NOMOVE|NOACTIVATE
            # over a part of the screen the watchers read, the window would hide it from their
            # screen copies: then it leaves screen captures (also Discord, recordings,
            # screenshots); elsewhere it shows in them. Windows 10 2004+
            r = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            over = any(r.left < z[2] and z[0] < r.right and r.top < z[3] and z[1] < r.bottom for z in zones)
            user32.SetWindowDisplayAffinity(hwnd, 0x11 if over else 0)  # WDA_EXCLUDEFROMCAPTURE / WDA_NONE
        self.root.after(2000, self.keep_on_top)

    def edit(self):
        """Dialog to set the counters, e.g. when starting mid-week."""
        d = tk.Toplevel(self.root)
        d.title("Régler les compteurs")
        d.attributes("-topmost", True)
        d.resizable(False, False)
        fields = [("catches_week", "Captures depuis lundi"),
                  ("lucky_pity", "Captures depuis le dernier Lucky"),
                  ("lucky_week", "Lucky obtenus cette semaine"),
                  ("super_pity", "Captures depuis le dernier Super Lucky"),
                  ("super_week", "Super Lucky obtenus cette semaine")]
        vars_ = {}
        for row, (key, label) in enumerate(fields):
            tk.Label(d, text=label, anchor="w").grid(row=row, column=0, sticky="w", padx=10, pady=4)
            v = tk.StringVar(value=str(getattr(self.state, key)))
            tk.Spinbox(d, from_=0, to=10000, textvariable=v, width=7).grid(row=row, column=1, padx=10)
            vars_[key] = v
        err = tk.Label(d, fg="red")
        err.grid(row=len(fields), column=0, columnspan=2)

        def ok():
            try:
                values = {k: int(v.get()) for k, v in vars_.items()}
                if min(values.values()) < 0:
                    raise ValueError
            except ValueError:
                err.config(text="Entiers positifs uniquement")
                return
            self.add({"ts": round(time.time(), 1), "k": "set", **values})
            self.show_flash("set")
            d.destroy()

        buttons = tk.Frame(d)
        buttons.grid(row=len(fields) + 1, column=0, columnspan=2, pady=8)
        tk.Button(buttons, text="Enregistrer", command=ok, width=12).pack(side="left", padx=4)
        tk.Button(buttons, text="Annuler", command=d.destroy, width=12).pack(side="left", padx=4)
        d.bind("<Return>", lambda e: ok())
        d.bind("<Escape>", lambda e: d.destroy())
        d.focus_force()


def message(text, title="Compteur pity Aniimo"):
    user32.MessageBoxW(None, text, title, 0x40)  # MB_ICONINFORMATION


def self_test():
    """Checks what the counter needs, without starting it; reports in test.txt and a message box."""
    lines = []

    def check(label, fn):
        try:
            lines.append(f"OK   {label} : {fn()}")
        except Exception as e:
            lines.append(f"ERREUR {label} : {e}")

    import cv2, cards, detect, game_audio
    import numpy as np

    def window():
        w = tk.Tk()
        w.withdraw()
        version = w.tk.call("info", "patchlevel")
        w.destroy()
        return f"Tk {version}"
    check("fenêtre", window)
    check("sons de référence", lambda: ", ".join(sorted(detect.load_refs())) or "aucun")
    import floraison
    check("modèles d'image", lambda: ", ".join(
        f"{os.path.basename(p)} {cv2.imread(p, cv2.IMREAD_GRAYSCALE).shape}"
        for p in (cards.TEMPLATE, cards.MASS_TEMPLATE, floraison.TITLE)))

    def ocr():
        reader = floraison.Ocr()
        img = np.full((60, 400, 3), 30, np.uint8)
        cv2.putText(img, "Tres faible", (10, 42), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 255, 255), 2)
        text = reader.read(img)
        reader.close()
        return f"lit « {text} »"
    check("reconnaissance de texte", ocr)
    pid = game_audio.game_pid()
    lines.append(f"{'OK  ' if pid else 'INFO'} jeu : {'lancé' if pid else 'pas lancé (lance Aniimo pour tester le son et l écran)'}")
    if pid:
        def sound():
            import numpy as np
            with game_audio.GameAudio(pid) as g:
                t0, x = time.time(), []
                while time.time() - t0 < 1:
                    x.append(g.read())
            x = np.concatenate(x)
            return f"{len(x) / game_audio.RATE:.1f} s reçues, {10 * np.log10((x ** 2).mean() + 1e-24):.0f} dB"
        check("son du jeu", sound)
        where = cards.game_client(pid)
        lines.append(f"{'OK  ' if where else 'INFO'} écran : " + (f"jeu au premier plan, image {where[2]}x{where[3]}" if where
                     else "le jeu n'est pas la fenêtre active (normal pendant ce test)"))
    report = "\n".join(lines)
    with open(os.path.join(HERE, "test.txt"), "w", encoding="utf-8") as f:
        f.write(report + "\n")
    if "--quiet" not in sys.argv:
        message(report, "Test du compteur")


def main():
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
    if "--test" in sys.argv:
        return self_test()
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW(None, False, "AniimoPityOverlay")
    if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        message("Le compteur tourne déjà.")
        sys.exit(1)
    os.makedirs(DATA, exist_ok=True)
    cfg = {**DEFAULT_CONFIG, **load_json(CONFIG, {})}
    cfg["hotkeys"] = {**DEFAULT_CONFIG["hotkeys"], **cfg["hotkeys"]}
    if not os.path.exists(CONFIG):
        save_json(CONFIG, cfg)
    inbox = queue.Queue()
    hotkeys = Hotkeys(inbox)
    hotkeys.start(cfg["hotkeys"])
    if cfg["sound_detection"]:
        import detect
        names = ("lucky", "super") + (("ball_success",) if cfg["sound_catch"] else ())
        detect.Listener(inbox, names, cfg["sound_threshold"]).start()
    watchers = []
    if cfg["screen_cards"] or cfg["floraison"]:
        import cards, game_audio
        if cfg["screen_cards"]:
            watchers.append(cards.Watcher(inbox, game_audio.game_pid))
        if cfg["floraison"]:
            import floraison
            watchers.append(floraison.Watcher(inbox, game_audio.game_pid))
        for w in watchers:
            w.start()
    root = tk.Tk()
    root.title("Compteur pity Aniimo")
    overlay = Overlay(root, cfg, Log(EVENTS), inbox)
    overlay.watchers, overlay.hotkeys = watchers, hotkeys
    root.mainloop()


if __name__ == "__main__":
    main()
