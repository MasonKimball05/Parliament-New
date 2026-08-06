"""
`get_client_ip` must be the only thing in the codebase that reads the proxy chain.

⚠️ WHY THIS EXISTS — FIVE COPIES, FOUND FROM PRODUCTION DATA.

`src/utils/security_utils.get_client_ip` knows two things that nothing else
does: that `BEHIND_CLOUDFLARE` means the visitor's address is in
`CF-Connecting-IP`, and that the *rightmost* X-Forwarded-For entry is the safe
one **only** when nginx is the sole proxy (nginx appends the socket peer, so an
attacker can forge leading entries but not the last one).

That second fact inverts when Cloudflare is upstream: nginx's socket peer is
then the Cloudflare edge, so rightmost returns Cloudflare and the visitor is
discarded. The helper handles it. Five other places reimplemented the mechanics
without the reasoning and got it wrong:

    src/models/activity.py:203       ActivityLog  — every audit row
    src/models/users.py:886          UserSession  — Active Sessions + fingerprint
    src/context_processors.py:424    every page render
    src/middleware/maintenance.py:71 maintenance-mode block logging
    src/view/admin_v2.py:3024        the lockdown whitelist SUGGESTION

Found 08-06-26 from a prod activity-log export: all 22 distinct IPs in it were
Cloudflare edges. A 47-attempt credential-stuffing burst and six members'
logins were recorded from the same address pool, four addresses appearing in
both — so the audit log could not distinguish an attacker from a brother.
`.env` had `BEHIND_CLOUDFLARE=True` the entire time, and it worked everywhere it
was consulted. These five never asked.

**The pattern, which CLAUDE.md has now recorded six releases running:** a rule
stated correctly, a helper written to enforce it, and call sites left outside
the helper. The distinguishing feature every time is that the copies look
*right* — they are the same four lines, and the thing that makes them wrong
lives in the docstring of the function they didn't call. Review cannot see an
absent call. A test can.

Pure source scanning — no DB, no rendering — so it runs under SimpleTestCase.
"""
import ast
import pathlib

from django.test import SimpleTestCase

SRC_ROOT = pathlib.Path(__file__).resolve().parent

#: The proxy headers only `get_client_ip` may read.
PROXY_HEADERS = {'HTTP_X_FORWARDED_FOR', 'HTTP_CF_CONNECTING_IP', 'REMOTE_ADDR'}

#: The one module allowed to read them, plus this test.
ALLOWED = {
    'utils/security_utils.py',
    pathlib.Path(__file__).name,
}

#: ⚠️ AWAITING A DECISION FROM MASON — NOT AN EXEMPTION ON THE MERITS.
#:
#: Writing this guard turned up 26 more `REMOTE_ADDR` reads across the slating
#: module, all feeding `SlatingActivity.ip_address` on vote-cast, application,
#: slate-build and interview rows. They are wrong in the same way as everything
#: else in this file's docstring — behind nginx's unix socket `REMOTE_ADDR` is
#: the socket peer, so those rows currently store nothing useful.
#:
#: They are NOT fixed here, deliberately, because fixing them **adds data that
#: is not currently recorded** to the one module whose anonymity rules Mason has
#: dispositioned most carefully (CLAUDE.md, 07-25-26): `SlatingVote.voted_at` is
#: excluded from the admin specifically because a timestamp is a join key, and
#: v3.18.5's rule is that *an IP does not have to name anyone, it only has to be
#: a join key*. Populating real IPs into slating audit rows is a change in kind,
#: not a bug fix, and it is his call — not something to slip into a batch
#: labelled "fix the Cloudflare IP thing".
#:
#: Current reading: `SlatingActivity` already records `user` and is visible by
#: design (participation is not secret; the *ballot* is), and `SlatingVote`
#: stores no IP, so a real IP here adds no new join against the ballot table.
#: That argues it is safe. It is still a decision, and it should be made on
#: purpose rather than by a sweep.
#:
#: This list must shrink to empty. It is here so the open question is visible in
#: the diff instead of living in someone's memory.
PENDING_DECISION = {
    'view/slating/apply.py',
    'view/slating/applications_review.py',
    'view/slating/form_builder.py',
    'view/slating/interview_manager.py',
    'view/slating/period_setup.py',
    'view/slating/position_manager.py',
    'view/slating/results.py',
    'view/slating/slate_builder.py',
    'view/slating/transition.py',
    'view/slating/vote.py',
}

#: Test modules legitimately BUILD requests carrying these headers rather than
#: reading them to decide something. Scanning them would flag every fixture.
def _is_test_module(rel):
    return rel.startswith('test_') or '/test_' in rel


def proxy_header_reads(tree):
    """
    Line numbers of every `…META.get('HTTP_X_FORWARDED_FOR')` /
    `…META['REMOTE_ADDR']` style READ.

    Deliberately looks for the header *string* in a subscript or `.get()` on
    something named `META`, rather than for the string anywhere in the file —
    a docstring that mentions `X-Forwarded-For` (this file, and the helper's own
    explanation) is not a read, and a guard that cannot tell those apart gets
    silenced rather than obeyed.
    """
    hits = []
    for node in ast.walk(tree):
        target = None
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == 'get' and node.args:
            target = (node.func.value, node.args[0])
        elif isinstance(node, ast.Subscript):
            target = (node.value, node.slice)
        if not target:
            continue

        container, key = target
        if not (isinstance(key, ast.Constant) and key.value in PROXY_HEADERS):
            continue
        # `request.META`, `req.META`, `self.request.META`, …
        if isinstance(container, ast.Attribute) and container.attr == 'META':
            hits.append((node.lineno, key.value))
    return hits


class ClientIpSingleSourceTests(SimpleTestCase):

    def test_only_the_helper_reads_the_proxy_headers(self):
        offenders = []
        for path in sorted(SRC_ROOT.rglob('*.py')):
            rel = str(path.relative_to(SRC_ROOT))
            if rel in ALLOWED or _is_test_module(rel) or '__pycache__' in rel:
                continue
            if rel in PENDING_DECISION:
                continue
            try:
                tree = ast.parse(path.read_text(encoding='utf-8', errors='replace'))
            except SyntaxError:
                continue
            for lineno, header in proxy_header_reads(tree):
                offenders.append(f'  {rel}:{lineno}  reads {header}')

        self.assertEqual(
            offenders, [],
            'Something other than `get_client_ip` is reading the proxy chain:\n\n'
            + '\n'.join(offenders)
            + '\n\nUse `from src.utils.security_utils import get_client_ip`.\n'
            'That function is the only place that knows BEHIND_CLOUDFLARE means '
            'the visitor is in CF-Connecting-IP, and that "rightmost XFF entry" '
            'is safe with nginx alone and WRONG behind Cloudflare. Five copies '
            'of this logic silently logged the Cloudflare edge into the audit '
            'log, session records and the lockdown whitelist suggestion until '
            '08-06-26. If you genuinely need raw header access, add the file to '
            'ALLOWED in this test and say why — deliberately, in the diff.',
        )

    def test_the_pending_list_has_no_dead_entries(self):
        """
        ⚠️ Keeps `PENDING_DECISION` honest in BOTH directions.

        An exemption list that outlives the thing it exempts is how a temporary
        allowance becomes permanent silently — CLAUDE.md's most expensive
        recorded lesson is a claim about the future left in a document nobody
        revisits. So: every file listed must still actually contain a raw read.
        Fix one and this test tells you to delete its entry, in the same commit.
        """
        stale = []
        for rel in sorted(PENDING_DECISION):
            path = SRC_ROOT / rel
            if not path.exists():
                stale.append(f'  {rel} — file no longer exists')
                continue
            if not proxy_header_reads(ast.parse(path.read_text(errors='replace'))):
                stale.append(f'  {rel} — no raw read left; remove it from PENDING_DECISION')

        self.assertEqual(
            stale, [],
            'PENDING_DECISION has entries that no longer need exempting:\n'
            + '\n'.join(stale),
        )

    def test_the_detector_finds_a_planted_read(self):
        """
        ⚠️ NEGATIVE CONTROL. The test above passes by finding nothing, which is
        also what a broken detector does — and the whole reason those five
        copies survived is that nothing was looking. A guard nobody has seen
        fail is a guard nobody has seen work.
        """
        bad = ast.parse(
            'def f(request):\n'
            "    xff = request.META.get('HTTP_X_FORWARDED_FOR')\n"
            "    return xff or request.META['REMOTE_ADDR']\n"
        )
        found = proxy_header_reads(bad)
        self.assertEqual(len(found), 2, f'Detector missed a planted read: {found}')

    def test_the_detector_ignores_prose_and_correct_usage(self):
        """
        The other half of the control. A file that talks about X-Forwarded-For
        in a docstring, or calls the helper properly, must not be flagged —
        otherwise the guard becomes noise and gets deleted.
        """
        good = ast.parse(
            '"""We take HTTP_X_FORWARDED_FOR seriously; see get_client_ip."""\n'
            'from src.utils.security_utils import get_client_ip\n'
            'def f(request):\n'
            "    # REMOTE_ADDR is handled inside the helper\n"
            '    return get_client_ip(request)\n'
        )
        self.assertEqual(proxy_header_reads(good), [])

    def test_the_helper_still_honours_behind_cloudflare(self):
        """
        The guard above proves everyone calls one function. This proves that
        function is worth calling — otherwise centralising the bug is all we
        achieved.
        """
        from django.test import RequestFactory, override_settings

        from src.utils.security_utils import get_client_ip

        request = RequestFactory().get('/')
        request.META['HTTP_CF_CONNECTING_IP'] = '203.0.113.9'
        # What Cloudflare→nginx actually produces: visitor first, edge appended.
        request.META['HTTP_X_FORWARDED_FOR'] = '203.0.113.9, 172.70.231.106'
        request.META['REMOTE_ADDR'] = '127.0.0.1'

        with override_settings(BEHIND_CLOUDFLARE=True):
            self.assertEqual(
                get_client_ip(request), '203.0.113.9',
                'Behind Cloudflare the visitor IP must come from CF-Connecting-IP. '
                'Returning 172.70.231.106 is the exact bug: that is the edge.',
            )

        with override_settings(BEHIND_CLOUDFLARE=False):
            self.assertEqual(
                get_client_ip(request), '172.70.231.106',
                'Without Cloudflare, rightmost-XFF is correct and deliberate — '
                'nginx appends the socket peer, so leading entries are forgeable '
                'and the last one is not.',
            )
