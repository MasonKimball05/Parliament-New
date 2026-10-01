"""
New pledges join the current pledge class automatically (v3.39.1, 10-01-26).

Mason: "if someone is added as a pledge it auto adds them to the current (or
next) pc." Found because a whole pledge class had no house-map color: Add
Member never set `pledge_class`, and bulk import saved the raw text without
its Greek letter. The house map and directory badge need both.

Run with: python manage.py test src.tests.users.test_default_pledge_class
"""
import json
from datetime import date

from django.test import Client, TestCase
from django.urls import reverse

from src.models import ParliamentUser
from src.pledge_classes import current_class


def _officer():
    user = ParliamentUser.objects.create(
        user_id='9001', name='Officer', username='9001',
        member_type='Officer', member_status='Active',
    )
    user.set_password('default-class-test-pass-12345!')
    user.save()
    return user


class CurrentClassTests(TestCase):

    def test_fall_from_july(self):
        self.assertEqual(current_class(date(2026, 10, 1))['label'], 'Fall 2026')
        self.assertEqual(current_class(date(2026, 10, 1))['greek'], 'Theta')
        self.assertEqual(current_class(date(2027, 7, 1))['label'], 'Fall 2027')

    def test_spring_from_january(self):
        self.assertEqual(current_class(date(2027, 1, 5))['label'], 'Spring 2027')
        self.assertEqual(current_class(date(2027, 6, 30))['label'], 'Spring 2027')


class NewPledgeGetsTheCurrentClassTests(TestCase):

    def setUp(self):
        self.expected = current_class()

    def test_creating_a_pledge_sets_class_and_greek(self):
        p = ParliamentUser.objects.create(user_id='P-AAAAAA', name='P', username='pa',
                                          member_type='Pledge')
        p.refresh_from_db()
        self.assertEqual(p.pledge_class, self.expected['label'])
        self.assertEqual(p.pledge_class_greek, self.expected['greek'])

    def test_a_class_given_at_creation_is_kept(self):
        p = ParliamentUser.objects.create(user_id='P-BBBBBB', name='P', username='pb',
                                          member_type='Pledge', pledge_class='Spring 2024',
                                          pledge_class_greek='Gamma')
        p.refresh_from_db()
        self.assertEqual(p.pledge_class, 'Spring 2024')

    def test_non_pledges_are_untouched(self):
        m = ParliamentUser.objects.create(user_id='9100', name='M', username='m9100',
                                          member_type='Member')
        m.refresh_from_db()
        self.assertEqual(m.pledge_class, '')

    def test_a_later_save_does_not_reassign(self):
        """Clearing it on purpose after creation sticks."""
        p = ParliamentUser.objects.create(user_id='P-CCCCCC', name='P', username='pc',
                                          member_type='Pledge')
        p.pledge_class, p.pledge_class_greek = '', ''
        p.save()
        p.refresh_from_db()
        self.assertEqual(p.pledge_class, '')


class OfficerPathsTests(TestCase):

    def setUp(self):
        self.client = Client()
        self.client.force_login(_officer())
        self.expected = current_class()

    def test_add_member(self):
        response = self.client.post(reverse('add_member'), data={
            'name': 'Pat Pledge', 'user_id': 'P-ABC123', 'email': None,
            'member_type': 'Pledge', 'member_status': 'Active', 'roles': [],
        }, content_type='application/json')
        self.assertTrue(response.json()['success'])
        p = ParliamentUser.objects.get(user_id='P-ABC123')
        self.assertEqual((p.pledge_class, p.pledge_class_greek),
                         (self.expected['label'], self.expected['greek']))

    def test_edit_member_into_a_pledge(self):
        m = ParliamentUser.objects.create(user_id='9200', name='Was Member', username='m9200',
                                          member_type='Member')
        response = self.client.post(reverse('edit_member', args=[m.user_id]),
                                    data=json.dumps({'member_type': 'Pledge'}),
                                    content_type='application/json')
        self.assertEqual(response.status_code, 200)
        m.refresh_from_db()
        self.assertEqual(m.pledge_class, self.expected['label'])

    def test_bulk_import_blank_column_gets_the_current_class(self):
        from src.view.officer.manage_members import _import_member_row
        result = _import_member_row({'name': 'Bulk One', 'username': 'bulkone',
                                     'member_type': 'Pledge'}, ParliamentUser.objects.get(user_id='9001'))
        self.assertEqual(result['status'], 'created', result)
        p = ParliamentUser.objects.get(username='bulkone')
        self.assertEqual(p.pledge_class_greek, self.expected['greek'])

    def test_bulk_import_shorthand_is_canonicalized_with_greek(self):
        from src.view.officer.manage_members import _import_member_row
        _import_member_row({'name': 'Bulk Two', 'username': 'bulktwo', 'member_type': 'Pledge',
                            'pledge_class': 'sp24'}, ParliamentUser.objects.get(user_id='9001'))
        p = ParliamentUser.objects.get(username='bulktwo')
        self.assertEqual((p.pledge_class, p.pledge_class_greek), ('Spring 2024', 'Gamma'))
