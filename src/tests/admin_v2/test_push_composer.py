"""v4.4.4 admin-v2 push composer: audiences, preview, confirm, opt-outs, link check, log, rate limit."""
from unittest import mock

from django.core import signing
from django.core.cache import cache
from django.test import Client
from django.urls import reverse

from src.mobile.tokens import create_device
from src.models import ActivityLog, Committee, PushSubscription
from src.models.mobile import MobileDevice
from src.models.users import UserPreferences
from src.models_feature_flags import FeatureFlag
from src.tests.admin_v2.base import AdminV2TestCase, make_user
from src.view import admin_v2_push as push_views

DELAY = 'src.tasks.send_push_notification.delay'


def with_app(member, token='abc'):
    device, _a, _r = create_device(member, 'password_totp', 'ios')
    MobileDevice.objects.filter(pk=device.pk).update(push_token=f'ExponentPushToken[{token}]')
    return device


def with_browser(member):
    return PushSubscription.objects.create(user=member, endpoint=f'https://push.example/{member.pk}',
                                           p256dh='k', auth='a')


def pushes_off(member):
    prefs, _ = UserPreferences.objects.get_or_create(user=member)
    data = prefs.prefs or {}
    data.setdefault('push', {})['announcements'] = False
    prefs.prefs = data
    prefs.save()


class PushComposerBase(AdminV2TestCase):

    def setUp(self):
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)
        self.amy = make_user('pc-amy', name='Amy Active')                       # app
        self.ben = make_user('pc-ben', name='Ben Browser')                      # browser only
        self.cal = make_user('pc-cal', name='Cal Nodevice')                     # nothing to push to
        self.oli = make_user('pc-oli', name='Oli Officer', member_type='Officer')
        self.pat = make_user('pc-pat', name='Pat Pledge', member_type='Pledge')
        self.alu = make_user('pc-alu', name='Al Alumnus', member_status='Alumni')
        self.rem = make_user('pc-rem', name='Rem Removed', member_status='Removed')
        self.opt = make_user('pc-opt', name='Opt Out')
        for m in (self.amy, self.oli, self.pat, self.alu, self.rem, self.opt):
            with_app(m, m.pk)
        with_browser(self.ben)
        pushes_off(self.opt)
        self.committee = Committee.objects.create(name='Push Test Committee', code='PUSHTEST')
        self.committee.members.add(self.amy)
        self.committee.chairs.add(self.ben)

    def form(self, **kw):
        data = {'audience': 'everyone', 'title': 'Chapter tonight', 'body': 'Meet at 7.', 'link': ''}
        data.update(kw)
        return data

    def preview(self, **kw):
        return self.client.post(reverse('admin_v2_push'), self.form(**kw))

    def send(self, **kw):
        token = self.preview(**kw).context['preview']['token']
        with mock.patch(DELAY) as delay:
            r = self.client.post(reverse('admin_v2_push_send'), {'token': token})
        return r, delay


class GateTests(PushComposerBase):

    def test_not_allowlisted_is_refused(self):
        other = make_user('pc-other', is_admin=True)
        c = Client()
        c.force_login(other)
        s = c.session
        s['admin_v2_authenticated'] = True
        s.save()
        for name in ('admin_v2_push', 'admin_v2_push_send'):
            r = c.post(reverse(name), self.form())
            self.assertRedirects(r, reverse('home'), fetch_redirect_response=False)

    def test_allowlisted_without_step_up_goes_to_admin_login(self):
        c = Client()
        c.force_login(self.admin)
        r = c.get(reverse('admin_v2_push'))
        self.assertRedirects(r, reverse('admin_v2_login'), fetch_redirect_response=False)
        with mock.patch(DELAY) as delay:
            c.post(reverse('admin_v2_push_send'), {'token': 'x'})
        delay.assert_not_called()

    def test_page_renders_and_is_in_nav(self):
        r = self.client.get(reverse('admin_v2_push'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Push composer')
        self.assertContains(r, f'href="{reverse("admin_v2_push")}"')


class PreviewTests(PushComposerBase):

    def count(self, **kw):
        r = self.preview(**kw)
        self.assertEqual(r.status_code, 200, getattr(r, 'context', {}) and r.context.get('error'))
        return r.context['preview']

    def test_everyone(self):
        p = self.count()
        # Active, not Removed: amy ben cal oli pat opt + the admin; alumni and removed out.
        self.assertEqual(p['members'], 7)
        self.assertEqual(p['opted_out'], 1)
        self.assertEqual(p['no_device'], 2)   # cal, the admin
        self.assertEqual(p['n'], 4)       # amy, ben, oli, pat
        self.assertEqual(sorted(p['names']), ['Amy Active', 'Ben Browser', 'Oli Officer', 'Pat Pledge'])

    def test_officers(self):
        p = self.count(audience='officers')
        self.assertEqual(p['members'], 2)     # oli + the admin (Officer)
        self.assertEqual(p['n'], 1)

    def test_committee_members_and_chairs(self):
        p = self.count(audience='committee', committee=str(self.committee.pk))
        self.assertEqual(sorted(p['names']), ['Amy Active', 'Ben Browser'])
        self.assertIn('Push Test Committee', p['audience_label'])

    def test_member_type(self):
        p = self.count(audience='type', member_type='Pledge')
        self.assertEqual(p['names'], ['Pat Pledge'])

    def test_member_status(self):
        p = self.count(audience='status', member_status='Alumni')
        self.assertEqual(p['names'], ['Al Alumnus'])

    def test_one_member(self):
        p = self.count(audience='member', user_id='pc-amy')
        self.assertEqual((p['members'], p['n']), (1, 1))

    def test_removed_member_cannot_be_chosen(self):
        r = self.preview(audience='member', user_id='pc-rem')
        self.assertEqual(r.status_code, 400)

    def test_bad_choices_are_400(self):
        for kw in ({'audience': 'nope'}, {'audience': 'type', 'member_type': 'Admin'},
                   {'audience': 'committee', 'committee': '999999'}, {'title': ''}, {'body': ''},
                   {'title': 'x' * 81}):
            self.assertEqual(self.preview(**kw).status_code, 400, kw)

    def test_preview_sends_nothing(self):
        with mock.patch(DELAY) as delay:
            self.preview()
        delay.assert_not_called()
        self.assertFalse(ActivityLog.objects.filter(object_type='AdminPush').exists())


class LinkTests(PushComposerBase):

    def test_same_site_paths_are_accepted(self):
        for link in ('/calendar/', '/calendar/?year=2026&month=11', '/announcements/'):
            self.assertEqual(push_views.clean_link(link), link)
        self.assertEqual(push_views.clean_link(''), '')

    def test_other_links_are_refused(self):
        for link in ('https://evil.example/', '//evil.example/', '/\\evil.example', 'calendar/',
                     'javascript:alert(1)', '/'.join(['', 'no-such-page-anywhere', '']), '/cal endar/'):
            with self.assertRaises(push_views.PushFormError, msg=link):
                push_views.clean_link(link)
        self.assertEqual(self.preview(link='https://evil.example/').status_code, 400)

    def test_link_goes_with_the_push(self):
        _r, delay = self.send(audience='member', user_id='pc-amy', link='/calendar/')
        self.assertEqual(delay.call_args.kwargs['url'], '/calendar/')

    def test_no_link_opens_notifications(self):
        _r, delay = self.send(audience='member', user_id='pc-amy')
        self.assertEqual(delay.call_args.kwargs['url'], '/notifications/')


class SendTests(PushComposerBase):

    def test_confirm_sends_to_recipients_only_and_logs(self):
        r, delay = self.send()
        self.assertRedirects(r, reverse('admin_v2_push'), fetch_redirect_response=False)
        sent_to = sorted(c.kwargs['user_id'] for c in delay.call_args_list)
        self.assertEqual(sent_to, ['pc-amy', 'pc-ben', 'pc-oli', 'pc-pat'])
        self.assertNotIn('pc-opt', sent_to)    # opted out
        self.assertNotIn('pc-cal', sent_to)    # no device
        self.assertEqual(delay.call_args.kwargs['title'], 'Chapter tonight')
        log = ActivityLog.objects.get(object_type='AdminPush')
        self.assertEqual(log.user, self.admin)
        self.assertEqual(log.metadata['n'], 4)
        self.assertEqual(log.metadata['audience'], {'kind': 'everyone', 'value': ''})
        self.assertEqual(log.metadata['title'], 'Chapter tonight')
        self.assertIn('everyone', log.description)
        page = self.client.get(reverse('admin_v2_push'))
        self.assertContains(page, 'Chapter tonight')
        self.assertContains(page, '4 members')

    def test_get_does_not_send(self):
        with mock.patch(DELAY) as delay:
            r = self.client.get(reverse('admin_v2_push_send'))
        self.assertEqual(r.status_code, 405)
        delay.assert_not_called()

    def test_send_without_a_token_sends_nothing(self):
        with mock.patch(DELAY) as delay:
            for data in ({}, {'token': 'forged'}, self.form()):
                self.client.post(reverse('admin_v2_push_send'), data)
        delay.assert_not_called()

    def test_token_from_another_admin_is_refused(self):
        token = signing.dumps({'a': {'kind': 'everyone', 'value': ''}, 't': 'T', 'b': 'B', 'l': '',
                               'u': 'someone-else', 'n': 'x'}, salt=push_views.TOKEN_SALT)
        with mock.patch(DELAY) as delay:
            self.client.post(reverse('admin_v2_push_send'), {'token': token})
        delay.assert_not_called()

    def test_token_is_single_use(self):
        token = self.preview().context['preview']['token']
        with mock.patch(DELAY) as delay:
            self.client.post(reverse('admin_v2_push_send'), {'token': token})
            first = delay.call_count
            self.client.post(reverse('admin_v2_push_send'), {'token': token})
        self.assertEqual(first, 4)
        self.assertEqual(delay.call_count, 4)
        self.assertEqual(ActivityLog.objects.filter(object_type='AdminPush').count(), 1)

    def test_opt_out_after_preview_is_still_respected(self):
        token = self.preview(audience='member', user_id='pc-amy').context['preview']['token']
        pushes_off(self.amy)
        with mock.patch(DELAY) as delay:
            self.client.post(reverse('admin_v2_push_send'), {'token': token})
        delay.assert_not_called()

    def test_signed_out_device_is_not_counted(self):
        MobileDevice.objects.filter(user=self.amy).update(revoked_at='2026-01-01T00:00:00Z')
        p = self.preview(audience='member', user_id='pc-amy').context['preview']
        self.assertEqual(p['n'], 0)

    def test_master_switch_off_sends_nothing(self):
        token = self.preview().context['preview']['token']
        FeatureFlag.objects.update_or_create(name='push_notifications_enabled',
                                             defaults={'is_enabled': False, 'display_name': 'Push'})
        cache.clear()
        with mock.patch(DELAY) as delay:
            self.client.post(reverse('admin_v2_push_send'), {'token': token})
        delay.assert_not_called()

    def test_rate_limit(self):
        for i in range(push_views.RATE_LIMIT):
            _r, delay = self.send(title=f'Push {i}')
            self.assertEqual(delay.call_count, 4)
        r, delay = self.send(title='One too many')
        delay.assert_not_called()
        self.assertEqual(ActivityLog.objects.filter(object_type='AdminPush').count(), push_views.RATE_LIMIT)
        self.assertEqual(push_views.sends_left(self.admin), 0)
