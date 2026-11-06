"""
Admin console, phase 2: the Messaging and Content pages (email logs, event
reminders, notifications, push composer, web push, test email targeting,
announcements, events, legislation).

With `admin_console_ui` on each page renders its console template (list
header, ac-* table/forms, detail views as slide-out panels via ?panel=1);
with it off the view renders the classic template, and the classic templates
themselves are pinned byte for byte (CLASSIC_SHA256) so the flag stays a
clean switch until it is retired. Every form, field name, URL and confirm the
classic pages had is still on the console pages; the panel fragments sit
behind the same gate as the full pages; the new "Delete selected" endpoints
(src/view/admin_v2_messaging.py) are POST only and re-check every id.
Nothing here sends a real push or email (push tasks are mocked, as in
test_push_composer).
"""
import hashlib
import re
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.test import Client, SimpleTestCase
from django.urls import reverse
from django.utils import timezone

from src.models import (
    ActivityLog, Announcement, AnnouncementEmailLog, AnnouncementEmailRecipient, Event,
    EventReminderLog, EventReminderRecipient, Legislation, PushSubscription,
)
from src.models.notifications import NotificationLog, NotificationSchedule
from src.tests.admin_v2.base import AdminV2TestCase, make_user
from src.tests.admin_v2.test_push_composer import DELAY, PushComposerBase

TEMPLATES = Path(settings.BASE_DIR) / 'templates' / 'admin_v2'

#: The classic templates, exactly as they were before the console conversion.
#: The flag-off look must not change while `admin_console_ui` exists; if one of
#: these has to change on purpose, update its hash here in the same commit.
CLASSIC_SHA256 = {
    'email_logs.html': '10a6c2b5d8b43343468c44d337fe50d8057a781f88b870f8b0af752108931ff0',
    'email_log_detail.html': '4ed5896e08135fac3cbe2900f86486affca3b9380edcd47db02d44085def1085',
    'event_reminder_logs.html': 'c2d6720a4338917e11c0032191637ee84813d16988237bafe1096699be30b57e',
    'event_reminder_log_detail.html': 'bebd0caa27922099cbb24b3c5a799a9c9b66bd35715d59244b55758f5f87b3e6',
    'push_composer.html': '74fe4a8e51e5e29e480d5ad35600b85ca3ba38bfce40f0d9ccec60cffc90ecd6',
    'push_subscriptions.html': '905925dd20e226b7ad7f4530a483668be0d493e41c5b19fe71fbf716f1003259',
    'test_email_targeting.html': '7f9951c7a06090b7472544cb1bd514b47dfd8ed4c3156821941a908d754cc839',
    'manage_announcements.html': 'e9799bcb9dd903a71db7667a9f62bbfe8b276e7f9ad356a98cd439a096475e76',
    'manage_events.html': '45378bdceb91f1c6e2d716de52d183ba88269b8f4c87b824d277fe258a2cf7e0',
    'manage_legislation.html': '76ab52ac487b5210d06167fdaf8a9c39283f8b61a428f18735d4db7814246ec7',
    'notifications/dashboard.html': '7d0df53cf15cc275179dca668cbfe8ea08172817bd913f94b8b14e84590563de',
    'notifications/schedules.html': '7862f67e3ec019b0f05be230dcb7bed395ec7c1ca6cedccf07dc271be3986b35',
    'notifications/logs.html': 'ead3c5499977cebedc16af161f942e830e645e8144a9e9aa27c0511459820f40',
    'notifications/log_detail.html': '029fa527f488849a848f2f7863c2ef435751108d57703ee468401ba20f0c0c3f',
}

#: The console templates (and partials) this group added.
CONSOLE_FILES = (
    'email_logs.html', 'email_log_detail.html', 'email_log_panel.html', '_email_log_body.html',
    '_email_status_pill.html', 'event_reminder_logs.html', 'event_reminder_log_detail.html',
    'event_reminder_log_panel.html', '_event_reminder_log_body.html', '_reminder_status_pill.html',
    'notifications.html', 'notification_schedules.html', 'notification_logs.html',
    'notification_log_detail.html', 'notification_log_panel.html', '_notification_log_body.html',
    '_notification_status_pill.html', 'push_composer.html', 'push_subscriptions.html',
    'test_email_targeting.html', 'manage_announcements.html', 'manage_events.html',
    'manage_legislation.html',
)


class MessagingFixtures(AdminV2TestCase):

    def setUp(self):
        super().setUp()
        now = timezone.now()
        self.author = make_user('cm-author', name='Ana Author', member_type='Officer')
        self.member = make_user('cm-member', name='Mo Member', email='mo@example.test')
        self.announcement = Announcement.objects.create(
            title='Chapter meeting Sunday', content='See you there.', posted_by=self.author, is_active=True)
        self.pending = Announcement.objects.create(
            title='Dues reminder', content='Pay up.', posted_by=self.author, is_active=True,
            send_email_on_publish=True)
        self.email_log = AnnouncementEmailLog.objects.create(
            announcement=self.announcement, initiated_by=self.author, total_active_users=10,
            users_matching_visibility=8, users_with_valid_email=7, emails_sent=6, emails_failed=1,
            status='partial', console_log='step 1\nstep 2', error_message='SMTP hiccup')
        AnnouncementEmailRecipient.objects.create(
            email_log=self.email_log, user=self.member, user_name='Mo Member', user_email='mo@example.test',
            user_member_type='Member', user_member_status='Active', status='sent')
        AnnouncementEmailRecipient.objects.create(
            email_log=self.email_log, user=None, user_name='Fay Failed', user_email='fay@example.test',
            user_member_type='Member', user_member_status='Active', status='failed', error_message='Mailbox full')
        self.event = Event.objects.create(
            title='Founders Day Dinner', date_time=now + timedelta(days=5), location='Chapter House',
            created_by=self.author, is_active=True, reminder_1_enabled=True, reminder_1_hours_before=24)
        self.reminder = EventReminderLog.objects.create(
            event=self.event, reminder_slot=1, users_eligible=5, users_subscribed=4, users_opted_out=1,
            notifications_dispatched=3)
        EventReminderRecipient.objects.create(
            reminder_log=self.reminder, user=self.member, user_name='Mo Member', user_member_type='Member',
            status='dispatched')
        self.schedule = NotificationSchedule.objects.create(
            name='Day-before reminder', notification_type='event_reminder', hours_before=24,
            message_template='Tomorrow: {event_name}', created_by=self.author)
        self.notification = NotificationLog.objects.create(
            schedule=self.schedule, notification_type='event_reminder', title='Dinner tomorrow',
            message='Founders Day Dinner is tomorrow.', recipient_count=4, successful_count=3,
            failed_count=1, status='sent', sent_via_in_app=True)
        self.legislation = Legislation.objects.create(
            title='Quiet hours bill', description='Moves quiet hours.', posted_by=self.author,
            available_at=now, vote_mode='percentage', required_percentage='51', document='test.pdf')
        self.subscription = PushSubscription.objects.create(
            user=self.member, endpoint='https://push.example/cm-member', p256dh='k', auth='a',
            user_agent='Mozilla/5.0 Test')


#: (url name, args factory, console template, classic template)
PAGES = (
    ('admin_v2_email_logs', lambda t: [], 'admin_v2/console/email_logs.html', 'admin_v2/email_logs.html'),
    ('admin_v2_email_log_detail', lambda t: [t.email_log.pk],
     'admin_v2/console/email_log_detail.html', 'admin_v2/email_log_detail.html'),
    ('admin_v2_event_reminder_logs', lambda t: [],
     'admin_v2/console/event_reminder_logs.html', 'admin_v2/event_reminder_logs.html'),
    ('admin_v2_event_reminder_log_detail', lambda t: [t.reminder.pk],
     'admin_v2/console/event_reminder_log_detail.html', 'admin_v2/event_reminder_log_detail.html'),
    ('admin_v2_notifications', lambda t: [],
     'admin_v2/console/notifications.html', 'admin_v2/notifications/dashboard.html'),
    ('admin_v2_notification_schedules', lambda t: [],
     'admin_v2/console/notification_schedules.html', 'admin_v2/notifications/schedules.html'),
    ('admin_v2_notification_logs', lambda t: [],
     'admin_v2/console/notification_logs.html', 'admin_v2/notifications/logs.html'),
    ('admin_v2_notification_log_detail', lambda t: [t.notification.pk],
     'admin_v2/console/notification_log_detail.html', 'admin_v2/notifications/log_detail.html'),
    ('admin_v2_push', lambda t: [], 'admin_v2/console/push_composer.html', 'admin_v2/push_composer.html'),
    ('admin_v2_push_subscriptions', lambda t: [],
     'admin_v2/console/push_subscriptions.html', 'admin_v2/push_subscriptions.html'),
    ('admin_v2_test_email_targeting', lambda t: [],
     'admin_v2/console/test_email_targeting.html', 'admin_v2/test_email_targeting.html'),
    ('admin_v2_manage_announcements', lambda t: [],
     'admin_v2/console/manage_announcements.html', 'admin_v2/manage_announcements.html'),
    ('admin_v2_manage_events', lambda t: [],
     'admin_v2/console/manage_events.html', 'admin_v2/manage_events.html'),
    ('admin_v2_manage_legislation', lambda t: [],
     'admin_v2/console/manage_legislation.html', 'admin_v2/manage_legislation.html'),
)


def template_names(response):
    return {t.name for t in response.templates if t.name}


class SwitchTests(MessagingFixtures):

    def test_on_renders_the_console_template(self):
        self.set_console(True)
        for name, args, console, classic in PAGES:
            with self.subTest(page=name):
                r = self.client.get(reverse(name, args=args(self)))
                self.assertEqual(r.status_code, 200)
                used = template_names(r)
                self.assertIn(console, used)
                self.assertNotIn(classic, used)
                html = r.content.decode()
                self.assertIn('class="ac-card', html)
                self.assertIn('class="ac-list-head"', html)

    def test_off_renders_the_classic_template_only(self):
        self.set_console(False)
        for name, args, console, classic in PAGES:
            with self.subTest(page=name):
                r = self.client.get(reverse(name, args=args(self)))
                self.assertEqual(r.status_code, 200)
                used = template_names(r)
                self.assertIn(classic, used)
                self.assertFalse([t for t in used if t.startswith('admin_v2/console/')], used)
                self.assertNotIn('ac-card', r.content.decode())

    def test_classic_templates_are_unchanged(self):
        for rel, digest in CLASSIC_SHA256.items():
            with self.subTest(template=rel):
                self.assertEqual(hashlib.sha256((TEMPLATES / rel).read_bytes()).hexdigest(), digest)

    def test_panel_fragment_ignores_the_flag_like_members(self):
        """?panel=1 is only ever asked for by panel.js; it answers the same either way."""
        self.set_console(False)
        r = self.client.get(reverse('admin_v2_email_log_detail', args=[self.email_log.pk]), {'panel': '1'})
        self.assertEqual(r['X-Admin-Panel'], '1')


class ListPageTests(MessagingFixtures):

    def get(self, name, *args, **params):
        return self.client.get(reverse(name, args=args), params).content.decode()

    def test_email_logs(self):
        html = self.get('admin_v2_email_logs')
        self.assertIn('<h1>Email logs <span class="ac-count">(1)</span></h1>', html)
        self.assertIn(f'href="{reverse("admin_v2_email_log_detail", args=[self.email_log.pk])}" '
                      'class="ac-row-link" data-ac-panel', html)
        self.assertIn(f'href="{reverse("admin_v2_test_email_targeting")}" class="ac-btn">Test targeting</a>', html)
        # The pending announcement keeps its Send now POST form, now confirmed by data-confirm.
        self.assertIn(f'action="{reverse("admin_v2_send_scheduled_email", args=[self.pending.pk])}" '
                      'class="send-now-form"', html)
        self.assertIn('data-confirm="Send emails for this announcement now?"', html)
        self.assertIn('Dues reminder', html)
        self.assertIn('ac-pill is-warn">Partial', html)

    def test_event_reminder_logs(self):
        html = self.get('admin_v2_event_reminder_logs')
        self.assertIn(f'href="{reverse("admin_v2_event_reminder_log_detail", args=[self.reminder.pk])}" '
                      'class="ac-row-link" data-ac-panel', html)
        self.assertIn('Still to fire', html)
        self.assertIn('Reminder 1: 24h before', html)

    def test_notifications_overview_and_logs(self):
        html = self.get('admin_v2_notifications')
        self.assertIn(reverse('admin_v2_notification_schedules'), html)
        self.assertIn(f'href="{reverse("admin_v2_notification_log_detail", args=[self.notification.pk])}" '
                      'class="ac-row-link" data-ac-panel', html)
        html = self.get('admin_v2_notification_logs', status='sent')
        for field in ('name="status"', 'name="type"', 'name="from"', 'name="to"'):
            self.assertIn(field, html)
        self.assertIn('<option value="sent" selected>Sent</option>', html)
        self.assertIn('Dinner tomorrow', html)
        self.assertIn('(1 failed)', html)
        html = self.get('admin_v2_notification_logs', status='failed')
        self.assertIn('ac-empty', html)

    def test_schedules_keep_their_hooks(self):
        html = self.get('admin_v2_notification_schedules')
        self.assertIn('href="#new-schedule" class="ac-btn ac-btn-primary">New schedule</a>', html)
        self.assertIn('<dialog id="new-schedule"', html)
        self.assertIn(f'data-create-url="{reverse("admin_v2_create_notification_schedule")}"', html)
        self.assertIn(f'data-toggle-url="{reverse("admin_v2_toggle_notification_schedule", args=[self.schedule.pk])}"', html)
        self.assertIn(f'data-delete-url="{reverse("admin_v2_delete_notification_schedule", args=[self.schedule.pk])}"', html)
        for field in ('name="name"', 'name="notification_type"', 'name="description"', 'name="hours_before"',
                      'name="target_audience"', 'name="send_email"', 'name="send_in_app"',
                      'name="message_template"', 'name="csrfmiddlewaretoken"'):
            self.assertIn(field, html)
        self.assertIn('js/admin_v2/notification_schedules.js', html)

    def test_push_subscriptions(self):
        html = self.get('admin_v2_push_subscriptions')
        self.assertIn(f'action="{reverse("admin_v2_delete_push_subscription", args=[self.subscription.pk])}"', html)
        self.assertIn(f'action="{reverse("admin_v2_clear_push_subscriptions")}"', html)
        self.assertIn('data-confirm="Delete ALL push subscriptions?', html)
        self.assertIn(f'value="{self.subscription.pk}" data-ac-row-select', html)
        self.assertIn(f'action="{reverse("admin_v2_delete_push_subscriptions_bulk")}" data-ac-bulk-form', html)

    def test_content_lists(self):
        cases = (
            ('admin_v2_manage_announcements', self.announcement, 'admin_v2_delete_announcement',
             'admin_v2_delete_announcements_bulk', 'Announcements', 2),
            ('admin_v2_manage_events', self.event, 'admin_v2_delete_event', 'admin_v2_delete_events_bulk', 'Events', 1),
            ('admin_v2_manage_legislation', self.legislation, 'admin_v2_delete_legislation',
             'admin_v2_delete_legislation_bulk', 'Legislation', 1),
        )
        for name, obj, delete, bulk, title, count in cases:
            with self.subTest(page=name):
                html = self.get(name)
                self.assertIn(f'<h1>{title} <span class="ac-count">({count})</span></h1>', html)
                form = html[html.index(f'action="{reverse(delete, args=[obj.pk])}"'):]
                self.assertIn('data-confirm=', form[:form.index('>')])
                self.assertIn(f'value="{obj.pk}" data-ac-row-select', html)
                self.assertIn(f'action="{reverse(bulk)}" data-ac-bulk-form data-confirm=', html)
                self.assertIn('class="ac-pager"', html)
        self.assertIn('target="_blank" rel="noopener">View</a>', self.get('admin_v2_manage_legislation'))
        # Filters keep their field names and values.
        html = self.get('admin_v2_manage_events', search='Founders', archived='no', active='yes')
        self.assertIn('name="search" value="Founders"', html)
        self.assertIn('<option value="no" selected>No</option>', html)
        html = self.get('admin_v2_manage_legislation', status='draft')
        self.assertIn('<option value="draft" selected>Draft</option>', html)

    def test_test_email_targeting(self):
        html = self.get('admin_v2_test_email_targeting')
        self.assertIn('name="visibility" value="Member"', html)
        self.assertIn('class="ac-btn ac-btn-primary">Test targeting</button>', html)
        r = self.client.post(reverse('admin_v2_test_email_targeting'), {'visibility': ['Member']})
        html = r.content.decode()
        self.assertIn('Would receive email', html)
        self.assertIn('name="visibility" value="Member" checked', html)
        self.assertIn('Excluded by visibility', html)

    def test_one_gold_button_at_most(self):
        self.set_console(True)
        for name, args, _console, _classic in PAGES:
            with self.subTest(page=name):
                html = self.client.get(reverse(name, args=args(self))).content.decode()
                self.assertLessEqual(html.count('ac-btn-primary'), 1)


class PanelTests(MessagingFixtures):

    def details(self):
        return (
            ('admin_v2_email_log_detail', self.email_log.pk, self.announcement.title,
             ('Delivery', 'Recipients', 'Console')),
            ('admin_v2_event_reminder_log_detail', self.reminder.pk, self.event.title, ('Summary', 'Recipients')),
            ('admin_v2_notification_log_detail', self.notification.pk, self.notification.title,
             ('Delivery', 'Message')),
        )

    def test_fragments(self):
        for name, pk, title, tabs in self.details():
            with self.subTest(page=name):
                r = self.client.get(reverse(name, args=[pk]), {'panel': '1'})
                self.assertEqual(r.status_code, 200)
                self.assertEqual(r['X-Admin-Panel'], '1')
                self.assertEqual(r['Cache-Control'], 'no-store')
                html = r.content.decode()
                self.assertNotIn('<html', html)
                self.assertNotIn('<script', html)
                self.assertIn('id="ac-blade-title"', html)
                self.assertIn(title, html)
                self.assertIn('data-ac-panel-close', html)
                for tab in tabs:
                    self.assertIn(f'data-ac-tab="{tab.lower()}">{tab}</button>', html)

    def test_email_log_panel_content(self):
        html = self.client.get(reverse('admin_v2_email_log_detail', args=[self.email_log.pk]),
                               {'panel': '1'}).content.decode()
        self.assertIn('Mailbox full', html)
        self.assertIn('SMTP hiccup', html)
        self.assertIn('step 1\nstep 2', html)
        self.assertIn('width="75"', html)  # 6 of 8 sent

    def test_full_pages_are_full_pages(self):
        for name, pk, title, _tabs in self.details():
            with self.subTest(page=name):
                r = self.client.get(reverse(name, args=[pk]))
                self.assertNotIn('X-Admin-Panel', r)
                html = r.content.decode()
                self.assertIn('<html', html)
                self.assertIn(f'<li class="ac-crumb-item">{title}</li>', html)
                self.assertNotIn('role="tabpanel"', html)  # sections stacked, no tabs on the page

    def test_same_gate_as_the_full_page(self):
        outsider = Client()
        outsider.force_login(make_user('cm-outsider', is_admin=True))
        stale = Client()
        stale.force_login(self.admin)
        for name, pk, title, _tabs in self.details():
            url = reverse(name, args=[pk])
            with self.subTest(page=name):
                r = Client().get(url, {'panel': '1'})
                self.assertEqual(r.status_code, 302)
                self.assertNotIn('X-Admin-Panel', r)
                r = outsider.get(url, {'panel': '1'})
                self.assertEqual(r.status_code, 302)
                self.assertNotIn(title, r.content.decode())
                r = stale.get(url, {'panel': '1'})
                self.assertRedirects(r, reverse('admin_v2_login'), fetch_redirect_response=False)

    def test_missing_is_404(self):
        for name, _pk, _title, _tabs in self.details():
            with self.subTest(page=name):
                self.assertEqual(self.client.get(reverse(name, args=[999999]), {'panel': '1'}).status_code, 404)


class PushComposerConsoleTests(PushComposerBase):
    """The preview → confirm → send flow, in the console look."""

    def test_compose_form_keeps_its_fields(self):
        html = self.client.get(reverse('admin_v2_push')).content.decode()
        self.assertIn(f'action="{reverse("admin_v2_push")}"', html)
        for field in ('audience', 'committee', 'member_type', 'member_status', 'user_id', 'title', 'body', 'link'):
            self.assertIn(f'name="{field}"', html)
        self.assertIn('<button type="submit" class="ac-btn">Preview</button>', html)
        self.assertNotIn(reverse('admin_v2_push_send'), html)  # nothing to confirm before a preview

    def test_preview_then_confirm_then_send(self):
        r = self.preview()
        self.assertTemplateUsed(r, 'admin_v2/console/push_composer.html')
        html = r.content.decode()
        n = r.context['preview']['n']
        self.assertGreater(n, 0)
        self.assertIn(f'data-recipient-count>{n}</dd>', html)
        self.assertIn(f'action="{reverse("admin_v2_push_send")}"', html)
        self.assertIn(f'data-confirm="Send this push to {n} member(s)? It can\'t be taken back."', html)
        self.assertIn(f'name="token" value="{r.context["preview"]["token"]}"', html)
        self.assertIn('class="ac-btn ac-btn-primary">Send to', html)
        self.assertIn('Chapter tonight', html)
        sent, delay = self.send()
        self.assertEqual(sent.status_code, 302)
        self.assertTrue(delay.called)

    def test_preview_error_is_shown(self):
        r = self.preview(title='')
        self.assertEqual(r.status_code, 400)
        self.assertTemplateUsed(r, 'admin_v2/console/push_composer.html')
        self.assertIn('role="alert"', r.content.decode())

    def test_master_switch_off_hides_send(self):
        from src.view import admin_v2_push as push_views
        with mock.patch.object(push_views, 'push_master_on', return_value=False):
            html = self.preview().content.decode()
        self.assertIn('switched off site-wide', html)
        self.assertNotIn(reverse('admin_v2_push_send'), html)

    def test_classic_flow_when_off(self):
        self.set_console(False)
        r = self.preview()
        self.assertTemplateUsed(r, 'admin_v2/push_composer.html')
        self.assertTemplateNotUsed(r, 'admin_v2/console/push_composer.html')
        self.assertIn(f'action="{reverse("admin_v2_push_send")}"', r.content.decode())


class BulkDeleteTests(MessagingFixtures):

    def cases(self):
        return (
            ('admin_v2_delete_announcements_bulk', Announcement, [self.announcement.pk, self.pending.pk],
             'announcement_deleted', 'admin_v2_manage_announcements'),
            ('admin_v2_delete_events_bulk', Event, [self.event.pk], 'event_deleted', 'admin_v2_manage_events'),
            ('admin_v2_delete_legislation_bulk', Legislation, [self.legislation.pk], 'legislation_deleted',
             'admin_v2_manage_legislation'),
            ('admin_v2_delete_push_subscriptions_bulk', PushSubscription, [self.subscription.pk],
             'push_subscription_deleted', 'admin_v2_push_subscriptions'),
        )

    def test_get_is_refused(self):
        for name, *_rest in self.cases():
            with self.subTest(url=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 405)

    def test_deletes_and_logs_each_ticked_row(self):
        for name, model, ids, action, back in self.cases():
            with self.subTest(url=name):
                r = self.client.post(reverse(name), {'ids': [str(i) for i in ids] + ['nope', str(ids[0]), '999999']})
                self.assertRedirects(r, reverse(back), fetch_redirect_response=False)
                self.assertFalse(model.objects.filter(pk__in=ids).exists())
                self.assertEqual(ActivityLog.objects.filter(action_type=action).count(), len(ids))

    def test_nothing_ticked_deletes_nothing(self):
        r = self.client.post(reverse('admin_v2_delete_events_bulk'), {'ids': ['x']}, follow=True)
        self.assertTrue(Event.objects.filter(pk=self.event.pk).exists())
        self.assertIn('Nothing was deleted', r.content.decode())

    def test_same_gate(self):
        outsider = Client()
        outsider.force_login(make_user('cm-bulk-out', is_admin=True))
        for name, model, ids, _action, _back in self.cases():
            with self.subTest(url=name):
                r = Client().post(reverse(name), {'ids': ids})
                self.assertEqual(r.status_code, 302)
                r = outsider.post(reverse(name), {'ids': ids})
                self.assertEqual(r.status_code, 302)
                self.assertTrue(model.objects.filter(pk__in=ids).exists())


class TemplateRulesTests(SimpleTestCase):
    """CSP: no style=, no inline <script>, no inline handlers in the new templates."""

    def test_no_inline_style_or_script(self):
        for name in CONSOLE_FILES:
            text = (TEMPLATES / 'console' / name).read_text()
            with self.subTest(template=name):
                self.assertIsNone(re.search(r'<[a-zA-Z][^>]*\sstyle=', text))
                for tag in re.findall(r'<script\b[^>]*>', text):
                    self.assertIn('src=', tag)
                self.assertIsNone(re.search(r'<[a-zA-Z][^>]*\bon[a-z]+=', text))

    def test_page_css_stays_in_messaging_css(self):
        css = (Path(settings.BASE_DIR) / 'static' / 'css' / 'admin-console' / 'messaging.css').read_text()
        self.assertIn('.ac-msg-meter', css)
        self.assertIn('.ac-dialog', css)
