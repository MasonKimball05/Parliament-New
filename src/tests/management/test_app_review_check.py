"""manage.py app_review_check (11-17-26): read-only, PASS/WARN/FAIL, exit 1 on a FAIL."""
from io import StringIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings

from src.models import ChatChannel, ChatMessage, Song
from src.models_feature_flags import FeatureFlag, SiteSetting
from src.tests.mobile.sess_helpers import make_user


def _flag(name, on=True):
    FeatureFlag.objects.update_or_create(name=name, defaults={'display_name': name, 'description': name,
                                                              'is_enabled': on})


def _run():
    out = StringIO()
    try:
        call_command('app_review_check', stdout=out)
        code = 0
    except SystemExit as e:
        code = e.code
    return out.getvalue(), code


@override_settings(APP_REVIEW_USERNAMES=frozenset({'appreview'}), GEOIP_CITY_DB='/nonexistent.mmdb')
class AppReviewCheckTests(TestCase):

    def setUp(self):
        from src import location_checkin
        for name in (*location_checkin.FLAGS, 'chats'):
            _flag(name)
        self.reviewer = make_user('rev-1', username='appreview')
        self.poster = make_user('rev-2')
        channel = ChatChannel.objects.create(name='General', access_type='open', created_by=self.poster)
        ChatMessage.objects.create(channel=channel, sender=self.poster, message='Hello')
        self.song = Song.objects.create(title='Song', lyrics='...', is_active=True,
                                        audio_file=SimpleUploadedFile('s.mp3', b'ID3', content_type='audio/mpeg'))
        self.addCleanup(self.song.audio_file.delete, save=False)

    def test_ready(self):
        SiteSetting.set_setting('mobile_ios_update_url', 'https://apps.apple.com/app/id1')
        out, code = _run()
        self.assertEqual(code, 0, out)
        self.assertIn('Ready for App Review', out)
        self.assertIn('WARN  GeoLite2', out)   # a warning doesn't fail it

    def test_each_failure_is_named(self):
        _flag('chats', False)
        self.reviewer.roles.create(name='Treasurer', code='TRSREV')
        Song.objects.update(audio_file='')
        out, code = _run()
        self.assertEqual(code, 1)
        self.assertIn('not a plain Member', out)
        self.assertIn('Chat is off', out)
        self.assertIn('No Songbook song has a recording', out)

    def test_missing_account_and_empty_setting(self):
        self.reviewer.delete()
        out, code = _run()
        self.assertEqual(code, 1)
        self.assertIn('does not exist', out)
        with override_settings(APP_REVIEW_USERNAMES=frozenset()):
            out, _ = _run()
        self.assertIn('APP_REVIEW_USERNAMES is empty', out)

    def test_a_channel_without_messages_fails(self):
        ChatMessage.objects.all().delete()
        out, code = _run()
        self.assertEqual(code, 1)
        self.assertIn('none with messages', out)
