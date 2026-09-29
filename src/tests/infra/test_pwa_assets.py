"""
09-27-26 — multi-chapter phase 2: PWA assets rendered from the chapter config
(src/view/pwa.py). Were static/manifest.json, static/js/service-worker.js and
static/offline.html, which could not use `{% chapter %}`.

Pins:
  * both assets are ANONYMOUS (browsers fetch the manifest cookieless, and the
    worker must register for a session that is mid-2FA);
  * the manifest and worker follow settings.CHAPTER;
  * the offline page is embedded in the worker, so there is no offline URL to
    cache (the redirect-into-cache failure mode described in pwa.py);
  * a hostile chapter value cannot break out of the worker's JS string — the
    worker is run through `node --check` when node is available.

Run with: python manage.py test src.tests.infra.test_pwa_assets
"""
import json
import shutil
import subprocess
import tempfile

from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse

OTHER = {**settings.CHAPTER, 'chapter_name': 'Gamma Delta', 'crest': 'images/gd-crest.png',
         'primary_color': '#123456'}
HOSTILE = {**settings.CHAPTER, 'chapter_name': "O'Brien </script><script>alert(1)</script>\n'"}


class WebManifestTests(TestCase):
    def test_manifest_is_anonymous_json_for_the_default_chapter(self):
        r = self.client.get(reverse('web_manifest'))
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r['Content-Type'].startswith('application/manifest+json'))
        m = json.loads(r.content)
        self.assertEqual(m['name'], 'Parliament — Alpha Mu')
        self.assertEqual(m['theme_color'], '#003DA5')
        self.assertEqual({i['src'] for i in m['icons']}, {'/static/images/am-coat-of-arms.png'})
        self.assertEqual(m['start_url'], '/home/')

    @override_settings(CHAPTER=OTHER)
    def test_manifest_follows_the_chapter_config(self):
        m = json.loads(self.client.get(reverse('web_manifest')).content)
        self.assertEqual(m['name'], 'Parliament — Gamma Delta')
        self.assertEqual(m['description'], 'Gamma Delta Chapter Management System')
        self.assertEqual(m['background_color'], '#123456')
        self.assertEqual({i['src'] for i in m['icons']}, {'/static/images/gd-crest.png'})

    def test_base_template_links_the_rendered_manifest(self):
        with open(settings.BASE_DIR / 'templates' / 'base.html') as f:
            base = f.read()
        self.assertIn("{% url 'web_manifest' %}", base)
        self.assertNotIn("manifest.json", base)


class ServiceWorkerTests(TestCase):
    def _sw(self):
        r = self.client.get('/service-worker.js')
        self.assertEqual(r.status_code, 200)
        return r, r.content.decode()

    def test_worker_is_anonymous_and_root_scoped(self):
        r, _ = self._sw()
        self.assertEqual(r['Service-Worker-Allowed'], '/')
        self.assertEqual(r['Cache-Control'], 'no-cache')
        self.assertTrue(r['Content-Type'].startswith('application/javascript'))

    def test_worker_uses_the_chapter_crest_and_embeds_the_offline_page(self):
        _, body = self._sw()
        self.assertIn("const ICON = '/static/images/am\\u002Dcoat\\u002Dof\\u002Darms.png';", body)
        self.assertIn('const OFFLINE_HTML =', body)
        self.assertIn('You\\u0027re offline', body)
        # No offline URL any more — nothing to cache a redirect into.
        self.assertNotIn('OFFLINE_URL', body)
        self.assertNotIn('caches.match', body)
        self.assertIn("parliament\\u002Doffline\\u002Dv2", body)

    @override_settings(CHAPTER=OTHER)
    def test_worker_follows_the_chapter_config(self):
        _, body = self._sw()
        self.assertIn('gd\\u002Dcrest.png', body)
        self.assertNotIn('am\\u002Dcoat', body)

    @override_settings(CHAPTER=HOSTILE)
    def test_hostile_chapter_value_cannot_break_out_of_the_js_string(self):
        _, body = self._sw()
        self.assertNotIn('</script>', body)
        self.assertNotIn("O'Brien", body)
        node = shutil.which('node')
        if not node:
            self.skipTest('node not installed — syntax check skipped')
        with tempfile.NamedTemporaryFile('w', suffix='.js') as f:
            f.write(body)
            f.flush()
            check = subprocess.run([node, '--check', f.name], capture_output=True, text=True)
        self.assertEqual(check.returncode, 0, check.stderr)

    def test_the_static_copies_are_gone(self):
        # A leftover static copy would be served by nginx at its old URL with
        # the old chapter's literals, and nothing would notice.
        for rel in ('static/manifest.json', 'static/js/service-worker.js', 'static/offline.html'):
            self.assertFalse((settings.BASE_DIR / rel).exists(), rel)


class PwaAssetsReachableMidTwoFactorTests(TestCase):
    """09-28-26 — the module docstring's promise, pinned.

    A member with a confirmed TOTP device who has passed the password step but
    not the TOTP step is "mid-2FA". base.html (extended by two_factor/verify.html)
    registers the worker for every authenticated page, so these two URLs must
    not 302 to the verify page for that session. They carry no member data.
    """

    def setUp(self):
        from django_otp.plugins.otp_totp.models import TOTPDevice
        from src.models import ParliamentUser
        self.user = ParliamentUser.objects.create_user(
            user_id='pwa2fa1', name='PWA Mid 2FA', username='pwa2fa', member_type='Member')
        self.user.set_password('testpass123')
        self.user.save()
        TOTPDevice.objects.create(user=self.user, name='default', confirmed=True)
        self.client.force_login(self.user)

    def test_the_session_really_is_mid_2fa(self):
        # Control: an ordinary page IS held at the verify step, so the two
        # assertions below are about the exemption, not a missing enforcement.
        r = self.client.get(reverse('home'))
        self.assertEqual(r.status_code, 302)
        self.assertIn('/accounts/two-factor/verify/', r['Location'])

    def test_service_worker_is_served(self):
        r = self.client.get('/service-worker.js')
        self.assertEqual(r.status_code, 200)

    def test_manifest_is_served(self):
        r = self.client.get(reverse('web_manifest'))
        self.assertEqual(r.status_code, 200)
