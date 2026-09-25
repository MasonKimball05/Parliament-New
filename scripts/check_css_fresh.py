#!/usr/bin/env python3
"""
Fail if static/css/tailwind.css is missing classes the templates use (09-25-26).

WHY: tailwind.css is PREBUILT and checked in (`build_css.sh`). Add a class to a
template, forget to rebuild, and it silently does nothing in prod — no error,
no failing test. On 09-24-26 the resolution sticky-note margins stacked under
the text because `md:flex` had never been compiled; a scan the next day found
~a dozen more such classes across 15 templates.

HOW: build a fresh CSS to a temp file with the pinned CLI, then compare
SELECTOR SETS (not bytes — the committed file is built on macOS, CI runs on
Linux, and byte-for-byte equality across platforms is not something to bet a
red build on).
  * selectors the fresh build has but the committed file lacks → FAIL
    ("run ./build_css.sh")
  * selectors only in the committed file (classes no longer used) → reported,
    not failed.

Usage: python scripts/check_css_fresh.py path/to/tailwindcss-binary
"""
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMMITTED = os.path.join(ROOT, 'static', 'css', 'tailwind.css')
INPUT = os.path.join(ROOT, 'static', 'css', 'tailwind-input.css')


def selectors(css_text):
    found = {re.sub(r'\\(.)', r'\1', s) for s in re.findall(r'\.((?:\\.|[A-Za-z0-9_-])+)', css_text)}
    # Arbitrary-PROPERTY selectors (`[x:y]`) are ignored. Tailwind scans
    # src/**/*.py, so every Python slice like `text[pos:end]` compiles into a
    # junk rule; none is a class anyone meant. The project uses no real
    # arbitrary properties (checked 09-25-26); arbitrary VALUES such as
    # `max-h-[calc(...)]` start with a utility name and are still compared.
    return {s for s in found if not s.startswith('[')}


def main(binary):
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, 'fresh.css')
        subprocess.run([binary, '-i', INPUT, '-o', out, '--minify'], cwd=ROOT, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        fresh = selectors(open(out, encoding='utf-8').read())
    committed = selectors(open(COMMITTED, encoding='utf-8').read())
    missing = sorted(fresh - committed)
    stale = sorted(committed - fresh)
    if stale:
        print(f'note: {len(stale)} selector(s) in tailwind.css are no longer used (harmless): {", ".join(stale[:20])}')
    if missing:
        print(f'FAIL: {len(missing)} class(es) used in templates/JS are missing from static/css/tailwind.css:')
        for s in missing:
            print(f'  {s}')
        print('Run ./build_css.sh (see its header for the CLI download) and commit the result.')
        return 1
    print('tailwind.css is up to date.')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else './tailwindcss'))
