"""
Per-chapter CONTENT files (multi-chapter phase 2, 09-27-26).

Identity (names, crest, colours) lives in settings.CHAPTER / src/chapter.py.
Content — text a chapter writes and amends, like its Constitution & Bylaws —
lives in files under `chapter_content/<chapter>/`, and
`settings.CHAPTER_CONTENT_DIR` says which directory this deployment uses.

Constitution & Bylaws
---------------------
`load_cnb_documents(source=None)` returns the DOCUMENTS list that
`seed_cnb_documents` imports, from:

  * `source`, if given (the command's `--source`), else
  * `<CHAPTER_CONTENT_DIR>/cnb.json`, else `<CHAPTER_CONTENT_DIR>/cnb.py`.

JSON is the normal format for a new chapter (`export_cnb_documents` writes it).
A `.py` file is EXECUTED to read its `DOCUMENTS`, so it is only accepted from
inside the repository's own `chapter_content/` directory — an operator
pointing `--source` at an arbitrary .py file gets an error, not an import.

Every load is validated (`validate_cnb_documents`) before anything touches the
database, and every problem is reported at once rather than the first one.

Reference documents (09-28-26)
------------------------------
`load_reference_documents()` reads `<CHAPTER_CONTENT_DIR>/reference_documents.json`,
the chapter's OWN reference PDFs (today: its ratified C&B PDF), as
`{"<slug>": {"path": "<path under MEDIA_ROOT>", "title": ..., "description": ...}}`.
`title`/`description` are optional. A missing file means the chapter has none,
not an error. Fraternity-wide documents (the Code, the Kai binder …) are listed
in `src/view/view_document.py`, not here.
"""
import json
import runpy
from pathlib import Path

from django.conf import settings

REPO_CONTENT_ROOT = Path(settings.BASE_DIR) / 'chapter_content'

DOC_KEYS = {'doc_type', 'title', 'display_order', 'amendment_protection_weeks', 'preamble', 'articles'}
ARTICLE_KEYS = {'number', 'title', 'sections'}
SECTION_KEYS = {'number', 'title', 'content', 'is_active'}


class ChapterContentError(ValueError):
    """A content file is missing, not allowed, or malformed."""


def content_dir():
    return Path(settings.CHAPTER_CONTENT_DIR)


def _default_cnb_source():
    base = content_dir()
    for name in ('cnb.json', 'cnb.py'):
        if (base / name).is_file():
            return base / name
    raise ChapterContentError(
        f'No Constitution & Bylaws source in {base} (looked for cnb.json and cnb.py). '
        f'Set CHAPTER_CONTENT_DIR, or pass --source. `manage.py export_cnb_documents` '
        f'writes a cnb.json to start from.'
    )


def _is_inside(path, root):
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def load_cnb_documents(source=None):
    path = Path(source) if source else _default_cnb_source()
    if not path.is_file():
        raise ChapterContentError(f'C&B source not found: {path}')
    if path.suffix == '.json':
        try:
            docs = json.loads(path.read_text(encoding='utf-8'))
        except json.JSONDecodeError as e:
            raise ChapterContentError(f'{path}: not valid JSON ({e})') from e
        if isinstance(docs, dict):
            docs = docs.get('documents')
    elif path.suffix == '.py':
        if not _is_inside(path, REPO_CONTENT_ROOT):
            raise ChapterContentError(
                f'{path}: a .py source is executed to read it, so it must live inside '
                f'{REPO_CONTENT_ROOT}. Use a .json file (export_cnb_documents writes one).'
            )
        docs = runpy.run_path(str(path)).get('DOCUMENTS')
    else:
        raise ChapterContentError(f'{path}: unsupported format (use .json)')
    validate_cnb_documents(docs, label=str(path))
    return docs


def validate_cnb_documents(docs, label='C&B source'):
    from src.models import GoverningDocument
    valid_types = {k for k, _ in GoverningDocument.DOCUMENT_TYPES}
    problems = []
    if not isinstance(docs, list) or not docs:
        raise ChapterContentError(f'{label}: expected a non-empty list of documents')
    seen_types = set()
    for i, d in enumerate(docs):
        where = f'document #{i + 1}'
        if not isinstance(d, dict):
            problems.append(f'{where}: not an object')
            continue
        where = f"document '{d.get('doc_type', '?')}'"
        for key in sorted(set(d) - DOC_KEYS):
            problems.append(f'{where}: unknown key {key!r}')
        if d.get('doc_type') not in valid_types:
            problems.append(f"{where}: doc_type must be one of {sorted(valid_types)}")
        elif d['doc_type'] in seen_types:
            problems.append(f'{where}: appears twice')
        seen_types.add(d.get('doc_type'))
        if not str(d.get('title') or '').strip():
            problems.append(f'{where}: title is required')
        for key in ('display_order', 'amendment_protection_weeks'):
            if key in d and not (isinstance(d[key], int) and d[key] >= 0):
                problems.append(f'{where}: {key} must be a non-negative integer')
        articles = d.get('articles', [])
        if not isinstance(articles, list):
            problems.append(f'{where}: articles must be a list')
            continue
        if not articles and not str(d.get('preamble') or '').strip():
            problems.append(f'{where}: has neither articles nor preamble text')
        seen_articles = set()
        for a in articles:
            if not isinstance(a, dict):
                problems.append(f'{where}: an article is not an object')
                continue
            aw = f"{where} Art. {a.get('number', '?')}"
            for key in sorted(set(a) - ARTICLE_KEYS):
                problems.append(f'{aw}: unknown key {key!r}')
            num = str(a.get('number') or '').strip()
            if not num:
                problems.append(f'{aw}: number is required')
            elif num in seen_articles:
                problems.append(f'{aw}: appears twice')
            seen_articles.add(num)
            if not str(a.get('title') or '').strip():
                problems.append(f'{aw}: title is required')
            seen_sections = set()
            for s in a.get('sections', []) or []:
                if not isinstance(s, dict):
                    problems.append(f'{aw}: a section is not an object')
                    continue
                sw = f"{aw} § {s.get('number', '?')}"
                for key in sorted(set(s) - SECTION_KEYS):
                    problems.append(f'{sw}: unknown key {key!r}')
                snum = str(s.get('number') or '').strip()
                if not snum:
                    problems.append(f'{sw}: number is required')
                elif snum in seen_sections:
                    problems.append(f'{sw}: appears twice')
                seen_sections.add(snum)
                if not str(s.get('content') or '').strip():
                    problems.append(f'{sw}: content is required')
                if 'is_active' in s and not isinstance(s['is_active'], bool):
                    problems.append(f'{sw}: is_active must be true or false')
    if problems:
        raise ChapterContentError(f'{label}: {len(problems)} problem(s):\n  - ' + '\n  - '.join(problems))
    return docs


REFERENCE_DOC_KEYS = {'path', 'title', 'description'}
_SLUG = __import__('re').compile(r'^[a-z0-9][a-z0-9-]*$')


def load_reference_documents():
    """This chapter's own reference documents; {} when it has none. See module docstring."""
    path = content_dir() / 'reference_documents.json'
    if not path.is_file():
        return {}
    try:
        docs = json.loads(path.read_text(encoding='utf-8'))
    except json.JSONDecodeError as e:
        raise ChapterContentError(f'{path}: not valid JSON ({e})') from e
    problems = []
    if not isinstance(docs, dict):
        raise ChapterContentError(f'{path}: expected an object of slug -> document')
    for slug, d in docs.items():
        if not _SLUG.match(str(slug)):
            problems.append(f'{slug!r}: slug must be lowercase letters, digits and hyphens')
        if not isinstance(d, dict):
            problems.append(f'{slug}: not an object')
            continue
        for key in sorted(set(d) - REFERENCE_DOC_KEYS):
            problems.append(f'{slug}: unknown key {key!r}')
        for key in ('title', 'description'):
            if key not in d:
                continue
            if not isinstance(d[key], str):
                problems.append(f'{slug}: {key} must be a string')
                continue
            # get_reference_documents() runs .format(fraternity=, school_short=,
            # chapter_name=) on these; a stray brace would 500 every page.
            try:
                d[key].format(fraternity='', school_short='', chapter_name='')
            except (KeyError, IndexError, ValueError, AttributeError) as e:
                problems.append(f'{slug}: {key} has a bad {{placeholder}} ({e!r}); '
                                'allowed: {fraternity} {school_short} {chapter_name}, literal braces as {{ }}')
        p = str(d.get('path') or '')
        if not p.strip():
            problems.append(f'{slug}: path is required')
        elif p.startswith('/') or '..' in Path(p).parts:
            # Joined onto MEDIA_ROOT/MEDIA_URL by the viewer — keep it inside.
            problems.append(f'{slug}: path must be relative to MEDIA_ROOT, without ".."')
    if problems:
        raise ChapterContentError(f'{path}: {len(problems)} problem(s):\n  - ' + '\n  - '.join(problems))
    return docs
