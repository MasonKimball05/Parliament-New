"""
Fraternity-wide CONTENT (multi-chapter, 09-28-26).

Per Mason's 09-25-26 scope decision, Parliament serves chapters of one
fraternity, and some content is the same for every one of them. That lives in
`fraternity_content/<fraternity>/`, chosen by `settings.FRATERNITY_CONTENT_DIR`:

  * songs.json: the default songbook (the fraternity's published song book).
    Every chapter starts from it (`manage.py seed_default_songs`), and each
    chapter adds its OWN songs in the app (/songbook/, "Add song"). Chapter
    songs are ordinary Song rows, and nothing here touches them.

Chapter-specific content lives in `chapter_content/<chapter>/` instead
(src/chapter_content.py). Houses (named for the founders) and the exec roles
are also shared, but they are code-level structure, not content files.
"""
import json
from pathlib import Path

from django.conf import settings

SONG_KEYS = {'title', 'category', 'lyrics'}
CATEGORY_KEYS = {'name', 'color', 'display_order'}
COLORS = {'blue', 'green', 'red', 'yellow', 'purple', 'pink', 'gray'}
PLACEHOLDER_PREFIX = '[Lyrics not available'


class FraternityContentError(ValueError):
    """A fraternity content file is missing or malformed."""


def content_dir():
    return Path(settings.FRATERNITY_CONTENT_DIR)


def load_default_songs(source=None):
    """{'categories': [...], 'songs': [...]}, validated. See module docstring."""
    path = Path(source) if source else content_dir() / 'songs.json'
    if not path.is_file():
        raise FraternityContentError(f'Default songbook not found: {path}')
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except json.JSONDecodeError as e:
        raise FraternityContentError(f'{path}: not valid JSON ({e})') from e
    return validate_songbook(data, label=str(path))


def validate_songbook(data, label='songbook'):
    problems = []
    if not isinstance(data, dict):
        raise FraternityContentError(f'{label}: expected an object with "categories" and "songs"')
    categories = data.get('categories', [])
    songs = data.get('songs')
    if not isinstance(categories, list):
        problems.append('categories must be a list')
        categories = []
    if not isinstance(songs, list) or not songs:
        raise FraternityContentError(f'{label}: "songs" must be a non-empty list')
    names = set()
    for c in categories:
        if not isinstance(c, dict):
            problems.append('a category is not an object')
            continue
        where = f"category {c.get('name', '?')!r}"
        for key in sorted(set(c) - CATEGORY_KEYS):
            problems.append(f'{where}: unknown key {key!r}')
        name = str(c.get('name') or '').strip()
        if not name:
            problems.append(f'{where}: name is required')
        elif name in names:
            problems.append(f'{where}: appears twice')
        names.add(name)
        if c.get('color', 'blue') not in COLORS:
            problems.append(f'{where}: color must be one of {sorted(COLORS)}')
        if 'display_order' in c and not isinstance(c['display_order'], int):
            problems.append(f'{where}: display_order must be an integer')
    titles = set()
    for s in songs:
        if not isinstance(s, dict):
            problems.append('a song is not an object')
            continue
        where = f"song {s.get('title', '?')!r}"
        for key in sorted(set(s) - SONG_KEYS):
            problems.append(f'{where}: unknown key {key!r}')
        title = str(s.get('title') or '').strip()
        if not title:
            problems.append(f'{where}: title is required')
        elif title.lower() in titles:
            problems.append(f'{where}: appears twice')
        titles.add(title.lower())
        if not str(s.get('lyrics') or '').strip():
            problems.append(f'{where}: lyrics are required (use the "[Lyrics not available…]" placeholder if unknown)')
        cat = s.get('category')
        if cat and cat not in names:
            problems.append(f'{where}: category {cat!r} is not in "categories"')
    if problems:
        raise FraternityContentError(f'{label}: {len(problems)} problem(s):\n  - ' + '\n  - '.join(problems))
    return data


def has_lyrics(song):
    return not str(song.get('lyrics', '')).startswith(PLACEHOLDER_PREFIX)
