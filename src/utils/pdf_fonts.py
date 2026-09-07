"""
Unicode font support for this project's ReportLab PDF generators.

ReportLab's base-14 built-in fonts (Times-Roman/Bold/Italic/BoldItalic,
Helvetica*, Courier*) only cover WinAnsi/Latin-1. Any character outside
that range — Greek, Cyrillic, anything not in Windows-1252 — has no glyph
in those fonts and silently renders as a missing-glyph box, with no error
raised anywhere in the pipeline.

v3.29.21 — found via the chapter motto, "Ἀρετή Μονάζει", quoted in the
Foreword's preamble (`src/management/data/cnb_data.py`) and rendered by
`generate_cnb_document_pdf_buffer` (`src/view/officer/cnb.py`) using
Times-Roman throughout, per the "Times New Roman convention" fixed in
v3.29.8. Confirmed directly: neither ReportLab's own bundled fallback font
(`reportlab/fonts/Vera.ttf`, Bitstream Vera Sans) nor Liberation Serif has
full coverage for this string — Vera has none of the required Greek
glyphs at all, and Liberation Serif is missing the polytonic rough-
breathing character (Ἀ, U+1F08). DejaVu Serif does cover the full string,
including Greek Extended (polytonic) — confirmed with fontTools against
the actual glyph set, not assumed from general reputation.

Bundled here (`resources/fonts/dejavu/`, not under `static/` — this is a
server-side rendering resource, not something ever served to a browser or
scanned by the CSP/vendor-integrity tooling that walks `static/vendor/**`)
rather than referenced by a system font path like `/usr/share/fonts/...`:
a font at a system path is only as available as whatever packages happen
to be installed on whichever machine runs this — the same category of
risk as any other unpinned runtime dependency. Bundling means dev,
CI, and prod all get the exact same font file regardless of OS/image.
"""
import os
import re
import threading

from django.conf import settings
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

#: Font family name used in reportlab Paragraph markup, e.g.
#: `<font name="{UNICODE_FONT}">...</font>`. Registered as a full family
#: (regular/bold/italic/boldItalic) via `registerFontFamily` so nesting
#: inside `<b>`/`<i>` tags picks the right weight automatically.
UNICODE_FONT = 'DejaVuSerif'
UNICODE_FONT_BOLD = 'DejaVuSerif-Bold'
UNICODE_FONT_ITALIC = 'DejaVuSerif-Italic'
UNICODE_FONT_BOLD_ITALIC = 'DejaVuSerif-BoldItalic'

_FONT_DIR = os.path.join(settings.BASE_DIR, 'resources', 'fonts', 'dejavu')

_lock = threading.Lock()
_registered = False


def ensure_unicode_fonts_registered():
    """
    Idempotent, thread-safe. Safe to call on every PDF-generation request —
    the actual `pdfmetrics.registerFont` calls only happen once per process.
    """
    global _registered
    if _registered:
        return
    with _lock:
        if _registered:
            return
        pdfmetrics.registerFont(TTFont(UNICODE_FONT, os.path.join(_FONT_DIR, 'DejaVuSerif.ttf')))
        pdfmetrics.registerFont(TTFont(UNICODE_FONT_BOLD, os.path.join(_FONT_DIR, 'DejaVuSerif-Bold.ttf')))
        pdfmetrics.registerFont(TTFont(UNICODE_FONT_ITALIC, os.path.join(_FONT_DIR, 'DejaVuSerif-Italic.ttf')))
        pdfmetrics.registerFont(TTFont(UNICODE_FONT_BOLD_ITALIC, os.path.join(_FONT_DIR, 'DejaVuSerif-BoldItalic.ttf')))
        pdfmetrics.registerFontFamily(
            UNICODE_FONT,
            normal=UNICODE_FONT,
            bold=UNICODE_FONT_BOLD,
            italic=UNICODE_FONT_ITALIC,
            boldItalic=UNICODE_FONT_BOLD_ITALIC,
        )
        _registered = True


#: Matches a maximal run of characters outside Latin-1 (ord > 0xFF) — the
#: boundary of what WinAnsiEncoding/the base-14 fonts can render. ASCII
#: (including HTML entities like `&mdash;`, which are pure ASCII until
#: ReportLab's own parser expands them at render time) is left alone, so
#: adjacent runs separated only by ASCII punctuation/spaces end up as
#: separate <font> spans rather than one merged span — functionally
#: identical rendering, since the untouched ASCII in between renders
#: exactly the same in either font.
_NON_LATIN1_RUN_RE = re.compile(r'[^\x00-\xff]+')


def wrap_unicode_runs(text, font_name=UNICODE_FONT):
    """
    Wrap any run of non-Latin-1 characters in a ReportLab Paragraph
    `<font name="...">` tag, leaving ordinary Latin-1 text untouched.

    Call this on already-escaped text (i.e. after `&`/`<`/`>` have been
    replaced with entities) immediately before it's used to build a
    Paragraph — the same place `esc()`-style helpers are called in this
    project's PDF generators. Escaping first is safe because it only
    touches ASCII characters (`&`, `<`, `>`), which this function's regex
    ignores entirely.

    Nesting is safe: `<font>` tags nest correctly inside `<b>`/`<i>` in
    ReportLab's mini-XML, and because the font is registered as a full
    family (see `ensure_unicode_fonts_registered`), a Greek run inside a
    `<i>...</i>` block still renders in the Unicode font's italic face
    rather than losing italics or falling back to Times.

    Idempotent with `ensure_unicode_fonts_registered()` not yet called is
    still safe to build markup with — the font just needs to be registered
    before `doc.build(elements)` actually renders it, not before this
    function runs.
    """
    if not text:
        return text
    return _NON_LATIN1_RUN_RE.sub(
        lambda m: f'<font name="{font_name}">{m.group(0)}</font>',
        text,
    )
