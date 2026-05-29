#!/usr/bin/env python3
"""
check_contributors.py — Scan changelog files for contributor mentions.

Usage:
    python scripts/check_contributors.py            # all changelogs
    python scripts/check_contributors.py --external # only external contributors
    python scripts/check_contributors.py --summary  # one-line summary per file

External = anyone who isn't Mason Kimball or Claude/Anthropic.
"""

import os
import re
import sys
import argparse
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CHANGELOGS_DIR = REPO_ROOT / 'changelogs'

MASON_PATTERNS = re.compile(
    r'mason\s+kimball|masonkimball05',
    re.IGNORECASE,
)
CLAUDE_PATTERNS = re.compile(r'claude|anthropic', re.IGNORECASE)


def parse_version(filename):
    v = filename.replace('.md', '').lstrip('v')
    parts = v.split('-', 1)[0].split('.')
    try:
        return tuple(int(x) for x in parts)
    except ValueError:
        return (0, 0, 0)


def parse_contributors(content):
    m = re.search(r'## Contributors\n(.*?)(?:\n---|\n## |\Z)', content, re.DOTALL)
    if not m:
        return []
    return [
        line.strip().lstrip('- ').strip()
        for line in m.group(1).splitlines()
        if line.strip().startswith('-')
    ]


def classify(contributors):
    mason, claude, external = [], [], []
    for c in contributors:
        if MASON_PATTERNS.search(c):
            mason.append(c)
        elif CLAUDE_PATTERNS.search(c):
            claude.append(c)
        else:
            external.append(c)
    return mason, claude, external


def main():
    parser = argparse.ArgumentParser(description='Scan changelogs for contributors.')
    parser.add_argument('--external', action='store_true',
                        help='Only show changelogs with external (non-Mason) contributors')
    parser.add_argument('--summary', action='store_true',
                        help='One-line summary per file instead of full detail')
    args = parser.parse_args()

    files = sorted(
        [f for f in os.listdir(CHANGELOGS_DIR) if f.endswith('.md') and f.startswith('v')],
        key=parse_version,
        reverse=True,
    )

    any_printed = False
    for filename in files:
        path = CHANGELOGS_DIR / filename
        content = path.read_text(encoding='utf-8')
        contributors = parse_contributors(content)
        mason, claude, external = classify(contributors)

        if args.external and not external:
            continue

        any_printed = True
        version = filename.replace('.md', '')

        if args.summary:
            tags = []
            if mason:
                tags.append('Mason')
            if claude:
                tags.append('Claude')
            if external:
                tags.append(f'⭐ {len(external)} external')
            label = ', '.join(tags) if tags else '(no contributors section)'
            marker = ' ←' if external else ''
            print(f'{version:12s}  {label}{marker}')
        else:
            if not contributors:
                if not args.external:
                    print(f'{version}: (no ## Contributors section)')
                continue
            print(f'\n{"=" * 50}')
            print(f'  {version}')
            print(f'{"=" * 50}')
            if mason:
                print('  Mason Kimball:')
                for c in mason:
                    print(f'    • {c}')
            if claude:
                print('  Claude/Anthropic:')
                for c in claude:
                    print(f'    • {c}')
            if external:
                print('  ⭐ External contributors:')
                for c in external:
                    print(f'    • {c}')

    if not any_printed:
        print('No changelogs matched the filter.')
        return

    if not args.summary:
        # Print overall stats
        total = len(files)
        with_section = sum(
            1 for f in files
            if parse_contributors((CHANGELOGS_DIR / f).read_text(encoding='utf-8'))
        )
        with_external = sum(
            1 for f in files
            if classify(parse_contributors((CHANGELOGS_DIR / f).read_text(encoding='utf-8')))[2]
        )
        print(f'\n{"=" * 50}')
        print(f'  Total changelogs:          {total}')
        print(f'  With Contributors section: {with_section}')
        print(f'  With external contributors:{with_external}')
        print(f'{"=" * 50}')


if __name__ == '__main__':
    main()
