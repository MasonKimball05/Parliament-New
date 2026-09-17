"""
CSP template regression tests (v3.15.10, 07-24 report item #5;
inline-event-handler check added v3.31.1).

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
  3. No onclick=/onchange=/onsubmit= attribute anywhere — 'unsafe-inline' being
     absent from script-src blocks these exactly as it blocks a nonce-less
     <script>, and a nonce on the <script> that DEFINES the handler function
     does NOT cover an onclick= attribute elsewhere that CALLS it (the nonce
     only ever covers that one element's own content). Found the hard way:
     Mason reported the education dashboard's "Add Meeting" button did
     nothing in prod — every onclick/onchange/onsubmit on that page (and the
     shared modal shell's own close/cancel buttons) was silently dead, while
     working perfectly in dev. See templates/base.html's data-modal-open/
     data-modal-close comment for the fix pattern (delegated addEventListener
     + data-* attributes) used to remove them from that page.

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


# Matches onclick=/onchange=/onsubmit= only when it appears inside an actual
# HTML tag's attribute list (i.e. after an unclosed `<tagname`) — this is
# what deliberately does NOT flag the very sentences in this file, or in the
# templates' own explanatory comments, that mention "onclick=" as prose. A
# naive substring search would also match a JS comment or a `el.onclick = fn`
# property assignment (the latter is a normal, CSP-legal, JS-side operation
# performed by an already-permitted script — nothing to flag).
INLINE_HANDLER_RE = re.compile(r'<[a-zA-Z][^>]*\b(?:onclick|onchange|onsubmit)\s*=', re.IGNORECASE)

#: ⚠️ RATCHET — may only shrink. These templates had a real onclick=/onchange=/
#: onsubmit= attribute as of v3.31.1 and were NOT touched by that release
#: (which fixed committee/education.html, committee/education_meeting_form.html,
#: and components/modal_open.html + modal_close.html — the files implicated in
#: Mason's actual bug report). Each entry here is silently broken in prod
#: right now in exactly the same way, for the same reason, and is a real
#: known bug — not a false positive. `base.html` is the one worth prioritizing
#: first: it's the layout every page extends, and its two offending onclick
#: attributes are the chat-unread "mark all read" (×) button, so this is a
#: prod-only dead control on every page that has unread chats, found
#: incidentally while fixing the reported one. Fix a file, remove it here —
#: do NOT add a new entry without also fixing it, the same rule as
#: KNOWN_ORPHANS in test_reachable_pages.py.
KNOWN_INLINE_HANDLER_OFFENDERS = frozenset({
    'admin_v2/notifications/logs.html',
    'base.html',
    'committee/candidate_list.html',
    'committee/documents.html',
    'committee/recruitment_dashboard.html',
    'committee/recruitment_event_detail.html',
    'components/_progress_item.html',
    'components/profile_progress.html',
    'includes/confirm_modal.html',
    'kai/manage_commendations.html',
    'my_feedback_requests.html',
    'officer/manage_poll_qr.html',
    'officer/manage_qr_checkin.html',
    'officer/transition_checklist.html',
    'preferences.html',
    'service_hours/service_event_attendance.html',
    'service_hours/service_event_detail.html',
})


class NoInlineEventHandlerAttributesTests(SimpleTestCase):
    """
    v3.31.1 — onclick=/onchange=/onsubmit= are blocked in prod exactly like a
    nonce-less <script>, and for the same reason (no 'unsafe-inline' in
    script-src). Unlike the nonce check above, there is no fix-in-place
    attribute to add — every instance has to be rewired to a delegated
    addEventListener keyed off a data-* attribute (see templates/base.html's
    data-modal-open/data-modal-close for the pattern this release used).

    Scoped as a ratchet (KNOWN_INLINE_HANDLER_OFFENDERS) rather than an
    outright ban, because 18 pre-existing files have this and fixing all of
    them was out of scope for the bug Mason actually reported — but nothing
    NEW may be added, and the known list may only shrink.
    """

    def test_no_new_inline_event_handlers_outside_the_known_list(self):
        offenders = {}
        for rel, content in _template_files():
            matches = INLINE_HANDLER_RE.findall(content)
            if matches:
                offenders[rel] = len(matches)

        new_offenders = {
            rel: count for rel, count in offenders.items()
            if rel not in KNOWN_INLINE_HANDLER_OFFENDERS
        }
        self.assertEqual(
            new_offenders, {},
            'New onclick=/onchange=/onsubmit= attribute(s) — silently dead '
            'in prod (script-src has no \'unsafe-inline\'), works fine in '
            'dev. Use a data-* attribute and a delegated addEventListener '
            'instead (see templates/base.html\'s data-modal-open/'
            'data-modal-close for the pattern):\n  ' +
            '\n  '.join(f'{rel}: {count}' for rel, count in new_offenders.items())
        )

    def test_the_known_offender_list_only_shrinks(self):
        """
        ⚠️ THE POINT OF THE RATCHET. Pinning the exact count means fixing one
        of these files requires removing it from the set here too — the
        moment someone does, this assertion fails until they do, so the list
        cannot silently stay stale once a fix lands.
        """
        self.assertEqual(
            len(KNOWN_INLINE_HANDLER_OFFENDERS), 17,
            'KNOWN_INLINE_HANDLER_OFFENDERS changed size. If you fixed one of '
            'these files, remove its entry — do not just update this number.',
        )

    def test_every_known_offender_still_exists_and_still_offends(self):
        """
        The other direction: an entry that no longer offends (fixed without
        being removed from the set) or no longer exists (renamed/deleted)
        should be pruned, not left as dead weight nobody will ever revisit.
        """
        stale = []
        for rel in sorted(KNOWN_INLINE_HANDLER_OFFENDERS):
            path = TEMPLATE_DIR / rel
            if not path.exists():
                stale.append(f'{rel}: file no longer exists')
                continue
            if not INLINE_HANDLER_RE.search(path.read_text(errors='replace')):
                stale.append(f'{rel}: no longer has an inline event handler — remove from the set')
        self.assertEqual(stale, [], 'Stale KNOWN_INLINE_HANDLER_OFFENDERS entries:\n  ' + '\n  '.join(stale))

    def test_the_detector_actually_detects(self):
        """The control — an assertion that can't fail is not an assertion."""
        self.assertTrue(INLINE_HANDLER_RE.search('<button onclick="doThing()">Go</button>'))
        self.assertTrue(INLINE_HANDLER_RE.search('<input onchange="update(this)">'))
        self.assertTrue(INLINE_HANDLER_RE.search('<form onsubmit="return check()">'))
        # Prose mentioning the attribute name, and a JS property assignment,
        # must NOT be flagged — both are legal and both appear in this
        # codebase's own comments.
        self.assertIsNone(INLINE_HANDLER_RE.search('// blocks onclick=, onchange=, onsubmit= outright'))
        self.assertIsNone(INLINE_HANDLER_RE.search('el.onclick = function() { doThing(); };'))
        self.assertIsNone(INLINE_HANDLER_RE.search('<button data-action="thing">Go</button>'))
