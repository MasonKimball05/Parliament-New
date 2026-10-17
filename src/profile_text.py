"""
Plain-text rules for the profile fields a member edits themselves (v3.44.9).

These values are shown to other people: in the directory card, the house
map, chat, the minutes editor and the member list. On 10-08-26 several of
those pages were found writing them into `innerHTML` unescaped, so a member
could put markup on a page an officer was looking at. The pages now escape.
This is the second layer: the short, single-line fields have no reason to
contain `<` or `>`, so a value that does is refused when it is saved.

`about_me` is not listed. It is free text ("GPA < 3.0", "<3") and is only
ever rendered as text.

v3.50.0 adds two rules and a second entry point:

* **No spreadsheet formulas.** A value starting with `=`, `+`, `-` or `@` is
  refused unless it is only digits and phone punctuation ("+1 (205) 555-0100").
  v3.45.2 made the CSV/XLSX exports neutralise these; this stops them being
  saved at all. Social handles may still start with "@" ("@masonk").
* **Phone numbers** are digits, spaces and `+ ( ) - .`, optionally "ext 12".
* Both only apply to a value that CHANGED, like the other-email check, so a
  member with an old odd value can still save the rest of their profile.
* `member_fields_error()` is the same rules for the officer and admin paths
  that edit someone else (Officers → Members edit/add/import, admin v2's
  profile editor), which until now accepted any text, any member type and
  any email.
"""
import re

from django.core.exceptions import ValidationError
from django.core.validators import validate_email

#: POST key -> the label used in the error message.
PLAIN_PROFILE_FIELDS = {
    'username': 'Username',
    'preferred_name': 'Preferred name',
    'email': 'Email',
    'phone_number': 'Phone number',
    'other_email': 'Other email',
    'pledge_class': 'Pledge class',
    'pledge_class_greek': 'Pledge class letter',
    'graduation_semester': 'Graduation semester',
    'instagram': 'Instagram',
    'twitter': 'Twitter / X',
    'linkedin': 'LinkedIn',
    'snapchat': 'Snapchat',
    'facebook': 'Facebook',
    'rh_role_name': 'Role name',
    'rh_start_semester': 'Start semester',
    'rh_end_semester': 'End semester',
    'cs_platform': 'Platform',
    'cs_handle': 'Handle',
    'ic_school': 'School',
    'ic_chapter': 'Chapter',
    'ic_role_number': 'Roll number',
    'ai_value': 'Major / minor / concentration',
}

_MARKUP_CHARS = ('<', '>')

#: A spreadsheet treats a cell starting with one of these as a formula.
FORMULA_START = ('=', '+', '-', '@')
#: ...unless it is only digits and phone punctuation (same rule as v3.45.2's
#: `csv_safe_cell`), so "+1 205 555 0100" and "-3.5" are fine.
_PHONEISH = re.compile(r'^[0-9+()\-.\s]+$')
#: Handles may start with "@"; only plain handle characters may follow.
_HANDLE = re.compile(r'^@?[A-Za-z0-9._-]+$')
HANDLE_FIELDS = {'instagram', 'twitter', 'snapchat', 'cs_handle'}
_PHONE = re.compile(r'^[0-9+()\-.\s]{0,25}(?:\s*(?:x|ext\.?)\s*\d{1,6})?$', re.IGNORECASE)


def looks_like_formula(value, handle=False):
    v = (value or '').strip()
    if not v or v[0] not in FORMULA_START:
        return False
    if _PHONEISH.fullmatch(v):
        return False
    if handle and _HANDLE.fullmatch(v):
        return False
    return True


def phone_error(value):
    v = (value or '').strip()
    if v and (not _PHONE.fullmatch(v) or not any(ch.isdigit() for ch in v)):
        return 'Phone number can only contain digits, spaces and + ( ) - . (and "ext").'
    return ''


def _text_error(key, label, value, changed):
    if any(ch in value for ch in _MARKUP_CHARS):
        return f'{label} can\'t contain "<" or ">".'
    if not changed:
        return ''
    if looks_like_formula(value, handle=key in HANDLE_FIELDS):
        return f'{label} can\'t start with = + - or @.'
    if key == 'phone_number':
        return phone_error(value)
    return ''


def profile_text_error(post, current_other_email='', user=None):
    """
    The reason this profile POST must not be saved, or '' if it is fine.

    `post` is `request.POST`. `current_other_email` is the member's saved
    value: an unchanged address is not re-validated, so someone with an old
    oddly-typed address can still save the rest of their profile. `user`
    (v3.50.0) does the same for the formula and phone rules.
    """
    for key, label in PLAIN_PROFILE_FIELDS.items():
        value = post.get(key, '')
        current = getattr(user, key, None) if user is not None else None
        changed = user is None or (value or '').strip() != str(current or '').strip()
        error = _text_error(key, label, value, changed)
        if error:
            return error

    other_email = post.get('other_email', '').strip()
    if other_email and other_email != (current_other_email or ''):
        try:
            validate_email(other_email)
        except ValidationError:
            return 'Other email is not a valid email address.'
    return ''


#: Fields the officer/admin editors set, key -> label.
MEMBER_TEXT_FIELDS = {
    'name': 'Name',
    'preferred_name': 'Preferred name',
    'username': 'Username',
    'email': 'Email',
    'phone_number': 'Phone number',
    'pledge_class': 'Pledge class',
    'pledge_class_greek': 'Pledge class letter',
    'graduation_semester': 'Graduation semester',
    'role_number': 'Roll number',
}
_MAX_LENGTHS = {'name': 100, 'preferred_name': 50, 'username': 100, 'phone_number': 20}


def member_fields_error(values, member=None):
    """
    v3.50.0 — the reason an officer/admin edit of a member must not be saved,
    or ''. `values` holds only the fields being set (key -> value); `member`
    is the row being edited (None when creating). Same text rules as the
    member's own profile, plus: a name can't be blank, member type and status
    must be real choices, and an email must be an email.
    """
    from src.models import ParliamentUser

    for key, label in MEMBER_TEXT_FIELDS.items():
        if key not in values:
            continue
        value = values[key] if values[key] is not None else ''
        if not isinstance(value, str):
            return f'{label} must be text.'
        current = getattr(member, key, None) if member is not None else None
        changed = member is None or value.strip() != str(current or '').strip()
        error = _text_error(key, label, value, changed)
        if error:
            return error
        limit = _MAX_LENGTHS.get(key)
        if limit and len(value.strip()) > limit:
            return f'{label} can be at most {limit} characters.'

    if 'name' in values and not (values['name'] or '').strip():
        return 'Name can\'t be blank.'

    email = (values.get('email') or '').strip()
    if email and (member is None or email.lower() != (member.email or '').lower()):
        try:
            validate_email(email)
        except ValidationError:
            return 'Email is not a valid email address.'

    if 'member_type' in values and values['member_type'] not in {k for k, _ in ParliamentUser.MEMBER_TYPES}:
        return 'Unknown member type.'
    if 'member_status' in values and values['member_status'] not in {k for k, _ in ParliamentUser.MEMBER_STATUS}:
        return 'Unknown member status.'
    return ''
