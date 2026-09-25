"""
09-25-26 — meeting agendas → minutes drafts ("meeting mode", first slice).

Run with: python manage.py test src.tests.officer.test_meeting_agendas
"""
import datetime

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from src.models import AgendaItem, ChapterMinutes, MeetingAgenda, MinutesSection, ParliamentUser


def make_user(uid, member_type='Member', **kw):
    u = ParliamentUser.objects.create(user_id=uid, username=uid, name=f'User {uid}',
                                      member_type=member_type, member_status='Active', **kw)
    u.set_password('agenda-test-pass-12345!')
    u.save()
    return u


class MeetingAgendaTests(TestCase):
    def setUp(self):
        self.officer = make_user('AG-OFF', member_type='Officer')
        self.member = make_user('AG-MEM')
        self.oc = Client(); self.oc.force_login(self.officer)
        self.mc = Client(); self.mc.force_login(self.member)
        self.day = (timezone.localdate() + datetime.timedelta(days=3)).isoformat()

    def _create(self, standard=True):
        data = {'title': 'Chapter Meeting', 'date': self.day, 'start_time': '19:00'}
        if standard:
            data['standard_order'] = 'on'
        r = self.oc.post(reverse('create_agenda'), data)
        self.assertEqual(r.status_code, 302)
        return MeetingAgenda.objects.latest('pk')

    def test_create_with_the_standard_order_of_business(self):
        a = self._create()
        self.assertEqual([i.item_type for i in a.items.all()], AgendaItem.STANDARD_ORDER)

    def test_members_cannot_create_or_edit(self):
        r = self.mc.post(reverse('create_agenda'), {'title': 'x', 'date': self.day, 'start_time': '19:00'})
        self.assertEqual(r.status_code, 403)
        a = self._create()
        self.assertEqual(self.mc.get(reverse('edit_agenda', args=[a.pk])).status_code, 403)

    def test_members_see_only_published_agendas(self):
        a = self._create()
        self.assertEqual(self.mc.get(reverse('agenda_detail', args=[a.pk])).status_code, 404)
        self.assertNotIn('Chapter Meeting', self.mc.get(reverse('agenda_list')).content.decode())
        self.oc.post(reverse('set_agenda_status', args=[a.pk]), {'status': 'published'})
        self.assertEqual(self.mc.get(reverse('agenda_detail', args=[a.pk])).status_code, 200)
        self.assertIn('Chapter Meeting', self.mc.get(reverse('agenda_list')).content.decode())

    def test_add_edit_reorder_and_remove_items(self):
        a = self._create(standard=False)
        url = reverse('add_agenda_item', args=[a.pk])
        self.oc.post(url, {'item_type': 'new_business', 'title': 'Budget vote', 'notes': 'Spring budget',
                           'presenter': self.officer.pk, 'duration_minutes': '10'})
        self.oc.post(url, {'item_type': 'announcements', 'title': ''})
        first, second = a.items.all()
        self.assertEqual((first.title, first.presenter, first.duration_minutes), ('Budget vote', self.officer, 10))
        self.assertEqual(second.title, 'Announcements')                      # defaults to the type
        self.oc.post(reverse('update_agenda_item', args=[a.pk, second.pk]), {'action': 'up'})
        self.assertEqual([i.title for i in a.items.all()], ['Announcements', 'Budget vote'])
        self.oc.post(reverse('update_agenda_item', args=[a.pk, first.pk]), {'action': 'delete'})
        self.assertEqual(a.items.count(), 1)

    def test_bad_input_is_clamped(self):
        a = self._create(standard=False)
        self.oc.post(reverse('add_agenda_item', args=[a.pk]),
                     {'item_type': 'nonsense', 'title': 'x' * 500, 'duration_minutes': '99999'})
        item = a.items.get()
        self.assertEqual(item.item_type, 'custom')
        self.assertEqual(len(item.title), 200)
        self.assertIsNone(item.duration_minutes)

    def test_start_minutes_lays_out_the_agenda_and_is_idempotent(self):
        a = self._create(standard=False)
        self.oc.post(reverse('add_agenda_item', args=[a.pk]),
                     {'item_type': 'officer_reports', 'title': 'Officer Reports', 'notes': 'Treasurer first',
                      'presenter_text': 'Guest Advisor'})
        r = self.oc.post(reverse('start_minutes_from_agenda', args=[a.pk]))
        a.refresh_from_db()
        self.assertRedirects(r, reverse('edit_chapter_minutes', args=[a.minutes_id]), fetch_redirect_response=False)
        secs = list(MinutesSection.objects.filter(minutes=a.minutes).order_by('order'))
        self.assertEqual([(s.section_type, s.title) for s in secs][0], ('header', 'Officer Reports'))
        self.assertIn('Presented by Guest Advisor.', secs[1].content)
        self.assertIn('Treasurer first', secs[1].content)
        self.oc.post(reverse('start_minutes_from_agenda', args=[a.pk]))
        self.assertEqual(ChapterMinutes.objects.count(), 1)

    def test_the_editor_and_list_render(self):
        a = self._create()
        self.assertEqual(self.oc.get(reverse('edit_agenda', args=[a.pk])).status_code, 200)
        self.assertEqual(self.oc.get(reverse('agenda_list')).status_code, 200)


class MeetingAgendaInputValidationTests(TestCase):
    """09-25-26 (auto-run finding) — malformed date/time/event were a 500."""
    def setUp(self):
        self.officer = make_user('AGV-OFF', member_type='Officer')
        self.oc = Client(); self.oc.force_login(self.officer)
        self.day = (timezone.localdate() + datetime.timedelta(days=3)).isoformat()

    def test_malformed_date_or_time_is_a_form_error_not_a_500(self):
        for data in ({'date': '2026-13-45', 'start_time': '19:00'},
                     {'date': 'tomorrow', 'start_time': '19:00'},
                     {'date': self.day, 'start_time': '25:99'}):
            r = self.oc.post(reverse('create_agenda'), {'title': 'Meeting', **data})
            self.assertEqual(r.status_code, 302, data)
            self.assertRedirects(r, reverse('agenda_list'), fetch_redirect_response=False)
        self.assertFalse(MeetingAgenda.objects.exists())

    def test_a_non_numeric_event_id_is_ignored(self):
        r = self.oc.post(reverse('create_agenda'), {'title': 'Meeting', 'date': self.day,
                                                    'start_time': '19:00', 'event': 'abc'})
        self.assertEqual(r.status_code, 302)
        self.assertIsNone(MeetingAgenda.objects.get().event)

    def test_start_minutes_twice_still_makes_one_draft(self):
        self.oc.post(reverse('create_agenda'), {'title': 'Meeting', 'date': self.day, 'start_time': '19:00'})
        a = MeetingAgenda.objects.get()
        self.oc.post(reverse('start_minutes_from_agenda', args=[a.pk]))
        self.oc.post(reverse('start_minutes_from_agenda', args=[a.pk]))
        self.assertEqual(ChapterMinutes.objects.count(), 1)
