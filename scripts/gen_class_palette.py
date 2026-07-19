#!/usr/bin/env python3
"""
Regenerates CLASS_PALETTE in src/pledge_classes.py (v3.15.0).

Farthest-point sampling in CIELAB: each color is placed as far as possible
(max-min ΔE) from all previously chosen colors AND from the founder gold,
over a readable badge gamut (mid lightness, decent saturation — white text
stays legible in both light and dark mode). Deterministic: the fixed seed
(a blue, far from gold) makes the output byte-for-byte reproducible.

Run:  python3 scripts/gen_class_palette.py           # prints the list + min ΔE
This is tooling only; the app imports the baked constant, not this script.
"""
import colorsys
import math

N = 48  # 24 years of classes


def to_lab(r, g, b):
    def lin(c):
        return ((c + 0.055) / 1.055) ** 2.4 if c > 0.04045 else c / 12.92
    r, g, b = lin(r), lin(g), lin(b)
    x = r * 0.4124 + g * 0.3576 + b * 0.1805
    y = r * 0.2126 + g * 0.7152 + b * 0.0722
    z = r * 0.0193 + g * 0.1192 + b * 0.9505
    xn, yn, zn = 0.95047, 1.0, 1.08883

    def f(t):
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116
    fx, fy, fz = f(x / xn), f(y / yn), f(z / zn)
    return (116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz))


def build():
    cands = []
    for h in range(0, 360, 4):
        for s in (58, 66, 74):
            for l in (42, 48, 54):
                rgb = colorsys.hls_to_rgb(h / 360, l / 100, s / 100)
                cands.append(((h, s, l), rgb, to_lab(*rgb)))
    gold = to_lab(*colorsys.hls_to_rgb(45 / 360, 0.5, 0.75))
    picked = [min(cands, key=lambda c: abs(c[0][0] - 215))]  # deterministic seed
    while len(picked) < N:
        best, bestd = None, -1
        for c in cands:
            d = min([math.dist(c[2], p[2]) for p in picked]
                    + [math.dist(c[2], gold)])
            if d > bestd:
                bestd, best = d, c
        picked.append(best)
    return picked


if __name__ == '__main__':
    picked = build()
    hexes = ['#%02x%02x%02x' % tuple(round(x * 255) for x in c[1])
             for c in picked]
    mind = min(math.dist(picked[i][2], picked[j][2])
               for i in range(N) for j in range(i + 1, N))
    print(f'min pairwise ΔE across {N} colors = {mind:.1f}')
    print('[' + ', '.join(f'"{h}"' for h in hexes) + ']')
