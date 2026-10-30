"""
Profile pictures get a fresh file name on every upload (10-29-26), so the
app's photo cache (keyed on the name: mobile.views_directory.photo_path)
notices a new picture even when the phone uploads "image.jpg" every time.

    python manage.py test src.tests.users.test_profile_picture_names
"""
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from src.mobile.views_directory import photo_path
from src.tests.mobile.sess_helpers import make_user


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class ProfilePictureNameTests(TestCase):

    def upload(self):
        f = SimpleUploadedFile('image.jpg', b'\xff\xd8\xff fake jpeg', content_type='image/jpeg')
        return self.client.post('/profile/', {'profile_picture_submit': '1', 'profile_picture': f})

    def test_same_upload_name_gets_a_new_stored_name_and_photo_path(self):
        user = make_user('pp-1')
        self.client.force_login(user)
        self.upload()
        user.refresh_from_db()
        first_name, first_path = user.profile_picture.name, photo_path(user)
        self.assertTrue(first_name.endswith('.jpg'))
        self.assertNotIn('image', first_name)
        self.upload()
        user.refresh_from_db()
        self.assertNotEqual(user.profile_picture.name, first_name)
        self.assertNotEqual(photo_path(user), first_path)
        user.profile_picture.delete(save=False)
