"""
v3.52.0 follow-up (10-26-26 auto-run): ``tasks.prune_expired_mobile_auth_tickets``.

``MobileAuthTicket`` rows are minted on every web-view open, socket, 2FA step
and (unauthenticated) passkey begin, and nothing deleted them. The task keeps
anything that expired less than a day ago and deletes the rest.

Run with: python manage.py test src.tests.mobile.test_ticket_prune
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from src.management.commands.setup_celery_schedules import SCHEDULES
from src.models.mobile import MobileAuthTicket
from src.tasks.cleanup import prune_expired_mobile_auth_tickets
from src.tests.mobile.base import make_user


class PruneExpiredMobileAuthTicketsTests(TestCase):
    def _ticket(self, purpose, expired_ago, user=None, used=False):
        _raw, t = MobileAuthTicket.issue(purpose, user=user)
        MobileAuthTicket.objects.filter(pk=t.pk).update(
            expires_at=timezone.now() - expired_ago,
            used_at=timezone.now() if used else None)
        return t

    def test_deletes_only_tickets_expired_over_a_day_ago(self):
        user = make_user('prune1')
        old_unused = self._ticket(MobileAuthTicket.PURPOSE_PASSKEY_LOGIN, timedelta(days=2))
        old_used = self._ticket(MobileAuthTicket.PURPOSE_MFA, timedelta(days=3), user=user, used=True)
        recent = self._ticket(MobileAuthTicket.PURPOSE_WS, timedelta(hours=2), user=user, used=True)
        _raw, live = MobileAuthTicket.issue(MobileAuthTicket.PURPOSE_WEB_HANDOFF, user=user)

        self.assertEqual(prune_expired_mobile_auth_tickets(), 2)

        left = set(MobileAuthTicket.objects.values_list('pk', flat=True))
        self.assertEqual(left, {recent.pk, live.pk})
        self.assertNotIn(old_unused.pk, left)
        self.assertNotIn(old_used.pk, left)

    def test_live_ticket_still_redeemable_after_prune(self):
        user = make_user('prune2')
        raw, _t = MobileAuthTicket.issue(MobileAuthTicket.PURPOSE_MFA, user=user)
        prune_expired_mobile_auth_tickets()
        self.assertIsNotNone(MobileAuthTicket.consume(MobileAuthTicket.PURPOSE_MFA, raw))

    def test_nothing_to_do_returns_zero(self):
        self.assertEqual(prune_expired_mobile_auth_tickets(), 0)

    def test_is_scheduled(self):
        tasks = {s['task'] for s in SCHEDULES}
        self.assertIn('tasks.prune_expired_mobile_auth_tickets', tasks)
