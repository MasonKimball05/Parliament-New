"""
Preferences in the app (v4.4.0, src/mobile/views_preferences.py): the same
values, validation and side effects as the website's Preferences page, through
the helpers both share (src/view/preferences.py).

Until its route is in src/mobile/urls.py, this module is its own urlconf: the
real mobile routes plus ``preferences/``.

    python manage.py test src.tests.mobile.test_preferences
"""
import json

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import include, path

from src.forms import UserPreferencesForm
from src.mobile import urls as mobile_urls
from src.mobile import views_preferences
from src.models import ActivityLog, UserPreferences
from src.tests.mobile.sess_helpers import bearer, make_device, make_user
from src.tests.mobile.test_home_and_notifications import flag

API = '/api/mobile/v1/'

urlpatterns = [
    path('api/mobile/v1/', include(([
        path('preferences/', views_preferences.PreferencesView.as_view(), name='mobile_preferences'),
        *mobile_urls.urlpatterns,
    ], 'mobile'))),
    path('', include('src.urls')),
]


@override_settings(ROOT_URLCONF=__name__, ALLOWED_HOSTS=['testserver', 'localhost'])
class _Base(TestCase):

    def setUp(self):
        cache.clear()
        flag('dark_mode', True)
        self.member = make_user('pref-1')
        _, self.access, _ = make_device(self.member)
        self.api = Client()

    def call(self, method, body=None):
        fn = getattr(self.api, method)
        if body is None:
            return fn(API + 'preferences/', **bearer(self.access))
        return fn(API + 'preferences/', json.dumps(body), content_type='application/json', **bearer(self.access))

    def items(self, body):
        return {i['key']: i for s in body['sections'] for i in s['items']}

    def prefs(self):
        return UserPreferences.objects.get(user=self.member)


class ReadTests(_Base):

    def test_every_form_field_is_listed_once(self):
        listed = [f for _, _, _, fields in views_preferences.SECTIONS for f, _ in fields]
        self.assertEqual(sorted(listed), sorted(UserPreferencesForm.base_fields))
        self.assertEqual(len(listed), len(set(listed)))

    def test_values_and_choices(self):
        r = self.call('get')
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual(body['menu_limit'], 9)
        items = self.items(body)
        self.assertEqual(items['push_chat'], {'key': 'push_chat', 'label': 'Chat messages',
                                              'value': True, 'kind': 'toggle'})
        self.assertFalse(items['show_chats_menu']['value'])   # off by default
        self.assertEqual(items['theme']['kind'], 'choice')
        self.assertEqual([c['value'] for c in items['theme']['choices']], ['light', 'dark', 'auto'])
        self.assertEqual(items['home_layout']['value'], 'modern')

    def test_theme_is_hidden_while_dark_mode_is_off(self):
        flag('dark_mode', False)
        self.assertNotIn('theme', self.items(self.call('get').json()))
        self.assertEqual(self.call('patch', {'theme': 'dark'}).status_code, 400)

    def test_needs_a_token(self):
        self.assertEqual(self.api.get(API + 'preferences/').status_code, 401)


class SaveTests(_Base):

    def test_patch_changes_only_what_it_sends(self):
        prefs = self.prefs()
        prefs.prefs['dev'] = {'enabled': True}   # another surface's section survives
        prefs.save()
        r = self.call('patch', {'push_chat': False, 'landing_page': 'calendar'})
        self.assertEqual(r.status_code, 200, r.content)
        items = self.items(r.json())
        self.assertFalse(items['push_chat']['value'])
        self.assertEqual(items['landing_page']['value'], 'calendar')
        prefs = self.prefs()
        self.assertFalse(prefs.push_chat)
        self.assertEqual(prefs.landing_page, 'calendar')
        self.assertTrue(prefs.push_mentions)
        self.assertTrue(prefs.email_events)
        self.assertEqual(prefs.prefs['dev'], {'enabled': True})
        self.assertTrue(ActivityLog.objects.filter(user=self.member, action_type='preferences_updated').exists())

    def test_busts_the_preferences_cache(self):
        cache.set(f'user_prefs_{self.member.pk}', 'stale')
        self.call('patch', {'compact_view': True})
        self.assertIsNone(cache.get(f'user_prefs_{self.member.pk}'))

    def test_theme(self):
        self.assertEqual(self.call('patch', {'theme': 'dark'}).status_code, 200)
        self.assertEqual(self.prefs().theme, 'dark')

    def test_bad_values_are_refused_and_nothing_is_saved(self):
        for body in ({'push_chat': 'no'}, {'theme': 'purple'}, {'nonsense': True}, {}, [1]):
            r = self.call('patch', body)
            self.assertEqual(r.status_code, 400, body)
            self.assertEqual(r.json()['error'], 'invalid')
        self.assertTrue(self.prefs().push_chat)

    def test_menu_limit_is_the_websites(self):
        # Seven on by default; two more is nine, a tenth is refused.
        self.assertEqual(self.call('patch', {'show_chats_menu': True, 'show_cnb_menu': True}).status_code, 200)
        r = self.call('patch', {'show_excuses_menu': True})
        self.assertEqual(r.status_code, 400)
        self.assertIn('at most 9', r.json()['detail'])
        self.assertFalse(self.prefs().show_excuses_menu)


class WebPageTests(_Base):
    """The website's form post, now through the same helper."""

    def test_web_post_still_saves(self):
        self.api.force_login(self.member)
        data = {k: v for k, v in UserPreferencesForm(instance=self.prefs()).initial.items() if v is not False}
        data['theme'] = 'auto'
        data.pop('push_events')   # an unticked box isn't posted
        r = self.api.post('/preferences/', data, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json(), {'success': True, 'theme_changed': True})
        prefs = self.prefs()
        self.assertEqual(prefs.theme, 'auto')
        self.assertFalse(prefs.push_events)

    def test_web_page_saves_while_dark_mode_is_off(self):
        # v4.4.0: the theme field isn't shown then, and as a required field its
        # absence failed every save; the page now posts it hidden.
        flag('dark_mode', False)
        self.api.force_login(self.member)
        page = self.api.get('/preferences/').content.decode()
        self.assertIn('<input type="hidden" name="theme" value="light">', page)
        data = {k: v for k, v in UserPreferencesForm(instance=self.prefs()).initial.items() if v is not False}
        data['theme'] = 'light'   # what the hidden input posts
        data.pop('push_events')
        r = self.api.post('/preferences/', data, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertFalse(self.prefs().push_events)
