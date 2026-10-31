"""
Preferences in the app (v4.4.0): the website's Preferences page
(src/view/preferences.py), read and saved through the same functions —
``preference_values`` and ``save_preferences`` — so the app gets the same
validation (``UserPreferencesForm``, its nine-item menu limit), the same
dark-mode rule, cache bust and activity log.

* ``GET preferences/`` — every preference the app shows, in sections: each
  item's ``key`` (the form field), ``label``, ``kind`` (``toggle`` or
  ``choice``), ``choices`` for a choice, and its ``value``. ``menu_limit`` is
  the website menu's cap.
* ``PATCH preferences/`` ``{key: value, ...}`` — changes some of them; the
  rest keep their saved values. A toggle takes a JSON boolean, a choice one of
  its values. Answers like ``GET``, with the saved values. ``400 invalid`` for
  an unknown key, a wrong type, or what the form refuses (more than nine menu
  items).

Left on the website: developer mode (its own gated endpoint), the browser's
push subscription and install button, the developer API token, the security
links and the onboarding tour.

Signed-in members, like the web page (which has no page toggle).
"""
from rest_framework.response import Response

from src.feature_flag_decorators import check_feature_enabled
from src.forms import UserPreferencesForm
from src.mobile.api import MobileAPIView
from src.mobile.views_excuses_polls import BadRequest
from src.view.preferences import preference_values, preferences_for, save_preferences

#: The website's cap on menu items (UserPreferencesForm.clean).
MENU_LIMIT = 9

#: (section key, title, footer, [(field, label)]). Labels are the app's
#: sentence-case copy; every UserPreferencesForm field appears exactly once
#: (src/tests/mobile/test_preferences.py checks).
SECTIONS = (
    ('push', 'Push notifications',
     'Sent to this phone and to any browser where you turned them on.', (
         ('push_announcements', 'Announcements'),
         ('push_legislation', 'Legislation and voting'),
         ('push_events', 'Events'),
         ('push_slating', 'Officer elections (slating)'),
         ('push_chat', 'Chat messages'),
         ('push_mentions', 'Chat mentions'),
         ('push_excuses', 'Excuse reviews'),
         ('push_reminders', 'Scheduled reminders'),
         ('push_security', 'Security alerts'),
     )),
    ('notifications', 'Notifications',
     'What appears in your notifications list, in the app and on the website.', (
         ('notify_announcements', 'Announcements'),
         ('notify_legislation', 'Legislation and voting'),
         ('notify_events', 'Events'),
         ('notify_slating', 'Officer elections (slating)'),
         ('notify_excuses', 'Excuse reviews'),
     )),
    ('email', 'Email', 'Which emails Parliament sends you.', (
        ('email_announcements', 'Announcements'),
        ('email_legislation', 'New legislation'),
        ('email_events', 'Upcoming events'),
        ('email_committee_updates', 'Committee updates'),
    )),
    ('website', 'On the website', 'These change the website, not the app.', (
        ('theme', 'Color theme'),
        ('home_layout', 'Home page layout'),
        ('landing_page', 'Landing page after signing in'),
        ('compact_view', 'Compact view'),
        ('show_announcement_popups', 'Announcement pop-ups'),
    )),
    ('menu', 'Website menu', f'Up to {MENU_LIMIT} items in the website’s navigation menu.', (
        ('show_vote_menu', 'Vote'),
        ('show_committees_menu', 'Committees'),
        ('show_chats_menu', 'Chats'),
        ('show_documents_menu', 'Documents'),
        ('show_announcements_menu', 'Announcements'),
        ('show_calendar_menu', 'Calendar'),
        ('show_legislation_menu', 'Legislation'),
        ('show_cnb_menu', 'Constitution & Bylaws'),
        ('show_resolutions_menu', 'Resolutions'),
        ('show_excuses_menu', 'My excuses'),
        ('show_search_menu', 'Search'),
        ('show_roberts_rules_menu', 'Robert’s Rules'),
    )),
)

_FIELDS = UserPreferencesForm.base_fields


def _is_choice(field):
    return hasattr(_FIELDS[field], 'choices')


def _shown(field):
    # The website shows the theme only while dark mode is on chapter-wide
    # (and saves 'light' whatever is sent while it's off: save_preferences).
    return field != 'theme' or check_feature_enabled('dark_mode')


def _payload(preferences):
    values = preference_values(preferences)
    sections = []
    for key, title, footer, fields in SECTIONS:
        items = []
        for field, label in fields:
            if not _shown(field):
                continue
            item = {'key': field, 'label': label, 'value': values[field]}
            if _is_choice(field):
                item['kind'] = 'choice'
                item['choices'] = [{'value': v, 'label': str(lbl)} for v, lbl in _FIELDS[field].choices]
            else:
                item['kind'] = 'toggle'
                item['value'] = bool(values[field])
            items.append(item)
        sections.append({'key': key, 'title': title, 'footer': footer, 'items': items})
    return {'sections': sections, 'menu_limit': MENU_LIMIT}


def _clean_changes(body):
    """``{field: value}`` from the PATCH body, or a 400."""
    if not isinstance(body, dict) or not body:
        raise BadRequest('Send the preferences to change.', code='invalid')
    changes = {}
    for field, value in body.items():
        if field not in _FIELDS or not _shown(field):
            raise BadRequest(f'Unknown preference: {field}.', code='invalid')
        if _is_choice(field):
            if value not in {v for v, _ in _FIELDS[field].choices}:
                raise BadRequest(f'Not a choice for {field}.', code='invalid')
        elif not isinstance(value, bool):
            raise BadRequest(f'{field} is true or false.', code='invalid')
        changes[field] = value
    return changes


class PreferencesView(MobileAPIView):
    web_page = 'preferences'
    web_page_toggle = False   # preferences_view has no @require_page_enabled

    def get(self, request):
        return Response(_payload(preferences_for(request.user)))

    def patch(self, request):
        changes = _clean_changes(request.data)
        preferences = preferences_for(request.user)
        # The form validates a whole page's worth of fields: the saved values,
        # with this request's changes on top.
        data = {**preference_values(preferences), **changes}
        form, _ = save_preferences(request, preferences, data)
        if not form.is_valid():
            errors = form.non_field_errors() or [e for errs in form.errors.values() for e in errs]
            raise BadRequest(str(errors[0]) if errors else 'Invalid preferences.', code='invalid')
        return Response(_payload(preferences))
