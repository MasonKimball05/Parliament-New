"""v4.4.4 admin-v2 permissions map: gate, each section, each flag, filter, bounded queries."""
from datetime import timedelta
from unittest import mock

from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from src.models import (
    APIToken, Committee, CommitteePermissions, EducationMemberPermission, KaiBreakGlassGrant,
    KaiMemberPermission, RecruitmentMemberPermission, Role, SlatingPeriod,
)
from src.tests.admin_v2.base import ADMIN_ID, AdminV2TestCase, make_user
from src.view import admin_v2
from src.view.admin_v2_permissions import (
    FLAG_INACTIVE_COMMITTEE, FLAG_NO_CHAIR, FLAG_NOT_ACTIVE, FLAG_PLEDGE, FLAG_STALE, permissions_map,
)


def section(data, key):
    return next(s for s in data['sections'] if s['key'] == key)


def entries(data, key, label=None):
    return [e for g in section(data, key)['groups'] if label is None or g['label'] == label for e in g['entries']]


def ids(data, key, label=None):
    return sorted(e['member'].user_id for e in entries(data, key, label))


def entry(data, key, uid):
    return next(e for e in entries(data, key) if e['member'].user_id == uid)


def group(data, key, label):
    return next(g for g in section(data, key)['groups'] if g['label'] == label)


class PermissionsGateTests(AdminV2TestCase):

    def test_not_allowlisted_is_refused(self):
        other = make_user('pm-other', is_admin=True)
        c = Client()
        c.force_login(other)
        s = c.session
        s['admin_v2_authenticated'] = True
        s.save()
        r = c.get(reverse('admin_v2_permissions'))
        self.assertRedirects(r, reverse('home'), fetch_redirect_response=False)

    def test_needs_the_admin_v2_step_up(self):
        c = Client()
        c.force_login(self.admin)
        r = c.get(reverse('admin_v2_permissions'))
        self.assertRedirects(r, reverse('admin_v2_login'), fetch_redirect_response=False)

    def test_page_renders_links_and_nav(self):
        r = self.client.get(reverse('admin_v2_permissions'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, reverse('admin_v2_member', args=[ADMIN_ID]))
        self.assertContains(r, f'href="{reverse("admin_v2_permissions")}"')
        self.assertContains(r, 'aria-current="page"')


class PermissionsSectionTests(AdminV2TestCase):

    def setUp(self):
        super().setUp()
        now = timezone.now()
        self.admin.last_seen_at = now
        self.admin.save(update_fields=['last_seen_at'])
        self.pres = make_user('pm-pres', name='Pres Ident', member_type='Officer', last_seen_at=now)
        self.role = Role.objects.get_or_create(code='PMTEST', defaults={'name': 'PM Test Role'})[0]
        self.pres.roles.add(self.role)
        self.chair = make_user('pm-chair', name='Chair Person', member_type='Chair')
        self.advisor = make_user('pm-adv', name='Ad Visor', member_type='Advisor')
        self.plain = make_user('pm-plain', name='Plain Member')
        self.live = Committee.objects.create(name='PM Live Committee', code='PMLIVE')
        self.live.chairs.add(self.chair)
        self.empty = Committee.objects.create(name='PM Empty Committee', code='PMEMPTY')
        self.old = Committee.objects.create(name='PM Old Committee', code='PMOLD', is_active=False)
        self.old.chairs.add(self.plain)
        self.manager = make_user('pm-mgr', name='Slate Manager')
        SlatingPeriod.objects.create(name='PM Fall', academic_term='Fall', slating_manager=self.manager)
        self.kai_user = make_user('pm-kai', name='Kai Member')
        kai = Committee.objects.create(name='PM Kai', code='PMKAI', is_kai_committee=True)
        KaiMemberPermission.objects.create(committee=kai, user=self.kai_user, can_view_report_list=True)
        self.glass = make_user('pm-glass', name='Break Glass')
        KaiBreakGlassGrant.objects.create(user=self.glass, reason='test', expires_at=now + timedelta(hours=1))
        expired = make_user('pm-expired', name='Expired Glass')
        KaiBreakGlassGrant.objects.create(user=expired, reason='test', expires_at=now - timedelta(hours=1))
        self.edu = make_user('pm-edu', name='Edu Grader')
        edu = Committee.objects.create(name='PM Edu', code='PMEDU', is_education_committee=True)
        EducationMemberPermission.objects.create(committee=edu, user=self.edu, can_grade_submissions=True)
        self.rec = make_user('pm-rec', name='Rec Events')
        rec = Committee.objects.create(name='PM Rec', code='PMREC', is_recruitment_committee=True)
        RecruitmentMemberPermission.objects.create(committee=rec, user=self.rec, can_manage_events=True)
        RecruitmentMemberPermission.objects.create(committee=rec, user=self.plain)  # nothing granted
        self.minutes = make_user('pm-min', name='Minute Taker')
        CommitteePermissions.objects.create(committee=self.live, user=self.minutes, can_take_minutes=True)
        self.tok = make_user('pm-tok', name='Token Holder')
        APIToken.objects.create(user=self.tok, name='Sheets sync', key='pm-k1', scopes=['members:read'],
                                status=APIToken.STATUS_ACTIVE)
        APIToken.objects.create(user=self.plain, name='Revoked', key='pm-k2', status=APIToken.STATUS_REVOKED)

    def test_sections(self):
        data = permissions_map()
        self.assertEqual(ids(data, 'allowlist'), [ADMIN_ID])
        self.assertEqual(ids(data, 'is_admin'), [ADMIN_ID])
        self.assertEqual(ids(data, 'roles', 'PM Test Role'), ['pm-pres'])
        self.assertEqual(ids(data, 'member_types', 'Advisor'), ['pm-adv'])
        self.assertEqual(ids(data, 'member_types', 'Chair'), ['pm-chair'])
        self.assertIn('pm-pres', ids(data, 'member_types', 'Officer'))
        self.assertEqual(ids(data, 'chairs', 'PM Live Committee'), ['pm-chair'])
        self.assertEqual(ids(data, 'slating'), ['pm-mgr'])
        self.assertEqual(ids(data, 'kai'), ['pm-glass', 'pm-kai'])
        self.assertIn('view report list', entry(data, 'kai', 'pm-kai')['detail'])
        self.assertEqual(ids(data, 'education'), ['pm-edu'])
        self.assertEqual(ids(data, 'recruitment'), ['pm-rec'])
        self.assertEqual(ids(data, 'committee_perms'), ['pm-min'])
        self.assertIn('take minutes', entry(data, 'committee_perms', 'pm-min')['detail'])
        self.assertEqual(ids(data, 'api_tokens'), ['pm-tok'])
        self.assertIn('Sheets sync', entry(data, 'api_tokens', 'pm-tok')['detail'])
        self.assertNotIn('pm-plain', ids(data, 'member_types'))

    def test_allowlist_id_with_no_member(self):
        with mock.patch.object(admin_v2, 'ALLOWED_USER_IDS', {ADMIN_ID, 'ghost-id'}):
            data = permissions_map()
        self.assertEqual(data['unknown_allowlist'], ['ghost-id'])

    def test_flags(self):
        data = permissions_map()
        # fresh sign-ins: no stale flag
        self.assertNotIn(FLAG_STALE, entry(data, 'allowlist', ADMIN_ID)['flags'])
        # chair of an inactive committee
        self.assertEqual(entry(data, 'chairs', 'pm-plain')['flags'], [FLAG_INACTIVE_COMMITTEE])
        self.assertEqual(entry(data, 'chairs', 'pm-chair')['flags'], [])
        # committee with no chair (active, not exec board); inactive committees aren't flagged
        self.assertEqual(group(data, 'chairs', 'PM Empty Committee')['flags'], [FLAG_NO_CHAIR])
        self.assertEqual(group(data, 'chairs', 'PM Live Committee')['flags'], [])
        counts = {f['key']: f['n'] for f in data['flag_counts']}
        self.assertGreaterEqual(counts[FLAG_NO_CHAIR], 1)

    def test_exec_board_without_chair_is_not_flagged(self):
        Committee.objects.create(name='PM Exec', code='PMEXEC', is_exec_board=True)
        data = permissions_map()
        self.assertFalse(any(g['label'] == 'PM Exec' for g in section(data, 'chairs')['groups']))

    def test_stale_admin_flag(self):
        old = timezone.now() - timedelta(days=61)
        type(self.admin).objects.filter(pk=ADMIN_ID).update(last_seen_at=old, last_login=old)
        never = make_user('pm-never', is_admin=True)
        data = permissions_map()
        self.assertIn(FLAG_STALE, entry(data, 'allowlist', ADMIN_ID)['flags'])
        self.assertIn(FLAG_STALE, entry(data, 'is_admin', ADMIN_ID)['flags'])
        self.assertIn(FLAG_STALE, entry(data, 'is_admin', never.user_id)['flags'])
        # a recent last_login counts even if last_seen_at is old
        type(self.admin).objects.filter(pk=ADMIN_ID).update(last_login=timezone.now())
        self.assertNotIn(FLAG_STALE, entry(permissions_map(), 'is_admin', ADMIN_ID)['flags'])

    def test_pledge_and_alumni_holding_elevated_permissions(self):
        pledge = make_user('pm-pledge', member_type='Pledge')
        pledge.roles.add(self.role)
        alum = make_user('pm-alum', member_type='Officer', member_status='Alumni')
        self.live.chairs.add(alum)
        data = permissions_map()
        self.assertIn(FLAG_PLEDGE, entry(data, 'roles', 'pm-pledge')['flags'])
        self.assertIn(FLAG_NOT_ACTIVE, entry(data, 'chairs', 'pm-alum')['flags'])
        self.assertIn(FLAG_NOT_ACTIVE, entry(data, 'member_types', 'pm-alum')['flags'])
        self.assertEqual(entry(data, 'roles', 'pm-pres')['flags'], [])

    def test_search_and_flagged_filter(self):
        data = permissions_map(q='kai member')
        self.assertEqual(ids(data, 'kai'), ['pm-kai'])
        self.assertEqual(ids(data, 'roles'), [])
        data = permissions_map(q='PM Live')  # group label match
        self.assertEqual(ids(data, 'chairs'), ['pm-chair'])
        data = permissions_map(flagged_only=True)
        self.assertEqual(ids(data, 'chairs'), ['pm-plain'])
        self.assertTrue(any(g['label'] == 'PM Empty Committee' for g in section(data, 'chairs')['groups']))
        r = self.client.get(reverse('admin_v2_permissions'), {'q': 'Slate'})
        self.assertContains(r, 'Slate Manager')
        self.assertNotContains(r, 'Token Holder')

    def test_query_count_is_flat(self):
        def count():
            with CaptureQueriesContext(connection) as ctx:
                self.client.get(reverse('admin_v2_permissions'))
            return len(ctx.captured_queries)

        count()  # warm the per-process caches (flags, settings) the first request fills
        before = count()
        for i in range(15):
            m = make_user(f'pm-bulk-{i}', member_type='Chair', is_admin=True)
            m.roles.add(self.role)
            c = Committee.objects.create(name=f'PM Bulk {i}', code=f'PMB{i}')
            c.chairs.add(m)
            APIToken.objects.create(user=m, name=f't{i}', key=f'pm-bulk-k{i}', status=APIToken.STATUS_ACTIVE)
            CommitteePermissions.objects.create(committee=c, user=m, can_upload_docs=True)
        self.assertEqual(count(), before)
