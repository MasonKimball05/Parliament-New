"""Shared setup for admin-v2 tests: an allowlisted admin with an admin-v2 session."""
from unittest import mock

from django.test import Client, TestCase
from django.utils import timezone

from src.models import ParliamentUser
from src.view import admin_v2

ADMIN_ID = 'av2-admin'


def make_user(uid, **kwargs):
    defaults = dict(name=uid.replace('-', ' ').title(), username=uid, member_type='Member',
                    member_status='Active')
    defaults.update(kwargs)
    return ParliamentUser.objects.create_user(user_id=uid, password='pw-for-tests-1', **defaults)


class AdminV2TestCase(TestCase):
    def setUp(self):
        self.admin = make_user(ADMIN_ID, is_admin=True, member_type='Officer')
        patcher = mock.patch.object(admin_v2, 'ALLOWED_USER_IDS', {ADMIN_ID})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = Client()
        self.client.force_login(self.admin)
        session = self.client.session
        session['admin_v2_authenticated'] = True
        session['admin_v2_auth_time'] = timezone.now().isoformat()
        session.save()
