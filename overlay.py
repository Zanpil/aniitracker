"""Aniitracker: always-on-top Lucky / Super Lucky pity counter for Aniimo ("Compteur pity Aniimo" up to 1.3.0).

Never touches the game process: actions come from global hotkeys (RegisterHotKey), from the
capture cards seen on screen (cards.Watcher) and from the game's sound, which Windows hands over
(detect.Listener), all through the same queue.
Run with: pythonw overlay.py  (or the exe built by tools/build.py)
"""
import ctypes, json, os, queue, subprocess, sys, threading, time, traceback
import tkinter as tk
from ctypes import wintypes

from PIL import ImageTk

import pity
import theme
import updater

VERSION = "1.4.1"

# your files (counters, settings) sit next to the script, or next to the exe once built
HERE = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))
# what ships with the program (inside the exe: unpacked to sys._MEIPASS)
RES = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
ICON = os.path.join(RES, "images", "icone.ico")
# how to start this program again (its self-test, the new version): the exe, or python and this script
PROGRAM = [sys.executable] if getattr(sys, "frozen", False) else [
    os.path.join(os.path.dirname(sys.executable), "pythonw.exe"), os.path.abspath(__file__)]
DATA = os.path.join(HERE, "data")
EVENTS = os.path.join(DATA, "events.jsonl")
WINDOW = os.path.join(DATA, "window.json")
BANNER = os.path.join(DATA, "window_floraison.json")
READINGS = os.path.join(DATA, "floraison.json")
ERRORS = os.path.join(DATA, "erreurs.txt")
CONFIG = os.path.join(HERE, "config.json")
# check_updates: None until the player is asked
DEFAULT_CONFIG = {"server": "EU", "reset_pity_weekly": True, "opacity": 0.9,
                  "show_lucky": True, "show_super": True, "show_floraison": True, "show_icon": True,
                  "sound_detection": True, "sound_catch": False, "screen_cards": True, "floraison": True,
                  "sound_threshold": 0.6, "floraison_gain": 480, "check_updates": None,
                  "hotkeys": {"catch": "F7", "lucky": "F8", "super": "F9", "undo": "F6"}}
MUTEX, SHOW_EVENT = "AniimoPityOverlay", "AniimoPityOverlayShow"  # the same for every version
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME, OLD_RUN_NAME = "Aniitracker", "Compteur pity Aniimo"  # the entry there, and its name up to 1.3.0

NIGHT, WARN, NOTE = "#0b1020", "#ff6b6b", "#41d8ff"
# colour the big number takes for a moment, to show what was just counted
FLASH = {"catch": {"lucky": theme.GREEN, "super": theme.GREEN}, "lucky": {"lucky": theme.WHITE, "super": theme.GREEN},
         "super": {"super": theme.WHITE}, "undo": {"lucky": theme.DIM, "super": theme.DIM},
         "set": {"lucky": theme.GREEN, "super": theme.GREEN}}
SOUND_KIND = {"lucky": "lucky", "super": "super", "ball_success": "catch"}  # sons/<name>.wav -> event

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
user32.GetAncestor.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND] + [ctypes.c_int] * 4 + [wintypes.UINT]
user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
user32.MessageBoxW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]
kernel32.CreateMutexW.restype = kernel32.CreateEventW.restype = kernel32.OpenEventW.restype = wintypes.HANDLE
kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
kernel32.CreateEventW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR)
kernel32.OpenEventW.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR)
kernel32.SetEvent.argtypes = kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
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


def retry(write, tries=6):
    """Write to a file, again for a moment if it fails: an antivirus or a synced folder
    (OneDrive) can hold a file for a few tenths of a second."""
    for i in range(tries):
        try:
            return write()
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(0.05 * 2 ** i)  # 1.5 s in all


def log_error(where, exc_info=None, details=None):
    """Note an unexpected error at the end of data\\erreurs.txt; the self-test shows the last ones.
    `details`: the traceback already written out (an error from another thread)."""
    text = details or ("".join(traceback.format_exception(*exc_info)) if exc_info else traceback.format_exc())
    try:
        if os.path.exists(ERRORS) and os.path.getsize(ERRORS) > 200_000:
            with open(ERRORS, encoding="utf-8", errors="replace") as f:
                kept = f.read()[-100_000:]
            with open(ERRORS, "w", encoding="utf-8") as f:
                f.write(kept)
        with open(ERRORS, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} · {VERSION} · {where}\n{text}\n")
    except OSError:
        pass
    if sys.stderr:  # from the sources, in a console
        sys.stderr.write(text)


def last_errors(n=3):
    """The last errors noted: their time, where, and what (one line each)."""
    try:
        with open(ERRORS, encoding="utf-8", errors="replace") as f:
            entries = [e.strip().splitlines() for e in f.read().split("\n\n") if e.strip()]
    except OSError:
        return []
    return [f"{e[0]} : {e[-1]}" for e in entries[-n:]]


class Log:
    """Event file kept in time order; appends when possible, rewrites otherwise. An event that could
    not be written stays in memory and goes with the next write."""

    def __init__(self, path):
        self.path, self.events, self.unsaved = path, [], False
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
        if i < len(self.events) - 1 or self.unsaved:
            self.save()
            return

        def append():
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(e) + "\n")
        try:
            retry(append)
        except OSError:
            self.unsaved = True
            raise

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
        def write():
            with open(self.path + ".tmp", "w", encoding="utf-8") as f:
                f.writelines(json.dumps(x) + "\n" for x in self.events)
            os.replace(self.path + ".tmp", self.path)
        try:
            retry(write)
            self.unsaved = False
        except OSError:
            self.unsaved = True
            raise


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path, obj):
    """Written beside it, then put in its place: never half a file."""
    def write():
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=2, ensure_ascii=False)
        os.replace(path + ".tmp", path)
    retry(write)


def run_value(name):
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return None


def autostart():
    """What Windows runs for the counter when the player signs in, or None."""
    return run_value(RUN_NAME) or run_value(OLD_RUN_NAME)


def set_autostart(on):
    """Start the counter when the player signs in, waiting for Aniimo (--demarrage), or not."""
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if on:
            winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ, subprocess.list2cmdline(PROGRAM + ["--demarrage"]))
        for name in (OLD_RUN_NAME,) if on else (RUN_NAME, OLD_RUN_NAME):
            try:
                winreg.DeleteValue(key, name)
            except FileNotFoundError:
                pass


def refresh_autostart():
    """If Windows starts the counter, it starts it from where it is now: the folder moved, or the entry
    is the one of a version from before Aniitracker."""
    command = autostart()
    if command and (command != subprocess.list2cmdline(PROGRAM + ["--demarrage"]) or run_value(OLD_RUN_NAME)):
        try:
            set_autostart(True)
        except OSError:
            pass


def take_new_name():
    """A 1.3.0 counter updates itself under its own name, "Compteur pity Aniimo.exe": the new version takes
    the name Aniitracker.exe (Windows lets a running program be renamed). True if it did."""
    exe = PROGRAM[0]
    new = os.path.join(os.path.dirname(exe), updater.EXE)
    if os.path.basename(exe).lower() != updater.OLD_EXE.lower() or os.path.exists(new):
        return False
    try:
        os.rename(exe, new)
    except OSError:
        return False
    PROGRAM[0] = new
    return True


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


class Panel:
    """One panel in its own borderless always-on-top window: dragged on its own, or all panels
    together with Shift; it snaps to the other panels and to the screen edges (Overlay.snap)."""

    def __init__(self, overlay, name, menu):
        self.o, self.name = overlay, name
        self.top = tk.Toplevel(overlay.root)
        self.top.withdraw()
        self.top.overrideredirect(True)
        self.top.attributes("-topmost", True)
        self.top.attributes("-alpha", overlay.cfg["opacity"])
        self.top.configure(bg=theme.KEY_HEX)
        self.top.attributes("-transparentcolor", theme.KEY_HEX)  # only the rounded panel shows
        self.label = tk.Label(self.top, bd=0, highlightthickness=0, bg=theme.KEY_HEX)
        self.label.pack()
        # errors, shown under the first panel on screen
        self.status = tk.Label(self.top, bg=NIGHT, fg=WARN, font=("Segoe UI", 8), anchor="w", justify="left",
                               wraplength=round(theme.W * overlay.scale))
        self.photo, self.image, self.shown = None, None, False
        x, y = overlay.positions[name]
        self.top.geometry(f"+{x}+{y}")
        self.top.bind("<ButtonPress-1>", lambda e: overlay.drag_start(self, e))
        self.top.bind("<B1-Motion>", overlay.drag_move)
        self.top.bind("<ButtonRelease-1>", overlay.drag_end)
        self.top.bind("<Button-3>", menu)
        self.top.bind("<MouseWheel>", lambda e: overlay.resize(self, e))

    @property
    def scale(self):
        """Screen scale times the size the player chose for this panel."""
        return self.o.scale * self.o.sizes.get(self.name, 1.0)

    def set_image(self, image):
        if self.image is not None and image == self.image:  # the same picture: no repaint
            return
        self.image = image
        self.photo = ImageTk.PhotoImage(image)  # kept: tk forgets pictures nobody holds
        self.label.config(image=self.photo)

    def show(self, visible):
        self.shown = visible
        if visible:
            self.top.deiconify()
        else:
            self.top.withdraw()

    def size(self):
        self.top.update_idletasks()
        return self.top.winfo_reqwidth(), self.top.winfo_reqheight()


def number(n):
    return f"{n:,}".replace(",", "\u202f")


class Banner:
    """Top-of-screen strip: prismatic energy of the area last read in the Enrichir menu, what is
    missing to Full, and how many Lucky / Super Lucky that makes. Lucky and Super Lucky counted
    since that reading are added as an estimate (≈) until the menu is read again."""

    def __init__(self, overlay, scale):
        import floraison
        self.fl, self.overlay, self.scale = floraison, overlay, scale
        self.data = load_json(READINGS, {"readings": {}, "last": None})
        self.panel = Panel(overlay, "floraison", overlay.popup)
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
        if not self.panel.shown:
            return
        fl = self.fl
        zone = self.data.get("last")
        r = self.data["readings"].get(zone)
        if not r:
            image = theme.floraison_panel(None, None, None, fl.FULL, None, 0, 0, "", None, self.panel.scale,
                                          hint="Ouvre le menu Enrichir d'une zone")
        else:
            energy, lucky, sup = self.estimate(r)
            stage, progress = fl.state_of(energy)  # the game's bar: how far into the state
            n = fl.needs(energy, self.overlay.cfg["floraison_gain"])
            since = " ".join(t for t in (f"+{lucky} Lucky" if lucky else "", f"+{sup} Super" if sup else "") if t)
            image = theme.floraison_panel(zone, fl.STAGES[stage][0], energy, fl.FULL, n["missing"], n["lucky"],
                                          n["super"], since, progress, self.panel.scale, blooms=n["blooms"])
        self.panel.set_image(image)


class MenuWindow:
    """The menu, in a small window that stays open until closed (✕, Échap, the bubble again or an
    action): Windows' right-click menus closed by themselves over the game."""

    BG, FG, DIM, HOVER, LINE, GOLD = "#121a30", "#edebff", "#929cc4", "#2a3760", "#2e3b61", "#ffd166"

    def __init__(self, overlay):
        self.o, self.top = overlay, None

    def is_open(self):
        return self.top is not None and self.top.winfo_exists()

    def toggle(self, x, y):
        if self.is_open():
            self.close()
        else:
            self.open(x, y)

    def close(self):
        if self.is_open():
            self.top.destroy()
        self.top = None

    def open(self, x, y):
        self.close()
        o, px = self.o, self.o.px
        t = self.top = tk.Toplevel(o.root)
        t.withdraw()
        t.overrideredirect(True)
        t.attributes("-topmost", True)
        t.configure(bg=self.LINE)
        body = tk.Frame(t, bg=self.BG, padx=px(12), pady=px(10))
        body.pack(padx=1, pady=1)
        head = tk.Frame(body, bg=self.BG)
        head.pack(fill="x")
        tk.Label(head, text="Aniitracker", bg=self.BG, fg=self.FG, font=("Segoe UI Semibold", 11)).pack(side="left")
        tk.Label(head, text=VERSION, bg=self.BG, fg=self.DIM, font=("Segoe UI", 9)).pack(side="left", padx=px(6))
        close = tk.Label(head, text="✕", bg=self.BG, fg=self.DIM, font=("Segoe UI", 11), cursor="hand2", padx=px(4))
        close.pack(side="right")
        close.bind("<Button-1>", lambda e: self.close())
        if o.update:
            self.button(body, f"✦ Installer la version {o.update['version']}…", o.show_update, color=self.GOLD)
        s = o.state
        lucky, sup = pity.odds(s)
        for line in (odds_line("Lucky", lucky), odds_line("Super Lucky", sup),
                     f"Semaine : {s.catches_week} captures · {s.lucky_week} Lucky · {s.super_week} Super",
                     f"Reset dans {duration(s.week + 7 * 86400 - time.time())}"):
            tk.Label(body, text=line, bg=self.BG, fg=self.DIM, font=("Segoe UI", 9), anchor="w", justify="left",
                     wraplength=px(360)).pack(fill="x")
        keys = o.cfg["hotkeys"]
        self.section(body, "Corriger")
        for label, action in (("+1 capture", "catch"), ("Lucky !", "lucky"), ("Super Lucky !", "super"),
                              ("Annuler la dernière action", "undo")):
            self.button(body, label, lambda a=action: o.do(a), keys.get(action, ""))
        if o.banner and o.banner.data["readings"]:
            self.section(body, "Floraison : zones lues")
            fl = o.banner.fl
            for zone, r in sorted(o.banner.data["readings"].items(), key=lambda kv: -kv[1]["ts"]):
                n = fl.needs(r["energy"], o.cfg["floraison_gain"])
                mark = "▸ " if zone == o.banner.data.get("last") else "   "
                self.button(body, f"{mark}{zone} · {fl.STAGES[r['stage']][0]} · {number(r['energy'])} pts",
                            lambda z=zone: o.banner.choose(z),
                            f"≈ {n['lucky']} Lucky ({n['lucky_best']} à {n['lucky_worst']}) · {n['super']} Super · "
                            f"{n['blooms']} Floraisons")
            self.button(body, "Oublier les lectures", o.banner.forget)
        self.section(body, "Panneaux : verrouillé · lié (les panneaux liés bougent ensemble)")
        for panel in o.all_panels():
            self.panel_row(body, panel.name)
        locked_all = all(o.locks.get(p.name) for p in o.all_panels())
        self.button(body, "Tout déverrouiller" if locked_all else "Tout verrouiller",
                    lambda: o.lock_all(not locked_all))
        self.section(body, "Compteur")
        self.button(body, "Régler les compteurs…", o.edit)
        self.button(body, "Réglages…", o.settings)
        self.button(body, "Ouvrir le dossier", lambda: os.startfile(HERE))
        self.button(body, "Vérifier le fonctionnement", o.self_check)
        self.button(body, "Rechercher une mise à jour", lambda: o.check_update(manual=True))
        self.button(body, "Quitter", o.root.destroy)
        t.bind("<Escape>", lambda e: self.close())
        t.update_idletasks()
        w, h = t.winfo_reqwidth(), t.winfo_reqheight()
        sw, sh = t.winfo_screenwidth(), t.winfo_screenheight()
        x, y = min(max(x + px(8), 0), sw - w), min(max(y + px(8), 0), sh - h)
        t.geometry(f"+{x}+{y}")
        t.deiconify()

    def panel_row(self, parent, name):
        """'Lucky   ☑ verrouillé   ☐ lié': two switches that leave the menu open."""
        o = self.o
        row = tk.Frame(parent, bg=self.BG)
        row.pack(fill="x")
        tk.Label(row, text=o.PANEL_NAMES[name], bg=self.BG, fg=self.FG, font=("Segoe UI", 10), anchor="w",
                 width=12, padx=self.o.px(6)).pack(side="left")
        for word, state, toggle in (("verrouillé", lambda: o.locks.get(name, False), lambda: o.toggle_lock(name)),
                                    ("lié", lambda: name in o.links, lambda: o.toggle_link(name))):
            switch = tk.Label(row, bg=self.BG, fg=self.FG, font=("Segoe UI", 10), cursor="hand2", padx=self.o.px(6))
            switch.pack(side="left")

            def show(sw=switch, word=word, state=state):
                sw.config(text=("☑ " if state() else "☐ ") + word, fg=self.FG if state() else self.DIM)

            def flip(e, toggle=toggle, show=show):
                toggle()
                show()
            show()
            switch.bind("<Button-1>", flip)
            switch.bind("<Enter>", lambda e, sw=switch: sw.config(bg=self.HOVER))
            switch.bind("<Leave>", lambda e, sw=switch: sw.config(bg=self.BG))

    def section(self, parent, title):
        tk.Frame(parent, bg=self.LINE, height=1).pack(fill="x", pady=(self.o.px(8), self.o.px(4)))
        tk.Label(parent, text=title.upper(), bg=self.BG, fg=self.DIM, font=("Segoe UI Semibold", 8),
                 anchor="w").pack(fill="x")

    def button(self, parent, text, command, hint="", color=None):
        """A line of the menu: lights up under the mouse, runs `command` and closes the menu."""
        row = tk.Frame(parent, bg=self.BG, cursor="hand2")
        row.pack(fill="x")
        parts = [tk.Label(row, text=text, bg=self.BG, fg=color or self.FG, font=("Segoe UI", 10), anchor="w",
                          padx=self.o.px(6), pady=self.o.px(2))]
        parts[0].pack(side="left")
        if hint:
            parts.append(tk.Label(row, text=hint, bg=self.BG, fg=self.DIM, font=("Segoe UI", 9), padx=self.o.px(6)))
            parts[1].pack(side="right")

        def light(color):
            for w in [row] + parts:
                w.config(bg=color)

        def run(e):
            self.close()
            command()
        for w in [row] + parts:
            w.bind("<Enter>", lambda e: light(self.HOVER))
            w.bind("<Leave>", lambda e: light(self.BG))
            w.bind("<Button-1>", run)


class Settings:
    """What to show, the correction keys and the server; every change applies at once and is saved."""

    def __init__(self, overlay):
        self.o, cfg = overlay, overlay.cfg
        if getattr(overlay, "settings_window", None) and overlay.settings_window.winfo_exists():
            overlay.settings_window.lift()
            return
        d = self.d = overlay.settings_window = tk.Toplevel(overlay.root)
        self.ready = False  # sliders call back when they get their first value: not a change
        d.title("Réglages du compteur")
        d.attributes("-topmost", True)
        d.resizable(False, False)
        pad = {"padx": 10, "pady": 3}

        show = tk.LabelFrame(d, text="Afficher", padx=8, pady=4)
        show.pack(fill="x", padx=10, pady=(10, 4))
        self.flags = {}
        items = [("show_lucky", "Panneau Lucky"), ("show_super", "Panneau Super Lucky")]
        if overlay.banner:
            items.append(("show_floraison", "Panneau Floraison (énergie prismatique)"))
        items.append(("show_icon", "Bulle (un clic ouvre le menu)"))
        for key, label in items:
            v = self.flags[key] = tk.BooleanVar(value=cfg[key])
            tk.Checkbutton(show, text=label, variable=v, command=lambda k=key: self.set_flag(k)).pack(anchor="w")
        tk.Label(show, fg="#555", justify="left", text=(
            "Glisse un panneau pour le déplacer seul, Maj + glisser pour tous les déplacer ensemble. "
            "Près d'un autre panneau ou d'un bord de l'écran, il s'aimante."), wraplength=400).pack(anchor="w")
        sizes = tk.LabelFrame(d, text="Taille de chaque panneau (Ctrl + molette sur un panneau marche aussi)",
                              padx=8, pady=4)
        sizes.pack(fill="x", padx=10, pady=4)
        self.sizes = {}
        names = [("lucky", "Lucky"), ("super", "Super Lucky")] + ([("floraison", "Floraison")] if overlay.banner
                                                                    else []) + [("icone", "Bulle")]
        for r, (name, label) in enumerate(names):
            tk.Label(sizes, text=label, anchor="w", width=12).grid(row=r, column=0, sticky="w")
            scale = self.sizes[name] = tk.Scale(sizes, from_=60, to=160, resolution=5, orient="horizontal",
                                                length=220, showvalue=True,
                                                command=lambda v, n=name: self.set_size(n, int(float(v))))
            scale.set(round(overlay.sizes.get(name, 1.0) * 100))
            scale.grid(row=r, column=1, sticky="w")
            tk.Label(sizes, text="%").grid(row=r, column=2, sticky="w")
        row = tk.Frame(show)
        row.pack(anchor="w", pady=(4, 0))
        tk.Label(row, text="Opacité", width=7, anchor="w").pack(side="left")
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

        start = tk.LabelFrame(d, text="Démarrage et mises à jour", padx=8, pady=4)
        start.pack(fill="x", padx=10, pady=4)
        self.autostart = tk.BooleanVar(value=autostart() is not None)
        tk.Checkbutton(start, variable=self.autostart, command=self.toggle_autostart, justify="left", wraplength=420,
                       text="Lancer le compteur avec Windows : ses panneaux apparaissent quand Aniimo "
                            "démarre et disparaissent quand il se ferme").pack(anchor="w")
        self.updates = tk.BooleanVar(value=bool(cfg["check_updates"]))
        tk.Checkbutton(start, variable=self.updates, command=self.set_updates, justify="left", wraplength=420,
                       text="Chercher les nouvelles versions au démarrage (le compteur demande à GitHub le "
                            "numéro de la dernière version, rien d'autre n'est envoyé)").pack(anchor="w")
        row = tk.Frame(start)
        row.pack(anchor="w", pady=(2, 2))
        tk.Button(row, text="Rechercher maintenant", command=lambda: overlay.check_update(manual=True)).pack(side="left")
        tk.Label(row, text=f"Version installée : {VERSION}", fg="#555").pack(side="left", padx=8)

        tk.Button(d, text="Fermer", width=12, command=self.close).pack(pady=(4, 10))
        d.protocol("WM_DELETE_WINDOW", self.close)
        d.bind("<KeyPress>", self.key_pressed)
        self.capturing = None
        d.after(300, lambda: setattr(self, "ready", True))
        d.focus_force()

    def save(self):
        save_json(CONFIG, self.o.cfg)

    def set_flag(self, key):
        if not any(v.get() for v in self.flags.values()):  # nothing left to right-click on to come back here
            self.flags[key].set(True)
            self.note.config(text="Garde au moins un panneau affiché : c'est par son clic droit qu'on revient ici.")
            return
        self.o.cfg[key] = self.flags[key].get()
        self.o.layout()
        self.save()

    def set_opacity(self, percent):
        if not self.ready:
            return
        self.o.cfg["opacity"] = percent / 100
        for panel in self.o.all_panels():
            panel.top.attributes("-alpha", percent / 100)
        self.save()

    def set_size(self, name, percent):
        if not self.ready:
            return
        self.o.sizes[name] = percent / 100
        self.o.resized()

    def toggle_autostart(self):
        try:
            set_autostart(self.autostart.get())
        except OSError as e:
            self.autostart.set(autostart() is not None)
            self.note.config(text=f"Windows refuse le lancement automatique : {e}")

    def set_updates(self):
        self.o.cfg["check_updates"] = self.updates.get()
        self.save()
        if self.updates.get():
            self.o.check_update()

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
        self.scale = scale = root.winfo_fpixels("1i") / 96
        self.px = px = lambda n: round(n * scale)
        root.withdraw()  # every panel has its own window
        self.positions, locked = self.load_positions()
        saved = load_json(WINDOW, {})
        self.sizes = {k: float(v) for k, v in saved.get("sizes", {}).items()}
        # a lock per panel (before 1.3: one for all) and the panels linked together
        self.locks = {k: bool(v) for k, v in saved.get("locks", {}).items()} or \
            {k: locked for k in ("lucky", "super", "floraison", "icone")}
        self.links = set(saved.get("links", []))
        self.hotkeys = None      # Hotkeys, restarted by the settings when a key changes
        self.show_event = None   # set by a second start of the counter: show it (see main)
        self.update = None       # a newer version, found on GitHub
        self.update_window = None
        self.notice = ""         # a short message under the first panel, in blue
        self.affinity = {}       # capture setting last given to each window
        # started with Windows (--demarrage): the panels wait for Aniimo, and go when it closes
        self.follow_game, self.game_running = "--demarrage" in sys.argv, False
        root.report_callback_exception = lambda *exc: log_error("tk", exc)
        self.menu_window = MenuWindow(self)
        self.panels = {kind: Panel(self, kind, self.popup) for kind in ("lucky", "super")}
        self.banner = Banner(self, scale) if cfg["floraison"] else None
        self.icon = Panel(self, "icone", self.popup)  # the bubble: one click opens the menu
        self.layout()
        self.rebuild()
        self.tick()
        self.poll()
        self.keep_on_top()
        if self.follow_game:
            self.watch_game()
        if cfg["check_updates"] is None and not self.follow_game:
            root.after(1500, self.ask_updates)
        root.after(5000, self.auto_check)

    def popup(self, e):
        """Right click on a panel, or a click on the bubble: the menu, which stays open."""
        self.menu_window.toggle(e.x_root, e.y_root)

    def all_panels(self):
        return list(self.panels.values()) + ([self.banner.panel] if self.banner else []) + [self.icon]

    def load_positions(self):
        """Where each panel goes, and whether they are locked. Before 1.2 the counter was one window
        (window.json x, y) and the banner another (window_floraison.json): carried over."""
        win = load_json(WINDOW, {})
        pos = {k: list(v) for k, v in win.get("panels", {}).items()}
        height, gap = round(theme.H * self.scale), self.px(6)
        if "lucky" not in pos:
            x, y = win.get("x", 40), win.get("y", 40)
            pos["lucky"], pos["super"] = [x, y], [x, y + height + gap]
        old = load_json(BANNER, {})
        if "floraison" not in pos:
            if old.get("x") is not None:
                pos["floraison"] = [old["x"], old.get("y", 8)]
            else:
                pos["floraison"] = [(self.root.winfo_screenwidth() - round(theme.W * self.scale)) // 2, 8]
        if "icone" not in pos:  # the bubble, right of the Lucky panel
            pos["icone"] = [pos["lucky"][0] + round(theme.W * self.scale) + gap, pos["lucky"][1]]
        if old.get("shown") is False and "show_floraison" not in load_json(CONFIG, {}):
            self.cfg["show_floraison"] = False
        return pos, win.get("locked", old.get("locked", False))

    def save_positions(self):
        save_json(WINDOW, {"panels": self.positions, "sizes": self.sizes, "locks": self.locks,
                           "links": sorted(self.links)})

    PANEL_NAMES = {"lucky": "Lucky", "super": "Super Lucky", "floraison": "Floraison", "icone": "Bulle"}

    def toggle_lock(self, name):
        self.locks[name] = not self.locks.get(name, False)
        self.save_positions()

    def toggle_link(self, name):
        self.links ^= {name}
        self.save_positions()

    def lock_all(self, value):
        self.locks = {p.name: value for p in self.all_panels()}
        self.save_positions()

    def resize(self, panel, e):
        """Ctrl + wheel over a panel: bigger or smaller, from 60 to 160 % (even locked: never by accident)."""
        if not e.state & 0x0004:
            return
        size = self.sizes.get(panel.name, 1.0) * (1.1 if e.delta > 0 else 1 / 1.1)
        self.sizes[panel.name] = round(min(max(size, 0.6), 1.6), 2)
        self.resized()

    def resized(self):
        self.redraw()
        for panel in self.all_panels():
            panel.status.config(wraplength=round(theme.W * panel.scale))
        self.save_positions()

    def drag_start(self, panel, e):
        """Drag a panel; with Shift all the panels, and a linked panel brings the other linked ones.
        A locked panel never moves."""
        self.press = (panel, e.x_root, e.y_root)
        self.drag = None
        if self.locks.get(panel.name):
            return
        shown = [p for p in self.all_panels() if p.shown and not self.locks.get(p.name)]
        if e.state & 0x0001:  # Shift
            moved = shown
        elif panel.name in self.links:
            moved = [p for p in shown if p.name in self.links]
        else:
            moved = [panel]
        together = len(moved) > 1  # a group keeps its shape: no snapping
        self.drag = (panel, together, [(p, p.top.winfo_x() - e.x_root, p.top.winfo_y() - e.y_root) for p in moved])

    def drag_move(self, e):
        if not self.drag:
            return
        panel, together, moved = self.drag
        for p, dx, dy in moved:
            x, y = e.x_root + dx, e.y_root + dy
            if not together:
                x, y = self.snap(p, x, y)
            p.top.geometry(f"+{x}+{y}")
            self.positions[p.name] = [x, y]

    def drag_end(self, e):
        if self.drag:
            self.drag = None
            self.save_positions()
        panel, x, y = getattr(self, "press", (None, 0, 0))
        if panel is self.icon and abs(e.x_root - x) + abs(e.y_root - y) < self.px(5):
            self.popup(e)  # a click on the bubble, not a drag

    def snap(self, panel, x, y):
        """Pull a panel being dragged against the others (stacked, side by side or aligned) and
        against the screen edges when it comes within a few pixels."""
        reach, gap = self.px(14), self.px(6)
        w, h = panel.size()
        xs, ys = [0, self.root.winfo_screenwidth() - w], [0, self.root.winfo_screenheight() - h]
        for other in self.all_panels():
            if other is panel or not other.shown:
                continue
            ox, oy = self.positions[other.name]
            ow, oh = other.size()
            xs += [ox, ox + ow - w, ox + ow + gap, ox - w - gap]
            ys += [oy, oy + oh - h, oy + oh + gap, oy - h - gap]
        best_x = min(xs, key=lambda v: abs(v - x))
        best_y = min(ys, key=lambda v: abs(v - y))
        return (best_x if abs(best_x - x) <= reach else x), (best_y if abs(best_y - y) <= reach else y)

    def layout(self):
        """Show the panels the settings ask for (none while waiting for the game); errors, or else
        a notice, go under the first one shown."""
        hidden = self.follow_game and not self.game_running
        for kind, panel in self.panels.items():
            panel.show(self.cfg[f"show_{kind}"] and not hidden)
        if self.banner:
            self.banner.panel.show(self.cfg["show_floraison"] and not hidden)
        self.icon.show(self.cfg["show_icon"] and not hidden)
        shown = [p for p in self.all_panels() if p.shown]
        for panel in self.all_panels():
            panel.status.pack_forget()
        if (self.errors or self.notice) and shown:
            shown[0].status.config(text=" · ".join(self.errors) or self.notice, fg=WARN if self.errors else NOTE)
            shown[0].status.pack(fill="x", padx=8, pady=(2, 0))
        if self.banner:
            self.banner.redraw()

    def settings(self):
        Settings(self)

    def rebuild(self):
        self.state = pity.build(self.log.events, time.time(), self.cfg["server"], self.cfg["reset_pity_weekly"])
        self.redraw()

    def redraw(self):
        self.icon.set_image(theme.bubble(ICON, self.icon.scale, badge=bool(self.update)))
        for kind, o in zip(("lucky", "super"), pity.odds(self.state)):
            image = theme.pity_panel(kind, o.pity, o.cap, o.done, o.left, self.state.catches_week,
                                     self.panels[kind].scale, self.highlight.get(kind))
            self.panels[kind].set_image(image)
        if self.banner:
            self.banner.redraw()  # Lucky counted since the last reading change its estimate

    def add(self, e):
        try:
            self.log.add(e)
            if any(x.startswith("Enregistrement") for x in self.errors):
                self.clear_errors("Enregistrement")
        except OSError as err:  # kept in memory: written with the next one
            log_error("enregistrement")
            self.show_error(f"Enregistrement impossible ({err.strerror or err}), nouvel essai à la prochaine capture")
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
        try:
            if pity.week_start(time.time(), self.cfg["server"]) != self.state.week:
                self.rebuild()
        finally:
            self.root.after(5000, self.tick)

    def poll(self):
        """Messages from the hotkeys, the screen and the sound. One that goes wrong is noted in
        data\\erreurs.txt and shown, and the counting goes on: an error here used to stop it."""
        try:
            while True:
                msg = self.inbox.get_nowait()
                try:
                    self.handle(msg)
                except Exception as e:
                    log_error(f"message {msg[0]}")
                    try:
                        self.show_error(f"Erreur : {e} (détails dans data\\erreurs.txt)")
                    except Exception:
                        pass
        except queue.Empty:
            pass
        finally:
            if self.show_event and kernel32.WaitForSingleObject(self.show_event, 0) == 0:  # WAIT_OBJECT_0
                self.show_again()
            self.root.after(50, self.poll)

    def handle(self, msg):
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
        elif msg[0] == "update":  # GitHub answered (check_update)
            self.on_update(*msg[1:])
        elif msg[0] == "ok":  # a screen reader works again after an error: its message goes
            self.clear_errors(msg[1] + " :")
        else:
            if len(msg) > 2 and msg[1] not in self.errors:  # an error in a screen reader, with its traceback
                log_error(msg[1].split(" :")[0], details=msg[2])
            self.show_error(msg[1])

    def show_notice(self, text, seconds=20):
        self.notice = text
        self.layout()
        self.root.after(seconds * 1000, lambda: (setattr(self, "notice", ""), self.layout()))

    def show_again(self):
        """The counter was started again while it runs: it stops waiting for the game and opens its menu."""
        self.follow_game = False
        self.layout()
        x, y = self.positions["icone" if self.cfg["show_icon"] else "lucky"]
        self.menu_window.open(x, y)

    def watch_game(self):
        """Started with Windows: the panels show while Aniimo runs."""
        try:
            import game_audio
            running = game_audio.game_pid() is not None
            if running != self.game_running:
                self.game_running = running
                self.layout()
        finally:
            if self.follow_game:
                self.root.after(3000, self.watch_game)

    def self_check(self):
        """Menu > Vérifier le fonctionnement: the self-test in a process of its own; its report
        opens in a message box and goes to test.txt."""
        updater.start(PROGRAM + ["--test"], HERE)

    def ask_updates(self):
        """First start: may the counter look for new versions on GitHub? Closed unanswered: asked next time."""
        d = self.update_window = tk.Toplevel(self.root)
        d.title("Mises à jour du compteur")
        d.attributes("-topmost", True)
        d.resizable(False, False)
        tk.Label(d, justify="left", wraplength=440, text=(
            "Le compteur peut regarder sur GitHub, à chaque démarrage, s'il existe une nouvelle version. "
            "Il demande seulement le numéro de la dernière version : rien sur vous ni sur vos captures "
            "n'est envoyé. S'il y en a une, la bulle porte un point doré et le menu propose de l'installer "
            "en un clic, en gardant vos compteurs et vos réglages.\n\n"
            "Vous pourrez changer d'avis dans les réglages.")).pack(padx=14, pady=(14, 8))

        def answer(yes):
            self.cfg["check_updates"] = yes
            save_json(CONFIG, self.cfg)
            d.destroy()
            if yes:
                self.check_update()
        row = tk.Frame(d)
        row.pack(pady=(0, 12))
        tk.Button(row, text="Oui, chercher les mises à jour", command=lambda: answer(True)).pack(side="left", padx=4)
        tk.Button(row, text="Non merci", command=lambda: answer(False)).pack(side="left", padx=4)
        center(d)

    def auto_check(self):
        """Look for a new version at start, then twice a day, if the player said yes."""
        if self.cfg["check_updates"]:
            self.check_update()
        self.root.after(12 * 3600 * 1000, self.auto_check)

    def check_update(self, manual=False):
        """Ask GitHub, in a thread, for a version newer than this one; the answer comes through the inbox."""
        def run():
            try:
                self.inbox.put(("update", updater.latest(VERSION), manual, None))
            except Exception as e:
                self.inbox.put(("update", None, manual, str(e)))
        threading.Thread(target=run, daemon=True).start()

    def on_update(self, info, manual, error):
        if info:
            self.update = info
            self.redraw()  # the bubble's gold dot
            if manual:
                self.show_update()
        elif manual:
            self.tell("Mises à jour du compteur",
                      f"Impossible de joindre GitHub : {error}" if error else
                      f"Vous avez la dernière version ({VERSION}).")

    def show_update(self):
        UpdateWindow(self, self.update)

    def tell(self, title, text):
        """A small message that does not block the counting (a message box would)."""
        if self.update_window is not None and self.update_window.winfo_exists():
            self.update_window.destroy()
        d = self.update_window = tk.Toplevel(self.root)
        d.title(title)
        d.attributes("-topmost", True)
        d.resizable(False, False)
        tk.Label(d, text=text, justify="left", wraplength=420).pack(padx=14, pady=(14, 8))
        tk.Button(d, text="OK", width=10, command=d.destroy).pack(pady=(0, 12))
        d.bind("<Return>", lambda e: d.destroy())
        d.bind("<Escape>", lambda e: d.destroy())
        center(d)

    def show_error(self, text):
        if text not in self.errors:
            self.errors.append(text)
        self.layout()

    def clear_errors(self, prefix):
        self.errors = [e for e in self.errors if not e.startswith(prefix)]
        self.layout()

    def keep_on_top(self):
        try:
            self.on_top()
        finally:
            self.root.after(2000, self.keep_on_top)

    def on_top(self):
        zones = [z for w in self.watchers for z in w.zones]
        # the menu and the dialogs last: each call puts its window above the ones before, and
        # putting the panels back on top every 2 s used to hide the right-click menu
        extra = [self.menu_window.top, getattr(self, "settings_window", None), getattr(self, "edit_window", None),
                 self.update_window]
        for window in [p.top for p in self.all_panels() if p.shown] + [w for w in extra if w is not None]:
            if not window.winfo_exists() or not window.winfo_viewable():
                continue
            # borderless games can push themselves above other topmost windows
            hwnd = user32.GetAncestor(window.winfo_id(), 2)  # GA_ROOT
            user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x13)  # HWND_TOPMOST, NOSIZE|NOMOVE|NOACTIVATE
            # over a part of the screen the watchers read, the window would hide it from their
            # screen copies: then it leaves screen captures (also Discord, recordings,
            # screenshots); elsewhere it shows in them. Windows 10 2004+. Given only when it
            # changes: given again and again, it can make the window flicker
            r = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            over = any(r.left < z[2] and z[0] < r.right and r.top < z[3] and z[1] < r.bottom for z in zones)
            want = 0x11 if over else 0  # WDA_EXCLUDEFROMCAPTURE / WDA_NONE
            if self.affinity.get(hwnd) != want:
                user32.SetWindowDisplayAffinity(hwnd, want)
                self.affinity[hwnd] = want

    def edit(self):
        """Dialog to set the counters, e.g. when starting mid-week."""
        d = self.edit_window = tk.Toplevel(self.root)
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


class UpdateWindow:
    """A new version: what it brings, and the button that installs it (download, check, swap, restart)."""

    def __init__(self, overlay, info):
        o, self.info = overlay, info
        self.o, self.progress, self.result = o, None, None
        if o.update_window is not None and o.update_window.winfo_exists():
            o.update_window.destroy()
        d = self.d = o.update_window = tk.Toplevel(o.root)
        d.title("Mise à jour du compteur")
        d.attributes("-topmost", True)
        d.resizable(False, False)
        tk.Label(d, text=f"Version {info['version']} disponible (vous avez la {VERSION})",
                 font=("Segoe UI Semibold", 11)).pack(anchor="w", padx=12, pady=(12, 4))
        notes = tk.Text(d, width=70, height=14, wrap="word", font=("Segoe UI", 9), relief="flat", bg=d.cget("bg"))
        notes.insert("1.0", info["notes"].replace("**", "").strip() or "Pas de notes pour cette version.")
        notes.config(state="disabled")
        notes.pack(padx=12, pady=4)
        tk.Label(d, text="Vos compteurs, vos réglages et la place des panneaux sont gardés.",
                 fg="#555").pack(anchor="w", padx=12)
        self.status = tk.Label(d, text="", anchor="w", justify="left", wraplength=480)
        self.status.pack(fill="x", padx=12, pady=(4, 0))
        row = tk.Frame(d)
        row.pack(pady=10)
        self.button = None
        if getattr(sys, "frozen", False) and info["url"]:
            self.button = tk.Button(row, text="Installer et redémarrer", width=22, command=self.install)
            self.button.pack(side="left", padx=4)
        else:  # from the sources: git pull
            self.status.config(text="Lancé depuis les sources : mettez-les à jour avec git pull.")
        tk.Button(row, text="Voir sur GitHub", width=16, command=lambda: os.startfile(info["page"])).pack(side="left",
                                                                                                         padx=4)
        self.later = tk.Button(row, text="Plus tard", width=12, command=d.destroy)
        self.later.pack(side="left", padx=4)
        d.bind("<Escape>", lambda e: self.later["state"] == "normal" and d.destroy())
        center(d)

    def install(self):
        self.button.config(state="disabled")
        self.later.config(state="disabled")
        self.d.protocol("WM_DELETE_WINDOW", lambda: None)  # not closed halfway
        self.result = None

        def run():
            try:
                path = updater.download(self.info, HERE, VERSION, lambda done, total: setattr(self, "progress",
                                                                                              (done, total)))
                self.progress = "install"
                updater.install(path, PROGRAM[0])
                self.result = "ok"
            except Exception as e:
                log_error("mise à jour")
                self.result = f"La mise à jour n'a pas pu se faire : {e}"
        threading.Thread(target=run, daemon=True).start()
        self.follow()

    def follow(self):
        """Progress; once installed, the new version is started and this one quits."""
        if self.result == "ok":
            self.status.config(text="Installée. Redémarrage…")
            if self.o.hotkeys:
                self.o.hotkeys.stop()  # its keys free for the new version
            updater.start(PROGRAM + ["--apres-maj"] + (["--demarrage"] if self.o.follow_game else []), HERE)
            self.o.root.after(300, self.o.root.destroy)
            return
        if self.result:
            self.status.config(text=self.result + " Vous pouvez aussi la télécharger sur GitHub.", fg=WARN)
            self.button.config(state="normal")
            self.later.config(state="normal")
            self.d.protocol("WM_DELETE_WINDOW", self.d.destroy)
            return
        if self.progress == "install":
            self.status.config(text="Vérification et installation…")
        elif self.progress:
            done, total = self.progress
            self.status.config(text=f"Téléchargement… {done * 100 // total} %" if total else
                               f"Téléchargement… {done // 1_000_000} Mo")
        else:
            self.status.config(text="Téléchargement…")
        self.d.after(200, self.follow)


def center(window):
    window.update_idletasks()
    w, h = window.winfo_reqwidth(), window.winfo_reqheight()
    window.geometry(f"+{(window.winfo_screenwidth() - w) // 2}+{(window.winfo_screenheight() - h) // 3}")


def message(text, title="Aniitracker"):
    user32.MessageBoxW(None, text, title, 0x40 | 0x40000)  # MB_ICONINFORMATION | MB_TOPMOST


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

    lines.append(f"OK   version : {VERSION} ({'exe' if getattr(sys, 'frozen', False) else 'sources'})")
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
    errors = last_errors()
    lines.append("INFO dernières erreurs notées : " + ("aucune" if not errors else "\n     " + "\n     ".join(errors)))
    report = "\n".join(lines)
    with open(os.path.join(HERE, "test.txt"), "w", encoding="utf-8") as f:
        f.write(report + "\n")
    if "--quiet" not in sys.argv:
        message(report, "Test du compteur")


def main():
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
    if "--test" in sys.argv:
        return self_test()
    # one counter at a time; after an update, the new version waits for the old one to quit
    for _ in range(80 if "--apres-maj" in sys.argv else 1):
        mutex = kernel32.CreateMutexW(None, False, MUTEX)
        if kernel32.GetLastError() != 183:  # ERROR_ALREADY_EXISTS
            break
        kernel32.CloseHandle(mutex)
        time.sleep(0.25)
    else:
        # started again: the running counter shows itself and opens its menu
        show = kernel32.OpenEventW(0x0002, False, SHOW_EVENT)  # EVENT_MODIFY_STATE
        if show:
            if "--demarrage" not in sys.argv:
                kernel32.SetEvent(show)
            kernel32.CloseHandle(show)
        else:
            message("Le compteur tourne déjà.")
        sys.exit(0)
    show_event = kernel32.CreateEventW(None, False, False, SHOW_EVENT)
    renamed = False
    if getattr(sys, "frozen", False):
        renamed = take_new_name()
        # what an update left: the old exe, once it has quit
        threading.Thread(target=updater.cleanup, args=(PROGRAM[0],), daemon=True).start()
    os.makedirs(DATA, exist_ok=True)
    cfg = {**DEFAULT_CONFIG, **load_json(CONFIG, {})}
    cfg["hotkeys"] = {**DEFAULT_CONFIG["hotkeys"], **cfg["hotkeys"]}
    if not os.path.exists(CONFIG):
        save_json(CONFIG, cfg)
    refresh_autostart()
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
    root.title("Aniitracker")
    try:
        root.iconbitmap(default=ICON)  # dialogs and settings
    except tk.TclError:
        pass
    overlay = Overlay(root, cfg, Log(EVENTS), inbox)
    overlay.watchers, overlay.hotkeys, overlay.show_event = watchers, hotkeys, show_event
    if "--apres-maj" in sys.argv:
        overlay.show_notice(f"Le compteur s'appelle maintenant Aniitracker : version {VERSION} installée" if renamed
                            else f"Aniitracker mis à jour : version {VERSION}")
    root.mainloop()


if __name__ == "__main__":
    main()
