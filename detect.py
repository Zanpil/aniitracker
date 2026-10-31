"""Recognise Aniimo's catch sounds in audio: log-band spectrogram + normalised correlation.

References are the game's own sounds (sons/*.wav, from tools/extract_sounds.py), or when they
are not there their fingerprints (sons/empreintes.npz): the very templates compared below, which
can't be played back, so the game's sounds themselves need not be shipped.
Live: Listener hears the game's sound only (game_audio: not Discord, a stream or music) and posts
what it recognises.
Offline, for calibration: python detect.py <recording.wav> [threshold]
prints the detections and, if the recording has its .json, the keys pressed meanwhile.
python detect.py --empreintes rewrites sons/empreintes.npz from sons/*.wav.
"""
import glob, json, os, sys, threading, time, wave
import numpy as np
import game_audio

HERE = os.path.dirname(os.path.abspath(__file__))
# inside the exe, the reference sounds are unpacked to sys._MEIPASS
SONS = os.path.join(getattr(sys, "_MEIPASS", HERE), "sons")
FINGERPRINTS = os.path.join(SONS, "empreintes.npz")
SHIPPED = ("lucky", "super", "ball_success")  # the references the counter uses
EVENTS = os.path.join(HERE, "data", "events.jsonl")
FPS = 100                        # feature frames per second
EDGES = np.geomspace(150, 7500, 65)
MAX_TEMPLATE_S = 1.5             # long sounds: keep the start, less overlap with other sounds
# in game the tail of the pod success sound is covered by other sounds: on the 30/10 video, 0.5 s
# separated captures (>= 0.73) from the rest (<= 0.44) better than 0.74 s (0.58 / 0.42) or 1 s
TEMPLATE_S = {"ball_success": 0.5}
CHUNK = 3000                     # frames per FFT batch, bounds memory on long recordings
MIN_DB = -60                     # quieter windows are ignored: correlation of near-silence is noise
# real catch / Lucky sounds measured at -37..-32 dBFS; Game Bar silence (-240 dB) matched at 0.63


def read_wav(path):
    """Mono float32 samples and sample rate."""
    w = wave.open(path)
    ch, width, rate = w.getnchannels(), w.getsampwidth(), w.getframerate()
    x = np.frombuffer(w.readframes(w.getnframes()), {2: np.int16, 4: np.int32}[width]).astype(np.float32)
    x /= 2 ** (8 * width - 1)
    return x.reshape(-1, ch).mean(axis=1), rate


def features(x, rate):
    """(frames, 64) log energy in log-spaced bands, one frame every 10 ms, for any sample rate.

    Each frame has its mean removed: only the spectral shape is kept, not the loudness, which
    every catch sound shares and which made all references match any loud moment.
    """
    hop = rate // FPS
    nfft = int(rate * 0.064)  # same 64 ms window whatever the rate, so bands match across files
    x = np.pad(x, (0, nfft))
    win = np.hanning(nfft).astype(np.float32)
    idx = np.searchsorted(np.fft.rfftfreq(nfft, 1 / rate), EDGES)
    n = (len(x) - nfft) // hop + 1
    out = np.empty((n, len(EDGES) - 1), np.float32)
    frames = np.lib.stride_tricks.sliding_window_view(x, nfft)[::hop]
    for i in range(0, n, CHUNK):
        mag = np.abs(np.fft.rfft(frames[i:i + CHUNK] * win, axis=1))
        c = np.concatenate([np.zeros((len(mag), 1), np.float32), np.cumsum(mag, axis=1)], axis=1)
        out[i:i + CHUNK] = np.log1p(10 * (c[:, idx[1:]] - c[:, idx[:-1]]))
    return out - out.mean(axis=1, keepdims=True)


def load_refs(folder=SONS):
    """{name: template (frames, 64)} from every sons/*.wav, silence trimmed; without wav files,
    the fingerprints of sons/empreintes.npz (the same templates)."""
    refs = {}
    for path in sorted(glob.glob(os.path.join(folder, "*.wav"))):
        x, rate = read_wav(path)
        f = features(x, rate)
        # silence trimming on the raw level, since features no longer carry loudness
        level = np.array([np.abs(x[i * rate // FPS:(i + 1) * rate // FPS]).max(initial=0) for i in range(len(f))])
        loud = np.nonzero(level > level.max() * 0.05)[0]
        name = os.path.splitext(os.path.basename(path))[0]
        refs[name] = f[loud[0]: loud[-1] + 1][: int(TEMPLATE_S.get(name, MAX_TEMPLATE_S) * FPS)]
    if not refs and folder == SONS and os.path.exists(FINGERPRINTS):
        with np.load(FINGERPRINTS) as f:
            refs = {name: f[name] for name in f.files}
    return refs


def save_fingerprints():
    """Write the templates of the sounds the counter uses to sons/empreintes.npz."""
    refs = load_refs()
    np.savez_compressed(FINGERPRINTS, **{name: refs[name] for name in SHIPPED})
    return {name: refs[name].shape for name in SHIPPED}


def correlate(S, tpl):
    """Normalised correlation of tpl against every position of S (valid positions only).

    Each band is centred over the window (template and audio alike), so steady background such as
    music or ambience drops out and only how the spectrum moves over time is compared.
    """
    L = len(tpl)
    N = len(S) - L + 1
    if N <= 0:
        return np.zeros(0)
    t = tpl - tpl.mean(axis=0)
    t /= np.sqrt((t ** 2).sum()) + 1e-9
    size = 1 << int(np.ceil(np.log2(len(S) + L)))
    num = np.fft.irfft(np.fft.rfft(S, size, axis=0) * np.conj(np.fft.rfft(t, size, axis=0)), size, axis=0)
    num = num[:N].sum(axis=1)  # band means of the window drop out: t sums to 0 in each band
    # energy of the band-centred window, from running sums per band
    S64 = S.astype(np.float64)
    c1 = np.concatenate([np.zeros((1, S.shape[1])), np.cumsum(S64, axis=0)])
    c2 = np.concatenate([np.zeros((1, S.shape[1])), np.cumsum(S64 ** 2, axis=0)])
    s1, s2 = c1[L:] - c1[:-L], c2[L:] - c2[:-L]
    # the floor keeps FFT round-off over a silent window from turning into a big ratio
    energy = np.maximum((s2 - s1 ** 2 / L).sum(axis=1), 1e-6 * L * S.shape[1])
    return num / np.sqrt(energy)


def levels(x, rate):
    """Mean power per 10 ms frame (same frames as features), for the loudness gate."""
    hop = rate // FPS
    n = len(x) // hop
    return (x[:n * hop].reshape(n, hop) ** 2).mean(axis=1)


def loud(power, i, L):
    """Is the window of L frames starting at i louder than MIN_DB?"""
    return 10 * np.log10(power[i:i + L].mean() + 1e-24) >= MIN_DB


def peaks(c, threshold, min_gap):
    """Indices of local maxima above threshold, at least min_gap apart (best first)."""
    out = []
    for i in np.argsort(c)[::-1]:
        if c[i] < threshold:
            break
        if all(abs(i - j) >= min_gap for j in out):
            out.append(i)
    return sorted(out)


def scan(S, power, refs, threshold):
    """[(frame, name, score)] for every reference over a feature matrix."""
    hits = []
    for name, tpl in refs.items():
        c = correlate(S, tpl)
        hits += [(int(i), name, float(c[i])) for i in peaks(c, threshold, len(tpl)) if loud(power, i, len(tpl))]
    return sorted(hits)


class Listener(threading.Thread):
    """Hears the game and posts ("heard", name, unix time of the sound) to `out`."""

    STEP = 0.25  # seconds between two analyses

    def __init__(self, out, names, threshold):
        super().__init__(daemon=True)
        self.out, self.threshold = out, threshold
        self.refs = {k: v for k, v in load_refs().items() if k in names}

    def run(self):
        if not self.refs:
            self.out.put(("err", "son : lance « Préparer les sons.bat »"))
            return
        warned = None
        while True:
            pid = game_audio.game_pid()
            if pid is None:  # game not started yet, or closed: nothing to hear
                time.sleep(5)
                continue
            try:
                self.listen(pid)
            except Exception as e:
                if str(e) != warned:
                    self.out.put(("err", f"son : écoute du jeu impossible ({e})"))
                    warned = str(e)
                time.sleep(5)

    def keep(self, rate):
        """Samples of audio kept for each analysis: the longest reference plus a second."""
        return int((max(len(t) for t in self.refs.values()) / FPS + 1.0) * rate)

    def analyse(self, buf, rate, end, last):
        """Post the references found in buf (ending at unix time `end`) and not posted yet."""
        S = features(buf, rate)[:-7]  # last frames overlap the zero padding
        power = levels(buf, rate)
        start = end - len(buf) / rate
        for name, tpl in self.refs.items():
            for i in peaks(correlate(S, tpl), self.threshold, len(tpl)):
                if not loud(power, i, len(tpl)):
                    continue
                t = start + i / FPS
                # the next analyses see the same sound again at (almost) the same time; two catches
                # can be 0.86 s apart (30/10 video), so the gap is the template length, not more
                if t > last.get(name, -np.inf) + len(tpl) / FPS:
                    last[name] = t
                    self.out.put(("heard", name, t))

    def listen(self, pid):
        """Analyse the game's sound until the game closes."""
        rate, last = game_audio.RATE, {}
        keep = self.keep(rate)
        buf, end, next_check, next_alive = np.zeros(0, np.float32), time.time(), 0.0, time.time() + 5
        with game_audio.GameAudio(pid) as game:
            while True:
                x = game.read()
                if len(x):
                    buf = np.concatenate([buf, x])[-keep:]
                    end = time.time()
                now = time.time()
                if now >= next_alive:
                    if game_audio.game_pid() != pid:
                        return
                    next_alive = now + 5
                if now >= next_check and len(buf) == keep:
                    next_check = now + self.STEP
                    self.analyse(buf, rate, end, last)


def main():
    if sys.argv[1:] == ["--empreintes"]:
        print(save_fingerprints())
        return
    path = sys.argv[1]
    threshold = float(sys.argv[2]) if len(sys.argv) > 2 else 0.4
    refs = load_refs()
    if not refs:
        sys.exit("Aucun son de référence dans sons/ : lance d'abord « Préparer les sons.bat ».")
    x, rate = read_wav(path)
    S = features(x, rate)
    timeline = [(i / FPS, f"{name:17} {score:.2f}") for i, name, score in scan(S, levels(x, rate), refs, threshold)]
    meta = os.path.splitext(path)[0] + ".json"
    start = json.load(open(meta))["start"] if os.path.exists(meta) else None
    if start and os.path.exists(EVENTS):
        end = start + len(S) / FPS
        for line in open(EVENTS, encoding="utf-8"):
            e = json.loads(line)
            if start <= e["ts"] <= end:
                timeline.append((e["ts"] - start, f"    [touche] {e['k']}"))
    for t, text in sorted(timeline):
        clock = time.strftime(" %H:%M:%S", time.localtime(start + t)) if start else ""
        print(f"{int(t // 60):02d}:{t % 60:05.2f}{clock}  {text}")


if __name__ == "__main__":
    main()
