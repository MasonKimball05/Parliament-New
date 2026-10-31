"""
The app's Profile screen (v4.4.0, src/mobile/views_profile.py): the website's
Profile page through the same helpers, so the same validation, logging and
email confirmation.

The routes aren't in src/mobile/urls.py yet, so this module is its own urlconf:
the profile routes under ``api/mobile/v1/`` ahead of the site's own.

    python manage.py test src.tests.mobile.test_profile
"""
import json
from unittest.mock import patch

from django.core import mail
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import include, path

from src.mobile import views_profile
from src.models import ActivityLog, EmailVerificationToken, RoleHistory
from src.tests.mobile.sess_helpers import bearer, make_device, make_user
import src.urls

profile_routes = [
    path('profile/', views_profile.ProfileView.as_view(), name='mobile_profile'),
    path('profile/photo/', views_profile.ProfilePhotoView.as_view(), name='mobile_profile_photo'),
    path('profile/role-history/', views_profile.RoleHistoryListView.as_view(), name='mobile_profile_roles'),
    path('profile/role-history/<int:rh_id>/', views_profile.RoleHistoryItemView.as_view(),
         name='mobile_profile_role'),
    path('profile/socials/', views_profile.CustomSocialListView.as_view(), name='mobile_profile_socials'),
    path('profile/socials/<int:index>/', views_profile.CustomSocialItemView.as_view(),
         name='mobile_profile_social'),
    path('profile/initiation-chapters/', views_profile.InitiationChapterListView.as_view(),
         name='mobile_profile_chapters'),
    path('profile/initiation-chapters/<int:index>/', views_profile.InitiationChapterItemView.as_view(),
         name='mobile_profile_chapter'),
    path('profile/academics/', views_profile.AcademicListView.as_view(), name='mobile_profile_academics'),
    path('profile/academics/<str:kind>/<int:index>/', views_profile.AcademicItemView.as_view(),
         name='mobile_profile_academic'),
]
urlpatterns = [path('api/mobile/v1/', include(profile_routes)), *src.urls.urlpatterns]

API = '/api/mobile/v1/'


@override_settings(ROOT_URLCONF=__name__)
class ProfileAPITests(TestCase):

    def setUp(self):
        cache.clear()
        self.user = make_user('pro-1', name='Michael Smith', email='mike@parliament.test', phone_number='2055550100')
        _, self.access, _ = make_device(self.user)
        self.other = make_user('pro-2', name='Other Brother', email='other@parliament.test')
        self.api = Client()

    def call(self, method, url, body=None):
        fn = getattr(self.api, method)
        if body is None:
            return fn(API + url, **bearer(self.access))
        return fn(API + url, json.dumps(body), content_type='application/json', **bearer(self.access))

    def refresh(self):
        self.user.refresh_from_db()
        return self.user

    # ── reading ──

    def test_get(self):
        RoleHistory.objects.create(user=self.user, role_name='Treasurer', start_semester='Fall 2025')
        r = self.call('get', 'profile/')
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        p = body['profile']
        self.assertEqual((p['username'], p['email'], p['phone_number']), ('pro-1', 'mike@parliament.test', '2055550100'))
        self.assertEqual(p['role_history'][0]['role_name'], 'Treasurer')
        self.assertFalse(body['security']['has_2fa'])
        self.assertEqual(body['security']['passkeys'], [])
        self.assertIn({'user_id': 'pro-2', 'name': 'Other Brother'}, body['choices']['big_brothers'])
        self.assertNotIn('pro-1', [b['user_id'] for b in body['choices']['big_brothers']])
        self.assertEqual(body['links']['sessions'], '/account/sessions/')
        self.assertFalse(body['photo_removed_by_admin'])

    def test_photo_removed_notice_shows_once(self):
        self.user.profile_picture_removed_by_admin = True
        self.user.save(update_fields=['profile_picture_removed_by_admin'])
        self.assertTrue(self.call('get', 'profile/').json()['photo_removed_by_admin'])
        self.assertFalse(self.call('get', 'profile/').json()['photo_removed_by_admin'])

    def test_needs_a_token(self):
        self.assertEqual(self.api.get(API + 'profile/').status_code, 401)

    def test_page_toggle_applies(self):
        with patch('src.models_feature_flags.PageToggle.is_page_enabled', return_value=False):
            r = self.call('get', 'profile/')
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()['error'], 'page_disabled')

    # ── account settings ──

    def test_partial_patch_keeps_other_fields_and_logs(self):
        r = self.call('patch', 'profile/', {'preferred_name': 'Mike'})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['profile']['display_name'], 'Mike Smith')
        u = self.refresh()
        self.assertEqual((u.preferred_name, u.phone_number, u.email), ('Mike', '2055550100', 'mike@parliament.test'))
        log = ActivityLog.objects.get(action_type='profile_updated')
        self.assertEqual(log.metadata['changes'], [{'field': 'preferred_name', 'old': '', 'new': 'Mike'}])

    def test_new_email_waits_for_confirmation(self):
        r = self.call('patch', 'profile/', {'email': 'New@Parliament.test', 'phone_number': '205-555-0199'})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['email_pending'], 'new@parliament.test')
        self.assertEqual(r.json()['profile']['email_pending'], 'new@parliament.test')
        u = self.refresh()
        self.assertEqual((u.email, u.phone_number), ('mike@parliament.test', '205-555-0199'))
        self.assertTrue(EmailVerificationToken.objects.filter(user=u, new_email='new@parliament.test').exists())
        self.assertEqual(mail.outbox[-1].to, ['new@parliament.test'])

    def test_email_in_use_saves_nothing(self):
        r = self.call('patch', 'profile/', {'preferred_name': 'Mike', 'email': 'other@parliament.test'})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json(), {'error': 'profile_invalid',
                                    'detail': 'This email address is already in use by another user.'})
        self.assertEqual(self.refresh().preferred_name, '')

    def test_plain_text_rules(self):
        for body, detail in (
            ({'preferred_name': '<b>Mike</b>'}, 'Preferred name can\'t contain "<" or ">".'),
            ({'phone_number': 'call me'}, 'Phone number can only contain digits'),
            ({'instagram': '=HYPERLINK("x")'}, 'Instagram can\'t start with'),
            ({'email': 'not-an-email'}, 'Email is not a valid email address.'),
            ({'username': 'pro-2'}, 'That username is taken.'),
            ({'graduation_year': '99999'}, 'Graduation year'),
            ({'graduation_semester': 'Winter'}, 'Graduation semester'),
            ({'big_brother': 'pro-1'}, 'own big brother'),
            ({'twitter': 'x' * 61}, 'at most 60'),
            ({'about_me': ['a list']}, 'must be text'),
        ):
            r = self.call('patch', 'profile/', body)
            self.assertEqual(r.status_code, 400, body)
            self.assertIn(detail, r.json()['detail'], body)

    # ── public profile ──

    def test_public_profile(self):
        r = self.call('patch', 'profile/', {'about_me': 'Hi', 'instagram': '@mike', 'big_brother': 'pro-2',
                                            'graduation_semester': 'Spring', 'graduation_year': 2027})
        self.assertEqual(r.status_code, 200, r.content)
        p = r.json()['profile']
        self.assertEqual(p['socials']['instagram'], 'mike')        # the page strips "@"
        self.assertEqual(p['big_brother'], {'user_id': 'pro-2', 'name': 'Other Brother'})
        self.assertEqual((p['graduation_semester'], p['graduation_year']), ('Spring', 2027))
        # Leaving a field out keeps it (the page posts the whole form back).
        self.call('patch', 'profile/', {'about_me': 'Hello'})
        u = self.refresh()
        self.assertEqual((u.about_me, u.instagram, u.big_brother_id), ('Hello', 'mike', 'pro-2'))

    def test_original_founder_is_refused(self):
        r = self.call('patch', 'profile/', {'pledge_class': '1879', 'pledge_class_greek': 'Original Founder'})
        self.assertEqual(r.status_code, 400)
        self.assertIn('Original Founder', r.json()['detail'])
        self.assertEqual(self.refresh().pledge_class_greek, '')

    # ── photo ──

    def test_remove_photo(self):
        self.assertEqual(self.call('delete', 'profile/photo/').status_code, 404)
        with patch('django.db.models.fields.files.FieldFile.delete') as delete:
            self.user.profile_picture.name = 'profile_pictures/x.jpg'
            self.user.save(update_fields=['profile_picture'])
            r = self.call('delete', 'profile/photo/')
        self.assertEqual(r.status_code, 200, r.content)
        delete.assert_called_once()
        self.assertTrue(ActivityLog.objects.filter(action_type='profile_picture_changed',
                                                   metadata__action='removed').exists())

    def upload(self, f):
        return self.api.post(API + 'profile/photo/', {'photo': f}, **bearer(self.access))

    def test_upload_photo(self):
        """v4.4.0: the app's image picker posts a JPEG."""
        r = self.upload(SimpleUploadedFile('image.jpg', b'\xff\xd8\xff fake jpeg', content_type='image/jpeg'))
        self.assertEqual(r.status_code, 200, r.content)
        user = self.refresh()
        self.assertTrue(user.profile_picture.name.endswith('.jpg'))
        self.assertTrue(r.json()['profile']['photo'])
        self.assertTrue(ActivityLog.objects.filter(action_type='profile_picture_changed',
                                                   metadata__action='uploaded').exists())
        user.profile_picture.delete(save=False)

    def test_upload_is_held_to_the_fields_checks(self):
        r = self.upload(SimpleUploadedFile('notes.pdf', b'%PDF-1.4', content_type='application/pdf'))
        self.assertEqual(r.status_code, 400)
        self.assertIn('JPEG', r.json()['detail'])
        big = SimpleUploadedFile('big.jpg', b'\xff' * (5 * 1024 * 1024 + 1), content_type='image/jpeg')
        r = self.upload(big)
        self.assertEqual(r.status_code, 400)
        self.assertIn('5 MB', r.json()['detail'])
        self.assertFalse(self.refresh().profile_picture)

    def test_upload_needs_a_file(self):
        r = self.api.post(API + 'profile/photo/', {}, **bearer(self.access))
        self.assertEqual(r.status_code, 400)

    # ── additional details ──

    def test_role_history(self):
        r = self.call('post', 'profile/role-history/', {'role_name': 'President', 'start_semester': 'Spring 2026'})
        self.assertEqual(r.status_code, 201, r.content)
        rh_id = r.json()['id']
        self.assertEqual(self.call('post', 'profile/role-history/', {'role_name': 'X'}).json()['detail'],
                         'Role name and start semester are required.')
        r = self.call('put', f'profile/role-history/{rh_id}/', {'role_name': 'President', 'start_semester': 'Spring 2026',
                                                                 'end_semester': 'Fall 2026'})
        self.assertEqual(r.json()['profile']['role_history'][0]['end_semester'], 'Fall 2026')
        theirs = RoleHistory.objects.create(user=self.other, role_name='Chair', start_semester='Fall 2025')
        self.assertEqual(self.call('put', f'profile/role-history/{theirs.id}/',
                                   {'role_name': 'Mine', 'start_semester': 'x'}).status_code, 404)
        self.assertEqual(self.call('delete', f'profile/role-history/{theirs.id}/').status_code, 404)
        self.assertEqual(self.call('delete', f'profile/role-history/{rh_id}/').status_code, 200)
        self.assertEqual(list(RoleHistory.objects.values_list('id', flat=True)), [theirs.id])

    def test_custom_socials_chapters_and_academics(self):
        self.assertEqual(self.call('post', 'profile/socials/', {'platform': 'Signal', 'handle': '@mike'}).status_code, 201)
        self.call('post', 'profile/initiation-chapters/', {'school': 'Auburn', 'chapter': 'Delta Zeta', 'role_number': '7'})
        self.call('post', 'profile/academics/', {'type': 'major', 'value': 'History'})
        dup = self.call('post', 'profile/academics/', {'type': 'major', 'value': 'History'})
        self.assertEqual(dup.json()['detail'], 'History is already listed.')
        p = self.call('get', 'profile/').json()['profile']
        self.assertEqual(p['custom_socials'], [{'index': 0, 'platform': 'Signal', 'handle': 'mike'}])
        self.assertEqual(p['initiation_chapters'][0]['role_number'], '7')
        self.assertEqual(p['academics']['majors'], ['History'])

        self.assertEqual(self.call('delete', 'profile/socials/3/').status_code, 404)
        self.assertEqual(self.call('delete', 'profile/socials/0/').status_code, 200)
        self.assertEqual(self.call('delete', 'profile/initiation-chapters/0/').status_code, 200)
        self.assertEqual(self.call('delete', 'profile/academics/hobby/0/').status_code, 400)
        r = self.call('delete', 'profile/academics/major/0/')
        self.assertEqual(r.json()['profile']['academics']['majors'], [])
        u = self.refresh()
        self.assertEqual((u.custom_socials, u.initiation_chapters), ([], []))

    def test_item_text_rules(self):
        r = self.call('post', 'profile/socials/', {'platform': '<i>', 'handle': 'x'})
        self.assertEqual(r.status_code, 400)
        self.assertIn('Platform', r.json()['detail'])
