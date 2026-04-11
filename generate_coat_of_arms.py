"""
Regenerate the web-optimized coat of arms PNG from the PDF source.

Ghost-box fix: render twice (with/without Bible), composite to remove overflow.
White fringe fix: flood-fill outer white transparent, then erode fringe pixels
                  adjacent to transparent areas.
Line thickening: dilate dark pixels into their light neighbors before scaling.

Run from the project root:
    python generate_coat_of_arms.py
"""

import fitz
import struct
import zlib
import collections

PDF_PATH = "exportable_media/AM coat of arms.pdf"
OUT_WEB  = "exportable_media/am-coat-of-arms.png"

OVERSAMPLE      = 4
SHRINK          = 2
CANVAS          = 1024
WHITE_BG        = 235   # flood-fill outer background threshold
FRINGE_PASSES   = 3     # iterations of fringe removal
FRINGE_MIN      = 160   # pixels brighter than this (adjacent to transparent) → remove
DARK_THRESH     = 70    # pixels darker than this are "black lines"
DARKEN_TO       = 30    # darken line-adjacent pixels to at most this value


def create_png(width, height, rgba_bytes):
    def chunk(tag, data):
        c = zlib.crc32(tag + data) & 0xffffffff
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', c)
    raw_rows = b''
    for y in range(height):
        raw_rows += b'\x00' + rgba_bytes[y * width * 4:(y + 1) * width * 4]
    ihdr = struct.pack('>II', width, height) + bytes([8, 6, 0, 0, 0])
    idat = zlib.compress(raw_rows, 9)
    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', ihdr)
            + chunk(b'IDAT', idat)
            + chunk(b'IEND', b''))


def render_page(page, mat):
    pix = page.get_pixmap(matrix=mat, alpha=False)
    pix.shrink(SHRINK)
    return pix


def main():
    doc = fitz.open(PDF_PATH)
    page = doc[0]

    # ── Find Bible raster image ──────────────────────────────────────────────
    bible_xref = None
    for img in page.get_images(full=True):
        xref, smask, w, h, bpc, cs = img[0], img[1], img[2], img[3], img[4], img[5]
        if cs == 'DeviceGray' and smask == 0 and 600 < w < 750:
            bible_xref = xref
            print(f"Found Bible image: xref={xref} size={w}×{h}")
            break

    mat = fitz.Matrix(OVERSAMPLE, OVERSAMPLE)

    # ── Render 1: full page (with Bible) ────────────────────────────────────
    full_pix = render_page(page, mat)
    pw, ph = full_pix.width, full_pix.height
    print(f"Full render: {pw}×{ph}")

    # ── Render 2: page without Bible (replace with 1×1 white) ───────────────
    if bible_xref is not None:
        tiny = create_png(1, 1, bytes([255, 255, 255, 255]))
        page.replace_image(bible_xref, stream=tiny)

    bg_pix = render_page(page, mat)
    bg_s = bg_pix.samples
    print(f"Background render: {bg_pix.width}×{bg_pix.height}")

    # ── Composite: where bg is colored, replace full with bg ────────────────
    full_s = bytearray(full_pix.samples)
    n = full_pix.n  # 3 (RGB, alpha=False)
    replaced = 0
    for i in range(pw * ph):
        b = i * n
        r, g, bv = bg_s[b], bg_s[b + 1], bg_s[b + 2]
        if not (r >= WHITE_BG and g >= WHITE_BG and bv >= WHITE_BG):
            full_s[b], full_s[b + 1], full_s[b + 2] = r, g, bv
            replaced += 1
    print(f"Ghost-box pixels replaced: {replaced}")

    # ── Thicken dark lines: dilate black pixels into light neighbors ─────────
    # Do this before transparency so dark pixels don't bleed into transparent areas
    thickened = bytearray(pw * ph * 3)
    thickened[:] = full_s
    for i in range(pw * ph):
        b = i * n
        r, g, bv = full_s[b], full_s[b + 1], full_s[b + 2]
        if r < DARK_THRESH and g < DARK_THRESH and bv < DARK_THRESH:
            x, y = i % pw, i // pw
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                nx, ny = x + dx, y + dy
                if 0 <= nx < pw and 0 <= ny < ph:
                    nb = (ny * pw + nx) * n
                    thickened[nb]     = min(thickened[nb],     DARKEN_TO)
                    thickened[nb + 1] = min(thickened[nb + 1], DARKEN_TO)
                    thickened[nb + 2] = min(thickened[nb + 2], DARKEN_TO)
    print("Line thickening done")

    # ── Build RGBA (alpha=255 everywhere to start) ───────────────────────────
    rgba = bytearray(pw * ph * 4)
    for i in range(pw * ph):
        b3, b4 = i * n, i * 4
        rgba[b4]     = thickened[b3]
        rgba[b4 + 1] = thickened[b3 + 1]
        rgba[b4 + 2] = thickened[b3 + 2]
        rgba[b4 + 3] = 255

    # ── Flood-fill outer white background → transparent ──────────────────────
    visited = bytearray(pw * ph)
    queue = collections.deque()

    def enqueue(idx):
        b4 = idx * 4
        if not visited[idx] and rgba[b4] >= WHITE_BG and rgba[b4+1] >= WHITE_BG and rgba[b4+2] >= WHITE_BG:
            visited[idx] = 1
            queue.append(idx)

    for x in range(pw):
        enqueue(x)
        enqueue((ph - 1) * pw + x)
    for y in range(ph):
        enqueue(y * pw)
        enqueue(y * pw + pw - 1)

    while queue:
        idx = queue.popleft()
        rgba[idx * 4 + 3] = 0
        x, y = idx % pw, idx // pw
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < pw and 0 <= ny < ph:
                enqueue(ny * pw + nx)

    # ── Fringe removal: erode near-white pixels adjacent to transparent ───────
    for pass_num in range(FRINGE_PASSES):
        to_clear = []
        for i in range(pw * ph):
            b4 = i * 4
            if rgba[b4 + 3] == 0:
                continue
            r, g, bv = rgba[b4], rgba[b4 + 1], rgba[b4 + 2]
            if r < FRINGE_MIN and g < FRINGE_MIN and bv < FRINGE_MIN:
                continue  # dark enough to keep
            x, y = i % pw, i // pw
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                nx, ny = x + dx, y + dy
                if 0 <= nx < pw and 0 <= ny < ph:
                    if rgba[(ny * pw + nx) * 4 + 3] == 0:
                        to_clear.append(i)
                        break
        for i in to_clear:
            rgba[i * 4 + 3] = 0
        print(f"Fringe pass {pass_num + 1}: cleared {len(to_clear)} pixels")

    # ── Crop to content bounds ────────────────────────────────────────────────
    min_x = min_y = min(pw, ph)
    max_x = max_y = 0
    for y in range(ph):
        for x in range(pw):
            if rgba[(y * pw + x) * 4 + 3] > 10:
                if x < min_x: min_x = x
                if x > max_x: max_x = x
                if y < min_y: min_y = y
                if y > max_y: max_y = y

    cw, ch = max_x - min_x + 1, max_y - min_y + 1
    print(f"Content bounds: {cw}×{ch} at ({min_x},{min_y})")

    # ── Scale and center on CANVAS×CANVAS ────────────────────────────────────
    scale = min(CANVAS / cw, CANVAS / ch)
    dw, dh = int(cw * scale), int(ch * scale)
    ox, oy = (CANVAS - dw) // 2, (CANVAS - dh) // 2

    canvas = bytearray(CANVAS * CANVAS * 4)
    for dy in range(dh):
        sy = int(dy / scale) + min_y
        if sy >= ph: continue
        for dx in range(dw):
            sx = int(dx / scale) + min_x
            if sx >= pw: continue
            src = (sy * pw + sx) * 4
            dst = ((oy + dy) * CANVAS + (ox + dx)) * 4
            canvas[dst:dst + 4] = rgba[src:src + 4]

    png_out = create_png(CANVAS, CANVAS, bytes(canvas))
    with open(OUT_WEB, 'wb') as f:
        f.write(png_out)
    print(f"Saved {OUT_WEB} ({len(png_out):,} bytes)")


if __name__ == '__main__':
    main()
