"""
v3.31.1 — Mason reported the education dashboard's "Add Meeting" button did
nothing. Root cause: prod's CSP (`script-src 'self' 'nonce-…'`, no
'unsafe-inline' — src/middleware/security.py) blocks every inline event
handler attribute (onclick=, onchange=, onsubmit=) outright, and dev sends no
CSP header at all — so the button worked in dev and was silently dead in
prod. Tracing it found the same problem on every other chair control on the
page (publish/duplicate/delete task, the completion grid, both restriction
endpoints) and on the meeting edit page's Delete button, all fixed the same
way: a `data-*` attribute plus a delegated `addEventListener`, instead of an
inline handler.

`src/tests/guards/test_csp_templates.py` covers this at the source-scanning
level, generally, across every template. These tests are the OUTPUT-level,
feature-specific complement — the same principle test_modal_component.py's
`EverySubmitButtonBelongsToAFormTests` states: "a scanner approximates; a
request does not." Render the actual page a chair gets and check the actual
wiring is there, not just that no onclick= survived.
"""
import re

from django.test import Client, TestCase
from django.urls import reverse

from src.models import Committee, EducationMeeting, Event
from src.tests.education._fixtures import EducationFixtureMixin

INLINE_HANDLER_RE = re.compile(r'<[a-zA-Z][^>]*\b(?:onclick|onchange|onsubmit)\s*=', re.IGNORECASE)


class EducationDashboardHasNoInlineHandlersTests(EducationFixtureMixin, TestCase):
    def setUp(self):
        self.build()

    def test_the_rendered_page_has_no_inline_event_handlers(self):
        html = self.client.get(reverse('education_home', args=[self.committee.code])).content.decode()
        self.assertIsNone(
            INLINE_HANDLER_RE.search(html),
            'The rendered education dashboard still has an onclick=/onchange=/'
            'onsubmit= attribute — silently dead in prod.',
        )

    def test_the_add_meeting_button_opens_the_right_modal_via_data_attribute(self):
        html = self.client.get(reverse('education_home', args=[self.committee.code])).content.decode()
        self.assertIn('data-modal-open="addMeetingModal"', html)
        # And the modal it points at actually exists on the page.
        self.assertIn('id="addMeetingModal"', html)

    def test_the_add_task_and_restriction_buttons_also_use_data_attributes(self):
        html = self.client.get(reverse('education_home', args=[self.committee.code])).content.decode()
        self.assertIn('data-modal-open="add-task-modal"', html)
        self.assertIn('data-modal-open="add-restriction-modal"', html)

    def test_a_non_chair_sees_no_add_meeting_button_at_all(self):
        # Not part of the bug, but worth confirming the fix didn't accidentally
        # widen who sees the control — {% if is_chair %} still gates it.
        self.client.force_login(self.brother)
        html = self.client.get(reverse('education_home', args=[self.committee.code])).content.decode()
        self.assertNotIn('data-modal-open="addMeetingModal"', html)


class EducationDashboardModalCloseButtonsWorkTests(EducationFixtureMixin, TestCase):
    """
    The X and Cancel buttons on every modal on this page come from the shared
    components/modal_open.html + modal_close.html include pair, which had the
    identical onclick= bug — fixed once, generically, in those two files
    (plus one delegated listener in base.html), which is why this checks all
    three modals on the page rather than just Add Meeting's.
    """

    def setUp(self):
        self.build()

    def test_every_modal_on_the_page_has_a_data_modal_close_button(self):
        html = self.client.get(reverse('education_home', args=[self.committee.code])).content.decode()
        for modal_id in ('addMeetingModal', 'add-task-modal', 'add-restriction-modal'):
            with self.subTest(modal_id=modal_id):
                self.assertIn(f'data-modal-close="{modal_id}"', html)


class EducationMeetingFormHasNoInlineHandlersTests(EducationFixtureMixin, TestCase):
    def setUp(self):
        self.build()
        event = Event.objects.create(
            title='Pledge Class Meeting', date_time=self._future(),
            committee=self.committee, created_by=self.chair,
        )
        self.meeting = EducationMeeting.objects.create(
            committee=self.committee, event=event, created_by=self.chair,
        )

    @staticmethod
    def _future():
        from django.utils import timezone
        return timezone.now() + timezone.timedelta(days=3)

    def test_the_rendered_edit_page_has_no_inline_event_handlers(self):
        html = self.client.get(
            reverse('education_edit_meeting', args=[self.committee.code, self.meeting.pk])
        ).content.decode()
        self.assertIsNone(
            INLINE_HANDLER_RE.search(html),
            'The rendered meeting-edit page still has an onclick=/onsubmit= '
            'attribute — the Delete meeting button was one of these.',
        )

    def test_the_delete_button_and_confirm_form_are_wired_by_id(self):
        html = self.client.get(
            reverse('education_edit_meeting', args=[self.committee.code, self.meeting.pk])
        ).content.decode()
        self.assertIn('id="delete-meeting-trigger"', html)
        self.assertIn('id="delete-meeting-form"', html)
