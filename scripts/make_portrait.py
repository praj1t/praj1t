#!/usr/bin/env python3
"""One-off helper: cut a portrait out of its background -> assets/portrait.png (transparent PNG).

    pip install opencv-python-headless numpy
    python scripts/make_portrait.py path/to/photo.jpg

The render script turns assets/portrait.png into the ASCII portrait. You only run this
again if you change the photo. The outline polygon below was drawn by eye for the current
photo (1440x1080, head turned right); for a different photo, edit POLY or swap in any
background-removal tool and save a transparent PNG to assets/portrait.png yourself.
"""
import sys
from pathlib import Path

import cv2
import numpy as np

POLY = [(560, 78), (640, 72), (740, 95), (820, 150), (880, 215), (895, 255), (870, 320), (830, 345), (822, 390),
        (826, 440), (832, 470), (815, 490), (805, 502), (808, 520), (795, 545), (785, 580), (760, 608), (720, 630),
        (690, 648), (670, 690), (640, 735), (655, 790), (690, 860), (700, 960), (690, 1080), (65, 1080), (70, 980),
        (120, 850), (190, 740), (280, 660), (360, 600), (400, 560), (430, 500), (400, 470), (375, 420), (372, 330),
        (392, 255), (430, 190), (480, 135), (520, 100)]
CROP = (330, 50, 920, 780)  # x0, y0, x1, y1: head and neck
FADE_FROM = 0.74          # the bottom of the crop fades out from here
OUT_WIDTH = 440

src = Path(sys.argv[1] if len(sys.argv) > 1 else "photo.jpg")
im = cv2.imread(str(src))
if im is None:
    sys.exit(f"cannot read {src}")
h, w = im.shape[:2]
ell = lambda r: cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))

inside = np.zeros((h, w), np.uint8)
cv2.fillPoly(inside, [np.array(POLY, np.int32)], 255)
mask = np.full((h, w), cv2.GC_BGD, np.uint8)
mask[cv2.dilate(inside, ell(26)) > 0] = cv2.GC_PR_BGD
mask[inside > 0] = cv2.GC_PR_FGD
mask[cv2.erode(inside, ell(22)) > 0] = cv2.GC_FGD
cv2.grabCut(im, mask, None, np.zeros((1, 65)), np.zeros((1, 65)), 6, cv2.GC_INIT_WITH_MASK)
fg = np.where((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 255, 0).astype(np.uint8)
fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, ell(7))
fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, ell(2))
n, lab, st, _ = cv2.connectedComponentsWithStats(fg)
fg = np.where(lab == 1 + np.argmax(st[1:, 4]), 255, 0).astype(np.uint8)
alpha = cv2.GaussianBlur(fg, (0, 0), 1.6).astype(np.float32) / 255

# lift local contrast on the face so features survive the ASCII conversion
lab_img = cv2.cvtColor(im, cv2.COLOR_BGR2LAB)
lab_img[..., 0] = cv2.createCLAHE(clipLimit=2.4, tileGridSize=(6, 6)).apply(lab_img[..., 0])
im = cv2.cvtColor(lab_img, cv2.COLOR_LAB2BGR)

x0, y0, x1, y1 = CROP
im, alpha = im[y0:y1, x0:x1], alpha[y0:y1, x0:x1]
ramp = np.clip((1 - np.linspace(0, 1, alpha.shape[0])) / (1 - FADE_FROM), 0, 1)[:, None]
alpha = alpha * ramp
rgba = np.dstack([im, (alpha * 255).astype(np.uint8)])
scale = OUT_WIDTH / rgba.shape[1]
rgba = cv2.resize(rgba, (OUT_WIDTH, round(rgba.shape[0] * scale)), interpolation=cv2.INTER_AREA)
out = Path(__file__).resolve().parent.parent / "assets" / "portrait.png"
out.parent.mkdir(exist_ok=True)
cv2.imwrite(str(out), rgba)
print(f"wrote {out} {rgba.shape[1]}x{rgba.shape[0]}")
