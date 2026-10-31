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
import ctypes, difflib, math, os, subprocess, tempfile, threading, time, unicodedata
import cv2
import numpy as np

import cards

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
        self.dir = tempfile.mkdtemp(prefix="compteur-pity-")
        script = os.path.join(self.dir, "ocr.ps1")
        with open(script, "w", encoding="utf-8-sig") as f:
            f.write(OCR_SCRIPT)
        self.proc = subprocess.Popen(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", script],
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
        """Title's top-left corner in 1440p screen coordinates, or None."""
        score = cv2.matchTemplate(cv2.cvtColor(band, cv2.COLOR_BGR2GRAY), self.title, cv2.TM_CCOEFF_NORMED)
        _, best, _, (x, y) = cv2.minMaxLoc(score)
        return (TITLE_AREA[0] + x, TITLE_AREA[1] + y) if best >= 0.7 else None

    @staticmethod
    def fill(band, corner):
        """Share of the state's bar that is filled: the bright run from the bar's start."""
        x0, y0 = corner[0] - TITLE_AREA[0], corner[1] - TITLE_AREA[1]
        rows = cv2.cvtColor(band[y0 + BAR_DY:y0 + BAR_DY + 4], cv2.COLOR_BGR2HSV)[..., 2]
        lit = (rows > 175).sum(axis=0) >= 3
        start = x0 + BAR_DX
        run = 0
        for x in range(start - 4, min(start + BAR_LEN + 6, lit.size)):
            if lit[x]:
                run += 1
            elif run:
                break
        return min(run / BAR_LEN, 1.0)

    @staticmethod
    def stage_crop(band, corner):
        x0, y0 = corner[0] - TITLE_AREA[0], corner[1] - TITLE_AREA[1]
        return band[max(y0 - 8, 0):y0 + 54, x0 + STAGE_DX:x0 + STAGE_DX + STAGE_W]

    @staticmethod
    def zone_crop(lists):
        """The chosen area's name: the biggest white pill in the two columns."""
        hsv = cv2.cvtColor(lists, cv2.COLOR_BGR2HSV)
        white = ((hsv[..., 2] > 215) & (hsv[..., 1] < 45)).astype(np.uint8)
        white[:, 450:1450] = 0  # between the columns: the flower in the middle
        white = cv2.morphologyEx(white, cv2.MORPH_CLOSE, np.ones((9, 25), np.uint8))
        n, _, stats, _ = cv2.connectedComponentsWithStats(white)
        pills = [stats[i] for i in range(1, n) if stats[i][3] >= 50 and stats[i][2] >= 120]
        if not pills:
            return None
        x, y, w, h, _ = max(pills, key=lambda s: s[4])
        return lists[y:y + h, max(x - 10, 0):min(x + 520, lists.shape[1])]


def signature(img):
    """Small grey thumbnail to notice when a crop changed (and must be read again)."""
    return cv2.resize(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (48, 12), interpolation=cv2.INTER_AREA).astype(np.int16)


class Watcher(threading.Thread):
    """Looks for the Enrichir menu twice a second while the game is in front.

    Posts ("floraison", {"zone", "stage", "fill", "energy", "ts"}) to `out` when what it shows
    changes. `zones` holds the screen rectangles read while the menu is open (see cards.Watcher):
    the rest of the time nothing is read but the title's band.
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
        last = None
        while True:
            try:
                time.sleep(0.5)
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
                    continue
                self.seen_menu = time.time()
                ocr = ocr or Ocr()
                fill = reader.fill(band, corner)
                stage = self.read(ocr, seen, "stage", reader.stage_crop(band, corner),
                                  lambda t: closest(t.split("|")[0], [s for s, _ in STAGES]))
                crop = reader.zone_crop(cards.Watcher.look(screens[1], LISTS))
                zone = self.read(ocr, seen, "zone", crop, lambda t: closest(t, ZONES, 0.5)) if crop is not None else None
                if stage is None:
                    continue
                index = [s for s, _ in STAGES].index(stage)
                reading = {"zone": zone, "stage": index, "fill": round(fill, 3),
                           "energy": energy_of(index, fill), "ts": round(time.time(), 1)}
                key = (zone, index, reading["energy"])
                if key != last:
                    last = key
                    self.out.put(("floraison", reading))
            except Exception as e:
                self.out.put(("err", f"floraison : {e}"))
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
