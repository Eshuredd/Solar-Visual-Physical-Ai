from __future__ import annotations

from pathlib import Path
import math
import random
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).parent / "app" / "static" / "assets"
ROOT.mkdir(parents=True, exist_ok=True)
random.seed(22)

try:
    FONT = ImageFont.truetype("DejaVuSans.ttf", 30)
    FONT_SM = ImageFont.truetype("DejaVuSans.ttf", 18)
except OSError:
    FONT = ImageFont.load_default()
    FONT_SM = ImageFont.load_default()


def rounded_panel(draw: ImageDraw.ImageDraw, box, fill=(18, 51, 76), outline=(85, 150, 179), width=3):
    x1, y1, x2, y2 = box
    draw.rounded_rectangle(box, radius=8, fill=fill, outline=outline, width=width)
    cols, rows = 6, 10
    for c in range(1, cols):
        x = x1 + (x2 - x1) * c / cols
        draw.line((x, y1 + 4, x, y2 - 4), fill=(63, 108, 132), width=1)
    for r in range(1, rows):
        y = y1 + (y2 - y1) * r / rows
        draw.line((x1 + 4, y, x2 - 4, y), fill=(63, 108, 132), width=1)


def add_label(img: Image.Image, title: str, subtitle: str):
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((24, 24, 500, 104), radius=14, fill=(5, 15, 17, 210))
    draw.text((42, 36), title, font=FONT, fill=(240, 250, 245))
    draw.text((42, 72), subtitle, font=FONT_SM, fill=(153, 190, 174))


def make_rgb_hotspot():
    img = Image.new("RGB", (1200, 760), (135, 166, 166))
    d = ImageDraw.Draw(img)
    # concrete/ground
    for y in range(760):
        shade = int(150 - 35 * y / 760)
        d.line((0, y, 1200, y), fill=(shade, shade + 16, shade + 12))
    for r in range(2):
        for c in range(4):
            x = 80 + c * 275 + (r * 26)
            y = 150 + r * 290
            rounded_panel(d, (x, y, x + 235, y + 205))
    # cracked/hot cell
    d.ellipse((646, 255, 700, 309), fill=(224, 90, 57), outline=(255, 205, 90), width=5)
    d.line((662, 265, 678, 298), fill=(255, 235, 210), width=3)
    d.line((678, 278, 694, 264), fill=(255, 235, 210), width=2)
    add_label(img, "RGB evidence", "Module B2-R08-M14 • suspected cell hotspot")
    img.save(ROOT / "rgb_hotspot.png", optimize=True)


def heat_color(v: float):
    v = max(0.0, min(1.0, v))
    stops = [
        (0.0, (19, 12, 50)),
        (0.2, (55, 24, 104)),
        (0.4, (156, 43, 111)),
        (0.6, (225, 84, 62)),
        (0.8, (252, 177, 72)),
        (1.0, (255, 247, 186)),
    ]
    for i in range(len(stops) - 1):
        a, ca = stops[i]
        b, cb = stops[i + 1]
        if a <= v <= b:
            t = (v - a) / (b - a)
            return tuple(int(ca[j] + (cb[j] - ca[j]) * t) for j in range(3))
    return stops[-1][1]


def make_thermal_hotspot():
    w, h = 1200, 760
    img = Image.new("RGB", (w, h))
    px = img.load()
    hotspots = [(690, 295, 1.05, 55), (335, 525, .62, 70)]
    for y in range(h):
        for x in range(w):
            base = 0.23 + 0.07 * math.sin(x / 90) + 0.05 * math.cos(y / 70)
            for hx, hy, strength, spread in hotspots:
                dist2 = (x - hx) ** 2 + (y - hy) ** 2
                base += strength * math.exp(-dist2 / (2 * spread * spread))
            px[x, y] = heat_color(base)
    img = img.filter(ImageFilter.GaussianBlur(1.2))
    d = ImageDraw.Draw(img)
    for r in range(2):
        for c in range(4):
            x = 80 + c * 275 + r * 26
            y = 150 + r * 290
            d.rounded_rectangle((x, y, x + 235, y + 205), radius=8, outline=(255, 245, 212), width=3)
            for col in range(1, 6):
                xx = x + 235 * col / 6
                d.line((xx, y + 4, xx, y + 201), fill=(255, 224, 180), width=1)
            for row in range(1, 10):
                yy = y + 205 * row / 10
                d.line((x + 4, yy, x + 231, yy), fill=(255, 224, 180), width=1)
    add_label(img, "Thermal evidence", "ΔT +21.7°C • normalized ΔT +18.9°C")
    img.save(ROOT / "thermal_hotspot.png", optimize=True)


def make_vegetation():
    img = Image.new("RGB", (1200, 760), (139, 188, 208))
    d = ImageDraw.Draw(img)
    d.rectangle((0, 400, 1200, 760), fill=(103, 113, 60))
    for i in range(1300):
        x = random.randrange(1200)
        y = random.randrange(410, 760)
        h = random.randrange(6, 52)
        col = random.choice([(55, 112, 52), (70, 132, 48), (115, 145, 50), (42, 96, 57)])
        d.line((x, y, x + random.randrange(-5, 6), y - h), fill=col, width=random.randrange(1, 4))
    for c in range(4):
        x = 70 + c * 290
        y = 185 + (c % 2) * 20
        rounded_panel(d, (x, y, x + 250, y + 210), fill=(19, 57, 85), outline=(112, 179, 197))
        d.line((x + 30, y + 210, x + 10, 540), fill=(62, 70, 72), width=8)
        d.line((x + 220, y + 210, x + 245, 540), fill=(62, 70, 72), width=8)
    # high encroachment near second panel
    for i in range(260):
        x = random.randrange(320, 610)
        y = random.randrange(390, 690)
        h = random.randrange(50, 190)
        d.line((x, y, x + random.randrange(-9, 10), y - h), fill=random.choice([(40, 105, 39), (73, 135, 44)]), width=3)
    add_label(img, "Visual evidence", "Vegetation encroachment • medium shading risk")
    img.save(ROOT / "rgb_vegetation.png", optimize=True)


def make_tracker():
    img = Image.new("RGB", (1200, 760), (156, 201, 223))
    d = ImageDraw.Draw(img)
    d.rectangle((0, 420, 1200, 760), fill=(184, 151, 92))
    for r in range(4):
        y = 150 + r * 120
        tilt = 0 if r != 2 else 28
        for c in range(8):
            x = 50 + c * 140
            box = (x, y + tilt * math.sin(c / 2), x + 122, y + 76 + tilt * math.sin(c / 2))
            rounded_panel(d, box, fill=(21, 61, 92), outline=(100, 169, 191), width=2)
        d.line((40, y + 88, 1160, y + 88), fill=(78, 80, 76), width=6)
    d.rounded_rectangle((540, 350, 815, 475), radius=16, outline=(255, 165, 78), width=7)
    add_label(img, "Tracker geometry", "Row C-07 differs 13.4° from its neighbors")
    img.save(ROOT / "rgb_tracker.png", optimize=True)


def make_wiring():
    img = Image.new("RGB", (1200, 760), (90, 101, 103))
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, 1200, 760), fill=(86, 92, 91))
    for c in range(4):
        x = 65 + c * 290
        d.rounded_rectangle((x, 120, x + 245, 560), radius=12, fill=(196, 201, 196), outline=(67, 72, 73), width=5)
        d.rectangle((x + 84, 250, x + 160, 328), fill=(47, 48, 49), outline=(18, 18, 18), width=3)
        d.line((x + 122, 328, x + 122, 490), fill=(34, 34, 34), width=14)
        d.line((x + 122, 490, x + 205, 610), fill=(34, 34, 34), width=14)
    # disconnected connector and hanging cable
    d.line((480, 448, 480, 650), fill=(28, 28, 28), width=18)
    d.rounded_rectangle((450, 615, 492, 680), radius=8, fill=(35, 35, 35), outline=(225, 90, 68), width=5)
    d.rounded_rectangle((504, 600, 548, 665), radius=8, fill=(35, 35, 35), outline=(225, 90, 68), width=5)
    d.line((510, 570, 526, 600), fill=(28, 28, 28), width=18)
    add_label(img, "Back-of-panel evidence", "Disconnected MC4 pair • safety risk")
    img.save(ROOT / "rgb_wiring.png", optimize=True)


def make_sample_thermal():
    w, h = 1024, 768
    img = Image.new("RGB", (w, h), (18, 12, 48))
    px = img.load()
    hot = [(245, 250, 0.9, 30), (705, 190, 1.0, 36), (520, 470, 0.75, 45), (820, 590, 0.95, 30), (355, 620, 0.6, 42)]
    for y in range(h):
        for x in range(w):
            v = 0.16 + 0.04 * math.sin(x / 43) + 0.04 * math.cos(y / 39)
            for hx, hy, strength, spread in hot:
                v += strength * math.exp(-((x-hx)**2 + (y-hy)**2)/(2*spread*spread))
            px[x, y] = heat_color(v)
    img = img.filter(ImageFilter.GaussianBlur(1.8))
    d = ImageDraw.Draw(img)
    for r in range(5):
        for c in range(7):
            x = 48 + c * 135
            y = 72 + r * 132
            d.rounded_rectangle((x, y, x+108, y+92), radius=5, outline=(249, 220, 184), width=2)
            d.line((x+54, y+2, x+54, y+90), fill=(240, 195, 166), width=1)
    d.rounded_rectangle((24, 18, 540, 58), radius=11, fill=(7, 8, 27, 205))
    d.text((40, 25), "DeepDrishti sample radiometric inspection", font=FONT_SM, fill=(255, 242, 216))
    img.save(ROOT / "sample_thermal_input.png", optimize=True)


def make_logo():
    svg = '''<svg xmlns="http://www.w3.org/2000/svg" width="220" height="52" viewBox="0 0 220 52">
  <rect width="52" height="52" rx="14" fill="#9dff75"/>
  <path d="M13 32c8-18 18-18 26 0-8 9-18 9-26 0Z" fill="#10251c"/>
  <circle cx="26" cy="28" r="5" fill="#9dff75"/>
  <path d="M17 15h18M26 10v10" stroke="#10251c" stroke-width="3" stroke-linecap="round"/>
  <text x="64" y="23" font-family="Inter,Arial,sans-serif" font-size="17" font-weight="700" fill="#edf7f1">DeepDrishti</text>
  <text x="64" y="42" font-family="Inter,Arial,sans-serif" font-size="14" font-weight="500" fill="#9cb7aa">SOLAR TWIN</text>
</svg>'''
    (ROOT / "logo.svg").write_text(svg)


for fn in [make_rgb_hotspot, make_thermal_hotspot, make_vegetation, make_tracker, make_wiring, make_sample_thermal, make_logo]:
    fn()
print("Generated assets in", ROOT)
