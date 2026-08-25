"""
v3.21.0 — the shared modal shell, and the one failure mode it introduces.

⚠️ WHY THE COMPONENT EXISTS. v3.20.0's overflow bug — a modal taller than the
viewport with its submit button below the fold and no way to scroll — was
present in all three modals on the education dashboard, because the shell had
been hand-copied three times. The scroll behaviour now lives in
`components/modal_open.html`, so the next modal cannot be born without it.

⚠️ WHY THIS TEST EXISTS. A Django `{% include %}` cannot wrap arbitrary markup
(only inheritance can, and inheritance is one-per-page, so it cannot serve three
modals on one dashboard). An open/close **pair** is the honest way to share the
shell — and an unbalanced pair is the single new way to get it wrong. It
produces unclosed `<div>`s, which browsers paper over silently and which no
other check in this project would catch.
"""

import re
from pathlib import Path

from django.conf import settings
from django.test import Client, SimpleTestCase, TestCase
from django.urls import reverse

OPEN_INCLUDE = 'components/modal_open.html'
CLOSE_INCLUDE = 'components/modal_close.html'


def _templates():
    return sorted((Path(settings.BASE_DIR) / 'templates').rglob('*.html'))


def _strip_comments(text):
    """Preserve line count; see `test_csrf_token_source` for why."""
    def blank(match):
        return '\n' * match.group(0).count('\n')

    text = re.sub(r'<!--.*?-->', blank, text, flags=re.DOTALL)
    text = re.sub(r'\{%\s*comment\s*%\}.*?\{%\s*endcomment\s*%\}', blank, text, flags=re.DOTALL)
    text = re.sub(r'\{#.*?#\}', blank, text)
    return text


class ModalPairsAreBalancedTests(SimpleTestCase):

    def test_the_component_exists(self):
        """The control — every assertion below is about files pointing here."""
        root = Path(settings.BASE_DIR) / 'templates'
        self.assertTrue((root / OPEN_INCLUDE).exists())
        self.assertTrue((root / CLOSE_INCLUDE).exists())

    def test_every_template_closes_as_many_modals_as_it_opens(self):
        offenders = []
        for path in _templates():
            try:
                body = _strip_comments(path.read_text(encoding='utf-8'))
            except (OSError, UnicodeDecodeError):
                continue
            opens = body.count(OPEN_INCLUDE)
            closes = body.count(CLOSE_INCLUDE)
            # The component files themselves name their partner in prose that
            # survives comment-stripping only if it is outside a comment; they
            # are excluded by name because a file cannot balance itself.
            if path.name in ('modal_open.html', 'modal_close.html'):
                continue
            if opens != closes:
                offenders.append(
                    f'{path.relative_to(settings.BASE_DIR)}: {opens} open, {closes} close'
                )
        self.assertEqual(
            offenders, [],
            'Unbalanced modal includes leave unclosed <div>s, which browsers '
            'hide and nothing else here checks:\n  ' + '\n  '.join(offenders),
        )

    def test_the_education_dashboard_uses_the_component(self):
        """
        The page the component was extracted from. If it drifts back to a
        hand-rolled shell, the overflow bug comes with it.
        """
        body = (Path(settings.BASE_DIR) / 'templates' / 'committee' / 'education.html').read_text(encoding='utf-8')
        self.assertEqual(body.count(OPEN_INCLUDE), 3)
        self.assertEqual(body.count(CLOSE_INCLUDE), 3)

    def test_the_shell_still_scrolls(self):
        """
        The whole reason the component exists. `items-start` rather than
        `items-center` is deliberate: a centred panel taller than the viewport
        clips at BOTH ends, hiding the title as well as the button.
        """
        shell = (Path(settings.BASE_DIR) / 'templates' / OPEN_INCLUDE).read_text(encoding='utf-8')
        self.assertIn('overflow-y-auto', shell)
        self.assertIn('max-h-', shell)
        self.assertIn('items-start', shell)
        self.assertNotIn('items-center justify-center', shell)


class EverySubmitButtonBelongsToAFormTests(TestCase):
    """
    ⚠️ v3.21.2 — REGRESSION TEST FOR A BUG THIS COMPONENT CAUSED.

    v3.21.0 moved the modal footer into `components/modal_close.html`, which
    renders **after** the caller's `</form>`. A `<button type="submit">` outside
    its form submits nothing, so Add Task, Add Meeting and Restrict a Page all
    silently stopped working the moment they were converted — the modal opened,
    the fields accepted input, and the button did nothing at all.

    Fixed with the HTML `form` attribute: the footer's button names its form by
    id, which lets it live anywhere in the document.

    ⚠️ THIS TEST PARSES THE RENDERED PAGE, NOT THE TEMPLATE. The template looked
    entirely correct — `<form>` … `</form>` … `{% include %}` reads fine, and
    every other check in this project passed. The defect only exists in the
    output, in the relationship between two elements. **A scanner approximates;
    a request does not** — same conclusion as the `committee.committee_code`
    500 in v3.20.0.
    """

    def setUp(self):
        from src.models import Committee, ParliamentUser

        self.committee = Committee.objects.create(
            name='Education', code='EDUCATION',
            is_active=True, is_education_committee=True,
        )
        self.chair = ParliamentUser.objects.create(
            user_id='9001', username='9001', name='Edu Chair',
            member_type='Officer', member_status='Active', is_admin=True,
        )
        self.chair.set_password('modal-test-pass-12345!')
        self.chair.save()
        self.committee.chairs.add(self.chair)

        self.client = Client()
        self.client.force_login(self.chair)

    def _orphan_submits(self, html):
        """
        Submit buttons that belong to no form.

        A submit button is fine if it is lexically inside a `<form>…</form>`, or
        if it carries `form="some-id"` naming a form that exists on the page.
        """
        forms = {m.group(1) for m in re.finditer(r'<form\b[^>]*\bid="([^"]+)"', html)}

        # Mask everything inside a form so a plain in-form button is not flagged.
        masked = re.sub(r'<form\b.*?</form>', lambda m: ' ' * len(m.group(0)), html, flags=re.DOTALL)

        orphans = []
        for match in re.finditer(r'<button\b[^>]*>', masked):
            tag = match.group(0)
            if 'type="submit"' not in tag:
                continue
            owner = re.search(r'\bform="([^"]+)"', tag)
            if not owner:
                orphans.append(f'no form= attribute: {tag[:90]}')
            elif owner.group(1) not in forms:
                orphans.append(f'form="{owner.group(1)}" does not exist: {tag[:90]}')
        return orphans

    def test_the_education_dashboard_has_no_orphaned_submit_buttons(self):
        html = self.client.get(
            reverse('education_home', args=[self.committee.code])
        ).content.decode()
        self.assertEqual(
            self._orphan_submits(html), [],
            'A submit button on this page belongs to no form, so clicking it '
            'does nothing. If it sits outside its <form> — which the modal '
            'component makes easy — give the form an id and pass form_id to '
            'components/modal_close.html.',
        )

    def test_the_detector_actually_detects(self):
        """
        ⚠️ The control, and it is not optional here: the assertion above passes
        trivially against a page with no submit buttons at all, which is exactly
        what a broken fixture would produce.
        """
        broken = '<div><button type="submit">Save</button></div>'
        self.assertEqual(len(self._orphan_submits(broken)), 1)

        wrong_id = '<form id="a"></form><button type="submit" form="b">Save</button>'
        self.assertEqual(len(self._orphan_submits(wrong_id)), 1)

        inside = '<form id="a"><button type="submit">Save</button></form>'
        self.assertEqual(self._orphan_submits(inside), [])

        by_attribute = '<form id="a"></form><button type="submit" form="a">Save</button>'
        self.assertEqual(self._orphan_submits(by_attribute), [])

    def test_the_three_modals_still_render_their_submit_buttons(self):
        """
        Guards the other direction: a footer that renders nothing would also
        pass the orphan check, and would be just as broken.
        """
        html = self.client.get(
            reverse('education_home', args=[self.committee.code])
        ).content.decode()
        for label in ('Add Task', 'Add Meeting'):
            with self.subTest(label=label):
                self.assertIn(f'>{label}</button>', html)
