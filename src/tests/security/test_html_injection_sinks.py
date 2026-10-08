"""
v3.44.9 (10-08-26) — member-typed text must not reach a page as HTML.

Found by the 10-08 auto-run and confirmed in Chromium on main:

  * A member saved markup as their "other email". The directory card wrote
    it into `innerHTML`, so an admin who opened the card got a form the
    member wrote. `base.html` adds a CSRF token to any POST form that lacks
    one, so one click anywhere submitted it as the admin. In the test that
    made the member a committee chair.
  * An Editor collaborator saved a citation marker containing a button. The
    resolution edit page drew marker chips with `innerHTML`, inside the main
    form. When the chair clicked the button the resolution was marked passed
    and its amendment was applied.

The CSP blocks injected scripts. It does not block injected forms, buttons,
images or styles.

Two layers are tested here:

  1. The pages escape (text checks on the templates; the suite has no
     browser, the same way `test_double_submit_guard` works).
  2. `profile_view` refuses `<` and `>` in the short profile fields.

Run with: python manage.py test src.tests.security.test_html_injection_sinks
"""
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from src.models import ParliamentUser, RoleHistory
from src.profile_text import PLAIN_PROFILE_FIELDS, profile_text_error

TEMPLATES = Path(settings.BASE_DIR) / 'templates'
PASSWORD = 'sink-test-12345!'

MARKUP = '"><form method=post action=/x/><button>go</button></form>'


def read(relative):
    return (TEMPLATES / relative).read_text(encoding='utf-8')


class EscapeHelperTests(SimpleTestCase):
    """`div.textContent = x; return div.innerHTML` does not escape quotes."""

    HELPER = re.compile(r'\.textContent\s*=\s*[^;\n]+;\s*return\s+\w+\.innerHTML\s*;')

    def test_no_template_uses_the_quote_blind_helper(self):
        offenders = [
            str(path.relative_to(TEMPLATES))
            for path in sorted(TEMPLATES.rglob('*.html'))
            if self.HELPER.search(path.read_text(encoding='utf-8'))
        ]
        self.assertEqual(
            offenders, [],
            'These templates escape with the textContent trick, which leaves '
            '" and \' alone and is unsafe inside an attribute. Use a helper '
            'that replaces & < > " and \'.')

    def test_helpers_escape_both_quote_characters(self):
        for relative in ('base.html', 'directory.html', 'house_map.html',
                         'chat/channel.html', 'calendar.html', 'user_list.html',
                         'officer/chapter_minutes_editor.html',
                         'officer/manage_roles.html', 'quote_book/book.html',
                         'kai/form_builder.html', 'service_hours/form_builder.html',
                         'admin_v2/manage_users.html'):
            with self.subTest(template=relative):
                text = read(relative)
                self.assertIn("'&quot;')", text)
                self.assertIn("'&#39;')", text)


class ProfileCardSinkTests(SimpleTestCase):
    """The three profile-card renderers escape every value from the card API."""

    RAW = re.compile(
        r'\$\{\s*(?:'
        r'd\.(?:name|email|other_email|phone|pledge_class|pledge_class_greek|graduation|profile_picture_url)'
        r'|typeLabel|range|r\.name|socials\[k\]\.(?:url|handle)'
        r')\s*\}')

    def test_card_values_are_never_interpolated_raw(self):
        for relative in ('directory.html', 'house_map.html', 'chat/channel.html'):
            with self.subTest(template=relative):
                found = [
                    f'{number}: {line.strip()[:120]}'
                    for number, line in enumerate(read(relative).splitlines(), 1)
                    if self.RAW.search(line)
                ]
                self.assertEqual(found, [], 'Wrap these in the page\'s escape helper.')


class ResolutionFormSinkTests(SimpleTestCase):
    def setUp(self):
        self.text = read('cnb/resolution_form.html')

    def test_citation_chips_are_set_as_text(self):
        self.assertNotIn("'<span>' + label + '</span>'", self.text)
        self.assertNotIn("'<span>' + marker.slice(1, -1) + '</span>'", self.text)
        self.assertEqual(self.text.count(".querySelector('.js-chip-label').textContent ="), 2)

    def test_section_context_is_set_as_text(self):
        self.assertNotIn("+ p.s.number +", self.text.split('var ctxLines')[0][-600:])
        self.assertIn("ctxLines[2].textContent = (p.s.content || '').slice(0, 200);", self.text)


class SubmitSpinnerTests(SimpleTestCase):
    def test_button_label_is_not_reparsed_as_html(self):
        text = read('base.html')
        self.assertNotIn("+ originalText + '...'", text.replace("createTextNode(originalText + '...')", ''))
        self.assertIn("submitBtn.appendChild(document.createTextNode(originalText + '...'));", text)


def make_user(uid='SINK-U1', **kwargs):
    defaults = dict(name='Sink User', username=uid.lower(), member_type='Member',
                    member_status='Active', email=f'{uid.lower()}@example.com')
    defaults.update(kwargs)
    user = ParliamentUser.objects.create(user_id=uid, **defaults)
    user.set_password(PASSWORD)
    user.save()
    return user


class ProfileTextErrorTests(SimpleTestCase):
    def test_every_listed_field_refuses_angle_brackets(self):
        for key in PLAIN_PROFILE_FIELDS:
            for bad in ('a<b', 'a>b', MARKUP):
                with self.subTest(field=key, value=bad):
                    self.assertTrue(profile_text_error({key: bad}))

    def test_ordinary_values_pass(self):
        post = {
            'username': 'jdoe', 'preferred_name': "D'Angelo", 'phone_number': '(205) 555-0100',
            'other_email': 'j.doe+beta@example.com', 'pledge_class': 'Fall 2023',
            'instagram': 'j.doe_1', 'linkedin': 'john-doe-123', 'rh_role_name': 'VP of Programming & Events',
            'about_me': 'GPA < 4.0 but > 3.5 <3',
        }
        self.assertEqual(profile_text_error(post), '')

    def test_other_email_must_be_an_email(self):
        self.assertTrue(profile_text_error({'other_email': 'not an email'}))
        self.assertTrue(profile_text_error({'other_email': '"quoted"@example.com<'}))
        self.assertEqual(profile_text_error({'other_email': ''}), '')

    def test_unchanged_other_email_is_not_revalidated(self):
        self.assertEqual(profile_text_error({'other_email': 'old value'}, 'old value'), '')


class ProfileViewRefusesMarkupTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.client.force_login(self.user)
        self.url = reverse('profile')

    def test_other_email_with_markup_is_not_saved(self):
        response = self.client.post(self.url, {'extended_profile_submit': '1', 'other_email': MARKUP})
        self.assertEqual(response.status_code, 302)
        self.user.refresh_from_db()
        self.assertFalse(self.user.other_email)

    def test_ajax_gets_a_400_with_the_reason(self):
        response = self.client.post(
            self.url, {'extended_profile_submit': '1', 'instagram': 'me<img src=x>'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 400)
        self.assertIn('Instagram', response.json()['error'])
        self.user.refresh_from_db()
        self.assertFalse(self.user.instagram)

    def test_nothing_else_in_the_same_post_is_saved(self):
        self.client.post(self.url, {'extended_profile_submit': '1', 'about_me': 'New bio',
                                    'twitter': 'ok', 'facebook': '<b>'})
        self.user.refresh_from_db()
        self.assertFalse(self.user.about_me)
        self.assertFalse(self.user.twitter)

    def test_username_and_preferred_name(self):
        self.client.post(self.url, {'profile_submit': '1', 'username': 'x"><b>', 'preferred_name': 'Sam',
                                    'email': self.user.email})
        self.user.refresh_from_db()
        self.assertEqual(self.user.username, 'sink-u1')
        self.assertFalse(self.user.preferred_name)

    def test_role_history_and_custom_social(self):
        self.client.post(self.url, {'role_history_add_submit': '1', 'rh_role_name': 'Scribe',
                                    'rh_start_semester': 'Fall <2025>'})
        self.assertFalse(RoleHistory.objects.filter(user=self.user).exists())
        self.client.post(self.url, {'custom_social_add_submit': '1', 'cs_platform': 'Site', 'cs_handle': '<a>'})
        self.user.refresh_from_db()
        self.assertFalse(self.user.custom_socials)

    def test_a_normal_profile_still_saves(self):
        response = self.client.post(self.url, {
            'extended_profile_submit': '1', 'about_me': 'I <3 this chapter',
            'other_email': 'second@example.com', 'instagram': '@sink.user',
            'pledge_class': 'Fall 2023',
        })
        self.assertEqual(response.status_code, 302)
        self.user.refresh_from_db()
        self.assertEqual(self.user.other_email, 'second@example.com')
        self.assertEqual(self.user.instagram, 'sink.user')
        self.assertEqual(self.user.about_me, 'I <3 this chapter')

    def test_password_form_is_not_touched(self):
        """A password may contain < or >. The plain-text rule skips that form."""
        self.user.set_password('Old-pass-<1>-xyz!')
        self.user.save()
        self.client.force_login(self.user)
        response = self.client.post(self.url, {
            'password_submit': '1', 'old_password': 'Old-pass-<1>-xyz!',
            'new_password1': 'New-<pass>-98765-qwe!', 'new_password2': 'New-<pass>-98765-qwe!',
            'username': 'ignored<',
        })
        self.assertNotEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('New-<pass>-98765-qwe!'))
