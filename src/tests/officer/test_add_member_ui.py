"""
v3.31.6 — Mason reported the Add Member flow on `/users/`:

    "When you click add it just adds on the username and temp password at
    the bottom and the add becomes a greyed out adding...
    If you click adding... it says that a user with this ID exist already,
    which makes me think it's trying to create another user with the same
    id. So you need to regenerate the id number if you want to make another
    user."

Two problems, one root cause each:

1. **The stuck button.** `submitAddMember()` showed the new credentials in a
   div (`#add-member-success`) appended below the Add Member form itself,
   and never re-enabled or reset the submit button on success — so the only
   way out was Cancel/X, which reloaded the whole page. Fixed by moving the
   credentials into a separate `#member-created-modal` popup, and having a
   successful submit hide-and-fully-reset the Add Member form *before* that
   popup even opens (`resetAddMemberForm()`), rather than leaving it in a
   used-up state.

2. **"ID already exists" on the next add.** The Member ID box is filled by
   client-side `generatePledgeId()` (for the Pledge default) and was never
   cleared or regenerated after a successful submission — so if an officer
   tried to add a second member with that same modal instance, the box still
   held the ID the server had *just* used, and resubmitting it collided.
   `resetAddMemberForm()` regenerates the ID as part of putting the form back
   in a clean state; `submitAddMember()` also regenerates automatically on
   an ID-collision error specifically, so a stale ID heals itself without
   the officer having to notice and click Generate by hand.

These tests are template/JS-wiring-level (mirroring
`test_education_dashboard_button_wiring.py`'s approach of rendering the real
page rather than reasoning about the source) plus one backend-level
regression test confirming the server side of the collision case behaves
like an ordinary validation error, not a crash.
"""
from django.test import Client, TestCase
from django.urls import reverse

from src.models import ParliamentUser


def make_officer(uid='9001', name='Add Member Officer'):
    user = ParliamentUser.objects.create(
        user_id=uid, name=name, username=uid,
        member_type='Officer', member_status='Active',
    )
    user.set_password('add-member-test-pass-12345!')
    user.save()
    return user


class AddMemberModalRenderingTests(TestCase):
    def setUp(self):
        self.officer = make_officer()
        self.client = Client()
        self.client.force_login(self.officer)

    def _get(self):
        return self.client.get(reverse('user_list')).content.decode()

    def test_the_old_inline_success_block_is_gone(self):
        html = self._get()
        self.assertNotIn('add-member-success', html)

    def test_a_separate_member_created_popup_exists(self):
        html = self._get()
        self.assertIn('id="member-created-modal"', html)
        self.assertIn('id="created-username"', html)
        self.assertIn('id="created-temp-password"', html)

    def test_the_popup_offers_add_another_and_done(self):
        html = self._get()
        self.assertIn('id="member-created-add-another-btn"', html)
        self.assertIn('id="member-created-done-btn"', html)

    def test_reset_add_member_form_regenerates_the_id_on_open_and_after_success(self):
        """
        The actual fix for the collision: the same reset function runs both
        when the modal is opened AND right after a successful add, so a used
        ID cannot survive into the next submission either way.
        """
        html = self._get()
        self.assertIn('function resetAddMemberForm()', html)
        self.assertIn('function openAddModal()', html)
        self.assertIn('resetAddMemberForm();', html)
        # openAddModal delegates to it rather than duplicating the reset logic
        self.assertIn(
            "document.getElementById('add-member-modal').classList.remove('hidden');\n    resetAddMemberForm();",
            html,
        )

    def test_a_successful_submit_hides_the_add_modal_before_popping_the_created_modal(self):
        html = self._get()
        self.assertIn("document.getElementById('add-member-modal').classList.add('hidden');\n            showMemberCreatedPopup(result);\n            resetAddMemberForm();", html)

    def test_an_id_collision_error_triggers_automatic_regeneration(self):
        html = self._get()
        self.assertIn('idRejected', html)
        self.assertIn('generatePledgeId();', html)

    def test_no_inline_event_handlers(self):
        import re
        html = self._get()
        self.assertIsNone(re.search(r'<[a-zA-Z][^>]*\b(?:onclick|onchange|onsubmit)\s*=', html, re.IGNORECASE))


class AddMemberBackendCollisionTests(TestCase):
    """
    The server side of the collision Mason hit. This already worked (a plain
    Django form validation error, not a crash) — confirmed here so the fix
    above is known to be sitting on a sound backend, and as a regression
    guard against that ever becoming a 500.
    """

    def setUp(self):
        self.officer = make_officer()
        self.client = Client()
        self.client.force_login(self.officer)
        self.url = reverse('add_member')

    def _payload(self, **overrides):
        payload = {
            'name': 'Pat Pledge',
            'user_id': 'P-ABC123',
            'email': None,
            'member_type': 'Pledge',
            'member_status': 'Active',
            'roles': [],
        }
        payload.update(overrides)
        return payload

    def test_first_submission_with_an_id_succeeds(self):
        response = self.client.post(self.url, data=self._payload(), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['success'])
        self.assertTrue(ParliamentUser.objects.filter(user_id='P-ABC123').exists())

    def test_resubmitting_the_same_id_is_a_clean_validation_error_not_a_500(self):
        self.client.post(self.url, data=self._payload(name='First Pledge'), content_type='application/json')
        response = self.client.post(
            self.url, data=self._payload(name='Second Pledge'), content_type='application/json'
        )
        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertFalse(body['success'])
        self.assertIn('user_id', body['errors'])
        self.assertIn('already exists', body['errors']['user_id'])
        # And the second member was never created under any ID.
        self.assertFalse(ParliamentUser.objects.filter(name='Second Pledge').exists())

    def test_leaving_the_id_blank_generates_a_fresh_one_each_time(self):
        first = self.client.post(
            self.url, data=self._payload(user_id='', name='Auto One'), content_type='application/json'
        )
        second = self.client.post(
            self.url, data=self._payload(user_id='', name='Auto Two'), content_type='application/json'
        )
        self.assertTrue(first.json()['success'])
        self.assertTrue(second.json()['success'])
        self.assertNotEqual(first.json()['member']['user_id'], second.json()['member']['user_id'])
