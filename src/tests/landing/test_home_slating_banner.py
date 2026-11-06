"""
11-06-26: the home's slating banner vanished between stages (applications
closed, the committee deliberating, votes being counted) — e.g. the day
before an election. Both home layouts, with and without the refresh, now show
it at every active stage; the app's Home shortcut follows.

    python manage.py test src.tests.landing.test_home_slating_banner
"""
from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse

from src.models import SlatingPeriod
from src.models_feature_flags import FeatureFlag
from src.tests.landing.test_web_refresh_pages_c import make_user


class HomeSlatingBannerTests(TestCase):
    def setUp(self):
        cache.clear()
        self.member = make_user('hsb-1')
        self.client = Client()
        self.client.force_login(self.member)

    def home(self, refresh):
        FeatureFlag.objects.update_or_create(name='web_refresh', defaults={
            'display_name': 'x', 'description': 'x', 'category': 'ui', 'is_enabled': refresh})
        cache.clear()
        return self.client.get(reverse('home')).content.decode()

    def test_every_active_stage_has_a_banner(self):
        expected = {'nominations_closed': 'Election coming up', 'deliberation': 'Election coming up',
                    'voting_closed': 'Counting votes'}
        for status, label in expected.items():
            SlatingPeriod.objects.all().delete()
            SlatingPeriod.objects.create(name='Fall Slate', status=status)
            for refresh in (False, True):
                with self.subTest(status=status, refresh=refresh):
                    html = self.home(refresh)
                    self.assertIn('Fall Slate', html)
                    self.assertIn(label, html)
                    self.assertIn(reverse('slating_dashboard'), html)

    def test_setup_and_archived_stay_hidden(self):
        for status in ('setup', 'archived'):
            SlatingPeriod.objects.all().delete()
            SlatingPeriod.objects.create(name='Hidden Slate', status=status)
            self.assertNotIn('Hidden Slate', self.home(True))
