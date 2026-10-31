"""Count captured Aniimo from the capture notifications the game shows on the left of the screen.

Every Aniimo caught with a pod, alone or in a group, gets its own card ("Jebond / Niv. 52"). A new
card fades in at the bottom of the stack: for about 0.1 s it is a uniform light-grey bubble, then
its text appears. That bubble, followed by a readable "Niv.", is what Counter counts. Sounds can't
do this: in a group capture the success sounds of several Aniimo overlap.

A mass capture with a Culbuton shows no cards but one notification, "Capturer avec un Culbuton x10",
over a grid of the portraits caught (5 a row, each with its level badge): MassCounter counts those.

Only the screen is read (GDI copy of one small area), never the game's process.
Geometry is measured on a 2560x1440 screen and scaled for other heights.
"""
import ctypes, os, sys, threading, time
from ctypes import wintypes
import cv2
import numpy as np

HERE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))  # inside the exe: unpacked files
TEMPLATE = os.path.join(HERE, "images", "niv.png")  # "Niv." as drawn on a card, at 1440p
# card column at 1440p: x 150..490, y 640..1120 (stack of up to 3 cards, centres 748 / 884 / 1020)
AREA = (150, 640, 340, 480)
# Culbuton notification at 1440p: x 40..640, y 600..1300 (header and up to 4 rows of portraits)
MASS_AREA = (40, 600, 600, 700)
MASS_TEMPLATE = os.path.join(HERE, "images", "culbuton.png")  # "Capturer avec un Culbuton", 1440p
LIGHT_X = (50, 170)    # columns of the card bubble checked for the light-grey fade-in (x 200..320)
NIV_X = (20, 110)      # columns where "Niv." is searched (x 170..260)
FPS = 30


class Counter:
    """Feed it frames of AREA (BGR, 1440p scale); `step` returns the times of new cards."""

    SLOTS = (108, 245, 381)  # card centres in AREA (1440p y 748 / 885 / 1021)

    def __init__(self, template):
        self.tpl = template
        self.cards = []      # light bubbles seen lately: {"c": centre, "last": frame, "t": time, "ok", "done"}
        self.prev_niv = None
        self.tracks = []     # "Niv." of the cards on screen: [y, last frame seen]
        self.i = 0

    def features(self, img):
        hsv = cv2.cvtColor(img[:, LIGHT_X[0]:LIGHT_X[1]], cv2.COLOR_BGR2HSV)
        light = ((hsv[..., 2] > 140) & (hsv[..., 1] < 40)).mean(axis=1)
        g = cv2.cvtColor(img[:, NIV_X[0]:NIV_X[1]], cv2.COLOR_BGR2GRAY)
        niv = cv2.matchTemplate(g, self.tpl, cv2.TM_CCOEFF_NORMED).max(axis=1)
        return light, niv

    @staticmethod
    def light_bands(light):
        """(top, bottom) of runs of light rows, small gaps bridged."""
        out, start, gap, end = [], None, 0, 0
        for y, v in enumerate(list(light >= 0.8) + [False] * 8):
            if v:
                if start is None:
                    start = y
                gap, end = 0, y
            elif start is not None:
                gap += 1
                if gap > 6:
                    out.append((start, end))
                    start = None
        return out

    @staticmethod
    def niv_peaks(niv):
        out = []
        for y in np.argsort(niv)[::-1]:
            if niv[y] < 0.7:
                break
            if all(abs(y - o) >= 40 for o in out):
                out.append(int(y))
        return out

    def new_niv(self, niv, i):
        """Positions of the "Niv." that just appeared, not those moving up with their card.

        Each "Niv." continues the nearest one seen up to 3 frames before at the same height or a bit
        below (cards only move up, at most ~50 px a frame when the stack slides).
        """
        self.tracks = [k for k in self.tracks if i - k[1] <= 3]
        new = []
        for y in sorted(self.niv_peaks(niv)):
            near = [k for k in self.tracks if k[1] < i and y - 15 <= k[0] <= y + 70 * (i - k[1])]
            if near:
                k = min(near, key=lambda k: k[0] - y)
                k[0], k[1] = y, i
            else:
                self.tracks.append([y, i])
                new.append(y)
        return new

    def step(self, img, t, fps=FPS):
        """Process one frame shown at time t; return the times of the cards confirmed now."""
        light, niv = self.features(img)
        i, self.i = self.i, self.i + 1
        prev_niv = niv if self.prev_niv is None else self.prev_niv
        self.prev_niv = niv
        runs = self.light_bands(light)
        bubbles = [(a + b) / 2 for a, b in runs if 50 <= b - a + 1 <= 120]
        # a bubble fades in at a slot, or a little higher when the stack is already sliding up
        at_slot = [c for c in bubbles if any(-70 <= c - s <= 20 for s in self.SLOTS)]
        # a flash behind the panel lights up several slots at once, or the whole column
        flash = len(at_slot) > 1 or sum(b - a + 1 for a, b in runs) > 250
        new = self.new_niv(niv, i)
        for c in bubbles:
            # during a burst the stack slides up one slot (136 px) while the new card is still light
            same = [k for k in self.cards if i - k["last"] <= 2 and -100 <= c - k["c"] <= 15]
            if same:
                same[0]["c"], same[0]["last"] = c, i
                continue
            # a card fades in at a slot, where no card was readable just before
            # (light at another height, or over a card already there, is an effect behind the panel)
            text = prev_niv[max(int(c) - 3, 0):int(c) + 18]
            ok = not flash and c in at_slot and text.max(initial=0) < 0.7
            self.cards.append({"c": c, "last": i, "start": i, "t": t, "ok": ok, "done": False})
        found = []
        for k in self.cards:
            # its "Niv." shows ~7 px below the bubble 0.07-0.13 s later, maybe one slot higher
            # if the stack slid up meanwhile
            if k["ok"] and not k["done"] and i > k["start"]:
                if any(k["c"] - 150 <= y <= k["c"] + 30 for y in new):
                    k["done"] = True
                    found.append(k["t"])
        self.cards = [k for k in self.cards if i - k["last"] <= 0.3 * fps]
        return found


class MassCounter:
    """Feed it frames of MASS_AREA (BGR, 1440p scale); `step` returns [(time, Aniimo caught)]."""

    COLS = (-41, 55, 150, 246, 342)  # portrait centres, from the header text's top-left corner
    ROW0, ROW_STEP = 145, 102

    def __init__(self, template):
        self.tpl = template
        self.seen = []        # portraits counted on the last frames showing the header
        self.counted = 0      # Aniimo of the notification on screen already posted
        self.gone_since = None

    @staticmethod
    def portrait(hsv, x, y):
        """A portrait at (x, y): something light above a dark level badge with white digits."""
        face = hsv[max(y - 30, 0):y + 5, max(x - 28, 0):x + 28]
        badge = hsv[max(y + 8, 0):y + 28, max(x - 20, 0):x + 20]
        if face.size == 0 or badge.size == 0:
            return False
        light = ((face[..., 2] > 120) & (face[..., 1] < 70)).mean()
        white = ((badge[..., 2] > 200) & (badge[..., 1] < 50)).mean()
        dark = (badge[..., 2] < 90).mean()
        return light >= 0.15 and 0.08 <= white <= 0.4 and dark >= 0.35

    def count(self, img, hx, hy):
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        total = 0
        for row in range(4):
            y = hy + self.ROW0 + row * self.ROW_STEP
            n = sum(self.portrait(hsv, hx + dx, y) for dx in self.COLS)
            total += int(n)
            if n < len(self.COLS):  # a row that is not full is the last one
                break
        return total

    def step(self, img, t):
        score = cv2.matchTemplate(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), self.tpl, cv2.TM_CCOEFF_NORMED)
        _, best, _, (hx, hy) = cv2.minMaxLoc(score)
        if best < 0.7:
            self.seen = []
            if self.gone_since is None:
                self.gone_since = t
            elif t - self.gone_since > 1:  # notification closed: the next one is a new capture
                self.counted = 0
            return []
        self.gone_since = None
        self.seen = (self.seen + [self.count(img, hx, hy)])[-2:]
        # the grid fades in with the header: count once two frames agree; a capture added to the
        # notification on screen grows its grid, post only the new ones
        if len(self.seen) == 2 and self.seen[0] == self.seen[1] and self.seen[1] > self.counted:
            n, self.counted = self.seen[1] - self.counted, self.seen[1]
            return [(t, n)]
        return []


class Screen:
    """GDI copy of one area of the screen (layered windows such as the counter itself are left out)."""

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                    ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]

    def __init__(self, x, y, w, h):
        self.x, self.y, self.w, self.h = x, y, w, h
        u, self.g = ctypes.windll.user32, ctypes.windll.gdi32
        self.g.CreateCompatibleDC.restype = self.g.CreateCompatibleBitmap.restype = wintypes.HANDLE
        self.g.CreateCompatibleDC.argtypes = (wintypes.HANDLE,)
        self.g.CreateCompatibleBitmap.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_int)
        self.g.SelectObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        self.g.BitBlt.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                  wintypes.HANDLE, ctypes.c_int, ctypes.c_int, wintypes.DWORD)
        self.g.GetDIBits.argtypes = (wintypes.HANDLE, wintypes.HANDLE, wintypes.UINT, wintypes.UINT, ctypes.c_void_p,
                                     ctypes.c_void_p, wintypes.UINT)
        u.GetDC.restype = wintypes.HANDLE
        self.scr = u.GetDC(None)
        self.mem = self.g.CreateCompatibleDC(self.scr)
        self.bmp = self.g.CreateCompatibleBitmap(self.scr, w, h)
        self.g.SelectObject(self.mem, self.bmp)
        self.info = self.BITMAPINFOHEADER(ctypes.sizeof(self.BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        self.buf = (ctypes.c_ubyte * (w * h * 4))()

    def grab(self):
        self.g.BitBlt(self.mem, 0, 0, self.w, self.h, self.scr, self.x, self.y, 0x00CC0020)  # SRCCOPY
        self.g.GetDIBits(self.mem, self.bmp, 0, self.h, self.buf, ctypes.byref(self.info), 0)
        return np.frombuffer(self.buf, np.uint8).reshape(self.h, self.w, 4)[..., :3]

    def close(self):
        self.g.DeleteObject(wintypes.HANDLE(self.bmp))
        self.g.DeleteDC(wintypes.HANDLE(self.mem))
        ctypes.windll.user32.ReleaseDC(None, wintypes.HANDLE(self.scr))


def game_client(pid):
    """(x, y, width, height) on screen of the game's picture if the game is the window in front."""
    u = ctypes.windll.user32
    u.GetForegroundWindow.restype = wintypes.HWND
    hwnd = u.GetForegroundWindow()
    owner = wintypes.DWORD()
    u.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
    if not hwnd or owner.value != pid:
        return None
    r, origin = wintypes.RECT(), wintypes.POINT(0, 0)
    u.GetClientRect(hwnd, ctypes.byref(r))
    u.ClientToScreen(hwnd, ctypes.byref(origin))
    return (origin.x, origin.y, r.right, r.bottom) if r.bottom > 0 else None


class Watcher(threading.Thread):
    """Watches the notification area while the game is the window in front.

    Posts ("cards", unix time, Aniimo caught) to `out`. `zones` holds the screen rectangles read
    (left, top, right, bottom), so the counter window can stay out of the copies when over them.
    """

    MASS_EVERY = 6  # the Culbuton notification stays ~10 s: 5 looks a second are plenty

    def __init__(self, out, game_pid):
        super().__init__(daemon=True)
        self.out, self.game_pid = out, game_pid
        u = ctypes.windll.user32
        self.zones = self.zones_for((0, 0, u.GetSystemMetrics(0), u.GetSystemMetrics(1)))

    @staticmethod
    def rect(client, area):
        cx, cy, cw, ch = client
        scale = ch / 1440  # the game's interface scales with the picture height
        x, y, w, h = (round(v * scale) for v in area)
        return cx + x, cy + y, w, h

    def zones_for(self, client):
        return [(x, y, x + w, y + h) for x, y, w, h in (self.rect(client, a) for a in (AREA, MASS_AREA))]

    def run(self):
        tpl = cv2.imread(TEMPLATE, cv2.IMREAD_GRAYSCALE)
        mass_tpl = cv2.imread(MASS_TEMPLATE, cv2.IMREAD_GRAYSCALE)
        if tpl is None or mass_tpl is None:
            self.out.put(("err", "cartes : images/niv.png ou culbuton.png manquant"))
            return
        screens, client, pid, pid_checked = None, None, None, 0.0
        counter, mass = Counter(tpl), MassCounter(mass_tpl)
        next_t, k = time.time(), 0
        while True:
            try:
                next_t = max(next_t + 1 / FPS, time.time() - 0.5)
                time.sleep(max(next_t - time.time(), 0))
                if time.time() - pid_checked > 5:
                    pid, pid_checked = self.game_pid(), time.time()
                where = game_client(pid) if pid else None
                if where is None:  # game closed, minimised or behind another window
                    client, counter, mass = None, Counter(tpl), MassCounter(mass_tpl)
                    time.sleep(0.5)
                    continue
                if where != client:  # first look, or the game window moved or changed size
                    client = where
                    for sc in screens or ():
                        sc.close()
                    screens = [Screen(*self.rect(client, a)) for a in (AREA, MASS_AREA)]
                    self.zones = self.zones_for(client)
                k += 1
                found = [(t, 1) for t in counter.step(self.look(screens[0], AREA), time.time())]
                if k % self.MASS_EVERY == 0:
                    found += mass.step(self.look(screens[1], MASS_AREA), time.time())
                for t, n in found:
                    self.out.put(("cards", t, n))
            except Exception as e:
                self.out.put(("err", f"cartes : {e}"))
                time.sleep(5)

    @staticmethod
    def look(screen, area):
        """The area as the game shows it at 1440p."""
        img = screen.grab()
        if img.shape[:2] != (area[3], area[2]):
            img = cv2.resize(img, (area[2], area[3]), interpolation=cv2.INTER_AREA)
        return img
