"""Look of the counter, after the mockup: rounded night-blue panels, a medallion on the left
(gold star for Lucky, pink star for Super Lucky, crystal for the Bloom), a big gradient bar and a
small card on the right.

Panels are drawn with Pillow at twice their size and then reduced, for smooth edges and text.
They sit on KEY, the colour the windows make transparent, so only the rounded panels show.
What never changes (background, medallion, title, card frames) is drawn once per panel kind.
"""
import functools, math, os
import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

KEY = (1, 2, 3)                 # made transparent by the windows (-transparentcolor)
KEY_HEX = "#010203"
NIGHT = (11, 16, 32)            # palette of the mockup: #0B1020 #1A2338 #2E3B61 ...
LINE = (46, 59, 97)
CYAN, GOLD, PINK, VIOLET, WHITE = (65, 216, 255), (255, 209, 102), (255, 122, 217), (155, 92, 255), (237, 235, 255)
DIM = (146, 156, 196)
GREEN, RED = (126, 224, 138), (255, 107, 107)
ACCENT = {"lucky": GOLD, "super": PINK, "floraison": CYAN}
BAR = {"lucky": [(232, 158, 52), GOLD, (255, 236, 176)],
       "super": [VIOLET, (214, 104, 236), PINK],
       "floraison": [CYAN, (110, 150, 255), VIOLET]}
TITLE = {"lucky": "Lucky", "super": "Super Lucky", "floraison": "Floraison"}
W, H = 500, 78                  # panel size before the screen scale
SS = 2                          # drawn this many times larger, then reduced
FONTS = os.path.join(os.environ.get("WINDIR", "C:/Windows"), "Fonts")
FACES = {"regular": "segoeui.ttf", "semibold": "seguisb.ttf", "bold": "segoeuib.ttf", "black": "seguibl.ttf"}


@functools.lru_cache(None)
def font(weight, size):
    try:
        return ImageFont.truetype(os.path.join(FONTS, FACES[weight]), size)
    except OSError:
        return ImageFont.load_default(size)


def gradient(width, height, colors, vertical=False):
    """RGBA image going through `colors` evenly, left to right (or top to bottom)."""
    n = height if vertical else width
    t = np.linspace(0, 1, max(n, 2))[:n]
    stops = np.linspace(0, 1, len(colors))
    ramp = np.stack([np.interp(t, stops, [c[i] for c in colors]) for i in range(3)], axis=1)
    arr = np.repeat(ramp[:, None, :], width, axis=1) if vertical else np.repeat(ramp[None, :, :], height, axis=0)
    rgba = np.concatenate([arr, np.full(arr.shape[:2] + (1,), 255.0)], axis=2).astype(np.uint8)
    return Image.fromarray(rgba, "RGBA")


def star(cx, cy, size, waist=0.24):
    """Points of a four-pointed star."""
    w = size * waist
    return [(cx, cy - size), (cx + w, cy - w), (cx + size, cy), (cx + w, cy + w),
            (cx, cy + size), (cx - w, cy + w), (cx - size, cy), (cx - w, cy - w)]


def hex_rgb(color):
    return tuple(int(color[i:i + 2], 16) for i in (1, 3, 5)) if isinstance(color, str) else color


class Painter:
    """A panel being drawn, in panel units (multiplied by scale and SS when drawing)."""

    def __init__(self, w, h, scale, base=None):
        self.w, self.h, self.k = w, h, scale * SS
        self.img = base.copy() if base is not None else Image.new("RGBA", (self.p(w), self.p(h)), KEY + (0,))
        self.d = ImageDraw.Draw(self.img)

    def p(self, v):
        return int(round(v * self.k))

    def box(self, x0, y0, x1, y1):
        return [self.p(x0), self.p(y0), self.p(x1), self.p(y1)]

    def blit(self, small, x, y, blur=0, alpha=1.0):
        """Composite a small RGBA picture at (x, y) in pixels, optionally blurred (a glow)."""
        if blur:
            pad = self.p(blur) * 3
            big = Image.new("RGBA", (small.width + 2 * pad, small.height + 2 * pad), (0, 0, 0, 0))
            big.paste(small, (pad, pad))
            small, x, y = big.filter(ImageFilter.GaussianBlur(self.p(blur))), x - pad, y - pad
        if alpha < 1:
            small = small.copy()
            small.putalpha(small.getchannel("A").point(lambda v: int(v * alpha)))
        # crop what falls outside the panel (alpha_composite wants a destination inside it)
        cx0, cy0 = max(-x, 0), max(-y, 0)
        cx1, cy1 = min(small.width, self.img.width - x), min(small.height, self.img.height - y)
        if cx1 > cx0 and cy1 > cy0:
            self.img.alpha_composite(small.crop((cx0, cy0, cx1, cy1)), (x + cx0, y + cy0))

    def shape(self, draw, colors, box, vertical=False, glow=0, glow_alpha=0.6):
        """Fill a shape (callback drawing on a mask) with a gradient over `box` (panel units)."""
        x0, y0, x1, y1 = self.box(*box)
        x0, y0, x1, y1 = max(x0, 0), max(y0, 0), min(x1, self.img.width), min(y1, self.img.height)
        mask = Image.new("L", self.img.size, 0)
        draw(ImageDraw.Draw(mask))
        layer = gradient(x1 - x0, y1 - y0, colors, vertical)
        # straight colours, transparency from the shape only: no dark fringe on smoothed edges
        layer.putalpha(mask.crop((x0, y0, x1, y1)))
        if glow:
            self.blit(layer, x0, y0, blur=glow, alpha=glow_alpha)
        self.img.alpha_composite(layer, (x0, y0))

    def solid(self, draw, color, glow=0, glow_alpha=0.7):
        """Draw with one colour (callback on a transparent layer), with an optional glow."""
        layer = Image.new("RGBA", self.img.size, (0, 0, 0, 0))
        draw(ImageDraw.Draw(layer), color + (255,))
        bbox = layer.getbbox()
        if not bbox:
            return
        small = layer.crop(bbox)
        if glow:
            self.blit(small, bbox[0], bbox[1], blur=glow, alpha=glow_alpha)
        self.img.alpha_composite(small, bbox[:2])

    def glow(self, draw, color, blur, alpha=0.6):
        """Only the blurred light of a shape (callback on a transparent layer)."""
        layer = Image.new("RGBA", self.img.size, (0, 0, 0, 0))
        draw(ImageDraw.Draw(layer), color + (255,))
        bbox = layer.getbbox()
        if bbox:
            self.blit(layer.crop(bbox), bbox[0], bbox[1], blur=blur, alpha=alpha)

    def text(self, x, y, s, size, color, weight="bold", anchor="ls", spacing=0.0):
        """Write s; `spacing` adds room between letters (small capitals). Returns its width."""
        f = font(weight, self.p(size))
        if not spacing:
            self.d.text((self.p(x), self.p(y)), s, font=f, fill=color, anchor=anchor)
            return self.d.textlength(s, font=f) / self.k
        width = self.measure(s, size, weight, spacing)
        cx = {"l": x, "m": x - width / 2, "r": x - width}[anchor[0]]
        for ch in s:
            self.d.text((self.p(cx), self.p(y)), ch, font=f, fill=color, anchor="l" + anchor[1])
            cx += self.d.textlength(ch, font=f) / self.k + spacing
        return width

    def measure(self, s, size, weight="bold", spacing=0.0):
        f = font(weight, self.p(size))
        if not spacing:
            return self.d.textlength(s, font=f) / self.k
        return sum(self.d.textlength(ch, font=f) / self.k + spacing for ch in s) - spacing

    def fit(self, s, size, weight, width, spacing=0.0, smallest=6.5):
        """Largest size <= size at which s fits in width."""
        while size > smallest and self.measure(s, size, weight, spacing) > width:
            size -= 0.5
        return size

    def done(self, scale, alpha=False):
        """The panel at screen size, on KEY (RGB, for a tk image), or with real transparency."""
        small = self.img.resize((int(round(self.w * scale)), int(round(self.h * scale))), Image.LANCZOS)
        if alpha:
            return small
        out = Image.new("RGB", small.size, KEY)
        out.paste(small, (0, 0), small)
        return out


def panel_base(pt, accent):
    """Night-blue rounded panel with a thin border and a soft light in the accent colour."""
    r = 16
    pt.shape(lambda d: d.rounded_rectangle(pt.box(0, 0, pt.w - 1, pt.h - 1), radius=pt.p(r), fill=255),
             [(24, 33, 58), (13, 18, 36)], (0, 0, pt.w, pt.h), vertical=True)
    sheen = Image.new("RGBA", pt.img.size, (0, 0, 0, 0))
    ImageDraw.Draw(sheen).ellipse(pt.box(-40, -60, 140, 60), fill=accent + (40,))
    sheen = sheen.filter(ImageFilter.GaussianBlur(pt.p(18)))
    mask = Image.new("L", pt.img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(pt.box(1, 1, pt.w - 2, pt.h - 2), radius=pt.p(r), fill=255)
    sheen.putalpha(ImageChops.multiply(sheen.getchannel("A"), mask))
    pt.img.alpha_composite(sheen)
    pt.d.rounded_rectangle(pt.box(0.5, 0.5, pt.w - 1.5, pt.h - 1.5), radius=pt.p(r), outline=LINE,
                           width=max(pt.p(1.4), 1))


def medallion(pt, kind, cx=40, cy=39, r=29):
    accent = ACCENT[kind]
    pt.shape(lambda d: d.ellipse(pt.box(cx - r, cy - r, cx + r, cy + r), fill=255),
             [(30, 40, 72), NIGHT], (cx - r, cy - r, cx + r, cy + r), vertical=True)
    pt.solid(lambda d, c: d.ellipse(pt.box(cx - r + 1.5, cy - r + 1.5, cx + r - 1.5, cy + r - 1.5), outline=c,
                                    width=pt.p(2.2)), accent, glow=3)
    if kind == "floraison":
        crystals(pt, cx, cy + 1)
        return
    colors = [(255, 250, 220), GOLD, (232, 150, 40)] if kind == "lucky" else [(255, 230, 250), PINK, VIOLET]
    pt.shape(lambda d: d.polygon([(pt.p(x), pt.p(y)) for x, y in star(cx, cy, 19)], fill=255),
             colors, (cx - 19, cy - 19, cx + 19, cy + 19), vertical=True, glow=4, glow_alpha=0.8)
    pt.solid(lambda d, c: d.polygon([(pt.p(x), pt.p(y)) for x, y in star(cx, cy, 8, 0.3)], fill=c), (255, 255, 255))
    for sx, sy, s in ((cx + 15, cy - 15, 3.4), (cx - 16, cy + 13, 2.4), (cx + 17, cy + 10, 1.8)):
        sparkle(pt, sx, sy, s, accent)


def sparkle(pt, x, y, size, color):
    pt.solid(lambda d, c: d.polygon([(pt.p(a), pt.p(b)) for a, b in star(x, y, size, 0.22)], fill=c), color,
             glow=1.2, glow_alpha=0.8)


def crystals(pt, cx, cy):
    """A cluster of three crystals, light faces left, dark faces right."""
    def one(x, y, h, w, tilt):
        top, left, right = (x + tilt, y - h), (x - w, y - h * 0.35), (x + w, y - h * 0.35)
        bl, br, bottom = (x - w * 0.8, y + h * 0.45), (x + w * 0.8, y + h * 0.45), (x, y + h * 0.62)
        for pts, colors in (([top, left, bl, bottom, (x, y)], [(210, 250, 255), CYAN]),
                            ([top, (x, y), bottom, br, right], [(120, 200, 255), (70, 110, 230)])):
            pt.shape(lambda d, pts=pts: d.polygon([(pt.p(a), pt.p(b)) for a, b in pts], fill=255),
                     colors, (x - w, y - h, x + w, y + h), vertical=True)
        pt.d.line([(pt.p(a), pt.p(b)) for a, b in (top, (x, y), bottom)], fill=(235, 252, 255), width=max(pt.p(0.8), 1))
    pt.glow(lambda d, c: d.ellipse(pt.box(cx - 13, cy - 15, cx + 13, cy + 13), fill=c), CYAN, 6, 0.45)
    one(cx - 11, cy + 5, 11, 5, -2)
    one(cx + 11, cy + 5, 12, 5, 2)
    one(cx, cy + 1, 19, 7.5, 0)
    sparkle(pt, cx + 16, cy - 15, 3, WHITE)


def ticket(pt, cx, cy, color):
    """The small tilted card of the right box."""
    size = pt.p(34)
    lay = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    d.rounded_rectangle([pt.p(6), pt.p(9.5), pt.p(28), pt.p(24.5)], radius=pt.p(3), fill=color + (255,))
    d.ellipse([pt.p(13.5), pt.p(13.5), pt.p(20.5), pt.p(20.5)], fill=(255, 255, 255, 130))
    d.ellipse([pt.p(15.5), pt.p(15.5), pt.p(18.5), pt.p(18.5)], fill=color + (255,))
    lay = lay.rotate(28, resample=Image.BICUBIC)
    pt.blit(lay, pt.p(cx) - size // 2, pt.p(cy) - size // 2, blur=2, alpha=0.6)
    pt.blit(lay, pt.p(cx) - size // 2, pt.p(cy) - size // 2)


def card(pt, x0, x1):
    pt.d.rounded_rectangle(pt.box(x0, 11, x1, 67), radius=pt.p(11), fill=(13, 19, 37, 255), outline=LINE,
                           width=max(pt.p(1), 1))


@functools.lru_cache(None)
def base(kind, scale):
    """Everything that never changes on a panel of this kind."""
    pt = Painter(W, H, scale)
    panel_base(pt, ACCENT[kind])
    medallion(pt, kind)
    pt.text(82, 31, TITLE[kind], 21, ACCENT[kind], "bold")
    if kind == "floraison":
        card(pt, 318, 488)
    else:
        card(pt, 380, 488)
        ticket(pt, 401, 39, ACCENT[kind])
    return pt.img


def bar(pt, kind, x0, x1, y0, y1, share, ticks=()):
    """Rounded track, gradient fill up to `share` (0..1), bright marker at its end."""
    r = (y1 - y0) / 2
    pt.d.rounded_rectangle(pt.box(x0, y0, x1, y1), radius=pt.p(r), fill=(9, 13, 27, 255),
                           outline=LINE, width=max(pt.p(1), 1))
    for t in ticks:
        x = x0 + (x1 - x0) * t
        pt.d.line([(pt.p(x), pt.p(y0 + 2)), (pt.p(x), pt.p(y1 - 2))], fill=(70, 84, 130), width=max(pt.p(1), 1))
    share = min(max(share, 0.0), 1.0)
    if share <= 0:
        return
    end = x0 + max((x1 - x0) * share, 2 * r)
    pt.shape(lambda d: d.rounded_rectangle(pt.box(x0 + 1, y0 + 1, end - 1, y1 - 1), radius=pt.p(r - 1), fill=255),
             BAR[kind], (x0, y0, x1, y1), glow=3, glow_alpha=0.55)
    if share < 1:
        pt.solid(lambda d, c: d.rounded_rectangle(pt.box(end - 1.6, y0 - 3, end + 0.4, y1 + 3), radius=pt.p(1), fill=c),
                 (255, 255, 255), glow=1.5, glow_alpha=0.9)


def numbers(pt, right, base_y, value, total, accent, note="", left=0.0):
    """'114 / 200 (57 %)' ending at `right`, the value shrunk if it would start before `left`."""
    size = 22
    while True:
        width = (pt.measure(note, 11.5, "regular") + 7 if note else 0) + pt.measure(f"/ {total}", 15, "semibold") + 5
        width += pt.measure(value, size, "bold")
        if right - width >= left or size <= 14:
            break
        size -= 1
    x = right
    if note:
        x -= pt.text(right, base_y, note, 11.5, DIM, "regular", "rs") + 7
    x -= pt.text(x, base_y, f"/ {total}", 15, WHITE, "semibold", "rs") + 5
    pt.text(x, base_y, value, size, accent, "bold", "rs")


def share_text(share):
    v = share * 100
    if 0 < v < 10:
        return f"({v:.1f} %)".replace(".", ",")
    return f"({min(math.floor(v + 1e-9), 99 if share < 1 else 100)} %)"  # 114 / 200 is 57 %, not 56.99


def group(n):
    return f"{n:,}".replace(",", "\u202f")


def pity_panel(kind, pity, cap, done, left, catches, scale=1.0, number_color=None, alpha=False):
    """Lucky / Super Lucky panel: pity / guarantee, bar, strikes this week."""
    accent = ACCENT[kind]
    pt = Painter(W, H, scale, base(kind, scale))
    if pity >= cap:
        sub = "au-delà de la garantie : compteur décalé ?"
    elif left == 1:
        sub = "la prochaine capture est garantie !"
    else:
        sub = f"garanti dans {left} · {catches} captures cette semaine"
    pt.text(83, 46, sub.upper(), pt.fit(sub.upper(), 8, "semibold", 280, 1.0), DIM, "semibold", spacing=1.0)
    share = min(pity / cap, 1) if cap else 0
    numbers(pt, 366, 31, str(pity), cap, hex_rgb(number_color) if number_color else accent, share_text(share),
            left=82 + pt.measure(TITLE[kind], 21, "bold") + 12)
    bar(pt, kind, 82, 366, 55, 65, share)
    pt.text(422, 37, str(done), 21, WHITE, "bold")
    pt.text(422, 53, "cette semaine", 9.5, DIM, "regular")
    return pt.done(scale, alpha)


def floraison_panel(zone, stage, energy, full, missing, lucky, super_, approx, thresholds, scale=1.0, hint=None,
                    alpha=False, blooms=None):
    """Bloom panel: energy of the area / full, bar with a tick per state, what is missing."""
    accent = ACCENT["floraison"]
    pt = Painter(W, H, scale, base("floraison", scale))
    sub = hint or " · ".join(s for s in (zone, stage, approx) if s)
    pt.text(83, 46, sub.upper(), pt.fit(sub.upper(), 8, "semibold", 220, 1.0), DIM, "semibold", spacing=1.0)
    left = 82 + pt.measure(TITLE["floraison"], 21, "bold") + 10
    if energy is None:
        numbers(pt, 306, 31, "—", group(full), DIM, left=left)
    else:
        numbers(pt, 306, 31, ("≈ " if approx else "") + group(energy), group(full), accent, left=left)
    bar(pt, "floraison", 82, 306, 55, 65, (energy or 0) / full, [t / full for t in thresholds])
    if energy is not None and missing == 0:
        pt.text(403, 38, "PLEIN", 19, GREEN, "black", "ms")
        pt.text(403, 55, "Prismana garanti", 10, WHITE, "semibold", "ms")
        return pt.done(scale, alpha)
    pt.text(330, 27, "RESTANT", 8, DIM, "semibold", spacing=1.0)
    pt.text(330, 52, "—" if missing is None else group(missing), 19, WHITE, "bold")
    if missing is not None:
        lines = [(lucky, "Lucky", GOLD), (super_, "Super Lucky", PINK)]
        if blooms is not None:
            lines.append((blooms, "Floraison" + ("s" if blooms > 1 else ""), CYAN))
        top, step = (27, 17) if len(lines) == 3 else (31, 23)
        for i, (n, label, color) in enumerate(lines):
            y = top + i * step
            sparkle(pt, 404, y - 4.2, 3.8, color)
            x = 412 + pt.text(412, y, str(n), 12.5, color, "bold") + 3
            pt.text(x, y, label, pt.fit(label, 10.5, "semibold", 486 - x), color, "semibold")
    return pt.done(scale, alpha)


@functools.lru_cache(None)
def bubble(path, scale, size=46):
    """The floating bubble: the round logo with a thin ring, on KEY (RGB, for a tk image)."""
    n = int(round(size * scale))
    big = n * 4
    try:
        logo = Image.open(path).convert("RGBA")  # an .ico: Pillow opens its largest picture
    except OSError:
        logo = Image.new("RGBA", (256, 256), NIGHT + (255,))
    # the logo is a rounded square: zoom in a little so the round cut keeps only the picture
    zoom = int(big * 1.14)
    logo = logo.resize((zoom, zoom), Image.LANCZOS).crop(((zoom - big) // 2, (zoom - big) // 2,
                                                           (zoom - big) // 2 + big, (zoom - big) // 2 + big))
    disc = Image.new("L", (big, big), 0)
    ImageDraw.Draw(disc).ellipse([2, 2, big - 3, big - 3], fill=255)
    out = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    out.paste(logo, (0, 0), ImageChops.multiply(logo.getchannel("A"), disc))
    d = ImageDraw.Draw(out)
    d.ellipse([2, 2, big - 3, big - 3], outline=LINE + (255,), width=max(big // 22, 2))
    d.ellipse([big // 22 + 2, big // 22 + 2, big - big // 22 - 3, big - big // 22 - 3],
              outline=CYAN + (170,), width=max(big // 60, 1))
    small = out.resize((n, n), Image.LANCZOS)
    rgb = Image.new("RGB", (n, n), KEY)
    rgb.paste(small, (0, 0), small)
    return rgb
