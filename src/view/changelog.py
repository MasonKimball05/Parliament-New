"""
Changelog view - displays version history for transparency
"""
import logging
import os
import re
import markdown
from django.shortcuts import render
from django.conf import settings
from django.core.cache import cache
from django.http import Http404

logger = logging.getLogger('src')

#: How long the parsed markdown is kept. Content only changes on deploy, so this
#: is generous; the point is to avoid re-reading ~100 files per request.
CONTENT_CACHE_TTL = 60 * 30

#: v3.17.4 — WHY THESE VIEWS NO LONGER USE @cache_page
#: -----------------------------------------------------
#: They used to be decorated with `@cache_page(60 * 30)`, which caches the whole
#: rendered *response*. These pages are public and extend base.html, so the
#: cached HTML included whatever the first visitor's navbar looked like: their
#: name, their avatar, their admin-only links, and the inline
#: `const theme = '…'` that sets dark mode.
#:
#: The result was one visitor's chrome served to everyone for 30 minutes —
#: reported as "the changelog page is suddenly in light mode" (an anonymous
#: visitor primed the cache, so every dark-mode member got light), but the same
#: mechanism showed a logged-in member's name to anonymous visitors.
#:
#: `Vary: Cookie` does NOT save you here. `UpdateCacheMiddleware` runs inside
#: the decorator and stores the response *before* SessionMiddleware adds the
#: Vary header, so the learned Vary list is empty and the cache key is the URL
#: alone. The header you see on the response is added afterwards, too late to
#: affect the key.
#:
#: The fix is to cache the expensive part — reading and parsing the markdown —
#: and let the template render per request. Same saving, no shared chrome.
#: Same failure class as the 07-18-26 Cloudflare seal incident: never cache a
#: response whose body depends on who asked for it.

# Names / substrings that count as "just Mason" — anything else is an external contributor
_MASON_PATTERNS = [
    r'mason\s+kimball',
    r'masonkimball05',
]
_MASON_RE = re.compile('|'.join(_MASON_PATTERNS), re.IGNORECASE)


def parse_contributors(content):
    """
    Extract the ## Contributors section from a changelog file.
    Returns a list of contributor name strings, or [] if no section exists.
    """
    m = re.search(r'## Contributors\n(.*?)(?:\n---|\n## |\Z)', content, re.DOTALL)
    if not m:
        return []
    lines = [l.strip().lstrip('- ').strip()
             for l in m.group(1).splitlines()
             if l.strip().startswith('-')]
    return lines


def has_external_contributors(contributors):
    """Return True if any contributor is not Mason (or Claude)."""
    for c in contributors:
        if not _MASON_RE.search(c) and 'claude' not in c.lower() and 'anthropic' not in c.lower():
            return True
    return False


def parse_version(filename):
    """
    Parse version string for proper semantic version sorting.
    Returns tuple of (major, minor, patch, suffix) for sorting.
    e.g., 'v2.10.0.md' -> (2, 10, 0, '')
          'v2.7.0-slating-system.md' -> (2, 7, 0, 'slating-system')
    """
    # Remove .md extension and 'v' prefix
    version = filename.replace('.md', '').lstrip('v')

    # Split on dash to separate version from suffix
    parts = version.split('-', 1)
    version_str = parts[0]
    suffix = parts[1] if len(parts) > 1 else ''

    # Parse version numbers
    version_parts = version_str.split('.')
    try:
        major = int(version_parts[0]) if len(version_parts) > 0 else 0
        minor = int(version_parts[1]) if len(version_parts) > 1 else 0
        patch = int(version_parts[2]) if len(version_parts) > 2 else 0
    except ValueError:
        # If parsing fails, return zeros
        major, minor, patch = 0, 0, 0

    return (major, minor, patch, suffix)


def changelog(request):
    """
    Display the changelog/version history page.
    Reads from CHANGELOG.md and renders it as HTML.
    """
    context = cache.get('changelog_index_v1')
    if context is not None:
        return render(request, 'changelog.html', context)

    changelog_content = ""
    changelog_html = ""

    # Try to read the main CHANGELOG.md
    changelog_path = os.path.join(settings.BASE_DIR, 'CHANGELOG.md')

    try:
        with open(changelog_path, 'r', encoding='utf-8') as f:
            changelog_content = f.read()

        # Convert markdown to HTML with extensions for better formatting
        md = markdown.Markdown(extensions=[
            'tables',
            'fenced_code',
            'codehilite',
            'toc',
            'nl2br',
        ])
        changelog_html = md.convert(changelog_content)

    except FileNotFoundError:
        changelog_html = "<p>Changelog file not found.</p>"
    except Exception:
        # v3.17.5: was `f"Error loading changelog: {str(e)}"`. This page is
        # public and renders `changelog_html` through `|safe`, so the exception
        # text — which for a filesystem error carries the server's absolute
        # BASE_DIR — was shown to anonymous visitors and then cached for 30
        # minutes. Same shape as the CSV export leak fixed on 07-28.
        logger.exception('Failed to render the changelog index')
        changelog_html = "<p>The changelog could not be loaded.</p>"

    # Get list of detailed changelogs if they exist
    detailed_changelogs = []
    changelogs_dir = os.path.join(settings.BASE_DIR, 'changelogs')

    v2_changelogs = []
    if os.path.exists(changelogs_dir):
        # Get all changelog files and sort by semantic version (newest first)
        changelog_files = [f for f in os.listdir(changelogs_dir)
                          if f.endswith('.md') and f.startswith('v')]
        changelog_files.sort(key=parse_version, reverse=True)

        for filename in changelog_files:
            version = filename.replace('.md', '')
            filepath = os.path.join(changelogs_dir, filename)
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    file_content = f.read()
            except Exception:
                file_content = ''
            contributors = parse_contributors(file_content)
            entry = {
                'version': version,
                'filename': filename,
                'contributors': contributors,
                'has_external': has_external_contributors(contributors),
            }
            major = parse_version(filename)[0]
            if major >= 3:
                detailed_changelogs.append(entry)
            else:
                v2_changelogs.append(entry)

    context = {
        'changelog_html': changelog_html,
        'detailed_changelogs': detailed_changelogs,
        'v2_changelogs': v2_changelogs,
    }
    cache.set('changelog_index_v1', context, CONTENT_CACHE_TTL)

    return render(request, 'changelog.html', context)


def known_versions():
    """
    The set of versions that actually have a changelog file, cached.

    v3.17.5 — this exists so `changelog_detail` can reject an unknown version
    *before* it touches the cache. See the comment there for why that matters.
    Same TTL as the parsed content: the set only changes on deploy.
    """
    versions = cache.get('changelog_versions_v1')
    if versions is None:
        changelogs_dir = os.path.join(settings.BASE_DIR, 'changelogs')
        try:
            versions = frozenset(
                name[:-3] for name in os.listdir(changelogs_dir)
                if name.endswith('.md')
            )
        except OSError:
            versions = frozenset()
        cache.set('changelog_versions_v1', versions, CONTENT_CACHE_TTL)
    return versions


def changelog_detail(request, version):
    """
    Display a specific version's detailed changelog.
    """
    changelog_html = ""

    # Sanitize version input (only allow alphanumeric, dots, and dashes)
    safe_version = ''.join(c for c in version if c.isalnum() or c in '.-')

    # v3.17.5 — VALIDATE BEFORE CACHING, AND 404 WHAT DOES NOT EXIST
    # --------------------------------------------------------------
    # This route is `changelog/<str:version>/` and is PUBLIC, so `safe_version`
    # is attacker-chosen. It used to go straight into the cache key, and the
    # not-found branch below fell through to the same `cache.set(...)` as a
    # hit — so every distinct junk URL an anonymous visitor requested minted
    # its own 30-minute cache entry. Measured: 25 requests to
    # `/changelog/<random-hex>/` created 25 entries, each returning HTTP 200
    # with the body "Changelog for version … not found."
    #
    # Sanitising the key stopped key *injection*. It did nothing about key
    # *cardinality*, and the comment that used to sit here said otherwise.
    #
    # ⚠️ WHY THAT IS WORSE THAN ORDINARY CACHE BLOAT: settings.py sets
    # SESSION_ENGINE = 'django.contrib.sessions.backends.cache' with
    # SESSION_CACHE_ALIAS = 'default', so **sessions and this cache are the
    # same Redis**. An unauthenticated loop therefore contends with session
    # storage: under an `allkeys-*` maxmemory policy it evicts sessions (a
    # chapter-wide logout), and under `noeviction` Redis refuses writes — and
    # the write that fails is the session write, so login breaks. Nothing
    # rate-limits this path; LoginRateLimitMiddleware and
    # PasswordResetRateLimitMiddleware are the only two and neither covers it.
    #
    # Checking the version against the directory listing bounds the key space
    # to the number of files that actually exist (~97) no matter what is
    # requested. `known_versions()` is itself cached, so this costs nothing per
    # request.
    if safe_version not in known_versions():
        raise Http404(f'No changelog for version {safe_version}')

    # Cache key uses the SANITISED version, which is now additionally known to
    # name a real file — so two spellings of the same version share one entry
    # and no request can create an entry that was not already possible.
    cache_key = f'changelog_detail_v1:{safe_version}'
    cached_html = cache.get(cache_key)
    if cached_html is not None:
        return render(request, 'changelog_detail.html',
                      {'changelog_html': cached_html, 'version': safe_version})

    changelog_path = os.path.join(settings.BASE_DIR, 'changelogs', f'{safe_version}.md')

    try:
        with open(changelog_path, 'r', encoding='utf-8') as f:
            changelog_content = f.read()

        # Convert markdown to HTML
        md = markdown.Markdown(extensions=[
            'tables',
            'fenced_code',
            'codehilite',
            'toc',
            'nl2br',
        ])
        changelog_html = md.convert(changelog_content)

    except FileNotFoundError:
        # Reachable only if the file was removed between `known_versions()`
        # being cached and this read — i.e. a deploy mid-TTL.
        raise Http404(f'No changelog for version {safe_version}')
    except Exception:
        # v3.17.5: was `f"Error loading changelog: {str(e)}"`, rendered through
        # `|safe` on a public page and then cached for 30 minutes. Log it
        # instead of showing it, and do NOT cache a failure — a transient read
        # error should not pin a broken page to that version for half an hour.
        logger.exception('Failed to render changelog for version %s', safe_version)
        return render(request, 'changelog_detail.html', {
            'changelog_html': '<p>This changelog could not be loaded.</p>',
            'version': safe_version,
        }, status=500)

    # Only a successful parse is cached. The failure paths above return early.
    cache.set(cache_key, changelog_html, CONTENT_CACHE_TTL)

    context = {
        'changelog_html': changelog_html,
        'version': safe_version,
    }

    return render(request, 'changelog_detail.html', context)


def roadmap(request):
    # No caching: the context below is literal lists built in Python, so there
    # was nothing to save — @cache_page here bought no speed and shared one
    # visitor's navbar with everyone.
    """Display the Parliament 3.0 roadmap page."""
    context = {
        'ships_before': [
            'Middleware false-positive tuning (allowlist approach)',
            'Slating process rework',
            'Admin-v2 UI redesign',
            'Attack mitigation phases 1–3',
            'iCal feed, bulk member import, changelog viewer',
            'Audit log retention policy',
        ],
        'prerequisites': [
            {'name': 'Redis', 'detail': 'must be running on server — install_redis.sh already in repo'},
            {'name': 'Celery worker + beat', 'detail': 'two new systemd units alongside gunicorn'},
            {'name': 'VAPID keys', 'detail': 'generated once, stored in .env for push notifications'},
            {'name': 'HTTPS', 'detail': 'already done via Cloudflare ✓'},
            {'name': 'models.py split', 'detail': 'ideally done before building new features on top'},
        ],
    }
    return render(request, 'roadmap.html', context)
