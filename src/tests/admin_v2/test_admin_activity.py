"""v4.1.0 admin activity feed: admin actions only, routine rows and non-admins left out."""
from django.urls import reverse

from src.models import ActivityLog
from src.tests.admin_v2.base import AdminV2TestCase, make_user


class AdminActivityTests(AdminV2TestCase):

    def setUp(self):
        super().setUp()
        self.member = make_user('act-member')
        ActivityLog.log_activity(action_type='user_role_changed', user=self.admin,
                                 description='Admin gave Historian to act-member',
                                 object_type='ParliamentUser', object_id='act-member', object_repr='Act Member')
        ActivityLog.log_activity(action_type='login', user=self.admin, description='Admin logged in')
        ActivityLog.log_activity(action_type='user_edited', user=self.member, description='Member edited something')

    def test_only_admin_actions(self):
        r = self.client.get(reverse('admin_v2_admin_activity'))
        descriptions = [log.description for log in r.context['page']]
        self.assertEqual(descriptions, ['Admin gave Historian to act-member'])
        self.assertContains(r, reverse('admin_v2_member', args=['act-member']))

    def test_filters(self):
        r = self.client.get(reverse('admin_v2_admin_activity'), {'action': 'user_edited'})
        self.assertEqual(list(r.context['page']), [])
        r = self.client.get(reverse('admin_v2_admin_activity'), {'q': 'historian'})
        self.assertEqual(len(r.context['page']), 1)
