"""
Changelog view - displays version history for transparency
"""
import os
import markdown
from django.shortcuts import render
from django.conf import settings


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

    if os.path.exists(changelogs_dir):
        for filename in sorted(os.listdir(changelogs_dir), reverse=True):
            if filename.endswith('.md') and filename.startswith('v'):
                version = filename.replace('.md', '')
                detailed_changelogs.append({
                    'version': version,
                    'filename': filename,
                })

    context = {
        'changelog_html': changelog_html,
        'detailed_changelogs': detailed_changelogs,
    }

    return render(request, 'changelog.html', context)


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
