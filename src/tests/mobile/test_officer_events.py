"""
v4.4.0 — the officers' event lists in the app (src/mobile/views_officer_events.py).

The app shows what ``view_all_events`` and ``view_archived_events`` show (the
same shared querysets), to the same people, and archives/unarchives like
``archive_event`` / ``unarchive_event`` (admins only).

Until its routes are in src/mobile/urls.py, this module is its own urlconf:
the project's urlconf plus the four routes.

    python manage.py test src.tests.mobile.test_officer_events
"""
import json
from datetime import timedelta

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import include, path, reverse
from django.utils import timezone

from src.mobile import views_officer_events as v
from src.models import Event
from src.tests.mobile.sess_helpers import bearer, make_device, make_user

API = '/api/mobile/v1/'

_routes = [
    path('officer/events/all/', v.OfficerAllEventsView.as_view(), name='mobile_officer_events_all'),
    path('officer/events/archived/', v.OfficerArchivedEventsView.as_view(), name='mobile_officer_events_archived'),
    path('officer/events/<int:event_id>/archive/', v.OfficerArchiveEventView.as_view(),
         name='mobile_officer_event_archive'),
    path('officer/events/<int:event_id>/unarchive/', v.OfficerUnarchiveEventView.as_view(),
         name='mobile_officer_event_unarchive'),
]


def _urlpatterns():
    from src import urls as root
    return [path('api/mobile/v1/', include((_routes, 'mobile_oe')))] + root.urlpatterns


urlpatterns = _urlpatterns()


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'], ROOT_URLCONF=__name__)
class OfficerEventListTests(TestCase):

    def setUp(self):
        cache.clear()
        now = timezone.now()
        self.officer = make_user('oe-off', member_type='Officer')
        self.admin = make_user('oe-admin', member_type='Officer', is_admin=True)
        self.advisor = make_user('oe-adv', member_type='Advisor')
        self.member = make_user('oe-mem')
        self.access = {u.user_id: make_device(u)[1] for u in (self.officer, self.admin, self.advisor, self.member)}

        def ev(title, days, **kw):
            return Event.objects.create(title=title, description=f'{title} desc', created_by=self.officer,
                                        date_time=now + timedelta(days=days), **kw)
        self.soon = ev('Soon', 1)
        self.later = ev('Later', 5, location='Chapter room')
        self.hidden = ev('Hidden upcoming', 2, is_active=False)
        self.yesterday = ev('Yesterday', -1)
        self.lastweek = ev('Last week', -7)
        self.old = ev('Old archived', -400, archived=True, is_active=False)
        self.api = Client()

    def get(self, who, url):
        return self.api.get(API + url, **bearer(self.access[who.user_id]))

    def post(self, who, url):
        return self.api.post(API + url, json.dumps({}), content_type='application/json',
                             **bearer(self.access[who.user_id]))

    def ids(self, r):
        return [e['id'] for e in r.json()['events']]

    def test_same_lists_as_the_web_page(self):
        from src.view.officer.view_all_events import all_events_querysets
        upcoming, past = all_events_querysets()
        r = self.get(self.officer, 'officer/events/all/')
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual(self.ids(r), [e.id for e in upcoming])
        self.assertEqual(self.ids(r), [self.soon.id, self.hidden.id, self.later.id])
        self.assertEqual(body['counts'], {'upcoming': 3, 'past': 2, 'archived': None})
        self.assertTrue(body['can_edit'])
        self.assertFalse(body['can_archive'])
        hidden = next(e for e in body['events'] if e['id'] == self.hidden.id)
        self.assertFalse(hidden['is_active'])
        self.assertFalse(hidden['viewable'])
        self.assertEqual(hidden['web_path'], reverse('edit_event', args=[self.hidden.id]))

        r = self.get(self.officer, 'officer/events/all/?section=past')
        self.assertEqual(self.ids(r), [e.id for e in past])
        self.assertEqual(self.ids(r), [self.yesterday.id, self.lastweek.id])

    def test_web_page_lists_match(self):
        self.api.force_login(self.officer)
        r = self.api.get(reverse('view_all_events'))
        self.assertEqual(r.status_code, 200)
        self.assertEqual([e.id for e in r.context['upcoming_events']], [self.soon.id, self.hidden.id, self.later.id])
        self.assertEqual([e.id for e in r.context['past_events']], [self.yesterday.id, self.lastweek.id])

    def test_search_and_paging(self):
        r = self.get(self.officer, 'officer/events/all/?q=chapter%20room')
        self.assertEqual(self.ids(r), [self.later.id])
        self.assertEqual(r.json()['counts']['upcoming'], 1)
        self.assertEqual(r.json()['q'], 'chapter room')
        for i in range(v.PAGE_SIZE + 2):
            Event.objects.create(title=f'Bulk {i}', created_by=self.officer,
                                 date_time=timezone.now() + timedelta(days=10, hours=i))
        r = self.get(self.officer, 'officer/events/all/')
        self.assertEqual(len(r.json()['events']), v.PAGE_SIZE)
        self.assertTrue(r.json()['has_next'])
        r = self.get(self.officer, 'officer/events/all/?page=2')
        self.assertEqual(r.json()['page'], 2)
        self.assertEqual(len(r.json()['events']), 5)
        self.assertFalse(r.json()['has_next'])
        self.assertEqual(self.get(self.officer, 'officer/events/all/?page=x').json()['page'], 1)

    def test_bad_section_is_invalid_input(self):
        r = self.get(self.officer, 'officer/events/all/?section=nope')
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()['error'], 'invalid_input')

    def test_who_sees_what(self):
        # officer_or_advisor_required: advisors read it, members don't.
        r = self.get(self.advisor, 'officer/events/all/')
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()['can_edit'])
        self.assertIsNone(r.json()['events'][0]['web_path'])
        self.assertEqual(self.get(self.member, 'officer/events/all/').status_code, 403)
        # admin_required: only admins get the archive.
        for who in (self.officer, self.advisor, self.member):
            self.assertEqual(self.get(who, 'officer/events/archived/').status_code, 403, who.user_id)
            self.assertEqual(self.post(who, f'officer/events/{self.soon.id}/archive/').status_code, 403)
            self.assertEqual(self.post(who, f'officer/events/{self.old.id}/unarchive/').status_code, 403)
        self.soon.refresh_from_db()
        self.assertFalse(self.soon.archived)

    def test_admin_archive_and_unarchive(self):
        r = self.get(self.admin, 'officer/events/all/')
        self.assertEqual(r.json()['counts']['archived'], 1)
        self.assertTrue(r.json()['can_archive'])
        r = self.get(self.admin, 'officer/events/archived/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.ids(r), [self.old.id])

        r = self.post(self.admin, f'officer/events/{self.soon.id}/archive/')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(r.json()['event']['archived'])
        self.soon.refresh_from_db()
        self.assertTrue(self.soon.archived)
        self.assertFalse(self.soon.is_active)
        self.assertNotIn(self.soon.id, self.ids(self.get(self.admin, 'officer/events/all/')))
        self.assertEqual(self.ids(self.get(self.admin, 'officer/events/archived/')), [self.soon.id, self.old.id])

        r = self.post(self.admin, f'officer/events/{self.old.id}/unarchive/')
        self.assertEqual(r.status_code, 200)
        self.old.refresh_from_db()
        self.assertFalse(self.old.archived)
        self.assertTrue(self.old.is_active)
        self.assertEqual(self.post(self.admin, 'officer/events/999999/archive/').status_code, 404)

    def test_web_archive_still_works(self):
        self.api.force_login(self.admin)
        r = self.api.post(reverse('archive_event', args=[self.soon.id]), HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.json(), {'success': True, 'archived': True})
        self.soon.refresh_from_db()
        self.assertTrue(self.soon.archived)
        self.assertFalse(self.soon.is_active)
        r = self.api.post(reverse('unarchive_event', args=[self.soon.id]))
        self.assertRedirects(r, reverse('view_archived_events'), fetch_redirect_response=False)
        self.soon.refresh_from_db()
        self.assertFalse(self.soon.archived)
        self.assertTrue(self.soon.is_active)
        r = self.api.get(reverse('view_archived_events'))
        self.assertEqual([e.id for e in r.context['archived_events']], [self.old.id])
