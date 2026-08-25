"""
v3.19.2 — the per-user "Release Login Lockout" button, and the bug it exposed.

⚠️ THE POINT OF THIS FILE: a lockout-clearing button that reports success while
leaving the member locked out is worse than no button. Two ways that happened:

1. **Two lockout systems, two key schemes.** `login_view.py` writes
   `account_login_lockout_{user}`; `LoginRateLimitMiddleware` writes
   `login_lockout_user_{user}`. The admin's existing "Clear lockout" action
   deleted six of the nine keys, under a comment reading *"Clear cache keys for
   all three systems"* — and the three it missed were the whole `account_*`
   family, i.e. the lockout an ordinary member hits by mistyping his password.

2. **The attempt counter re-locks instantly.** Delete the lockout key but leave
   the counter at its threshold and the next failed attempt locks the account
   again. Indistinguishable from the button not working, except it says it
   worked.

So these tests assert on the OBSERVABLE state — "is this account still locked
according to the code that does the locking" — rather than on which keys were
deleted. Asserting the key list would pass for a helper that deletes nine keys
nobody reads.
"""

from unittest.mock import patch

from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from src.view import admin_v2 as admin_v2_module

from src.models import LoginLockout, ParliamentUser
from src.utils.security_utils import clear_lockouts_for
from src.view.login_view import (
    get_account_attempts_key,
    get_account_lockout_key,
    is_account_locked,
)


def make_user(uid='lockout-user', **kwargs):
    defaults = dict(
        name='Locked Out', username=uid,
        member_type='Member', member_status='Active',
    )
    defaults.update(kwargs)
    user = ParliamentUser.objects.create(user_id=uid, **defaults)
    user.set_password('lockout-test-pass-12345!')
    user.save()
    return user


class ClearLockoutsHelperTests(TestCase):
    """`clear_lockouts_for` — the one place that knows every key."""

    def setUp(self):
        cache.clear()
        self.username = 'xguill'

    def _lock_the_account_the_way_login_view_does(self):
        cache.set(get_account_lockout_key(self.username), timezone.now(), 1800)
        cache.set(get_account_attempts_key(self.username), 5, 900)

    def _lock_the_account_the_way_middleware_does(self):
        cache.set(f'login_lockout_user_{self.username}', True, 1800)
        cache.set(f'login_attempts_user_{self.username}', 5, 900)

    def test_it_releases_the_account_lockout_login_view_sets(self):
        """
        ⚠️ THE REGRESSION TEST FOR THE ACTUAL BUG. The old admin code did not
        touch these keys, so this is the assertion it would fail.
        """
        self._lock_the_account_the_way_login_view_does()
        locked, _ = is_account_locked(self.username)
        self.assertTrue(locked, 'Fixture did not actually lock the account.')

        clear_lockouts_for(username=self.username)

        locked, _ = is_account_locked(self.username)
        self.assertFalse(
            locked,
            "The account is still locked after clearing. This is the "
            "`account_*` key family that the admin's old six-key list missed.",
        )

    def test_it_releases_the_middleware_username_lockout(self):
        self._lock_the_account_the_way_middleware_does()
        clear_lockouts_for(username=self.username)
        self.assertIsNone(cache.get(f'login_lockout_user_{self.username}'))

    def test_it_clears_the_attempt_counters_too(self):
        """
        ⚠️ THE SUBTLE HALF. A lockout key removed while the counter still sits
        at the threshold means the next failed attempt re-locks immediately —
        the button appears to work and does not.
        """
        self._lock_the_account_the_way_login_view_does()
        self._lock_the_account_the_way_middleware_does()

        clear_lockouts_for(username=self.username)

        self.assertIsNone(
            cache.get(get_account_attempts_key(self.username)),
            'The account attempt counter survived; one more failed login and '
            'they are locked out again.',
        )
        self.assertIsNone(cache.get(f'login_attempts_user_{self.username}'))

    def test_it_marks_lockout_rows_cleared(self):
        admin = make_user('clearing-admin', is_admin=True)
        row = LoginLockout.objects.create(
            ip_address='198.51.100.7', username=self.username,
            source='middleware_user',
            expires_at=timezone.now() + timezone.timedelta(minutes=30),
        )
        clear_lockouts_for(username=self.username, cleared_by=admin)

        row.refresh_from_db()
        self.assertTrue(row.is_cleared)
        self.assertEqual(row.cleared_by, admin)
        self.assertIsNotNone(row.cleared_at)

    def test_clearing_by_username_does_not_release_an_ip_lockout(self):
        """
        A member's lockout is on his account. His IP may be shared — campus
        NAT, or a Cloudflare edge in any row written before v3.18.8 — so
        releasing one member must not unlock an address used by others.
        """
        cache.set('login_lockout_203.0.113.5', True, 1800)
        clear_lockouts_for(username=self.username)
        self.assertTrue(
            cache.get('login_lockout_203.0.113.5'),
            'Clearing a username lockout also released an IP lockout, which '
            'would unlock every member behind that address.',
        )

    def test_clearing_an_absent_lockout_is_harmless(self):
        """The button is safe to press on a user who is not locked out."""
        result = clear_lockouts_for(username='never-locked')
        self.assertEqual(result['lockout_rows'], 0)


class ReleaseLockoutViewTests(TestCase):
    """The button itself."""

    def setUp(self):
        cache.clear()
        self.admin = make_user('lockout-admin', is_admin=True)
        self.member = make_user('xguill', name='Xander Guill')
        self.url = reverse(
            'admin_v2_release_user_lockout', args=[self.member.user_id]
        )
        self.client = Client()

    def _login_admin(self):
        """
        `require_admin_v2_auth` is a TWO-factor gate: `user_id` must be in the
        env-driven `ALLOWED_USER_IDS` allowlist **and** a separate admin-v2
        session must be active. Patching only one of them gives a redirect that
        looks like the view refusing, which is how a green-but-meaningless test
        gets written here. Same approach as
        `TwoFactorDashboardQueryBudgetTests` in `test_query_budgets.py`.
        """
        patcher = patch.object(
            admin_v2_module, 'ALLOWED_USER_IDS', {self.admin.user_id}
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        self.client.force_login(self.admin)
        session = self.client.session
        session['admin_v2_authenticated'] = True
        session['admin_v2_auth_time'] = timezone.now().isoformat()
        session.save()

    def test_it_releases_the_lockout(self):
        cache.set(get_account_lockout_key(self.member.username), timezone.now(), 1800)
        cache.set(get_account_attempts_key(self.member.username), 5, 900)

        self._login_admin()
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 302)

        locked, _ = is_account_locked(self.member.username)
        self.assertFalse(locked)

    def test_it_refuses_GET(self):
        """State-changing, so POST-only — `@require_POST` on the view."""
        self._login_admin()
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_a_non_admin_cannot_release_a_lockout(self):
        """
        The negative control that matters: this button unlocks accounts, so an
        ordinary member reaching it would be a way to undo the brute-force
        protection entirely.

        ⚠️ v3.19.7 — THIS TEST WAS UNPASSABLE BY CONSTRUCTION AND HAD NEVER
        PASSED. It asserted `assertNotEqual(status, 302)` and then, three lines
        later, `assertIn(status, (302, 403, 404))` — 302 forbidden and permitted
        by the same test. It could only go green if the denial path returned 403
        or 404, and it does not: `require_admin_v2_auth` answers a
        non-allowlisted user with `messages.error(...)` + `redirect('home')`.

        **The endpoint is not vulnerable — verified by reading the decorator,
        and now by the assertion below.** The defect was in the test, and the
        cost of it was that the only guard on an account-unlock endpoint sat red
        in the suite being read as "pre-existing".

        THE FIX, AND IT IS THIS REPO'S OWN RULE: *an assertion that cannot
        distinguish the bug from the fixture is not an assertion.* A denial that
        redirects and a success that redirects are the same integer, so the
        status code cannot answer the question at all. **Assert the effect.**
        The lockout either survived the outsider's POST or it did not, and that
        is true regardless of what the view returns, what middleware wraps it,
        or whether someone later changes the denial to a 403.
        """
        cache.set(get_account_lockout_key(self.member.username), timezone.now(), 1800)
        cache.set(get_account_attempts_key(self.member.username), 5, 900)

        outsider = make_user('not-an-admin')
        self.client.force_login(outsider)
        self.client.post(self.url)

        locked, _ = is_account_locked(self.member.username)
        self.assertTrue(
            locked,
            'A non-admin POST cleared the account lockout. The brute-force '
            'protection can be undone by any logged-in member.',
        )

    def test_the_denial_is_not_mistaken_for_a_success(self):
        """
        v3.19.7 — the companion control, and the reason the test above no longer
        looks at the status code.

        The positive test (`test_it_releases_the_lockout`) asserts a 302 on
        success. The denial is ALSO a 302. So this records, once and explicitly,
        that the two are indistinguishable by status — a fact that made the
        previous negative control unpassable, and that would otherwise be
        rediscovered by the next person who writes one.

        If the denial path is ever changed to a 403, this test fails and should
        be updated rather than deleted: the point is that somebody has decided
        what the denial looks like, not that it looks like this forever.
        """
        outsider = make_user('denial-shape')
        self.client.force_login(outsider)
        response = self.client.post(self.url)
        self.assertEqual(
            response.status_code, 302,
            'The admin-v2 gate refuses by redirecting. If that changed, the '
            'negative control above should assert the new shape too.',
        )

    def test_it_writes_an_audit_row(self):
        from src.models import AdminActionLog

        self._login_admin()
        self.client.post(self.url)

        entry = AdminActionLog.objects.filter(
            action='account_unlocked', target_user=self.member
        ).first()
        self.assertIsNotNone(
            entry, 'Releasing a lockout left no audit trail.'
        )
        self.assertEqual(entry.actor, self.admin)
