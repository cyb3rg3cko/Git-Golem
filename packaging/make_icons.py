"""Turn icon.png into the icon files the builds need.

  build/icon.ico      Windows .exe and installer icon (16–256 px)
  build/icon-256.png  Linux desktop icon (.deb)

Run from the project root:  python packaging/make_icons.py
"""
import os
import sys

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "icon.png")
OUT = os.path.join(ROOT, "build")

if not os.path.isfile(SRC):
    sys.exit(f"icon.png not found at {SRC}")

img = Image.open(SRC).convert("RGBA")

# Pad to a square so the icon is never stretched.
w, h = img.size
side = max(w, h)
square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
square.paste(img, ((side - w) // 2, (side - h) // 2))
icon256 = square.resize((256, 256), Image.LANCZOS)

os.makedirs(OUT, exist_ok=True)
icon256.save(os.path.join(OUT, "icon-256.png"))
icon256.save(os.path.join(OUT, "icon.ico"),
             sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print(f"Icons written to {OUT}")
