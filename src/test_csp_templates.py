"""
CSP template regression tests (v3.15.10, 07-24 report item #5).

Prod CSP is `script-src 'self' 'nonce-…'` with NO 'unsafe-inline'
(src/middleware/security.py). Dev sends no CSP header at all, which means a
nonce-less inline <script> works perfectly in dev and is silently dead in
prod — exactly how the chapter-stats charts and candidate status popover
shipped broken and stayed broken until v3.15.9.

These tests make that bug class fail at test time instead of in prod:

  1. Every inline <script> in templates/ must carry nonce="{{ request.csp_nonce }}".
  2. No <script src=…> may point at an external host — assets are self-hosted
     (script-src 'self' blocks external hosts anyway; mirrors check_env's
     supply-chain CDN check so it also runs in CI on every push).

Pure file scanning — no DB, no rendering — so it runs under SimpleTestCase.
"""
import re

from django.conf import settings
from django.test import SimpleTestCase

TEMPLATE_DIR = settings.BASE_DIR / 'templates'

# The Django admin is exempt from Parliament's CSP header (see
# add_security_headers in src/middleware/security.py) — its own inline
# scripts don't need nonces.
EXEMPT_PREFIXES = ('admin/',)

SCRIPT_TAG_RE = re.compile(r'<script\b[^>]*>', re.IGNORECASE)

# Known CDN/external hosts that must never appear in templates. Keep in sync
# with cdn_patterns in check_env.check_supply_chain (the deploy-time twin of
# this test).
CDN_PATTERNS = (
    'cdn.tailwindcss.com', 'play.tailwindcss.com',
    'cdn.quilljs.com', 'unpkg.com/',
    'cdnjs.cloudflare.com', 'cdn.jsdelivr.net',
)


def _template_files():
    for path in sorted(TEMPLATE_DIR.rglob('*.html')):
        rel = str(path.relative_to(TEMPLATE_DIR))
        if rel.startswith(EXEMPT_PREFIXES):
            continue
        yield rel, path.read_text(errors='replace')


class CspTemplateTests(SimpleTestCase):

    def test_inline_scripts_carry_csp_nonce(self):
        """Every executable inline <script> must have the per-request nonce.

        A hardcoded/other nonce value also fails: the middleware generates
        request.csp_nonce fresh per request, so anything else is still blocked
        in prod.
        """
        offenders = []
        for rel, content in _template_files():
            for m in SCRIPT_TAG_RE.finditer(content):
                tag = m.group(0)
                if 'src=' in tag:
                    continue  # external file — covered by the CDN test below
                if 'application/json' in tag or 'text/template' in tag:
                    continue  # data blocks don't execute; CSP doesn't apply
                if 'request.csp_nonce' in tag:
                    continue
                line = content.count('\n', 0, m.start()) + 1
                offenders.append(f'{rel}:{line}: {tag[:80]}')
        self.assertEqual(
            offenders, [],
            'Inline <script> without nonce="{{ request.csp_nonce }}" — '
            'works in dev (no CSP header) but is BLOCKED in prod:\n  '
            + '\n  '.join(offenders)
        )

    def test_no_external_script_hosts(self):
        """No template may reference a CDN — all assets are self-hosted."""
        offenders = []
        for rel, content in _template_files():
            for pat in CDN_PATTERNS:
                if pat in content:
                    line = content.count('\n', 0, content.find(pat)) + 1
                    offenders.append(f'{rel}:{line}: {pat}')
        self.assertEqual(
            offenders, [],
            "External CDN reference in templates — script-src 'self' blocks "
            'these in prod; vendor the asset instead (see '
            'static/vendor/.integrity.json):\n  ' + '\n  '.join(offenders)
        )
