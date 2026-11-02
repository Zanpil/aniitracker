"""Prismatic energy of an area (what makes a Bloom, « Floraison », bring a Prismana form): how far
it is from Full, and how many Lucky / Super Lucky that represents.

Rules from the game's own files (Data/formula_data.lua, area_rainbowPet_level_data):
- an area holds 0 to 10 500 points. States: Vide 0, Très faible 501, Léger 1 501, Abondant 3 001,
  Très élevé 6 001, Plein 10 500, where a Prismana form appears for sure;
- a Lucky adds 275 (40 %), 300 (50 %), 350 (5 %) or 500 points (5 %), 302.5 on average
  (formula 1124); a Super Lucky adds 1 500 (formula 1125);
- every gain also has a 3 % chance to fill the area at once (formula 1113);
- a nurture (one Floraison, its Veinfruit picked) gives about 480 points, 720 during the Vein
  abundance events (+50 %). This one is computed by the server: the number comes from a community
  guide whose Lucky figures (275 to 500) match the game's files, and it fits the effects the game
  plays when the fruit is picked (bigger from 500, 600 and 700 points). Adjustable in config.json.

The game never shows the number. Its Enrichir menu shows the state's name, and a bar filled with
(energy - state threshold) / (next threshold - state threshold): both together give the energy.
The bar is measured on the screen, the names are read by Windows' text recognition (OcrEngine,
through a PowerShell kept open). Only the screen is read, never the game's process.
"""
import collections, ctypes, difflib, math, os, subprocess, tempfile, threading, time, traceback, unicodedata
import cv2
import numpy as np

import cards
import updater

FULL = 10500
STAGES = [("Vide", 0), ("Très faible", 501), ("Léger", 1501), ("Abondant", 3001), ("Très élevé", 6001),
          ("Plein", FULL)]
LUCKY_GAINS = {275: 0.40, 300: 0.50, 350: 0.05, 500: 0.05}
LUCKY_MEAN = sum(g * p for g, p in LUCKY_GAINS.items())  # 302.5
SUPER_GAIN = 1500
BLOOM_GAIN = 480        # points of one nurture, see above ("floraison_gain" in config.json)
# areas with a Bloom (map_block_config_data), to correct what the text recognition reads
ZONES = ["Champs de Cumulaine", "Les Bois brumeux", "Le Détroit argenté", "Forêt des foudres", "Mer florale",
         "Côte de maréeflore", "Hautes-plaines ocres", "Voie de béryl", "Grève d'échoflux", "Crête des crocs",
         "Forêt des météores", "Pré-aux-frimas", "Isthme du zéphyr", "Bois de la tour-aux-roses",
         "Baie du croissant", "Marécages rosés", "Gorges des eaux sauvages", "Chutes de Nuage-Lointain"]

TITLE = os.path.join(cards.HERE, "images", "floraison_titre.png")  # « État de l'énergie prismatique... »
# Enrichir menu at 1440p, measured on a screenshot: the title's top-left corner is near (785, 280)
TITLE_AREA = (560, 220, 1520, 180)        # where the title (and the state's name after it) is looked for
TITLE_POS = (785, 280)
BAR_DX, BAR_DY, BAR_LEN = 313, 57, 348    # bar start and fill rows, from the title's corner
STAGE_DX, STAGE_W = 835, 320              # state's name, right of the title
LISTS = (300, 360, 2040, 760)             # the two columns of area names (the chosen one is a white pill)
COLUMNS = [(300, 360, 450, 760), (1750, 360, 590, 760)]  # LISTS without the flower in the middle


def energy_of(stage, fill):
    """Points of an area in state `stage` (index in STAGES) with its bar filled to `fill` (0..1)."""
    if stage >= len(STAGES) - 1:
        return FULL
    low, high = STAGES[stage][1], STAGES[stage + 1][1]
    return round(low + min(max(fill, 0.0), 1.0) * (high - low))


def state_of(energy):
    """(index in STAGES, share of that state filled): what the game's bar shows for an energy."""
    stage = max(i for i, (_, need) in enumerate(STAGES) if energy >= need)
    if stage >= len(STAGES) - 1:
        return stage, 1.0
    low, high = STAGES[stage][1], STAGES[stage + 1][1]
    return stage, (energy - low) / (high - low)


def needs(energy, bloom_gain=BLOOM_GAIN):
    """Points missing to Full, and the Lucky (average, best, worst), Super Lucky or Floraisons that makes."""
    missing = max(FULL - energy, 0)
    return {"missing": missing,
            "blooms": math.ceil(missing / bloom_gain),
            "lucky": math.ceil(missing / LUCKY_MEAN),
            "lucky_best": math.ceil(missing / max(LUCKY_GAINS)),
            "lucky_worst": math.ceil(missing / min(LUCKY_GAINS)),
            "super": math.ceil(missing / SUPER_GAIN)}


def plain(text):
    return "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))


def closest(text, choices, cutoff=0.6):
    """The choice that `text` (as read) most likely is, or None."""
    words = [plain(c) for c in choices]
    best = difflib.get_close_matches(plain(text).strip(" .!|:"), words, n=1, cutoff=cutoff)
    return choices[words.index(best[0])] if best else None


NOT_ZONES = ("augmenter", "zone inconnue")  # also on the pills: the "Augmenter" tag, locked areas


def words(text):
    return plain(text).replace("-", " ").replace("'", " ").replace("’", " ").split()


# words of 5 letters or more found in one area's name only: « cumulaine », « echoflux »...
_USES = collections.Counter(w for z in ZONES for w in set(words(z)))
OWN_WORDS = {w: z for z in ZONES for w in words(z) if len(w) >= 5 and _USES[w] == 1}


def zone_name(text):
    """The area named on the chosen pill, from what the text recognition read there (lines joined
    by ' | ', a long name takes two): the closest known area, or the one whose own word was read (a
    name cut short), else the text itself if it looks like a name (an area added by an update of
    the game), else None."""
    lines = [line.strip(" .!:-•") for line in text.split("|")]
    lines = [line for line in lines if line and plain(line) not in NOT_ZONES]
    for candidate in [" ".join(lines)] + lines:
        zone = closest(candidate, ZONES, 0.65)  # misread names score 0.86 and more, unknown ones ~0.5
        if zone:
            return zone
    scores = [(difflib.SequenceMatcher(None, word, own).ratio(), zone)
              for word in words(" ".join(lines)) for own, zone in OWN_WORDS.items()]
    if scores and max(scores)[0] >= 0.8:
        return max(scores)[1]
    name = " ".join("".join(c for c in " ".join(lines) if c.isalpha() or c in " '-’").split())
    return name[:40] if sum(c.isalpha() for c in name) >= 4 and "inconnue" not in plain(name) else None


OCR_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics, ContentType = WindowsRuntime]
$null = [Windows.Globalization.Language, Windows.Globalization, ContentType = WindowsRuntime]
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, $type) { $t = $asTask.MakeGenericMethod($type).Invoke($null, @($op)); $t.Wait(-1) | Out-Null; $t.Result }
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage([Windows.Globalization.Language]::new('fr-FR'))
if (-not $engine) { $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages() }
[Console]::Out.WriteLine('ready'); [Console]::Out.Flush()
while ($null -ne ($path = [Console]::In.ReadLine())) {
  $text = ''
  try {
    $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($path)) ([Windows.Storage.StorageFile])
    $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    $bitmap = Await ((Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])).GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
    $result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
    $stream.Dispose()
    $text = ($result.Lines | ForEach-Object { $_.Text }) -join ' | '
  } catch { }
  [Console]::Out.WriteLine($text); [Console]::Out.Flush()
}
"""


class Ocr:
    """Windows' text recognition, through one PowerShell started once and kept open."""

    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix="aniitracker-")
        script = os.path.join(self.dir, "ocr.ps1")
        with open(script, "w", encoding="utf-8-sig") as f:
            f.write(OCR_SCRIPT)
        self.proc = subprocess.Popen(
            [updater.POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", script],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            encoding="utf-8", creationflags=0x08000000)  # CREATE_NO_WINDOW
        if self.proc.stdout.readline().strip() != "ready":
            raise OSError("la reconnaissance de texte de Windows ne démarre pas")
        self.n = 0

    def read(self, img):
        """Text lines found in a BGR image, joined by ' | '."""
        self.n += 1
        path = os.path.join(self.dir, f"{self.n % 4}.png")
        # upscaling small text helps the recogniser
        cv2.imwrite(path, cv2.resize(img, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC))
        self.proc.stdin.write(path + "\n")
        self.proc.stdin.flush()
        return self.proc.stdout.readline().strip()

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.wait(2)
        except Exception:
            self.proc.kill()


class Reader:
    """Finds the Enrichir menu in frames of TITLE_AREA (+ LISTS) and reads state, bar and area."""

    def __init__(self, title):
        self.title = title

    def find(self, band):
        """Title's top-left corner in 1440p screen coordinates, or None.

        Only where the bar and the state's name, measured from the corner, are inside the band: a
        look-alike elsewhere (or a game window that is not 16:9) would leave them nothing to read."""
        score = cv2.matchTemplate(cv2.cvtColor(band, cv2.COLOR_BGR2GRAY), self.title, cv2.TM_CCOEFF_NORMED)
        _, best, _, (x, y) = cv2.minMaxLoc(score)
        if best < 0.7 or y + BAR_DY + 4 > band.shape[0] or x + STAGE_DX + STAGE_W > band.shape[1]:
            return None
        return TITLE_AREA[0] + x, TITLE_AREA[1] + y

    @staticmethod
    def fill(band, corner):
        """Share of the state's bar that is filled: the bright run that starts at the bar's start.

        The menu's light rays behind the bar can light a speck just before it, and a particle can
        cross the fill: a run starts only on 4 lit pixels, and a gap of 1 or 2 pixels does not end it."""
        x0, y0 = corner[0] - TITLE_AREA[0], corner[1] - TITLE_AREA[1]
        rows = cv2.cvtColor(band[y0 + BAR_DY:y0 + BAR_DY + 4], cv2.COLOR_BGR2HSV)[..., 2]
        lit = (rows > 175).sum(axis=0) >= 3
        start = x0 + BAR_DX
        first = next((x for x in range(start - 4, start + 6) if lit[x:x + 4].all()), None)
        if first is None:
            return 0.0
        last = gap = 0
        for x in range(first, min(start + BAR_LEN + 6, lit.size)):
            if lit[x]:
                last, gap = x, 0
            else:
                gap += 1
                if gap > 2:
                    break
        return min(max(last + 1 - start, 0) / BAR_LEN, 1.0)

    @staticmethod
    def stage_crop(band, corner):
        x0, y0 = corner[0] - TITLE_AREA[0], corner[1] - TITLE_AREA[1]
        return band[max(y0 - 8, 0):y0 + 54, x0 + STAGE_DX:x0 + STAGE_DX + STAGE_W]

    @staticmethod
    def zone_crop(lists):
        """The chosen area's name: the row of the biggest white pill, across its column.

        The pill is white only on its side toward the flower: on the left column, that is the end
        of the name, and what lies further right is the line « Une chance d'attirer… »."""
        hsv = cv2.cvtColor(lists, cv2.COLOR_BGR2HSV)
        white = ((hsv[..., 2] > 215) & (hsv[..., 1] < 45)).astype(np.uint8)
        white[:, 450:1450] = 0  # between the columns: the flower in the middle
        white = cv2.morphologyEx(white, cv2.MORPH_CLOSE, np.ones((9, 25), np.uint8))
        n, _, stats, _ = cv2.connectedComponentsWithStats(white)
        pills = [stats[i] for i in range(1, n) if stats[i][3] >= 50 and stats[i][2] >= 120]
        if not pills:
            return None
        x, y, w, h, _ = max(pills, key=lambda s: s[4])
        left, right = (0, 500) if x < 450 else (1450, lists.shape[1])  # « Une chance… » starts beyond 550
        return lists[max(y - 6, 0):y + h + 6, left:right]


def signature(img):
    """Small grey thumbnail to notice when a crop changed (and must be read again)."""
    return cv2.resize(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (48, 12), interpolation=cv2.INTER_AREA).astype(np.int16)


class Steady:
    """Readings of the open menu, kept until they hold still, then given again only when they
    really change. The bar moves when the menu opens or another area is chosen, and its glow can
    shift its end by a few pixels; the smallest real change is a Lucky, 275 points, 6 % of the
    widest state."""

    LOOKS = 5       # looks kept
    STEADY = 4      # of them within NOISE of their middle value: the bar holds still
    NOISE = 0.01    # share of the bar, about 3 pixels
    CHANGE = 0.015  # less than this from the last one given, same area and state: the same reading

    def __init__(self):
        self.recent, self.given = [], {}  # (zone, stage, fill) of the last looks; {zone: (stage, fill)}

    def reset(self, closed=False):
        """A look that could not be read; `closed`: the menu is gone, its next reading counts as new."""
        self.recent = []
        if closed:
            self.given = {}

    def add(self, zone, stage, fill):
        """The fill to give for this area and state, or None while it moves or did not change."""
        if self.recent and self.recent[-1][:2] != (zone, stage):
            self.recent = []
        self.recent = (self.recent + [(zone, stage, fill)])[-self.LOOKS:]
        fills = sorted(f for _, _, f in self.recent)
        middle = fills[len(fills) // 2]
        if sum(abs(f - middle) <= self.NOISE for f in fills) < self.STEADY:
            return None
        old = self.given.get(zone)
        if old and old[0] == stage and abs(old[1] - middle) < self.CHANGE:
            return None
        self.given[zone] = (stage, middle)
        return middle


class Watcher(threading.Thread):
    """Looks for the Enrichir menu twice a second while the game is in front, 4 times while it is open.

    Posts ("floraison", {"zone", "stage", "fill", "energy", "ts"}) to `out` once a reading holds
    still, then when it changes (Steady). `zones` holds the screen rectangles read while the menu
    is open (see cards.Watcher): the rest of the time nothing is read but the title's band.
    """

    def __init__(self, out, game_pid):
        super().__init__(daemon=True)
        self.out, self.game_pid = out, game_pid
        u = ctypes.windll.user32
        self.client = (0, 0, u.GetSystemMetrics(0), u.GetSystemMetrics(1))
        self.seen_menu = 0.0

    @property
    def zones(self):
        if time.time() - self.seen_menu > 3:
            return []
        rects = [cards.Watcher.rect(self.client, a) for a in [TITLE_AREA] + COLUMNS]
        return [(x, y, x + w, y + h) for x, y, w, h in rects]

    def run(self):
        title = cv2.imread(TITLE, cv2.IMREAD_GRAYSCALE)
        if title is None:
            self.out.put(("err", "floraison : images/floraison_titre.png manquant"))
            return
        reader, ocr = Reader(title), None
        screens, client, pid, pid_checked = None, None, None, 0.0
        seen = {"stage": (None, None), "zone": (None, None)}  # signature and what was read
        steady = Steady()
        failed = False  # an error is shown: taken back once the menu is read, or seen closed
        while True:
            try:
                time.sleep(0.25 if steady.recent else 0.5)
                if time.time() - pid_checked > 5:
                    pid, pid_checked = self.game_pid(), time.time()
                where = cards.game_client(pid) if pid else None
                if where is None:
                    client = None
                    continue
                if where != client:
                    client = self.client = where
                    for sc in screens or ():
                        sc.close()
                    screens = [cards.Screen(*cards.Watcher.rect(client, a)) for a in (TITLE_AREA, LISTS)]
                band = cards.Watcher.look(screens[0], TITLE_AREA)
                corner = reader.find(band)
                if corner is None:
                    steady.reset(closed=True)
                    if failed:
                        self.out.put(("ok", "floraison"))
                        failed = False
                    continue
                self.seen_menu = time.time()
                ocr = ocr or Ocr()
                fill = reader.fill(band, corner)
                stage = self.read(ocr, seen, "stage", reader.stage_crop(band, corner),
                                  lambda t: closest(t.split("|")[0], [s for s, _ in STAGES]))
                crop = reader.zone_crop(cards.Watcher.look(screens[1], LISTS))
                zone = self.read(ocr, seen, "zone", crop, zone_name) if crop is not None else None
                if stage is None or zone is None:  # not read this time: look again
                    steady.reset()
                    continue
                index = [s for s, _ in STAGES].index(stage)
                fill = steady.add(zone, index, fill)
                if failed:
                    self.out.put(("ok", "floraison"))
                    failed = False
                if fill is not None:
                    self.out.put(("floraison", {"zone": zone, "stage": index, "fill": round(fill, 3),
                                                "energy": energy_of(index, fill), "ts": round(time.time(), 1)}))
            except Exception as e:
                self.out.put(("err", f"floraison : {e}", traceback.format_exc()))
                failed = True
                if ocr:
                    ocr.close()
                ocr = None
                time.sleep(5)

    @staticmethod
    def read(ocr, seen, what, crop, interpret):
        """Read a crop again only when it changed on screen."""
        sig = signature(crop)
        old, value = seen[what]
        if old is not None and np.abs(sig - old).mean() < 6:
            return value
        value = interpret(ocr.read(crop))
        seen[what] = (sig, value)
        return value
