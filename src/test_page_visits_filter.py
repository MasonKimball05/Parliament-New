"""
Tests for the page-visits dashboard member filter (v3.15.6).

Covers:
- the new aggregate-level `user` filter (answer "what pages has this member
  been viewing" from the top-level dashboard, not just per-path drill-down)
- the drill-view name filter, which previously queried the nonexistent
  user__first_name / user__last_name fields — ParliamentUser extends
  AbstractBaseUser and has neither, so the filter box raised FieldError the
  moment it was actually used (latent bug fixed in v3.15.6)
- filter matches name, preferred_name, and username, case-insensitively
- the member-status filter (v3.15.7): Active/Inactive/Alumni/Removed options
  on both the aggregate and drill views
"""
from unittest import mock

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from src.models import ParliamentUser
from src.models.analytics import PageVisit
from src.view import admin_v2


def make_user(user_id, name, username, **kwargs):
    defaults = dict(name=name, username=username, member_type='Member')
    defaults.update(kwargs)
    return ParliamentUser.objects.create_user(
        user_id=user_id, password='testpass123', **defaults)


class PageVisitsDashboardFilterTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = make_user('900', 'Admin Aardvark', 'admin_900', is_admin=True)
        self.alice = make_user('901', 'Alice Alpha', 'alice_a')
        self.bob = make_user('902', 'Bob Beta', 'bob_b',
                             preferred_name='Bobby')
        self.zed = make_user('903', 'Zed Zeta', 'zed_z',
                             member_status='Alumni')

        PageVisit.objects.create(user=self.alice, path='/home/', count=5)
        PageVisit.objects.create(user=self.alice, path='/vote/', count=2)
        PageVisit.objects.create(user=self.bob, path='/home/', count=7)
        PageVisit.objects.create(user=self.zed, path='/home/', count=3)

        # require_admin_v2_auth: allowed id + session flags
        self._allowed = mock.patch.object(
            admin_v2, 'ALLOWED_USER_IDS', {'900'})
        self._allowed.start()
        self.addCleanup(self._allowed.stop)
        self.client.force_login(self.admin)
        session = self.client.session
        session['admin_v2_authenticated'] = True
        session['admin_v2_auth_time'] = timezone.now().isoformat()
        session.save()

    def _get(self, **params):
        return self.client.get(reverse('admin_v2_page_visits'), params)

    # -- aggregate view ------------------------------------------------------

    def test_unfiltered_aggregate_counts_everyone(self):
        response = self._get()
        self.assertEqual(response.status_code, 200)
        pages = {p['path']: p for p in response.context['pages']}
        self.assertEqual(pages['/home/']['total'], 15)   # 5 + 7 + 3 (alumni)
        self.assertEqual(pages['/home/']['unique_users'], 3)
        self.assertEqual(pages['/vote/']['total'], 2)

    def test_aggregate_user_filter_restricts_counts(self):
        response = self._get(user='alice')
        self.assertEqual(response.status_code, 200)
        pages = {p['path']: p for p in response.context['pages']}
        self.assertEqual(pages['/home/']['total'], 5)      # Bob's 7 excluded
        self.assertEqual(pages['/home/']['unique_users'], 1)
        self.assertEqual(pages['/vote/']['total'], 2)
        self.assertEqual([u.pk for u in response.context['matched_users']],
                         [self.alice.pk])

    def test_aggregate_filter_matches_preferred_name(self):
        response = self._get(user='bobby')
        pages = {p['path']: p for p in response.context['pages']}
        self.assertEqual(list(pages), ['/home/'])
        self.assertEqual(pages['/home/']['total'], 7)

    def test_aggregate_filter_matches_username(self):
        response = self._get(user='bob_b')
        pages = {p['path']: p for p in response.context['pages']}
        self.assertEqual(pages['/home/']['total'], 7)

    def test_aggregate_filter_no_match_is_empty_not_error(self):
        response = self._get(user='zzz-nobody')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context['pages']), [])
        self.assertEqual(response.context['matched_users'], [])

    # -- drill view ----------------------------------------------------------

    def test_drill_user_filter_no_longer_fielderrors(self):
        """The old drill filter used user__first_name/user__last_name, which
        don't exist on ParliamentUser — any real use raised FieldError."""
        response = self._get(path='/home/', user='alice')
        self.assertEqual(response.status_code, 200)
        rows = list(response.context['rows'])
        self.assertEqual([r.user_id for r in rows], [self.alice.pk])
        self.assertEqual(rows[0].count, 5)

    def test_drill_unfiltered_shows_all_visitors_desc(self):
        response = self._get(path='/home/')
        rows = list(response.context['rows'])
        self.assertEqual([r.count for r in rows], [7, 5, 3])

    # -- member-status filter (v3.15.7) --------------------------------------

    def test_aggregate_status_filter_active_excludes_alumni(self):
        response = self._get(status='Active')
        pages = {p['path']: p for p in response.context['pages']}
        self.assertEqual(pages['/home/']['total'], 12)   # Zed's 3 excluded
        self.assertEqual(pages['/home/']['unique_users'], 2)

    def test_aggregate_status_filter_alumni_only(self):
        response = self._get(status='Alumni')
        pages = {p['path']: p for p in response.context['pages']}
        self.assertEqual(list(pages), ['/home/'])
        self.assertEqual(pages['/home/']['total'], 3)
        self.assertEqual(pages['/home/']['unique_users'], 1)

    def test_status_filter_combines_with_name_filter(self):
        # Name matches Zed, but status=Active excludes him -> empty.
        response = self._get(user='zed', status='Active')
        self.assertEqual(list(response.context['pages']), [])
        self.assertEqual(response.context['matched_users'], [])

    def test_unknown_status_value_treated_as_all(self):
        response = self._get(status='Bogus')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['status_filter'], '')
        pages = {p['path']: p for p in response.context['pages']}
        self.assertEqual(pages['/home/']['total'], 15)

    def test_drill_status_filter(self):
        response = self._get(path='/home/', status='Alumni')
        rows = list(response.context['rows'])
        self.assertEqual([r.user_id for r in rows], [self.zed.pk])
        self.assertEqual(rows[0].count, 3)
