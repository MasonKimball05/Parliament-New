"""
Renders PDF preview pages in a short-lived process of its own (v3.45.6).

WHY A SEPARATE PROCESS
----------------------
PyMuPDF used to run inside Daphne, the one process that serves the whole
site. Measured 10-13-26: importing it and rendering a 26-page PDF added ~37 MB
to Daphne, and none of it came back until Daphne restarted, on a 1 GB server.
Now Daphne starts this script, it renders, writes JPEGs to the preview cache,
and exits, taking that memory with it. Startup is ~0.1 s.

RULES FOR THIS FILE
-------------------
* No Django, no `src` imports. It runs as `python -I pdf_preview_helper.py`
  (isolated mode), so it only sees the standard library and the venv's
  installed packages. Daphne also imports it, for `preview_zoom`, which is why
  PyMuPDF is imported inside the functions and never at the top.
* One JSON request on stdin, one JSON answer on stdout.

    {"op": "info", "path": ..., "max_pages": 50, "dpi": 150,
     "max_width": 1400, "max_height": 2000}
      -> {"ok": true, "total_pages": 7, "pages": [[1275, 1650], ...]}

    {"op": "render", "path": ..., "pages": [1, 2], "out_dir": ...,
     "dpi": 150, "max_width": 1400, "max_height": 2000, "quality": 80}
      -> {"ok": true, "rendered": [1, 2]}

  Anything that goes wrong is {"ok": false, "error": "..."} and exit code 1.
"""
import json
import os
import sys


def preview_zoom(page_width_pt, page_height_pt, dpi, max_width, max_height):
    """Zoom for `dpi`, reduced so the bitmap fits the preview's pixel cap."""
    zoom = dpi / 72
    if page_width_pt > 0 and page_width_pt * zoom > max_width:
        zoom = max_width / page_width_pt
    if page_height_pt > 0 and page_height_pt * zoom > max_height:
        zoom = max_height / page_height_pt
    return zoom


def _info(req):
    import fitz
    doc = fitz.open(req['path'])
    try:
        total = len(doc)
        pages = []
        for index in range(min(total, req['max_pages'])):
            rect = doc[index].rect
            zoom = preview_zoom(rect.width, rect.height, req['dpi'], req['max_width'], req['max_height'])
            pages.append([max(1, round(rect.width * zoom)), max(1, round(rect.height * zoom))])
    finally:
        doc.close()
    return {'ok': True, 'total_pages': total, 'pages': pages}


def _render(req):
    import fitz
    out_dir = req['out_dir']
    os.makedirs(out_dir, exist_ok=True)
    rendered = []
    doc = fitz.open(req['path'])
    try:
        for number in req['pages']:
            if not 1 <= number <= len(doc):
                continue
            page = doc[number - 1]
            zoom = preview_zoom(page.rect.width, page.rect.height, req['dpi'], req['max_width'], req['max_height'])
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            data = pix.tobytes('jpeg', jpg_quality=req['quality'])
            del pix, page
            final = os.path.join(out_dir, f'page-{number}.jpg')
            # Write then rename, so a reader never sees half a file.
            partial = f'{final}.{os.getpid()}.part'
            with open(partial, 'wb') as fh:
                fh.write(data)
            os.replace(partial, final)
            rendered.append(number)
    finally:
        doc.close()
    return {'ok': True, 'rendered': rendered}


def main():
    try:
        req = json.loads(sys.stdin.read())
        op = {'info': _info, 'render': _render}[req['op']]
        answer = op(req)
    except Exception as exc:  # noqa: BLE001 — every failure is reported, not raised
        sys.stdout.write(json.dumps({'ok': False, 'error': f'{type(exc).__name__}: {exc}'}))
        return 1
    sys.stdout.write(json.dumps(answer))
    return 0


if __name__ == '__main__':
    sys.exit(main())
