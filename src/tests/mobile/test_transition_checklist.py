"""11-17-26: the role transition checklist in the app (views_transition_checklist.py)."""
from django.core.cache import cache
from django.test import Client, TestCase, override_settings

from src.models import Role, RoleHistory, TransitionChecklistItem, TransitionChecklistStatus
from src.tests.mobile.sess_helpers import bearer, make_device, make_user

API = '/api/mobile/v1/'


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class TransitionChecklistTests(TestCase):

    def setUp(self):
        cache.clear()
        self.api = Client()
        self.holder = make_user('tc-hold', name='Jordan Treasurer')
        self.officer = make_user('tc-off', member_type='Officer')
        self.other = make_user('tc-other')
        role = Role.objects.create(name='Treasurer', code='TRSTC')
        self.rh = RoleHistory.objects.create(user=self.holder, role_name='Treasurer', start_semester='Spring 2027')
        self.first = TransitionChecklistStatus.objects.create(
            item=TransitionChecklistItem.objects.create(role=role, text='Bank login', order=0), role_history=self.rh)
        TransitionChecklistStatus.objects.create(
            item=TransitionChecklistItem.objects.create(role=role, text='Budget meeting', order=1),
            role_history=self.rh)

    def auth(self, user):
        _, access, _ = make_device(user)
        return bearer(access)

    def test_holder_and_officers_see_it_others_dont(self):
        d = self.api.get(API + f'transitions/checklist/{self.rh.id}/', **self.auth(self.holder)).json()
        self.assertEqual([i['text'] for i in d['items']], ['Bank login', 'Budget meeting'])
        self.assertEqual((d['progress'], d['is_mine'], d['role_name']), ({'done': 0, 'total': 2}, True, 'Treasurer'))
        self.assertEqual(self.api.get(API + f'transitions/checklist/{self.rh.id}/',
                                      **self.auth(self.officer)).status_code, 200)
        self.assertEqual(self.api.get(API + f'transitions/checklist/{self.rh.id}/',
                                      **self.auth(self.other)).status_code, 404)

    def test_toggle(self):
        url = API + f'transitions/checklist/items/{self.first.id}/toggle/'
        r = self.api.post(url, **self.auth(self.holder))
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual((r.json()['completed'], r.json()['progress']), (True, {'done': 1, 'total': 2}))
        self.first.refresh_from_db()
        self.assertEqual(self.first.completed_by, self.holder)
        r = self.api.post(url, **self.auth(self.officer))
        self.assertFalse(r.json()['completed'])
        self.assertEqual(self.api.post(url, **self.auth(self.other)).status_code, 404)

    def test_home_shows_the_unfinished_list(self):
        cards = self.api.get(API + 'home/', **self.auth(self.holder)).json()['transition_checklists']
        self.assertEqual(cards, [{'id': self.rh.id, 'role_name': 'Treasurer', 'done': 0, 'total': 2}])
        self.assertEqual(self.api.get(API + 'home/', **self.auth(self.other)).json()['transition_checklists'], [])
