"""
v3.49.0 — GroupMe bot: announcements echoed to GroupMe, managed from
/officers/groupme/. GroupMe itself is never called: `requests.request` in
src.groupme is mocked, and Celery `.delay` runs the task inline.

Run with: python manage.py test src.tests.officer.test_groupme_bot
"""
from contextlib import contextmanager
from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.db import connection
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from src import groupme
from src.models import (
    Announcement, GroupMeChannel, GroupMeMessage, GroupMeSettings, ParliamentUser,
)
from src.tasks import groupme as groupme_tasks

TOKEN = 'abcDEF1234567890tokenvalue'


def make_user(uid, member_type='Member'):
    u = ParliamentUser.objects.create(user_id=uid, username=uid, name=f'User {uid}',
                                      member_type=member_type, member_status='Active')
    u.set_password('groupme-test-pass-12345!')
    u.save()
    return u


def fake_response(status=200, payload=None):
    r = mock.Mock()
    r.status_code = status
    r.json.return_value = {'response': payload, 'meta': {'code': status}}
    r.text = ''
    return r


class FakeGroupMe:
    """Stands in for api.groupme.com and records every call."""

    def __init__(self):
        self.calls = []
        self.post_status = 202
        self.next_bot = 1

    def __call__(self, method, url, headers=None, json=None, params=None, **kw):
        path = url.replace(groupme.API_BASE, '')
        self.calls.append({'method': method, 'path': path, 'headers': headers or {}, 'json': json})
        if path == '/users/me':
            if (headers or {}).get('X-Access-Token') != TOKEN:
                return fake_response(401)
            return fake_response(200, {'id': '42', 'name': 'Mason K'})
        if path == '/groups':
            return fake_response(200, [{'id': '111', 'name': 'AM Brothers', 'members_count': 60},
                                       {'id': '222', 'name': 'AM Everyone', 'members_count': 80},
                                       {'id': '333', 'name': 'AM Brothers 2027', 'members_count': 61}])
        if path == '/bots':
            bot_id = f'bot{self.next_bot:06d}'
            self.next_bot += 1
            return fake_response(201, {'bot': {'bot_id': bot_id, 'group_id': json['bot']['group_id']}})
        if path == '/bots/destroy':
            return fake_response(200)
        if path == '/bots/post':
            return fake_response(self.post_status)
        return fake_response(404)

    def posts(self):
        return [c for c in self.calls if c['path'] == '/bots/post']


@contextmanager
def inline_celery():
    """`.delay()` runs the task now, like a worker would."""
    with mock.patch.object(groupme_tasks.dispatch_announcement_to_groupme, 'delay',
                           side_effect=lambda pk: groupme_tasks.dispatch_announcement_to_groupme(pk)), \
         mock.patch.object(groupme_tasks.send_groupme_message, 'delay',
                           side_effect=lambda pk: groupme_tasks.send_groupme_message(pk)):
        yield


class GroupMeFixture:
    def setUp(self):
        cache.clear()
        self.officer = make_user('GM-OFF', 'Officer')
        self.member = make_user('GM-MEM')
        self.oc = Client(); self.oc.force_login(self.officer)
        self.mc = Client(); self.mc.force_login(self.member)
        self.api = FakeGroupMe()
        patcher = mock.patch('src.groupme.requests.request', side_effect=self.api)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.brothers = GroupMeChannel.objects.create(
            name='Brothers', group_id='111', bot_id='botBROTHERS1', audience=['Member'])
        self.everyone = GroupMeChannel.objects.create(
            name='Brothers + Pledges', group_id='222', bot_id='botEVERYONE1', audience=['Member', 'Pledge'])

    def tearDown(self):
        cache.clear()

    def create(self, visible_to=(), channels=(), publish_at=None, title='Chapter meeting'):
        data = {'title': title, 'content': 'Sunday at 7 in the house.', 'is_active': 'on',
                'visible_to': list(visible_to),
                'groupme_channel_ids': [str(c.pk) for c in channels]}
        if publish_at:
            data['publish_at'] = timezone.localtime(publish_at).strftime('%Y-%m-%dT%H:%M')
        with inline_celery(), self.captureOnCommitCallbacks(execute=True):
            r = self.oc.post(reverse('create_announcement'), data)
        self.assertEqual(r.status_code, 302, r.content[:500])
        return Announcement.objects.latest('pk')


class AudienceRuleTests(TestCase):
    def ch(self, pk, audience):
        return GroupMeChannel(pk=pk, name=str(pk), bot_id='x', audience=audience)

    def test_everyone_announcement_allows_every_channel(self):
        self.assertTrue(groupme.channel_allowed(None, self.ch(1, ['Member', 'Pledge'])))

    def test_members_only_cannot_go_where_pledges_are(self):
        self.assertTrue(groupme.channel_allowed(['Member'], self.ch(1, ['Member'])))
        self.assertFalse(groupme.channel_allowed(['Member'], self.ch(2, ['Member', 'Pledge'])))

    def test_pledges_only_goes_nowhere_with_brothers_in_it(self):
        self.assertFalse(groupme.channel_allowed(['Pledge'], self.ch(1, ['Member'])))
        self.assertFalse(groupme.channel_allowed(['Pledge'], self.ch(2, ['Member', 'Pledge'])))

    def test_defaults_pick_the_widest_group_so_nobody_gets_it_twice(self):
        b, e = self.ch(1, ['Member']), self.ch(2, ['Member', 'Pledge'])
        self.assertEqual(groupme.default_channels(None, [b, e]), [e])
        self.assertEqual(groupme.default_channels(['Member'], [b, e]), [b])
        self.assertEqual(groupme.default_channels(['Pledge'], [b, e]), [])


class FormatTests(TestCase):
    def test_long_announcement_fits_and_keeps_the_link(self):
        a = Announcement(title='Big news', content='x' * 5000)
        text = groupme.format_announcement(a)
        self.assertLessEqual(len(text), groupme.MAX_MESSAGE_CHARS)
        self.assertTrue(text.endswith(reverse('announcements')))
        self.assertIn('…', text)

    def test_title_only_mode(self):
        a = Announcement(title='Big news', content='secret details')
        self.assertNotIn('secret details', groupme.format_announcement(a, include_full_text=False))


class AnnouncementEchoTests(GroupMeFixture, TestCase):

    def test_published_announcement_posts_once_to_the_ticked_group(self):
        a = self.create(channels=[self.everyone])
        posts = self.api.posts()
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]['json']['bot_id'], 'botEVERYONE1')
        self.assertIn('Chapter meeting', posts[0]['json']['text'])
        # Posting never sends the account token.
        self.assertNotIn('X-Access-Token', posts[0]['headers'])
        self.assertEqual(GroupMeMessage.objects.get(announcement=a).status, 'sent')
        # The 5-minute sweep and a second dispatch don't post it again.
        with inline_celery():
            groupme_tasks.sweep_groupme()
            groupme_tasks.dispatch_announcement_to_groupme(a.pk)
        self.assertEqual(len(self.api.posts()), 1)

    def test_server_drops_a_group_the_audience_rule_forbids(self):
        # A members-only announcement with the pledge group ticked anyway
        # (e.g. a hand-made POST): only the brothers group is kept.
        a = self.create(visible_to=['Member'], channels=[self.brothers, self.everyone])
        self.assertEqual(list(a.groupme_channels.all()), [self.brothers])
        self.assertEqual([p['json']['bot_id'] for p in self.api.posts()], ['botBROTHERS1'])

    def test_pledges_only_announcement_posts_nowhere(self):
        a = self.create(visible_to=['Pledge'], channels=[self.brothers, self.everyone])
        self.assertEqual(a.groupme_channels.count(), 0)
        self.assertEqual(self.api.posts(), [])

    def test_no_box_ticked_means_no_post(self):
        self.create()
        self.assertEqual(self.api.posts(), [])

    def test_scheduled_announcement_posts_when_it_publishes(self):
        a = self.create(channels=[self.brothers], publish_at=timezone.now() + timedelta(hours=2))
        self.assertEqual(self.api.posts(), [])
        Announcement.objects.filter(pk=a.pk).update(publish_at=timezone.now() - timedelta(minutes=1))
        with inline_celery():
            from src.tasks import publish_scheduled_announcements
            publish_scheduled_announcements()
        self.assertEqual([p['json']['bot_id'] for p in self.api.posts()], ['botBROTHERS1'])

    def test_visible_to_narrowed_after_ticking_is_skipped_at_send_time(self):
        a = self.create(channels=[self.everyone], publish_at=timezone.now() + timedelta(hours=2))
        Announcement.objects.filter(pk=a.pk).update(
            visible_to=['Member'], publish_at=timezone.now() - timedelta(minutes=1))
        with inline_celery():
            groupme_tasks.sweep_groupme()
        self.assertEqual(self.api.posts(), [])
        m = GroupMeMessage.objects.get(announcement=a)
        self.assertEqual(m.status, 'skipped')
        self.assertIn('Pledges', m.error)

    def test_turned_off_channel_is_skipped(self):
        a = self.create(channels=[self.brothers], publish_at=timezone.now() + timedelta(hours=2))
        GroupMeChannel.objects.filter(pk=self.brothers.pk).update(is_active=False)
        Announcement.objects.filter(pk=a.pk).update(publish_at=timezone.now() - timedelta(minutes=1))
        with inline_celery():
            groupme_tasks.sweep_groupme()
        self.assertEqual(self.api.posts(), [])
        self.assertEqual(GroupMeMessage.objects.get(announcement=a).status, 'skipped')

    def test_transient_failure_is_marked_failed_and_can_be_retried(self):
        self.api.post_status = 503
        retry = mock.patch.object(groupme_tasks.send_groupme_message, 'retry',
                                  side_effect=RuntimeError('retry scheduled'))
        with retry as retry_mock:
            # (inline, the Retry is swallowed by queue_send's broker guard; in a
            # worker it reschedules the task)
            a = self.create(channels=[self.brothers])
        self.assertTrue(retry_mock.called)
        m = GroupMeMessage.objects.get(announcement=a)
        self.assertEqual(m.status, 'failed')
        self.assertIn('503', m.error)
        self.api.post_status = 202
        with inline_celery():
            r = self.oc.post(reverse('groupme_retry_message', args=[m.pk]))
        self.assertEqual(r.status_code, 302)
        m.refresh_from_db()
        self.assertEqual(m.status, 'sent')

    def test_create_form_preticks_the_widest_group(self):
        html = self.oc.get(reverse('create_announcement')).content.decode()
        self.assertIn(f'value="{self.everyone.pk}" id="groupme-ch-{self.everyone.pk}"', html)
        tag_everyone = html.split(f'id="groupme-ch-{self.everyone.pk}"')[1].split('>')[0]
        tag_brothers = html.split(f'id="groupme-ch-{self.brothers.pk}"')[1].split('>')[0]
        self.assertIn('checked', tag_everyone)
        self.assertNotIn('checked', tag_brothers)

    def test_edit_page_shows_the_result_after_posting_and_cannot_repost(self):
        a = self.create(channels=[self.brothers])
        html = self.oc.get(reverse('edit_announcement', args=[a.pk])).content.decode()
        self.assertIn('Already handed to the bot', html)
        with inline_celery(), self.captureOnCommitCallbacks(execute=True):
            self.oc.post(reverse('edit_announcement', args=[a.pk]), {
                'title': 'Edited', 'content': 'x', 'is_active': 'on',
                'groupme_channel_ids': [str(self.everyone.pk)]})
        self.assertEqual(len(self.api.posts()), 1)


class DashboardTests(GroupMeFixture, TestCase):

    def test_members_are_refused(self):
        self.assertEqual(self.mc.get(reverse('groupme_dashboard')).status_code, 403)
        self.assertEqual(self.mc.post(reverse('groupme_add_channel'), {}).status_code, 403)

    def test_actions_are_post_only(self):
        for name, args in [('groupme_save_settings', []), ('groupme_add_channel', []),
                           ('groupme_test_channel', [self.brothers.pk]),
                           ('groupme_remove_channel', [self.brothers.pk])]:
            self.assertEqual(self.oc.get(reverse(name, args=args)).status_code, 405, name)

    def test_token_is_checked_encrypted_and_never_shown(self):
        r = self.oc.post(reverse('groupme_save_settings'), {'access_token': TOKEN, 'bot_name': 'Parliament',
                                                             'include_full_text': 'on'})
        self.assertEqual(r.status_code, 302)
        s = GroupMeSettings.get_instance()
        self.assertEqual(s.access_token, TOKEN)
        self.assertEqual(s.token_owner_name, 'Mason K')
        with connection.cursor() as cur:
            cur.execute('SELECT access_token FROM src_groupmesettings')
            raw = cur.fetchone()[0]
        self.assertNotEqual(raw, TOKEN)
        html = self.oc.get(reverse('groupme_dashboard')).content.decode()
        self.assertNotIn(TOKEN, html)
        self.assertNotIn('botBROTHERS1', html)  # bot ids are masked too
        self.assertIn('Connected as Mason K', html)
        self.assertIn('AM Brothers 2027', html)  # the token's groups are listed

    def test_a_bad_token_is_not_saved(self):
        self.oc.post(reverse('groupme_save_settings'), {'access_token': 'wrongtoken123', 'bot_name': 'P'})
        self.assertFalse(GroupMeSettings.get_instance().has_token)

    def _connect(self):
        s = GroupMeSettings.get_instance()
        s.access_token, s.token_owner_name = TOKEN, 'Mason K'
        s.save()

    def test_add_the_bot_to_a_group(self):
        self._connect()
        self.oc.post(reverse('groupme_add_channel'), {'name': 'Alumni test', 'group_id': '333',
                                                       'audience': ['Member']})
        c = GroupMeChannel.objects.get(name='Alumni test')
        create = [x for x in self.api.calls if x['path'] == '/bots'][0]
        self.assertEqual(create['json']['bot']['group_id'], '333')
        self.assertEqual(create['headers']['X-Access-Token'], TOKEN)
        self.assertEqual((c.group_name, c.bot_created_here, c.audience), ('AM Brothers 2027', True, ['Member']))

    def test_register_an_existing_bot_without_a_token(self):
        self.oc.post(reverse('groupme_add_channel'), {'name': 'Hand made', 'bot_id': 'handmadeBot99',
                                                       'group_name': 'Old group', 'audience': ['Member']})
        c = GroupMeChannel.objects.get(name='Hand made')
        self.assertEqual((c.bot_id, c.bot_created_here), ('handmadeBot99', False))

    def test_a_channel_needs_an_audience(self):
        self.oc.post(reverse('groupme_add_channel'), {'name': 'Nobody', 'bot_id': 'handmadeBot99'})
        self.assertFalse(GroupMeChannel.objects.filter(name='Nobody').exists())

    def test_move_to_a_new_group_keeps_the_channel_and_removes_the_old_bot(self):
        self._connect()
        GroupMeChannel.objects.filter(pk=self.brothers.pk).update(bot_created_here=True)
        self.oc.post(reverse('groupme_move_channel', args=[self.brothers.pk]), {'group_id': '333'})
        c = GroupMeChannel.objects.get(pk=self.brothers.pk)
        self.assertEqual((c.group_id, c.group_name, c.name), ('333', 'AM Brothers 2027', 'Brothers'))
        self.assertNotEqual(c.bot_id, 'botBROTHERS1')
        destroy = [x for x in self.api.calls if x['path'] == '/bots/destroy']
        self.assertEqual(destroy[0]['json'], {'bot_id': 'botBROTHERS1'})

    def test_remove_deletes_the_channel_and_keeps_the_log(self):
        GroupMeMessage.objects.create(channel=self.brothers, channel_name='Brothers', kind='test',
                                      text='hi', status='sent')
        self.oc.post(reverse('groupme_remove_channel', args=[self.brothers.pk]))
        self.assertFalse(GroupMeChannel.objects.filter(pk=self.brothers.pk).exists())
        self.assertEqual(GroupMeMessage.objects.get().channel_name, 'Brothers')

    def test_test_message_is_sent_and_rate_limited(self):
        self.oc.post(reverse('groupme_test_channel', args=[self.brothers.pk]))
        self.oc.post(reverse('groupme_test_channel', args=[self.brothers.pk]))
        self.assertEqual(len(self.api.posts()), 1)
        self.assertEqual(GroupMeMessage.objects.get(kind='test').status, 'sent')

    def test_changes_are_in_the_activity_log(self):
        from src.models import ActivityLog
        self.oc.post(reverse('groupme_edit_channel', args=[self.brothers.pk]),
                     {'name': 'Brothers', 'audience': ['Member'], 'is_active': ''})
        self.assertFalse(GroupMeChannel.objects.get(pk=self.brothers.pk).is_active)
        self.assertTrue(ActivityLog.objects.filter(action_type='settings_changed',
                                                   description__startswith='GroupMe:').exists())


class WhereToFindItTests(TestCase):
    """v3.49.0 — Mason: "I don't see how to access it" / "I don't see any UI on
    the make announcements page"."""

    def setUp(self):
        cache.clear()
        self.officer = make_user('GM-OFF2', 'Officer')
        self.chair = make_user('GM-CHAIR', 'Chair')
        self.member = make_user('GM-MEM2')
        self.advisor = make_user('GM-ADV', 'Advisor')
        self.url = reverse('groupme_dashboard')

    def html(self, user, name):
        c = Client(); c.force_login(user)
        r = c.get(reverse(name))
        self.assertEqual(r.status_code, 200, name)
        return r.content.decode()

    def test_the_predicate_matches_the_page(self):
        for user in (self.officer, self.chair, self.member, self.advisor):
            c = Client(); c.force_login(user)
            can_open = c.get(self.url).status_code == 200
            self.assertEqual(user.can_manage_groupme, can_open, user.member_type)

    def test_home_quick_links_show_it_only_to_people_who_can_open_it(self):
        for layout in ('home.html', 'home_modern.html'):
            from django.template.loader import get_template
            self.assertIn("url 'groupme_dashboard'", get_template(layout).template.source, layout)
        self.assertIn(self.url, self.html(self.officer, 'home'))
        self.assertNotIn(self.url, self.html(self.member, 'home'))

    def test_officer_home_quick_links_hide_it_from_advisors(self):
        self.assertIn(self.url, self.html(self.officer, 'officer_home'))
        self.assertNotIn(self.url, self.html(self.advisor, 'officer_home'))

    def test_announcement_form_says_so_when_nothing_is_set_up(self):
        html = self.html(self.officer, 'create_announcement')
        self.assertIn('No GroupMe groups are connected yet', html)
        self.assertIn(self.url, html)
        GroupMeChannel.objects.create(name='Off', bot_id='botOFF00001', audience=['Member'], is_active=False)
        self.assertIn('Every GroupMe group is turned off', self.html(self.officer, 'create_announcement'))
        GroupMeChannel.objects.create(name='Brothers', bot_id='botON000001', audience=['Member'])
        html = self.html(self.officer, 'create_announcement')
        self.assertIn('Manage GroupMe groups', html)
        self.assertIn('name="groupme_channel_ids"', html)


class EventReminderTests(GroupMeFixture, TestCase):
    """v3.50.0 — a reminder posted before an event."""

    def make_event(self, hours_away, visible_to=None, channels=(), reminder_hours=24, **kw):
        from src.models import Event
        e = Event.objects.create(title='Chapter meeting', description='x', location='The house',
                                 date_time=timezone.now() + timedelta(hours=hours_away),
                                 created_by=self.officer, visible_to=visible_to,
                                 groupme_reminder_hours=reminder_hours, **kw)
        e.groupme_channels.set(channels)
        return e

    def sweep(self):
        with inline_celery():
            groupme_tasks.sweep_groupme()

    def test_posts_once_when_the_reminder_time_comes(self):
        e = self.make_event(30, channels=[self.everyone])
        self.sweep()
        self.assertEqual(self.api.posts(), [])          # 30 h away, reminder at 24 h
        from src.models import Event
        Event.objects.filter(pk=e.pk).update(date_time=timezone.now() + timedelta(hours=20))
        self.sweep(); self.sweep()
        posts = self.api.posts()
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]['json']['bot_id'], 'botEVERYONE1')
        self.assertIn('⏰ Reminder: Chapter meeting', posts[0]['json']['text'])
        self.assertIn('The house', posts[0]['json']['text'])

    def test_past_events_and_unticked_events_are_ignored(self):
        self.make_event(-1, channels=[self.everyone])
        self.make_event(2)
        self.sweep()
        self.assertEqual(self.api.posts(), [])

    def test_members_only_event_skips_the_pledge_group(self):
        e = self.make_event(2, visible_to=['Member'], channels=[self.everyone, self.brothers])
        self.sweep()
        self.assertEqual([p['json']['bot_id'] for p in self.api.posts()], ['botBROTHERS1'])
        self.assertEqual(GroupMeMessage.objects.get(event=e, channel=self.everyone).status, 'skipped')

    def test_event_form_offers_the_groups(self):
        html = self.oc.get(reverse('create_event')).content.decode()
        self.assertIn('GroupMe reminder', html)
        self.assertIn('name="groupme_reminder_hours"', html)

    def test_save_keeps_only_allowed_groups(self):
        from django.test import RequestFactory
        from src.groupme import save_event_channels
        e = self.make_event(48, visible_to=['Member'])
        req = RequestFactory().post('/', {'groupme_channel_ids': [str(self.everyone.pk), str(self.brothers.pk)],
                                          'groupme_reminder_hours': '6'})
        save_event_channels(req, [e])
        e.refresh_from_db()
        self.assertEqual(list(e.groupme_channels.all()), [self.brothers])
        self.assertEqual(e.groupme_reminder_hours, 6)


class CspRetentionTests(TestCase):
    def test_reports_older_than_30_days_are_deleted(self):
        from src.models import CSPViolation
        from src.tasks.cleanup import prune_old_csp_violations
        old = CSPViolation.objects.create(violated_directive='script-src', blocked_uri='inline')
        new = CSPViolation.objects.create(violated_directive='script-src', blocked_uri='inline')
        CSPViolation.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=31))
        self.assertEqual(prune_old_csp_violations(), 1)
        self.assertEqual(list(CSPViolation.objects.values_list('pk', flat=True)), [new.pk])
