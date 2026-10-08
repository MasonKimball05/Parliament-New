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
"""
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


def profile_text_error(post, current_other_email=''):
    """
    The reason this profile POST must not be saved, or '' if it is fine.

    `post` is `request.POST`. `current_other_email` is the member's saved
    value: an unchanged address is not re-validated, so someone with an old
    oddly-typed address can still save the rest of their profile.
    """
    for key, label in PLAIN_PROFILE_FIELDS.items():
        value = post.get(key, '')
        if any(ch in value for ch in _MARKUP_CHARS):
            return f'{label} can\'t contain "<" or ">".'

    other_email = post.get('other_email', '').strip()
    if other_email and other_email != (current_other_email or ''):
        try:
            validate_email(other_email)
        except ValidationError:
            return 'Other email is not a valid email address.'
    return ''
