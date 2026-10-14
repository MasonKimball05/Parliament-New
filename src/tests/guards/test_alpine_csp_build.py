"""
v3.45.7 — Alpine must be the CSP build, and templates must stay inside what it can parse.

WHY. The production CSP (`SecurityHeadersMiddleware`) has no 'unsafe-eval'.
The standard Alpine build compiles every x-data / x-show / @click string with
the AsyncFunction constructor, which that CSP refuses. From 06-14-26 (when
Alpine was added) until v3.45.7 not one Alpine expression ran in production:
the onboarding wizard showed all six steps at once and its buttons did
nothing, the guide search did not filter, the Getting Started checklist did
not collapse, the URL explorer's sections never opened, and the service-event
reminder fields never hid. DEBUG=True skips the CSP, so none of it showed up
locally. Checked in Chromium with the real CSP header, before and after.

The fix is the CSP build of Alpine, not 'unsafe-eval'. With the standard build
plus 'unsafe-eval', any HTML that reaches a page carrying an x-data / x-init
attribute runs as script, nonce or not.

The CSP build parses simple expressions itself but cannot parse methods
written in an x-data attribute, and cannot see globals (`window`,
`document`). Those components are registered with Alpine.data in
static/js/alpine-components.js (or in the page's own nonce'd script).
"""
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, override_settings

BASE = Path(settings.BASE_DIR)
TEMPLATES = BASE / 'templates'
ALPINE = BASE / 'static' / 'js' / 'alpinejs.min.js'
COMPONENTS = BASE / 'static' / 'js' / 'alpine-components.js'

# x-data, x-show, x-model, x-text, x-html, x-init, x-effect, x-if, x-for,
# x-bind:foo / :foo, x-on:foo / @foo (with modifiers). Values in double quotes.
ATTR = re.compile(
    r'\s(x-(?:data|show|model|text|html|init|effect|if|for)|x-bind:[\w.-]+|:[a-z][\w.-]*'
    r'|x-on:[\w.-]+|@[a-z][\w.:-]*)="([^"]*)"',
    re.S,
)
REGISTERED = re.compile(r"Alpine\.data\(\s*'([A-Za-z_$][\w$]*)'")
METHOD_IN_LITERAL = re.compile(r'[A-Za-z_$][\w$]*\s*\([^()]*\)\s*\{')


def alpine_templates():
    for path in sorted(TEMPLATES.rglob('*.html')):
        text = path.read_text(encoding='utf-8')
        attrs = [(name, value) for name, value in ATTR.findall(text)
                 if not name.startswith(':') or 'x-' in text]
        if any(name == 'x-data' for name, _ in attrs):
            yield path, text, attrs


class AlpineBuildTests(SimpleTestCase):

    def test_vendored_alpine_is_the_csp_build(self):
        js = ALPINE.read_text(encoding='utf-8')
        self.assertIn('CSP Parser Error', js,
                      'static/js/alpinejs.min.js must be @alpinejs/csp (dist/cdn.min.js)')
        self.assertNotIn('getPrototypeOf(async function(){}).constructor', js,
                         'this is the standard Alpine build; it needs unsafe-eval')

    def test_alpine_is_loaded_with_a_versioned_url(self):
        # nginx serves /static/ immutable for 30 days; an unversioned URL would
        # keep the old (standard) build in browsers for a month after deploy.
        for name in ('base.html', 'onboarding/wizard.html'):
            text = (TEMPLATES / name).read_text(encoding='utf-8')
            self.assertIn("{% static_v 'js/alpinejs.min.js' %}", text, name)
            self.assertNotIn("{% static 'js/alpinejs.min.js' %}", text, name)

    def test_components_load_before_alpine(self):
        text = (TEMPLATES / 'base.html').read_text(encoding='utf-8')
        components = text.index("{% static_v 'js/alpine-components.js' %}")
        alpine = text.index("{% static_v 'js/alpinejs.min.js' %}")
        self.assertLess(components, alpine)

    @override_settings(DEBUG=False)
    def test_csp_has_no_unsafe_eval(self):
        from django.http import HttpResponse
        from src.middleware.security import InputSanitizationMiddleware
        middleware = InputSanitizationMiddleware(lambda r: HttpResponse('ok'))
        response = middleware.add_security_headers(HttpResponse('ok'), csp_nonce='abc', path='/home/')
        csp = response.get('Content-Security-Policy', '')
        self.assertIn("script-src 'self' 'nonce-abc'", csp)
        self.assertNotIn('unsafe-eval', csp)


class TemplateExpressionTests(SimpleTestCase):

    def registered(self, text=''):
        return set(REGISTERED.findall(COMPONENTS.read_text(encoding='utf-8'))) | set(REGISTERED.findall(text))

    def test_some_templates_use_alpine(self):
        self.assertGreaterEqual(len(list(alpine_templates())), 5)

    def test_x_data_is_a_registered_name_or_a_plain_object(self):
        for path, text, attrs in alpine_templates():
            names = self.registered(text)
            for name, value in attrs:
                if name != 'x-data':
                    continue
                v = value.strip()
                with self.subTest(template=str(path.relative_to(BASE)), x_data=v[:60]):
                    if not v or v.startswith('{'):
                        self.assertIsNone(
                            METHOD_IN_LITERAL.search(v),
                            'methods inside an x-data attribute: register the component with Alpine.data')
                        self.assertNotIn('=>', v)
                        self.assertNotIn('function', v)
                    else:
                        ident = re.match(r'([A-Za-z_$][\w$]*)\s*(\(.*\))?$', v, re.S)
                        self.assertIsNotNone(ident, 'x-data must be a name, name(...), or {...}')
                        self.assertIn(ident.group(1), names,
                                      f'{ident.group(1)} is not registered with Alpine.data')

    def test_expressions_do_not_reach_for_globals(self):
        # The CSP build has no access to window/document; such an expression
        # fails at runtime with "Undefined variable".
        for path, text, attrs in alpine_templates():
            for name, value in attrs:
                with self.subTest(template=str(path.relative_to(BASE)), attr=name, value=value[:60]):
                    self.assertIsNone(re.search(r'\b(window|document)\s*\.', value))

    def test_registered_components_exist_for_known_pages(self):
        names = self.registered()
        for name in ('guideSearch', 'urlExplorer', 'onboardingChecklist'):
            self.assertIn(name, names)
        wizard = (TEMPLATES / 'onboarding' / 'wizard.html').read_text(encoding='utf-8')
        self.assertIn("Alpine.data('onboarding', onboarding)", wizard)
