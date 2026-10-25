# SPDX-License-Identifier: AGPL-3.0-or-later
"""Original, reproducible boot artwork and GRUB selection tiles (Pillow + DejaVu Sans)."""
from pathlib import Path
import os
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
SIZE = (1024, 768)
ACCENT = "#32d4c8"
INK = "#f2f7fb"
MUTED = "#b4c6d5"


def font(size):
    return ImageFont.truetype(os.environ.get("LAYER8_ARTWORK_FONT", "DejaVuSans.ttf"), size)


def selection_tiles(destination):
    # GRUB styled boxes use nine pieces; only the edges/corners carry the border.
    scale, edge, middle = 3, 10, 4
    side = edge * 2 + middle
    tile = Image.new("RGBA", (side * scale, side * scale))
    draw = ImageDraw.Draw(tile)
    draw.rounded_rectangle((1, 1, side * scale - 2, side * scale - 2), radius=9 * scale,
                           fill="#153c49", outline=ACCENT, width=scale)
    tile = tile.resize((side, side), Image.Resampling.LANCZOS)
    regions = {"nw": (0, 0, edge, edge), "n": (edge, 0, edge + middle, edge),
               "ne": (edge + middle, 0, side, edge), "w": (0, edge, edge, edge + middle),
               "c": (edge, edge, edge + middle, edge + middle), "e": (edge + middle, edge, side, edge + middle),
               "sw": (0, edge + middle, edge, side), "s": (edge, edge + middle, edge + middle, side),
               "se": (edge + middle, edge + middle, side, side)}
    for name, box in regions.items():
        tile.crop(box).save(destination / f"select_{name}.png", optimize=True)


def render(destination=HERE):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", SIZE)
    draw = ImageDraw.Draw(image)
    for y in range(SIZE[1]):
        t = y / (SIZE[1] - 1)
        draw.line((0, y, SIZE[0], y), fill=(7 + round(4*t), 19 + round(8*t), 31 + round(11*t)))
    # Subtle network traces sit behind the human layer, clear of all menu text.
    for n in range(7):
        x, y = 685 + n * 28, 105 + n * 19
        draw.line([(x, 0), (x, y), (1010, y)], fill="#142f40", width=1)
        draw.ellipse((1007, y-3, 1013, y+3), fill="#224858")
    draw.rectangle((0, 0, 1023, 4), fill=ACCENT)
    draw.rounded_rectangle((64, 42, 98, 76), radius=9, outline=ACCENT, width=2)
    # Three simple stacked layers in the header; the human/switch mark stays prominent.
    for y in (51, 58, 65):
        draw.line([(72, y), (81, y+4), (90, y)], fill=ACCENT, width=2)
    draw.text((112, 44), "LAYER 8", font=font(25), fill=INK)
    draw.text((718, 51), "APPLIANCE INSTALLER", font=font(15), fill=MUTED)
    draw.text((62, 116), "Your service desk.", font=font(42), fill=INK)
    draw.text((62, 169), "From first boot.", font=font(42), fill=INK)
    draw.text((64, 237), "Self-hosted. Connected. Built for the human layer.", font=font(18), fill=MUTED)
    # Original person-at-a-desk over a seven-port switch: the unofficial eighth OSI layer.
    draw.rounded_rectangle((756, 112, 952, 270), radius=18, fill="#102838", outline="#274452", width=1)
    draw.ellipse((804, 132, 830, 158), fill=ACCENT)
    draw.line([(817, 167), (817, 194), (845, 194)], fill=ACCENT, width=10)
    draw.line([(793, 166), (793, 203), (817, 203), (817, 217)], fill=INK, width=5)
    draw.line([(839, 178), (919, 178), (919, 218)], fill=INK, width=4)
    draw.rounded_rectangle((873, 144, 909, 169), radius=3, outline=INK, width=3)
    draw.line((890, 169, 890, 177), fill=INK, width=3)
    draw.rounded_rectangle((774, 225, 935, 253), radius=6, fill=ACCENT)
    for x in range(785, 921, 20):
        draw.rounded_rectangle((x, 234, x+11, 244), radius=1, fill="#0a2331")
    draw.text((64, 292), "LET'S GET YOU SET UP", font=font(14), fill=ACCENT)
    # This entire area stays free of baked-in labels: firmware renders the real entries.
    draw.rounded_rectangle((48, 326, 976, 548), radius=15, fill="#102331", outline="#294150", width=1)
    draw.text((64, 576), "Choose disk and partitioning to control your storage layout.", font=font(17), fill=MUTED)
    draw.rounded_rectangle((64, 612, 70, 642), radius=2, fill="#f0b86a")
    draw.text((84, 612), "ERASE removes every partition on the first disk.", font=font(18), fill="#f5c98d")
    draw.line((64, 679, 960, 679), fill="#294150", width=1)
    draw.text((64, 700), "UP / DOWN   Select     ENTER   Continue", font=font(16), fill=INK)
    draw.text((733, 700), "OFFLINE INSTALLATION", font=font(14), fill=ACCENT)
    draw.text((64, 736), "YOUR INFRASTRUCTURE. YOUR DATA. YOUR HELP DESK.", font=font(13), fill=MUTED)
    image.save(destination / "background.png", optimize=True)
    selection_tiles(destination)
    return image


if __name__ == "__main__":
    render()
