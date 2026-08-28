"""
v3.27.0 — a password change now revokes every OTHER session immediately,
rather than leaving them to Django's own session-auth-hash check to reject on
their next use.

WHY THIS EXISTS
---------------
`update_session_auth_hash` (used by both `change_password` and
`forced_password_change`) was never broken — Django rejects any *other*
session the moment it is next used, because its stored auth hash no longer
matches. What was missing: nothing told `UserSession` (the Active Sessions
display table) or the actual `django_session` row for that other device that
anything had happened, so a member who just changed a password because they
suspected someone else had it would still see that device listed as an active
session, and the real session row would sit there — unusable, but present —
until it expired on its own or a cleanup task swept it up.

`UserSession.revoke_other_sessions` (src/models/users.py) is the single place
this now happens; `session_viewer.revoke_all_other_sessions` (the existing
"log out everywhere else" button) was refactored to call the same method
rather than keeping its own copy of the same three lines.

These tests build a SECOND real, valid Django session for the same user (not
just a second `UserSession` display row) so that "the other device is
actually logged out" is checked against the real session store, not just
against the display table agreeing with itself.
"""
from django.contrib.auth import BACKEND_SESSION_KEY, HASH_SESSION_KEY, SESSION_KEY
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.test import Client, TestCase
from django.urls import reverse

from src.models import ParliamentUser, UserSession


def make_user(uid='pwrevoke-user', **kwargs):
    defaults = dict(
        name='Password Revoke User', username=uid,
        member_type='Member', member_status='Active',
    )
    defaults.update(kwargs)
    user = ParliamentUser.objects.create(user_id=uid, **defaults)
    user.set_password('old-password-12345!')
    user.save()
    return user


def make_other_device_session(user):
    """
    A second, genuinely valid Django session for `user` — not the test
    client's own session. Mirrors what Django itself writes at login:
    SESSION_KEY, BACKEND_SESSION_KEY and HASH_SESSION_KEY, the last of which
    is exactly what `update_session_auth_hash` changes and what makes this
    session go stale on its own even if nothing else touched it. A matching
    `UserSession` row is created too, since that's the display record a
    member actually sees on /account/sessions/.
    """
    store = SessionStore()
    store[SESSION_KEY] = str(user.pk)
    store[BACKEND_SESSION_KEY] = 'django.contrib.auth.backends.ModelBackend'
    store[HASH_SESSION_KEY] = user.get_session_auth_hash()
    store.save()

    UserSession.objects.create(
        user=user,
        session_key=store.session_key,
        ip_address='203.0.113.9',
        device_type='mobile',
        browser='Safari',
        operating_system='iOS',
    )
    return store.session_key


class PasswordChangeRevokesOtherSessionsTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.client = Client()
        self.client.force_login(self.user)
        # force_login does not go through SessionTrackingMiddleware, so the
        # current session has no UserSession row yet. One real request does.
        self.client.get(reverse('home'))
        self.current_session_key = self.client.session.session_key

    def test_change_password_revokes_the_other_devices_session_and_record(self):
        other_key = make_other_device_session(self.user)
        self.assertTrue(Session.objects.filter(session_key=other_key).exists())
        self.assertTrue(UserSession.objects.filter(session_key=other_key).exists())

        response = self.client.post(reverse('change_password'), {
            'old_password': 'old-password-12345!',
            'new_password1': 'Brand-New-Password-98765!',
            'new_password2': 'Brand-New-Password-98765!',
        })
        self.assertEqual(response.status_code, 302)

        self.assertFalse(
            Session.objects.filter(session_key=other_key).exists(),
            'The other device\'s real Django session row survived a password '
            'change — it would have been rejected on its next request '
            'anyway, but the row itself should be gone immediately.',
        )
        self.assertFalse(
            UserSession.objects.filter(session_key=other_key).exists(),
            'The other device still appears on the Active Sessions list '
            'after a password change that should have removed it.',
        )

    def test_change_password_does_not_revoke_the_current_session(self):
        # NOTE: `update_session_auth_hash` calls `request.session.cycle_key()`
        # as session-fixation protection, so the session_key itself changes
        # across this request — asserting the OLD key's row survives would be
        # wrong (Django rotates it away on purpose, independent of anything
        # this change added). The actual thing to prove is behavioural: the
        # browser is still authenticated afterwards, using its own cookie jar
        # (which the test client updates from the Set-Cookie header, exactly
        # like a real browser).
        make_other_device_session(self.user)

        self.client.post(reverse('change_password'), {
            'old_password': 'old-password-12345!',
            'new_password1': 'Brand-New-Password-98765!',
            'new_password2': 'Brand-New-Password-98765!',
        })

        response = self.client.get(reverse('profile'))
        self.assertEqual(
            response.status_code, 200,
            'The current browser was logged out by its own password change — '
            'update_session_auth_hash exists precisely to prevent this, and '
            'the new revoke-other-sessions call must key off the '
            'POST-THE-CHANGE session, not a snapshot taken beforehand.',
        )

    def test_change_password_with_no_other_sessions_does_not_error(self):
        response = self.client.post(reverse('change_password'), {
            'old_password': 'old-password-12345!',
            'new_password1': 'Brand-New-Password-98765!',
            'new_password2': 'Brand-New-Password-98765!',
        })
        self.assertEqual(response.status_code, 302)


class ForcedPasswordChangeRevokesOtherSessionsTests(TestCase):
    def setUp(self):
        self.user = make_user(uid='pwrevoke-forced-user', force_password_change=True)
        self.client = Client()
        self.client.force_login(self.user)
        self.client.get(reverse('forced_password_change'))

    def test_forced_password_change_revokes_the_other_devices_session(self):
        other_key = make_other_device_session(self.user)

        response = self.client.post(reverse('forced_password_change'), {
            'old_password': 'old-password-12345!',
            'new_password1': 'Brand-New-Password-98765!',
            'new_password2': 'Brand-New-Password-98765!',
        })
        self.assertEqual(response.status_code, 302)

        self.assertFalse(Session.objects.filter(session_key=other_key).exists())
        self.assertFalse(UserSession.objects.filter(session_key=other_key).exists())

    def test_forced_password_change_does_not_revoke_the_current_session(self):
        # See the identical note in the change_password test above — the
        # session key itself is rotated by update_session_auth_hash, so the
        # behavioural check (still authenticated afterwards) is the correct
        # one, not "the same session_key string still has a row".
        make_other_device_session(self.user)

        self.client.post(reverse('forced_password_change'), {
            'old_password': 'old-password-12345!',
            'new_password1': 'Brand-New-Password-98765!',
            'new_password2': 'Brand-New-Password-98765!',
        })

        response = self.client.get(reverse('profile'))
        self.assertEqual(response.status_code, 200)


class RevokeOtherSessionsClassmethodTests(TestCase):
    """Direct tests of UserSession.revoke_other_sessions, independent of any view."""

    def setUp(self):
        self.user = make_user(uid='pwrevoke-classmethod-user')

    def test_keep_session_key_excludes_that_one_session(self):
        keep_key = make_other_device_session(self.user)
        drop_key = make_other_device_session(self.user)

        count = UserSession.revoke_other_sessions(self.user, keep_session_key=keep_key)

        self.assertEqual(count, 1)
        self.assertTrue(Session.objects.filter(session_key=keep_key).exists())
        self.assertTrue(UserSession.objects.filter(session_key=keep_key).exists())
        self.assertFalse(Session.objects.filter(session_key=drop_key).exists())
        self.assertFalse(UserSession.objects.filter(session_key=drop_key).exists())

    def test_no_keep_key_revokes_everything(self):
        make_other_device_session(self.user)
        make_other_device_session(self.user)

        count = UserSession.revoke_other_sessions(self.user)

        self.assertEqual(count, 2)
        self.assertEqual(UserSession.objects.filter(user=self.user).count(), 0)

    def test_another_users_sessions_are_left_alone(self):
        other_user = make_user(uid='pwrevoke-bystander-user')
        bystander_key = make_other_device_session(other_user)
        make_other_device_session(self.user)

        UserSession.revoke_other_sessions(self.user)

        self.assertTrue(
            Session.objects.filter(session_key=bystander_key).exists(),
            "Revoking one user's other sessions touched a different user's "
            "session — the filter must be scoped to `user`.",
        )

    def test_zero_other_sessions_returns_zero_and_does_not_error(self):
        self.assertEqual(UserSession.revoke_other_sessions(self.user), 0)


class RevokeAllOtherSessionsViewStillWorksTests(TestCase):
    """
    Regression coverage for session_viewer.revoke_all_other_sessions after its
    refactor onto UserSession.revoke_other_sessions — nothing existed here
    before v3.27.0.
    """

    def setUp(self):
        self.user = make_user(uid='pwrevoke-view-user')
        self.client = Client()
        self.client.force_login(self.user)
        self.client.get(reverse('home'))

    def test_the_button_still_revokes_other_sessions_and_keeps_this_one(self):
        other_key = make_other_device_session(self.user)
        current_key = self.client.session.session_key

        response = self.client.post(reverse('revoke_all_sessions'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['revoked_count'], 1)

        self.assertFalse(Session.objects.filter(session_key=other_key).exists())
        self.assertTrue(Session.objects.filter(session_key=current_key).exists())
