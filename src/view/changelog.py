"""
Changelog view - displays version history for transparency
"""
import os
import re
import markdown
from django.shortcuts import render
from django.conf import settings
from django.views.decorators.cache import cache_page

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


@cache_page(60 * 30)  # 30 minutes — content only changes on deploy
def changelog(request):
    """
    Display the changelog/version history page.
    Reads from CHANGELOG.md and renders it as HTML.
    """
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
    except Exception as e:
        changelog_html = f"<p>Error loading changelog: {str(e)}</p>"

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

    return render(request, 'changelog.html', context)


@cache_page(60 * 30)  # 30 minutes — keyed per URL so each version is cached separately
def changelog_detail(request, version):
    """
    Display a specific version's detailed changelog.
    """
    changelog_html = ""

    # Sanitize version input (only allow alphanumeric, dots, and dashes)
    safe_version = ''.join(c for c in version if c.isalnum() or c in '.-')

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
        changelog_html = f"<p>Changelog for version {safe_version} not found.</p>"
    except Exception as e:
        changelog_html = f"<p>Error loading changelog: {str(e)}</p>"

    context = {
        'changelog_html': changelog_html,
        'version': safe_version,
    }

    return render(request, 'changelog_detail.html', context)


@cache_page(60 * 30)  # 30 minutes — static content
def roadmap(request):
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
