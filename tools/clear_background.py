#!/usr/bin/env python3
"""
Make the plain (white) background of a product photo transparent.

    python3 tools/clear_background.py IN.png OUT.png [--threshold 215]

How it works, so the mouse itself is never eaten away:
  1. Flood-fill from the image border through pixels that are close to white
     (all channels >= threshold). Only background *connected to the border*
     is selected, so light details inside the mouse (highlights, a lit logo,
     a silver wheel surrounded by dark plastic) stay untouched.
  2. Grow that region by a couple of pixels to include the anti-aliased
     edge of the mouse.
  3. In that region apply "colour to alpha" against white: each pixel
     becomes the colour that, drawn over white, gives the original — pure
     white turns fully transparent, grey edges and soft shadows turn into
     semi-transparent dark pixels. This avoids white halos on dark themes.

Pure Python + GdkPixbuf (no numpy/PIL needed).
"""

import argparse
import sys
from collections import deque

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib  # noqa: E402

EDGE_GROW = 2          # pixels of mouse edge to soften
NOISE_ALPHA = 0.06     # alpha below this (near-white compression noise) -> fully clear


def load_rgba(path):
    """Read an image as (width, height, bytearray of RGBA rows without padding)."""
    pb = GdkPixbuf.Pixbuf.new_from_file(path)
    if not pb.get_has_alpha():
        pb = pb.add_alpha(False, 0, 0, 0)
    w, h, stride = pb.get_width(), pb.get_height(), pb.get_rowstride()
    raw = pb.get_pixels()
    out = bytearray(w * h * 4)
    for y in range(h):
        out[y * w * 4:(y + 1) * w * 4] = raw[y * stride:y * stride + w * 4]
    return w, h, out


def save_rgba(path, w, h, data):
    """Write RGBA bytes as a PNG."""
    pb = GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(bytes(data)), GdkPixbuf.Colorspace.RGB,
                                         True, 8, w, h, w * 4)
    pb.savev(path, "png", [], [])


def background_mask(w, h, px, threshold):
    """Bytearray mask (1 = background): near-white pixels connected to the border."""
    def whiteish(i):
        o = i * 4
        return px[o] >= threshold and px[o + 1] >= threshold and px[o + 2] >= threshold

    mask = bytearray(w * h)
    queue = deque()
    for x in range(w):
        for y in (0, h - 1):
            queue.append(y * w + x)
    for y in range(h):
        for x in (0, w - 1):
            queue.append(y * w + x)
    while queue:
        i = queue.popleft()
        if mask[i] or not whiteish(i):
            continue
        mask[i] = 1
        x, y = i % w, i // w
        if x > 0:
            queue.append(i - 1)
        if x < w - 1:
            queue.append(i + 1)
        if y > 0:
            queue.append(i - w)
        if y < h - 1:
            queue.append(i + w)
    return mask


def grow(mask, w, h, steps):
    """Dilate a mask by `steps` pixels (4-neighbourhood)."""
    for _ in range(steps):
        new = bytearray(mask)
        for i, v in enumerate(mask):
            if v:
                x, y = i % w, i // w
                if x > 0:
                    new[i - 1] = 1
                if x < w - 1:
                    new[i + 1] = 1
                if y > 0:
                    new[i - w] = 1
                if y < h - 1:
                    new[i + w] = 1
        mask = new
    return mask


def color_to_alpha(px, region):
    """Colour-to-alpha against white for every pixel in `region` (in place)."""
    for i, v in enumerate(region):
        if not v:
            continue
        o = i * 4
        r, g, b, a0 = px[o], px[o + 1], px[o + 2], px[o + 3]
        a = max(255 - r, 255 - g, 255 - b) / 255
        if a < NOISE_ALPHA:
            px[o + 3] = 0
            continue
        # colour c' such that c' * a + 255 * (1 - a) == c
        px[o] = max(0, min(255, round((r - 255 * (1 - a)) / a)))
        px[o + 1] = max(0, min(255, round((g - 255 * (1 - a)) / a)))
        px[o + 2] = max(0, min(255, round((b - 255 * (1 - a)) / a)))
        px[o + 3] = round(a * a0)


def clear_background(src, dst, threshold=215):
    """Read `src`, make its border-connected white background transparent, write `dst`."""
    w, h, px = load_rgba(src)
    mask = background_mask(w, h, px, threshold)
    region = grow(mask, w, h, EDGE_GROW)
    color_to_alpha(px, region)
    save_rgba(dst, w, h, px)
    cleared = sum(mask)
    return w, h, cleared


def main():
    """CLI entry point."""
    ap = argparse.ArgumentParser(description="Make a photo's white background transparent.")
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--threshold", type=int, default=215,
                    help="min value (0-255) of all channels for a pixel to count as background")
    args = ap.parse_args()
    w, h, cleared = clear_background(args.src, args.dst, args.threshold)
    print(f"{args.dst}: {w}x{h}, {cleared * 100 // (w * h)}% background cleared")
    return 0


if __name__ == "__main__":
    sys.exit(main())
