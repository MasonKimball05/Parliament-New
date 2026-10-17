"""
v3.50.0 — officer/admin member edits use the same text rules as a member's own
profile, and nothing saves a spreadsheet formula into a name or phone.
"""
import json

from django.test import Client, SimpleTestCase, TestCase
from django.urls import reverse

from src.models import ParliamentUser
from src.profile_text import looks_like_formula, member_fields_error, phone_error


def make_user(uid, member_type='Member', **kw):
    u = ParliamentUser.objects.create(user_id=uid, username=uid, name=f'User {uid}',
                                      member_type=member_type, member_status='Active', **kw)
    u.set_password('member-edit-test-pass-12345!')
    u.save()
    return u


class RulesTests(SimpleTestCase):

    def test_formulas(self):
        for v in ('=1+1', '=HYPERLINK("x")', '@SUM(A1)', '+cmd', '-x'):
            self.assertTrue(looks_like_formula(v), v)
        for v in ('Mason', '+1 (205) 555-0100', '-3.5', ''):
            self.assertFalse(looks_like_formula(v), v)
        self.assertFalse(looks_like_formula('@masonk', handle=True))
        self.assertTrue(looks_like_formula('@SUM(1)', handle=True))

    def test_phone(self):
        for v in ('205-555-0100', '+1 (205) 555 0100', '205.555.0100 ext 12', ''):
            self.assertEqual(phone_error(v), '', v)
        for v in ('call me', '=1', '555-0100<b>'):
            self.assertNotEqual(phone_error(v), '', v)


class OfficerEditTests(TestCase):

    def setUp(self):
        self.officer = make_user('ME-OFF', 'Officer')
        self.admin = make_user('ME-ADM', 'Officer', is_admin=True)
        self.member = make_user('ME-MEM')
        self.c = Client(); self.c.force_login(self.officer)

    def edit(self, user, **data):
        return self.c.post(reverse('edit_member', args=[user.user_id]), json.dumps(data),
                           content_type='application/json')

    def test_formula_name_is_refused(self):
        r = self.edit(self.member, name='=HYPERLINK("http://x","click")')
        self.assertEqual(r.status_code, 400)
        self.assertEqual(ParliamentUser.objects.get(pk=self.member.pk).name, 'User ME-MEM')

    def test_markup_and_bad_choices_are_refused(self):
        self.assertEqual(self.edit(self.member, preferred_name='<b>x</b>').status_code, 400)
        self.assertEqual(self.edit(self.member, member_type='Superuser').status_code, 400)
        self.assertEqual(self.edit(self.member, member_status='Ghost').status_code, 400)
        self.assertEqual(self.edit(self.member, email='not-an-email').status_code, 400)
        self.assertEqual(self.edit(self.member, name='   ').status_code, 400)

    def test_a_normal_edit_still_works(self):
        r = self.edit(self.member, name='Mason Kimball', preferred_name='Mase', member_type='Member')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(ParliamentUser.objects.get(pk=self.member.pk).name, 'Mason Kimball')

    def test_an_officer_cannot_edit_an_admin(self):
        r = self.edit(self.admin, email='officer@example.com')
        self.assertEqual(r.status_code, 403)
        self.assertIsNone(ParliamentUser.objects.get(pk=self.admin.pk).email)

    def test_an_admin_can_edit_an_admin(self):
        c = Client(); c.force_login(self.admin)
        other = make_user('ME-ADM2', 'Officer', is_admin=True)
        r = c.post(reverse('edit_member', args=[other.user_id]), json.dumps({'preferred_name': 'Al'}),
                   content_type='application/json')
        self.assertEqual(r.status_code, 200, r.content)

    def test_bulk_import_row_is_checked(self):
        from src.view.officer.manage_members import _import_member_row
        result = _import_member_row({'name': 'Real Name', 'username': 'realname', 'member_type': 'Pledge',
                                     'phone_number': '=1+1'}, self.officer)
        self.assertEqual(result['status'], 'error')
        self.assertFalse(ParliamentUser.objects.filter(username='realname').exists())


class OwnProfileTests(TestCase):

    def setUp(self):
        self.member = make_user('ME-OWN')
        self.c = Client(); self.c.force_login(self.member)

    def post(self, **fields):
        data = {'profile_submit': '1', 'username': self.member.username, 'email': '', 'phone_number': '',
                'preferred_name': ''}
        data.update(fields)
        return self.c.post(reverse('profile'), data)

    def test_formula_phone_is_refused(self):
        self.post(phone_number='=HYPERLINK("x")')
        self.assertEqual(ParliamentUser.objects.get(pk=self.member.pk).phone_number, '')

    def test_old_odd_phone_does_not_block_other_edits(self):
        ParliamentUser.objects.filter(pk=self.member.pk).update(phone_number='call me')
        self.post(phone_number='call me', preferred_name='Mase')
        self.assertEqual(ParliamentUser.objects.get(pk=self.member.pk).preferred_name, 'Mase')

    def test_member_fields_error_helper(self):
        self.assertEqual(member_fields_error({'name': 'Ok', 'member_type': 'Pledge'}), '')
