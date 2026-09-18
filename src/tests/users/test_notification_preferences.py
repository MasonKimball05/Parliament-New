"""
09-17-26 — the `notify_excuses` preference (UserPreferences), added so
'excuse_reviewed' notifications (v3.33.0) can be turned off like every other
notification type. Model-level default/property behavior and the
UserPreferencesForm round trip live here; whether the preference is actually
respected when an excuse is reviewed is covered separately in
src/tests/events/test_excuse_review_notifications.py, next to the fixtures
for creating an excuse.
"""
from django.test import TestCase

from src.forms import UserPreferencesForm
from src.models import ParliamentUser, UserPreferences


def make_user(uid):
    return ParliamentUser.objects.create(
        user_id=uid, name='Pref User', username=uid.lower(),
        member_type='Member', member_status='Active',
    )


class NotifyExcusesDefaultTests(TestCase):
    """
    A UserPreferences row is auto-created for every ParliamentUser by the
    create_user_preferences post_save signal (src/models/users.py) — so
    these tests read the row the signal already made, rather than creating
    a second one (which would 500 on the OneToOne unique constraint).
    """

    def test_defaults_to_true_on_the_row_the_signal_auto_created(self):
        user = make_user('pref-default-1')
        self.assertTrue(user.preferences.notify_excuses)

    def test_defaults_to_true_even_with_no_key_at_all(self):
        # _pref() falls back to the given default when the key is missing,
        # which is what a preferences row created before this key existed
        # would look like. Built unsaved so this doesn't touch the DB.
        user = make_user('pref-default-2')
        prefs = UserPreferences(user=user, prefs={'notifications': {}})
        self.assertTrue(prefs.notify_excuses)

    def test_can_be_turned_off(self):
        user = make_user('pref-default-3')
        prefs = user.preferences
        prefs.prefs['notifications']['excuses'] = False
        prefs.save()
        prefs.refresh_from_db()
        self.assertFalse(prefs.notify_excuses)


class UserPreferencesFormRoundTripTests(TestCase):
    """
    UserPreferencesForm.save() rebuilds `prefs` wholesale (see its own
    PRESERVED_SECTIONS comment) — a new field has to appear in the initial
    dict AND the save() dict, or it silently reverts to the class default
    every time the form is submitted, for every user, regardless of what
    they'd actually set. This is the check that would have caught leaving
    either one out.
    """

    def setUp(self):
        self.user = make_user('pref-form-1')
        # Auto-created by the create_user_preferences post_save signal —
        # see NotifyExcusesDefaultTests' docstring above.
        self.prefs = self.user.preferences

    def _base_post_data(self, **overrides):
        # Every BooleanField the form defines must be present (or omitted,
        # for "unchecked") on a real POST — build the full set once so
        # individual tests only need to state what they're overriding.
        data = {
            'theme': 'light',
            'email_announcements': 'on', 'email_legislation': 'on',
            'email_events': 'on', 'email_committee_updates': 'on',
            'show_announcement_popups': 'on', 'compact_view': '',
            'home_layout': 'modern', 'landing_page': 'home',
            'notify_announcements': 'on', 'notify_legislation': 'on',
            'notify_events': 'on', 'notify_slating': 'on',
            'notify_excuses': 'on',
            'push_announcements': 'on', 'push_legislation': 'on',
            'push_events': 'on', 'push_slating': 'on', 'push_chat': 'on',
            'show_vote_menu': 'on', 'show_committees_menu': 'on',
            'show_documents_menu': 'on', 'show_announcements_menu': 'on',
            'show_calendar_menu': 'on', 'show_legislation_menu': 'on',
            'show_search_menu': 'on',
        }
        data.update(overrides)
        return data

    def test_initial_reflects_the_stored_value(self):
        self.prefs.prefs['notifications']['excuses'] = False
        self.prefs.save()
        form = UserPreferencesForm(instance=self.prefs)
        self.assertFalse(form.initial['notify_excuses'])

    def test_submitting_with_the_box_checked_saves_true(self):
        form = UserPreferencesForm(self._base_post_data(notify_excuses='on'), instance=self.prefs)
        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        self.assertTrue(saved.notify_excuses)

    def test_submitting_with_the_box_unchecked_saves_false(self):
        data = self._base_post_data()
        del data['notify_excuses']  # an unchecked checkbox is simply absent from POST
        form = UserPreferencesForm(data, instance=self.prefs)
        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        self.assertFalse(saved.notify_excuses)

    def test_saving_does_not_disturb_other_notification_preferences(self):
        data = self._base_post_data()
        del data['notify_excuses']
        del data['notify_slating']
        form = UserPreferencesForm(data, instance=self.prefs)
        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        self.assertFalse(saved.notify_excuses)
        self.assertFalse(saved.notify_slating)
        self.assertTrue(saved.notify_announcements)
