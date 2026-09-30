"""
v3.37.0 (09-30-26) — /site-monitor/static-assets/ for go-sentinel's stale-CDN check.

Pins: 404 without a valid monitor token (even when none is configured);
correct SHA-256 for files in STATIC_ROOT; only .css/.js; no escaping STATIC_ROOT
(.., absolute, symlink); at most 20 paths; never cacheable.

Run with: python manage.py test src.tests.security.test_site_monitor_static_assets
"""
import hashlib
import os
import tempfile

from django.test import TestCase, override_settings

TOKEN = 's' * 40
URL = '/site-monitor/static-assets/'


class StaticAssetFingerprintTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = self.tmp.name
        os.makedirs(os.path.join(root, 'css'))
        self.css = b'body{color:red}'
        with open(os.path.join(root, 'css', 'site.css'), 'wb') as f:
            f.write(self.css)
        with open(os.path.join(root, 'secret.txt'), 'w') as f:
            f.write('nope')
        outside = os.path.join(os.path.dirname(root), 'outside-%s.js' % os.getpid())
        with open(outside, 'w') as f:
            f.write('x')
        self.addCleanup(os.remove, outside)
        os.symlink(outside, os.path.join(root, 'link.js'))
        self.settings_ctx = override_settings(STATIC_ROOT=root, SITE_MONITOR_TOKEN=TOKEN,
                                              BEHIND_CLOUDFLARE=False)
        self.settings_ctx.enable()
        self.addCleanup(self.settings_ctx.disable)

    def get(self, *paths, token=TOKEN):
        extra = {'HTTP_X_SITE_MONITOR_TOKEN': token} if token else {}
        return self.client.get(URL, {'path': list(paths)}, **extra)

    def test_no_token_is_404(self):
        self.assertEqual(self.get('css/site.css', token=None).status_code, 404)
        self.assertEqual(self.get('css/site.css', token='x' * 40).status_code, 404)

    @override_settings(SITE_MONITOR_TOKEN='')
    def test_unconfigured_is_404(self):
        self.assertEqual(self.get('css/site.css').status_code, 404)

    def test_hash_matches_and_static_prefix_is_accepted(self):
        r = self.get('css/site.css', '/static/css/site.css')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Cache-Control'], 'no-store')
        want = hashlib.sha256(self.css).hexdigest()
        data = r.json()
        self.assertEqual(data['css/site.css'], {'sha256': want, 'bytes': len(self.css)})
        self.assertEqual(data['static/css/site.css']['sha256'], want)

    def test_refuses_non_assets_and_escapes(self):
        data = self.get('secret.txt', '../../etc/passwd.js', 'link.js', 'missing.css',
                        os.path.join(os.sep, 'etc', 'hosts.css')).json()
        self.assertEqual(set(data.values()), {None})

    def test_caps_paths(self):
        data = self.get(*[f'css/site.css?{i}' for i in range(30)]).json()
        self.assertEqual(len(data), 20)
