from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.urls import reverse
from django.conf import settings
from django.core import signing
from django.http import FileResponse, Http404, HttpResponse, HttpResponseForbidden
from django.views.decorators.http import require_GET
from src.feature_flag_decorators import require_feature_flag
from src.models import Legislation, CommitteeDocument
from src.models.documents import DocumentVersion
from src.utils.content_disposition import apply_disposition
import mimetypes
import os
import re
import urllib.parse
import logging
import base64

import bleach

logger = logging.getLogger(__name__)


#: Largest image a single PDF page is rendered to for the in-page preview.
#: ⚠️ THIS CAP IS THE FIX FOR THE 10-04-26 OUTAGE (v3.44.4). The preview used
#: to render every page at 150 dpi of whatever size the PDF *claimed* the page
#: was. A phone or copier scan often declares its page in pixels-as-points
#: (2550 x 3300 pt, i.e. 35 x 46 inches), so one page became a 5,300 x 6,900
#: pixel bitmap: over 100 MB raw, per page, plus the PNG and base64 copies. A
#: 5-page, 4 MB scan drove the server past 570 MB and into swap, and because
#: every ordinary page shares Daphne's one sync thread, the whole site stopped
#: answering until it finished. 1,400 px is wider than any phone or iPad
#: column this preview is shown in.
PDF_PREVIEW_MAX_WIDTH_PX = 1400
PDF_PREVIEW_MAX_HEIGHT_PX = 2000
PDF_PREVIEW_JPEG_QUALITY = 80


def _pdf_preview_zoom(page_width_pt, page_height_pt, dpi):
    """Zoom for `dpi`, reduced so the bitmap fits the preview's pixel cap."""
    zoom = dpi / 72
    if page_width_pt > 0 and page_width_pt * zoom > PDF_PREVIEW_MAX_WIDTH_PX:
        zoom = PDF_PREVIEW_MAX_WIDTH_PX / page_width_pt
    if page_height_pt > 0 and page_height_pt * zoom > PDF_PREVIEW_MAX_HEIGHT_PX:
        zoom = PDF_PREVIEW_MAX_HEIGHT_PX / page_height_pt
    return zoom


#: Pages of a PDF the in-page preview offers. Past this the viewer says
#: "showing first N of M" and links to the file.
PDF_PREVIEW_MAX_PAGES = 50
PDF_PREVIEW_DPI = 150

#: How long a preview page link works after the viewer page was loaded.
#: The link is only handed to a member who has just passed the viewer's own
#: permission check, and it is tied to that member (see pdf_preview_page).
PDF_PREVIEW_TOKEN_MAX_AGE = 2 * 60 * 60
_PDF_PREVIEW_SALT = 'src.view.view_document.pdf_preview'


def _render_pdf_page_jpeg(fitz, doc, page_index, dpi):
    """
    Render one page of an open document. Returns (jpeg_bytes, width, height).

    The pixel cap (PDF_PREVIEW_MAX_*) is applied here, so both callers get it.
    The bitmap and MuPDF's cache of the page's decoded images are released
    before returning.
    """
    page = doc[page_index]
    zoom = _pdf_preview_zoom(page.rect.width, page.rect.height, dpi)
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    try:
        return pix.tobytes("jpeg", jpg_quality=PDF_PREVIEW_JPEG_QUALITY), pix.width, pix.height
    finally:
        del pix, page
        fitz.TOOLS.store_shrink(100)


def pdf_preview_manifest(file_path, user, max_pages=PDF_PREVIEW_MAX_PAGES, dpi=PDF_PREVIEW_DPI):
    """
    Describe a PDF's preview pages WITHOUT rendering any of them (v3.44.5).

    Returns ``{'images': [{'page', 'url', 'width', 'height'}, ...],
    'total_pages': n, 'truncated': bool}`` or ``None`` if the PDF cannot be
    read. Each ``url`` points at ``pdf_preview_page``, which renders that one
    page when the browser asks for it. The viewer used to render every page
    inside its own request, for every visitor, including desktop visitors who
    are shown the PDF in an iframe and never see the images.

    ``width``/``height`` are the size the page WILL be rendered at. The
    template puts them on the <img> so the browser can lay the page out before
    the image arrives; without them every lazy image is zero pixels tall, all
    of them are "on screen" at once, and all of them load at once.

    Call this only after the caller's own permission check has passed: the
    urls it returns are what grants access to the page images.
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        logger.warning("PyMuPDF (fitz) library not installed, cannot preview PDF")
        return None

    media_root = os.path.realpath(settings.MEDIA_ROOT)
    resolved = os.path.realpath(file_path)
    if not resolved.startswith(media_root + os.sep):
        logger.error("PDF preview refused for a file outside MEDIA_ROOT: %s", file_path)
        return None

    try:
        token = signing.dumps(
            {
                'u': str(user.pk),
                'p': os.path.relpath(resolved, media_root),
                # A replaced file gets a new token, so a browser never shows
                # cached pages of the old file under the new one's url.
                'm': int(os.path.getmtime(resolved)),
            },
            salt=_PDF_PREVIEW_SALT,
            compress=True,
        )
        images = []
        doc = fitz.open(resolved)
        try:
            total_pages = len(doc)
            num_pages = min(total_pages, max_pages)
            for page_num in range(num_pages):
                rect = doc[page_num].rect
                zoom = _pdf_preview_zoom(rect.width, rect.height, dpi)
                images.append({
                    'page': page_num + 1,
                    'url': reverse('pdf_preview_page', args=[token, page_num + 1]),
                    'width': max(1, round(rect.width * zoom)),
                    'height': max(1, round(rect.height * zoom)),
                })
        finally:
            doc.close()
        return {
            'images': images,
            'total_pages': total_pages,
            'truncated': num_pages < total_pages,
        }
    except Exception as e:
        logger.error(f"Error reading PDF for preview: {e}")
        return None


@login_required
@require_GET
def pdf_preview_page(request, token, page):
    """
    One page of a PDF preview, as a JPEG (v3.44.5).

    ``token`` is issued by ``pdf_preview_manifest`` after a viewer's permission
    check, and is signed, time-limited and tied to the member it was issued
    to. This view does not repeat the viewer's permission check (there are
    five viewers with five different rules); the token is the proof that one
    of them passed. A link copied to another member does not work for them.

    Everything that can go wrong is a 404, so the response does not say
    whether a token was valid for somebody else.
    """
    try:
        data = signing.loads(token, salt=_PDF_PREVIEW_SALT, max_age=PDF_PREVIEW_TOKEN_MAX_AGE)
        owner, rel_path = data['u'], data['p']
    except (signing.BadSignature, KeyError, TypeError):
        raise Http404("Preview not found")

    if owner != str(request.user.pk):
        raise Http404("Preview not found")
    if not 1 <= page <= PDF_PREVIEW_MAX_PAGES:
        raise Http404("Preview not found")

    # The path is signed, so this cannot be reached with a path the server did
    # not choose. The guard is kept anyway, in the same form as serve_media's.
    media_root = os.path.realpath(settings.MEDIA_ROOT)
    resolved = os.path.realpath(os.path.join(media_root, rel_path))
    if not resolved.startswith(media_root + os.sep) or not os.path.isfile(resolved):
        raise Http404("Preview not found")

    try:
        import fitz  # PyMuPDF
    except ImportError:
        raise Http404("Preview not found")

    try:
        doc = fitz.open(resolved)
        try:
            if page > len(doc):
                raise Http404("Preview not found")
            img_data, _width, _height = _render_pdf_page_jpeg(fitz, doc, page - 1, PDF_PREVIEW_DPI)
        finally:
            doc.close()
    except Http404:
        raise
    except Exception as e:
        logger.error(f"Error rendering PDF preview page: {e}")
        raise Http404("Preview not found")

    response = HttpResponse(img_data, content_type='image/jpeg')
    # A page of a member-only document: the member's own browser may keep it,
    # a shared or CDN cache must not. Same header as download_chapter_document.
    response['Cache-Control'] = 'private, max-age=3600'
    return response


def convert_pdf_to_images(file_path, max_pages=PDF_PREVIEW_MAX_PAGES, dpi=PDF_PREVIEW_DPI):
    """
    Convert PDF pages to base64 images, all at once.

    Not used by the document viewer since v3.44.5: it renders every page
    before returning, which is what the viewer must not do inside a request.
    The viewer uses ``pdf_preview_manifest`` + ``pdf_preview_page``. Kept for
    callers that want a whole (small) PDF as images in one go.

    Returns ``{'images': [...], 'total_pages': n, 'truncated': bool}`` or
    ``None`` if the PDF cannot be read. Each image is a JPEG no larger than
    PDF_PREVIEW_MAX_WIDTH_PX x PDF_PREVIEW_MAX_HEIGHT_PX (see the note on
    those constants before raising them).
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        logger.warning("PyMuPDF (fitz) library not installed, cannot convert PDF to images")
        return None

    try:
        images = []
        doc = fitz.open(file_path)
        try:
            # Read the page count while the document is open. Asking a closed
            # document for its length raises in current PyMuPDF, which used
            # to send every successful conversion to the except below.
            total_pages = len(doc)
            num_pages = min(total_pages, max_pages)

            for page_num in range(num_pages):
                img_data, width, height = _render_pdf_page_jpeg(fitz, doc, page_num, dpi)
                images.append({
                    'data': base64.b64encode(img_data).decode('ascii'),
                    'mime': 'image/jpeg',
                    'page': page_num + 1,
                    'width': width,
                    'height': height,
                })
                del img_data
        finally:
            doc.close()

        return {
            'images': images,
            'total_pages': total_pages,
            'truncated': num_pages < total_pages,
        }
    except Exception as e:
        logger.error(f"Error converting PDF to images: {e}")
        return None


# Tags/attributes allowed in DOCX preview HTML. mammoth output is inserted into
# the page via {{ docx_html|safe }}, so a malicious .docx must not be able to
# inject script/style/event-handler markup. Everything else is stripped.
_DOCX_ALLOWED_TAGS = [
    'p', 'br', 'b', 'i', 'em', 'strong', 'u', 's', 'strike',
    'a', 'blockquote', 'ol', 'ul', 'li',
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'table', 'thead', 'tbody', 'tfoot', 'tr', 'td', 'th',
    'img', 'hr', 'span', 'div', 'sup', 'sub', 'pre', 'code',
    'figure', 'figcaption',
]
_DOCX_ALLOWED_ATTRS = {
    # 'target' deliberately not allowed: a target=_blank link without
    # rel=noopener enables reverse tabnabbing. Links open in the same tab.
    'a':   ['href', 'title', 'rel'],
    'img': ['src', 'alt', 'width', 'height'],
}

# mammoth embeds every image in the docx directly as `<img src="data:{content
# type};base64,...">` (its default `convert_image`, `mammoth.images.data_uri`
# — there is no separate "enable image support" flag to pass). bleach's
# default `protocols` allowlist is only http/https/mailto, so without this
# every image silently lost its `src` attribute during sanitization — the
# `<img>` tag survived (it's in _DOCX_ALLOWED_TAGS), just with nothing to
# render, which is why images never showed up even though nothing here was
# ever explicitly blocking them.
#
# Widening `protocols` to include `data` is scoped as tightly as bleach's API
# allows: `_sanitize_docx_html_images` below runs immediately after
# bleach.clean() and (a) strips `data:` out of any `href` — bleach applies
# one global protocol allowlist across every URI attribute it checks, so
# widening it for `img[src]` also widens it for `a[href]`, which would
# otherwise let a crafted .docx smuggle a `data:text/html;...` link — and
# (b) only lets an `img[src]` through if it's actually
# `data:image/<type>;base64,<data>`, so a docx image part with a relabeled
# content-type can't turn its data URI into something other than an image.
_DOCX_ALLOWED_PROTOCOLS = list(bleach.sanitizer.ALLOWED_PROTOCOLS) + ['data']

_DOCX_IMAGE_DATA_URI_RE = re.compile(
    r'^data:image/([a-zA-Z0-9.+-]+);base64,[A-Za-z0-9+/]+=*$'
)

# Image formats an evergreen browser can actually decode from an <img src>
# data URI. mammoth itself is more permissive (it also waves through TIFF
# without a warning), but Chrome/Firefox render no TIFF at all, and legacy
# vector formats pasted in from older Office documents — EMF/WMF — are
# common in real chapter documents (clip art, letterhead, anything copied
# out of an old .doc or another Office app) and no browser renders those
# either. mammoth still emits a perfectly well-formed
# `data:image/x-emf;base64,...` for one of these — it isn't stripped by the
# security checks above, since it IS a genuine image, just not a
# web-renderable one — and a browser asked to decode it fails silently: no
# broken-image icon, no visible fallback, the image is just gone and the
# surrounding text reflows as if it was never there. That silent failure is
# indistinguishable from the original "images don't show up at all" bug
# unless you go looking for it, so `_replace_unsupported_image` below swaps
# it for a visible notice instead.
_DOCX_BROWSER_RENDERABLE_IMAGE_TYPES = {
    'png', 'jpeg', 'jpg', 'gif', 'webp', 'bmp', 'svg+xml',
}

_IMG_TAG_RE = re.compile(r'<img\b[^>]*>')
_IMG_ATTR_RE = re.compile(r'(\w+)="([^"]*)"')


def _replace_unsupported_image(img_tag):
    """
    Given a single `<img ...>` tag whose `src` is a well-formed
    `data:image/<type>;base64,<data>` URI (already validated by the caller),
    swap it for a visible notice if `<type>` isn't one a browser can
    actually render — see `_DOCX_BROWSER_RENDERABLE_IMAGE_TYPES` above.
    Otherwise returns the tag unchanged.
    """
    attrs = dict(_IMG_ATTR_RE.findall(img_tag))
    src = attrs.get('src', '')
    match = _DOCX_IMAGE_DATA_URI_RE.match(src)
    if not match or match.group(1).lower() in _DOCX_BROWSER_RENDERABLE_IMAGE_TYPES:
        return img_tag

    # `attrs` is parsed out of `html`, which has already been through
    # `bleach.clean()` by the time this runs (see `_sanitize_docx_html_images`)
    # — so the `alt` value pulled off the tag is already HTML-entity-escaped
    # (that's what a serialized HTML attribute value IS). Escaping it again
    # here double-escapes anything with an `&`, `<`, `>` or `"` in the
    # original alt text (`&amp;` -> `&amp;amp;`, etc.) instead of rendering
    # cleanly.
    alt_text = attrs.get('alt') or 'Image'
    return (
        '<span class="docx-unsupported-image">'
        f"{alt_text} — this image's format can't be shown here; "
        'download the document to view it.'
        '</span>'
    )


def _sanitize_docx_html_images(html):
    """
    Defense-in-depth pass, run after bleach.clean() has already widened
    `protocols` to allow `data:` (needed for mammoth's embedded images — see
    the comment on `_DOCX_ALLOWED_PROTOCOLS`). Confines that widening to
    exactly "an <img> whose src is a base64 image data URI":

    - any `href="data:...` is stripped outright (only `<a>` has `href` in
      the allowlist, and a link has no legitimate reason to be a data URI —
      the whole point of the widened protocol list is images, not links).
    - any `src="data:...` that isn't `data:image/<type>;base64,<data>`
      (e.g. a docx image part with a relabeled/malformed content-type) is
      stripped, leaving the `alt` text as a fallback.
    - any `src="data:image/<type>;base64,..."` that IS well-formed but
      names a type no browser renders (EMF/WMF, etc.) is swapped for a
      visible notice rather than left as a silently-blank image.
    """
    html = re.sub(r'\shref="data:[^"]*"', '', html)

    def _strip_bad_image_src(match):
        src_value = match.group(1)
        if _DOCX_IMAGE_DATA_URI_RE.match(src_value):
            return match.group(0)
        return ''

    html = re.sub(r'\ssrc="(data:[^"]*)"', _strip_bad_image_src, html)
    html = _IMG_TAG_RE.sub(lambda m: _replace_unsupported_image(m.group(0)), html)
    return html


def convert_docx_to_html(file_path):
    """Convert a DOCX file to HTML using mammoth and sanitize the output"""
    try:
        import mammoth

        with open(file_path, 'rb') as docx_file:
            result = mammoth.convert_to_html(docx_file)
            html = result.value

            # mammoth's own conversion warnings (unsupported styles, image
            # types "unlikely to display in web browsers" — e.g. an EMF/WMF
            # image pasted in from an older Office document, which mammoth
            # still emits an <img> for but which no browser can decode — a
            # missing/unresolvable image relationship, etc.) were previously
            # discarded outright. Logging them is the difference between
            # "the image silently doesn't show up" and "the log says exactly
            # why," the first time this needs debugging again.
            for message in result.messages:
                logger.warning("mammoth (%s) converting %s: %s", message.type, file_path, message.message)

            # Sanitize against a strict allowlist. This also removes any inline
            # style/class attributes (not in the allowlist) that could override
            # our CSS, replacing the previous fragile regex stripping.
            html = bleach.clean(
                html,
                tags=_DOCX_ALLOWED_TAGS,
                attributes=_DOCX_ALLOWED_ATTRS,
                protocols=_DOCX_ALLOWED_PROTOCOLS,
                strip=True,
            )
            html = _sanitize_docx_html_images(html)

            # Preserve tabs by converting them to a span with tab styling
            html = html.replace('\t', '<span class="docx-tab"></span>')

            # Preserve multiple spaces
            html = re.sub(r'  +', lambda m: '&nbsp;' * len(m.group()), html)

            return html
    except ImportError:
        logger.warning("mammoth library not installed, cannot preview DOCX files")
        return None
    except Exception as e:
        logger.error(f"Error converting DOCX to HTML: {e}")
        return None


def get_file_type_info(file_path):
    """Determine file type information based on extension"""
    if not file_path:
        return {
            'is_pdf': False,
            'is_image': False,
            'is_office_doc': False,
            'is_docx': False,
            'is_text': False,
            'file_extension': '',
        }

    ext = os.path.splitext(file_path)[1].lower()

    return {
        'is_pdf': ext == '.pdf',
        'is_image': ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.svg'],
        'is_office_doc': ext in ['.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.odt', '.ods', '.odp'],
        'is_docx': ext == '.docx',
        'is_text': ext in ['.txt', '.md', '.csv', '.log', '.json', '.xml', '.html', '.css', '.js', '.py'],
        'file_extension': ext.upper().replace('.', '') if ext else 'Unknown',
    }


def read_text_file(file_path, max_size=500000):
    """Read a text file and return its content, truncated if too large"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read(max_size)
            if len(content) == max_size:
                content += '\n\n... [File truncated - download for full content]'
            return content
    except UnicodeDecodeError:
        try:
            with open(file_path, 'r', encoding='latin-1') as f:
                content = f.read(max_size)
                if len(content) == max_size:
                    content += '\n\n... [File truncated - download for full content]'
                return content
        except Exception as e:
            logger.error(f"Error reading text file: {e}")
            return None
    except Exception as e:
        logger.error(f"Error reading text file: {e}")
        return None


def _build_document_context(document_field, *, user, title, document_type, back_url,
                            description=None, uploaded_by=None, uploaded_at=None):
    """
    Build the shared context dict every document-viewer render needs.

    `document_field` is a Django FileField (e.g. ``legislation.document``).
    Callers are responsible for object lookup and permission checks before
    calling this; this helper only handles the (identical) rendering prep:
    file-type detection plus DOCX/text conversion, and the list of PDF
    preview pages (rendered later, one request per page; see
    ``pdf_preview_manifest``). `user` is the member the preview page links
    are issued to.
    """
    file_info = get_file_type_info(document_field.name)

    # Only one of these applies for any given file (mutually exclusive by extension).
    docx_html = None
    pdf_images = None
    text_content = None
    if file_info.get('is_docx'):
        docx_html = convert_docx_to_html(document_field.path)
    if file_info.get('is_pdf'):
        pdf_images = pdf_preview_manifest(document_field.path, user)
    if file_info.get('is_text'):
        text_content = read_text_file(document_field.path)

    return {
        # Use relative URL - works on any host without localhost issues
        'document_url': document_field.url,
        'document_title': title,
        'document_type': document_type,
        'back_url': back_url,
        'document_description': description,
        'uploaded_by': uploaded_by,
        'uploaded_at': uploaded_at,
        'docx_html': docx_html,
        'pdf_images': pdf_images,
        'text_content': text_content,
        **file_info,
    }


@login_required
def view_legislation_document(request, legislation_id):
    """View for displaying legislation documents in an embedded viewer"""
    legislation = get_object_or_404(Legislation, id=legislation_id)

    if not legislation.document:
        from django.http import Http404
        raise Http404("No document attached to this legislation")

    context = _build_document_context(
        legislation.document,
        user=request.user,
        title=legislation.title,
        document_type='Legislation Document',
        back_url=reverse('legislation_detail', args=[legislation_id]),
        description=legislation.description,
        uploaded_by=legislation.posted_by.username if legislation.posted_by else None,
        uploaded_at=legislation.created_at,
    )

    return render(request, 'view_document.html', context)


@login_required
def view_chapter_document(request, document_id):
    """View for displaying chapter documents in an embedded viewer"""
    document = get_object_or_404(CommitteeDocument, id=document_id, published_to_chapter=True)

    # Check if user can view this document
    if not document.can_user_view(request.user):
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden("You don't have permission to view this document")

    context = _build_document_context(
        document.document,
        user=request.user,
        title=document.title,
        document_type=document.get_document_type_display() if hasattr(document, 'get_document_type_display') else 'Chapter Document',
        back_url=reverse('chapter_documents'),
        description=document.description,
        uploaded_by=document.uploaded_by.username if document.uploaded_by else None,
        uploaded_at=document.uploaded_at,
    )

    return render(request, 'view_document.html', context)


@login_required
def view_committee_document(request, code, document_id):
    """View for displaying committee documents in an embedded viewer"""
    from src.models import Committee

    committee = get_object_or_404(Committee, code=code)
    document = get_object_or_404(CommitteeDocument, id=document_id, committee=committee)

    # Check if user can view this document
    if not document.can_user_view(request.user):
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden("You don't have permission to view this document")

    context = _build_document_context(
        document.document,
        user=request.user,
        title=document.title,
        document_type=document.get_document_type_display() if hasattr(document, 'get_document_type_display') else 'Committee Document',
        back_url=reverse('committee_documents', args=[code]),
        description=document.description,
        uploaded_by=document.uploaded_by.username if document.uploaded_by else None,
        uploaded_at=document.uploaded_at,
    )

    return render(request, 'view_document.html', context)


@login_required
def view_passed_legislation_document(request, pk):
    """View for displaying passed legislation documents in an embedded viewer"""
    legislation = get_object_or_404(Legislation, pk=pk, passed=True)

    if not legislation.document:
        from django.http import Http404
        raise Http404("No document attached to this legislation")

    context = _build_document_context(
        legislation.document,
        user=request.user,
        title=legislation.title,
        document_type='Passed Legislation',
        back_url=reverse('passed_legislation_detail', args=[pk]),
        description=legislation.description,
        uploaded_by=legislation.posted_by.username if legislation.posted_by else None,
        uploaded_at=legislation.created_at,
    )

    return render(request, 'view_document.html', context)


@login_required
def download_legislation_document(request, legislation_id):
    """Protected download for legislation documents — enforces authentication."""
    legislation = get_object_or_404(Legislation, id=legislation_id)
    if not legislation.document:
        raise Http404("No document attached to this legislation")
    file_path = legislation.document.path
    if not os.path.exists(file_path):
        raise Http404("File not found")
    filename = os.path.basename(file_path)
    return FileResponse(open(file_path, 'rb'), as_attachment=True, filename=filename)


@login_required
def download_chapter_document(request, document_id):
    """
    Protected "open" endpoint for chapter documents — enforces authentication
    and permissions (`can_user_view`), same as before.

    Mason: "currently if you link a document to an announcement it will make
    you download the document if you click on it, can instead we make it a
    hyperlink to open the document from where it is saved and they have the
    option to download it then?" Despite the name (kept as-is — this is the
    same URL every existing link, including the one on the announcements
    page and in the announcement email, already points at), this no longer
    unconditionally forces a download: it now uses the same
    `content_disposition.py` convention `serve_media`/`legislation_drafts`
    already use for exactly this decision — `Content-Disposition: inline`
    for a PDF or image (opens in the browser's own viewer, which has its own
    download button), `attachment` for anything else (`.docx`/`.xlsx`/etc.,
    which browsers can't render anyway). Nothing about the access check
    changed — still `@login_required` + `published_to_chapter=True` +
    `can_user_view()`, all evaluated exactly as before this only touches
    what the response headers tell the browser to do with the bytes once
    that check passes.
    """
    document = get_object_or_404(CommitteeDocument, id=document_id, published_to_chapter=True)
    if not document.can_user_view(request.user):
        return HttpResponseForbidden("You don't have permission to view this document")
    file_path = document.document.path
    if not os.path.exists(file_path):
        raise Http404("File not found")
    filename = os.path.basename(file_path)
    content_type, _ = mimetypes.guess_type(file_path)
    content_type = content_type or 'application/octet-stream'
    response = FileResponse(open(file_path, 'rb'), content_type=content_type)
    apply_disposition(response, content_type, filename)
    # Same reasoning as `serve_media`: this can be a member-only visibility-
    # restricted document, so it must never land in a shared/CDN cache.
    response['Cache-Control'] = 'private, max-age=3600'
    return response


@login_required
def download_committee_document(request, code, document_id):
    """Protected download for committee documents — enforces authentication and permissions."""
    from src.models import Committee
    committee = get_object_or_404(Committee, code=code)
    document = get_object_or_404(CommitteeDocument, id=document_id, committee=committee)
    if not document.can_user_view(request.user):
        return HttpResponseForbidden("You don't have permission to download this document")
    file_path = document.document.path
    if not os.path.exists(file_path):
        raise Http404("File not found")
    filename = os.path.basename(file_path)
    return FileResponse(open(file_path, 'rb'), as_attachment=True, filename=filename)


@login_required
@require_feature_flag('document_versioning')
def download_committee_document_version(request, code, document_id, version_id):
    """
    Protected download for an archived DocumentVersion — same permission
    check as the current file (`can_user_view` on the parent document), since
    an old version of a document a member can see is not more sensitive than
    the current one.
    """
    from src.models import Committee
    committee = get_object_or_404(Committee, code=code)
    document = get_object_or_404(CommitteeDocument, id=document_id, committee=committee)
    version = get_object_or_404(DocumentVersion, id=version_id, document=document)
    if not document.can_user_view(request.user):
        return HttpResponseForbidden("You don't have permission to download this document")
    if not version.file:
        raise Http404("File not found")
    file_path = version.file.path
    if not os.path.exists(file_path):
        raise Http404("File not found")
    filename = os.path.basename(file_path)
    return FileResponse(open(file_path, 'rb'), as_attachment=True, filename=filename)


# Mapping of document slugs to their details
REFERENCE_DOCUMENTS = {
    'constitution-bylaws': {
        'path': 'legislation_docs/Constitution and Bylaws of the Samford Chapter - August 2025.pdf',
        'title': 'Constitution & Bylaws of the Samford Chapter',
        'description': 'The official Constitution and Bylaws of the Samford Chapter of Beta Theta Pi',
        'back_url_name': 'constitution_bylaws',
    },
    'code-of-beta': {
        'path': 'legislation_docs/Code-of-Beta-Theta-Pi_44th-Edition_10.18.2022.pdf',
        'title': 'Code of Beta Theta Pi (44th Edition)',
        'description': 'The governing document of Beta Theta Pi Fraternity',
        'back_url_name': 'constitution_bylaws',
    },
    'kai-binder': {
        'path': 'legislation_docs/Kai-Binder.pdf',
        'title': 'Kai Committee Binder',
        'description': 'Complete procedures and guidelines for Kai Committee operations',
        'back_url_name': 'constitution_bylaws',
    },
    'trial-by-chapter': {
        'path': 'legislation_docs/Trial-By-Chapter-Overview.pdf',
        'title': 'Trial by Chapter Overview',
        'description': 'Process for severe violations requiring expulsion consideration',
        'back_url_name': 'constitution_bylaws',
    },
    'roberts-rules': {
        'path': 'legislation_docs/Roberts-Rules-of-Order.pdf',
        'title': "Robert's Rules of Order",
        'description': 'Parliamentary procedure guide for conducting meetings',
        'back_url_name': 'roberts_rules',
    },
}


@login_required
def view_reference_document(request, doc_slug):
    """View for displaying reference documents (constitution, bylaws, etc.) in an embedded viewer"""
    from django.http import Http404

    if doc_slug not in REFERENCE_DOCUMENTS:
        raise Http404("Document not found")

    doc_info = REFERENCE_DOCUMENTS[doc_slug]
    file_path = doc_info['path']

    # Use relative URL - works on any host without localhost issues
    document_url = settings.MEDIA_URL + file_path

    file_info = get_file_type_info(file_path)

    # List the PDF's preview pages for mobile viewing (rendered on request)
    pdf_images = None
    if file_info.get('is_pdf'):
        full_path = os.path.join(settings.MEDIA_ROOT, file_path)
        if os.path.exists(full_path):
            pdf_images = pdf_preview_manifest(full_path, request.user)

    context = {
        'document_url': document_url,
        'document_title': doc_info['title'],
        'document_type': 'Reference Document',
        'back_url': reverse(doc_info['back_url_name']),
        'document_description': doc_info['description'],
        'pdf_images': pdf_images,
        **file_info,
    }

    return render(request, 'view_document.html', context)
