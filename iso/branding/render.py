# SPDX-License-Identifier: AGPL-3.0-or-later
"""Regenerate the original boot artwork: python render.py (requires Pillow)."""
from pathlib import Path
import os
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent


def font(size):
    # Use the same freely licensed DejaVu font on the build/preview host.
    return ImageFont.truetype(os.environ.get("LAYER8_ARTWORK_FONT", "DejaVuSans.ttf"), size)


def render():
    image = Image.new("RGB", (1024, 768), "#12243a")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 1024, 8), fill="#32d4c8")
    draw.rounded_rectangle((775, 65, 942, 225), radius=20, fill="#1c344e")
    # Layer eight: a person at a desk, above a seven-port network switch.
    draw.ellipse((827, 82, 853, 108), fill="#32d4c8")
    draw.line([(840, 116), (840, 145), (867, 145)], fill="#32d4c8", width=11)
    draw.line([(815, 116), (815, 155), (844, 155), (844, 173)], fill="#d1deeb", width=6)
    draw.line([(853, 130), (906, 130), (906, 173)], fill="#d1deeb", width=5)
    draw.rectangle((877, 107, 901, 125), outline="#d1deeb", width=3)
    draw.rounded_rectangle((794, 180, 924, 211), radius=5, fill="#32d4c8")
    for x in range(805, 905, 15):
        draw.rectangle((x, 190, x+8, 199), fill="#12243a")
    draw.text((80, 67), "LAYER 8", font=font(23), fill="#32d4c8")
    draw.text((78, 111), "Your service desk.", font=font(43), fill="#f2f6fa")
    draw.text((78, 166), "Your setup.", font=font(43), fill="#f2f6fa")
    draw.text((80, 248), "A self-hosted helpdesk for the human layer.", font=font(21), fill="#d1deeb")
    draw.rounded_rectangle((64, 309, 960, 540), radius=14, fill="#1c344e")
    draw.text((80, 573), "Choose your disk and layout, or install a dedicated appliance.", font=font(20), fill="#d1deeb")
    draw.text((80, 613), "The ERASE option replaces all data on the first disk.", font=font(19), fill="#32d4c8")
    draw.line((80, 682, 944, 682), fill="#35506a", width=1)
    draw.text((80, 706), "LOCAL ACCOUNTS FIRST  /  INTEGRATIONS WHEN YOU ARE READY", font=font(16), fill="#d1deeb")
    image.save(HERE / "background.png", optimize=True)


if __name__ == "__main__":
    render()
