"""
Tests for Parliament 3.0 — Pillar 3: Member Experience Overhaul

Covers:
  - Member directory view: authentication requirement, sort parameter handling,
    filter_type in context, grouping by member type, alumni toggle
  - Directory export: CSV/TXT/XLSX output, column selection, defaults vs
    explicit form submission
  - Officer/role transition tools: auth, GET render, atomic swap, member_type
    cascade, grants_admin flag, validation errors

Run with:
    python manage.py test src.test_pillar3 --settings=ci_settings
"""

from django.test import TestCase, Client, override_settings
from django.urls import reverse

from src.models import ParliamentUser, Role


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_user(user_id, member_type='Member', member_status='Active', name=None, **kwargs):
    """Create a ParliamentUser with sensible defaults."""
    defaults = dict(
        name=name or f'User {user_id}',
        username=f'user_{user_id}',
        member_type=member_type,
        member_status=member_status,
    )
    defaults.update(kwargs)
    return ParliamentUser.objects.create_user(user_id=user_id, password='testpass123', **defaults)


# ===========================================================================
# 1. MEMBER DIRECTORY — AUTHENTICATION
# ===========================================================================

class DirectoryAuthTests(TestCase):
    """Unauthenticated requests must be redirected to login."""

    def test_directory_requires_login(self):
        response = self.client.get(reverse('member_directory'))
        self.assertIn(response.status_code, [302, 301])
        self.assertIn('/login', response['Location'])

    def test_export_requires_login(self):
        response = self.client.get(reverse('export_directory'))
        self.assertIn(response.status_code, [302, 301])
        self.assertIn('/login', response['Location'])


# ===========================================================================
# 2. MEMBER DIRECTORY — GROUPING & CONTEXT
# ===========================================================================

class DirectoryGroupingTests(TestCase):
    """Members are placed in the correct context groups."""

    def setUp(self):
        self.client = Client()
        self.officer = make_user('off1', member_type='Officer', name='Alpha Officer')
        self.chair = make_user('chr1', member_type='Chair', name='Beta Chair')
        self.member = make_user('mem1', member_type='Member', name='Gamma Member')
        self.pledge = make_user('plg1', member_type='Pledge', name='Delta Pledge')
        self.advisor = make_user('adv1', member_type='Advisor', name='Epsilon Advisor')
        self.alumni = make_user('alm1', member_type='Member', member_status='Alumni', name='Zeta Alum')
        self.client.login(username='user_off1', password='testpass123')

    def test_officers_in_context(self):
        response = self.client.get(reverse('member_directory'))
        self.assertEqual(response.status_code, 200)
        self.assertIn(self.officer, response.context['officers'])

    def test_chairs_in_context(self):
        response = self.client.get(reverse('member_directory'))
        self.assertIn(self.chair, response.context['chairs'])

    def test_members_in_context(self):
        response = self.client.get(reverse('member_directory'))
        self.assertIn(self.member, response.context['members'])

    def test_pledges_in_context(self):
        response = self.client.get(reverse('member_directory'))
        self.assertIn(self.pledge, response.context['pledges'])

    def test_advisors_in_context(self):
        response = self.client.get(reverse('member_directory'))
        self.assertIn(self.advisor, response.context['advisors'])

    def test_advisor_not_in_regular_members(self):
        response = self.client.get(reverse('member_directory'))
        self.assertNotIn(self.advisor, response.context['members'])

    def test_alumni_hidden_by_default(self):
        response = self.client.get(reverse('member_directory'))
        self.assertEqual(response.context['alumni'], [])
        self.assertFalse(response.context['show_alumni'])

    def test_alumni_shown_with_toggle(self):
        response = self.client.get(reverse('member_directory') + '?show_alumni=1')
        self.assertIn(self.alumni, response.context['alumni'])
        self.assertTrue(response.context['show_alumni'])

    def test_alumni_not_in_active_members(self):
        response = self.client.get(reverse('member_directory') + '?show_alumni=1')
        self.assertNotIn(self.alumni, response.context['members'])

    def test_total_count_excludes_alumni(self):
        response = self.client.get(reverse('member_directory') + '?show_alumni=1')
        # total_count = active members (officer + chair + member + pledge) + advisors
        self.assertEqual(response.context['total_count'], 5)


# ===========================================================================
# 3. MEMBER DIRECTORY — SORT
# ===========================================================================

class DirectorySortTests(TestCase):
    """Sort parameter is passed through to context and applied correctly."""

    def setUp(self):
        self.client = Client()
        self.u1 = make_user('s1', member_type='Member', name='Charlie')
        self.u2 = make_user('s2', member_type='Member', name='Alice')
        self.u3 = make_user('s3', member_type='Member', name='Bob', role_number=5)
        self.u4 = make_user('s4', member_type='Member', name='Dave', role_number=2)
        self.client.login(username='user_s1', password='testpass123')

    def test_default_sort_is_name(self):
        response = self.client.get(reverse('member_directory'))
        self.assertEqual(response.context['sort'], 'name')

    def test_sort_name_asc(self):
        response = self.client.get(reverse('member_directory') + '?sort=name')
        names = [m.name for m in response.context['members']]
        self.assertEqual(names, sorted(names))

    def test_sort_name_desc(self):
        response = self.client.get(reverse('member_directory') + '?sort=name_desc')
        self.assertEqual(response.context['sort'], 'name_desc')
        names = [m.name for m in response.context['members']]
        self.assertEqual(names, sorted(names, reverse=True))

    def test_sort_roll(self):
        response = self.client.get(reverse('member_directory') + '?sort=roll')
        self.assertEqual(response.context['sort'], 'roll')
        members = response.context['members']
        # Members without roll numbers come after those with roll numbers
        with_roll = [m for m in members if m.role_number is not None]
        roll_nums = [m.role_number for m in with_roll]
        self.assertEqual(roll_nums, sorted(roll_nums))

    def test_unknown_sort_value_defaults_to_name(self):
        """An invalid sort value should not crash and falls back to name order."""
        response = self.client.get(reverse('member_directory') + '?sort=garbage')
        self.assertEqual(response.status_code, 200)
        # Sort key in context is passed through verbatim; name ordering is preserved from queryset
        names = [m.name for m in response.context['members']]
        self.assertEqual(names, sorted(names))


# ===========================================================================
# 4. MEMBER DIRECTORY — FILTER_TYPE CONTEXT KEY
# ===========================================================================

class DirectoryFilterContextTests(TestCase):
    """filter_type is passed through to context so the template can pre-select the active pill."""

    def setUp(self):
        self.client = Client()
        make_user('ft1', member_type='Member')
        self.client.login(username='user_ft1', password='testpass123')

    def test_filter_type_default_is_all(self):
        response = self.client.get(reverse('member_directory'))
        self.assertEqual(response.context['filter_type'], 'all')

    def test_filter_type_passed_through(self):
        for ftype in ['officer', 'chair', 'member', 'pledge', 'advisor', 'alumni']:
            response = self.client.get(reverse('member_directory') + f'?filter={ftype}')
            self.assertEqual(response.context['filter_type'], ftype)


# ===========================================================================
# 5. DIRECTORY EXPORT — DEFAULTS
# ===========================================================================

class DirectoryExportDefaultsTests(TestCase):
    """Direct link (no _export_submitted) uses sensible defaults."""

    def setUp(self):
        self.client = Client()
        make_user('exp1', member_type='Member', name='Export User',
                  email='exp@test.com', role_number=42)
        self.client.login(username='user_exp1', password='testpass123')

    def test_default_export_is_csv(self):
        response = self.client.get(reverse('export_directory'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('text/csv', response['Content-Type'])

    def test_csv_contains_header(self):
        response = self.client.get(reverse('export_directory'))
        content = b''.join(response.streaming_content) if hasattr(response, 'streaming_content') else response.content
        self.assertIn(b'Name', content)

    def test_csv_contains_member_name(self):
        response = self.client.get(reverse('export_directory'))
        content = b''.join(response.streaming_content) if hasattr(response, 'streaming_content') else response.content
        self.assertIn(b'Export User', content)


# ===========================================================================
# 6. DIRECTORY EXPORT — EXPLICIT FORM SUBMISSION
# ===========================================================================

class DirectoryExportFormTests(TestCase):
    """Explicit form submission (_export_submitted=1) respects checkbox state."""

    def setUp(self):
        self.client = Client()
        self.active_user = make_user('ef1', member_type='Member', name='Active Member',
                                     email='active@test.com', role_number=10)
        self.alumni_user = make_user('ef2', member_type='Member', name='Alum Member',
                                     member_status='Alumni', email='alum@test.com', role_number=99)
        self.client.login(username='user_ef1', password='testpass123')

    def test_alumni_excluded_when_not_checked(self):
        response = self.client.get(reverse('export_directory'), {
            '_export_submitted': '1',
            'format': 'csv',
            'include_email': 'include_email',
        })
        content = b''.join(response.streaming_content) if hasattr(response, 'streaming_content') else response.content
        self.assertNotIn(b'alum@test.com', content)

    def test_alumni_included_when_checked(self):
        response = self.client.get(reverse('export_directory'), {
            '_export_submitted': '1',
            'format': 'csv',
            'include_alumni': 'include_alumni',
            'include_email': 'include_email',
        })
        content = b''.join(response.streaming_content) if hasattr(response, 'streaming_content') else response.content
        self.assertIn(b'alum@test.com', content)

    def test_email_column_omitted_when_not_checked(self):
        response = self.client.get(reverse('export_directory'), {
            '_export_submitted': '1',
            'format': 'csv',
        })
        content = b''.join(response.streaming_content) if hasattr(response, 'streaming_content') else response.content
        # Header should not have 'Email' column
        first_line = content.split(b'\n')[0]
        self.assertNotIn(b'Email', first_line)

    def test_txt_format(self):
        response = self.client.get(reverse('export_directory'), {
            '_export_submitted': '1',
            'format': 'txt',
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn('text/plain', response['Content-Type'])
        self.assertIn(b'MEMBER DIRECTORY', response.content)

    def test_xlsx_format(self):
        """Excel export returns the correct content-type."""
        try:
            import openpyxl  # noqa: F401
        except ImportError:
            self.skipTest('openpyxl not installed')
        response = self.client.get(reverse('export_directory'), {
            '_export_submitted': '1',
            'format': 'xlsx',
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn('spreadsheetml', response['Content-Type'])

    def test_xlsx_falls_back_to_csv_without_openpyxl(self):
        """If openpyxl is not importable, falls back to CSV silently."""
        import builtins
        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == 'openpyxl':
                raise ImportError('mocked')
            return real_import(name, *args, **kwargs)

        import unittest.mock as mock
        with mock.patch('builtins.__import__', side_effect=mock_import):
            response = self.client.get(reverse('export_directory'), {
                '_export_submitted': '1',
                'format': 'xlsx',
            })
        self.assertIn('text/csv', response['Content-Type'])


# ===========================================================================
# 7. ROLE TRANSITIONS — AUTHENTICATION
# ===========================================================================

class TransitionAuthTests(TestCase):
    """Unauthenticated and non-officer requests are rejected."""

    def test_transitions_list_requires_login(self):
        response = self.client.get(reverse('role_transitions'))
        self.assertIn(response.status_code, [302, 301])

    def test_transfer_requires_login(self):
        role = Role.objects.create(name='TestRole', code='TR')
        response = self.client.post(
            reverse('transfer_role', kwargs={'role_id': role.id}),
            content_type='application/json',
            data=json.dumps({'incoming_user_id': 'x'}),
        )
        self.assertIn(response.status_code, [302, 301])

    def test_transitions_list_requires_officer(self):
        user = make_user('rt_plain', member_type='Member')
        client = Client()
        client.login(username='user_rt_plain', password='testpass123')
        response = client.get(reverse('role_transitions'))
        # officer_required redirects non-officers
        self.assertNotEqual(response.status_code, 200)


# ===========================================================================
# 8. ROLE TRANSITIONS — GET RENDER
# ===========================================================================

class TransitionListTests(TestCase):
    """GET /officers/transitions/ renders with correct context."""

    def setUp(self):
        self.officer = make_user('tl_off', member_type='Officer')
        self.client = Client()
        self.client.login(username='user_tl_off', password='testpass123')
        self.role = Role.objects.create(name='President', code='PRES', one_per_chapter=True, grants_admin=True)

    def test_renders_200(self):
        response = self.client.get(reverse('role_transitions'))
        self.assertEqual(response.status_code, 200)

    def test_context_contains_roles_data(self):
        response = self.client.get(reverse('role_transitions'))
        self.assertIn('roles_data', response.context)

    def test_context_stats(self):
        # Assign role to a member so filled_count > 0
        holder = make_user('tl_holder', member_type='Member')
        holder.roles.add(self.role)
        response = self.client.get(reverse('role_transitions'))
        self.assertEqual(response.context['filled_count'], 1)

    def test_vacant_role_in_data(self):
        response = self.client.get(reverse('role_transitions'))
        roles_data = response.context['roles_data']
        pres_data = next(d for d in roles_data if d['role'].id == self.role.id)
        self.assertEqual(pres_data['holder_count'], 0)

    def test_roles_json_and_assignable_json_in_context(self):
        response = self.client.get(reverse('role_transitions'))
        self.assertIn('roles_json', response.context)
        self.assertIn('assignable_json', response.context)
        # Both should be valid JSON
        json.loads(response.context['roles_json'])
        json.loads(response.context['assignable_json'])


# ===========================================================================
# 9. ROLE TRANSITIONS — TRANSFER LOGIC
# ===========================================================================

def _post_transfer(client, role_id, **body):
    return client.post(
        reverse('transfer_role', kwargs={'role_id': role_id}),
        content_type='application/json',
        data=json.dumps(body),
    )


class TransferRoleTests(TestCase):
    """transfer_role POST — core swap logic."""

    def setUp(self):
        self.officer = make_user('tr_off', member_type='Officer')
        self.outgoing = make_user('tr_out', member_type='Officer', name='Outgoing User')
        self.incoming = make_user('tr_in', member_type='Member', name='Incoming User')
        self.role = Role.objects.create(name='President', code='PRES', one_per_chapter=True, grants_admin=True)
        self.outgoing.roles.add(self.role)
        self.client = Client()
        self.client.login(username='user_tr_off', password='testpass123')

    def test_missing_incoming_returns_400(self):
        response = _post_transfer(self.client, self.role.id)
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertFalse(data['success'])

    def test_invalid_json_returns_400(self):
        response = self.client.post(
            reverse('transfer_role', kwargs={'role_id': self.role.id}),
            content_type='application/json',
            data='not-json',
        )
        self.assertEqual(response.status_code, 400)

    def test_basic_transfer_assigns_incoming(self):
        _post_transfer(self.client, self.role.id,
                       incoming_user_id='tr_in',
                       outgoing_user_id='tr_out')
        self.incoming.refresh_from_db()
        self.assertIn(self.role, self.incoming.roles.all())

    def test_basic_transfer_removes_outgoing(self):
        _post_transfer(self.client, self.role.id,
                       incoming_user_id='tr_in',
                       outgoing_user_id='tr_out')
        self.outgoing.refresh_from_db()
        self.assertNotIn(self.role, self.outgoing.roles.all())

    def test_transfer_returns_success(self):
        response = _post_transfer(self.client, self.role.id,
                                  incoming_user_id='tr_in',
                                  outgoing_user_id='tr_out')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])

    def test_grants_admin_sets_is_admin_on_incoming(self):
        _post_transfer(self.client, self.role.id,
                       incoming_user_id='tr_in',
                       outgoing_user_id='tr_out')
        self.incoming.refresh_from_db()
        self.assertTrue(self.incoming.is_admin)

    def test_incoming_type_updates_member_type(self):
        _post_transfer(self.client, self.role.id,
                       incoming_user_id='tr_in',
                       outgoing_user_id='tr_out',
                       incoming_type='Officer')
        self.incoming.refresh_from_db()
        self.assertEqual(self.incoming.member_type, 'Officer')

    def test_no_incoming_type_leaves_member_type_unchanged(self):
        _post_transfer(self.client, self.role.id,
                       incoming_user_id='tr_in',
                       outgoing_user_id='tr_out')
        self.incoming.refresh_from_db()
        self.assertEqual(self.incoming.member_type, 'Member')  # unchanged (was Member, no type arg)

    def test_demote_outgoing_reverts_to_member(self):
        # outgoing holds only this role — after removal, no roles remain → should demote
        _post_transfer(self.client, self.role.id,
                       incoming_user_id='tr_in',
                       outgoing_user_id='tr_out',
                       demote_outgoing=True)
        self.outgoing.refresh_from_db()
        self.assertEqual(self.outgoing.member_type, 'Member')

    def test_demote_outgoing_kept_if_other_admin_role_remains(self):
        second_role = Role.objects.create(name='EVP', code='EVP', grants_admin=True)
        self.outgoing.roles.add(second_role)
        _post_transfer(self.client, self.role.id,
                       incoming_user_id='tr_in',
                       outgoing_user_id='tr_out',
                       demote_outgoing=True)
        self.outgoing.refresh_from_db()
        # Still has EVP (grants_admin) → should NOT be demoted
        self.assertEqual(self.outgoing.member_type, 'Officer')

    def test_one_per_chapter_auto_clears_holder_when_no_outgoing_specified(self):
        _post_transfer(self.client, self.role.id,
                       incoming_user_id='tr_in')
        self.outgoing.refresh_from_db()
        self.assertNotIn(self.role, self.outgoing.roles.all())

    def test_nonexistent_role_returns_404(self):
        response = _post_transfer(self.client, 99999, incoming_user_id='tr_in')
        self.assertEqual(response.status_code, 404)

    def test_inactive_incoming_returns_404(self):
        inactive = make_user('tr_inactive', member_type='Member', member_status='Alumni')
        response = _post_transfer(self.client, self.role.id,
                                  incoming_user_id='tr_inactive')
        self.assertEqual(response.status_code, 404)
