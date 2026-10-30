"""
Roll call order (10-30-26): members by roll number, numerically, then
pledges by last name. The same order as ``chapter_minutes``' roll call (see
``get_roll_call_sort_key`` there for why roll numbers sort as numbers, and why
a blank or non-numeric one goes last); the app's attendance roster uses it as
its default.
"""


def last_name(user):
    parts = (user.name or '').strip().split()
    return parts[-1].lower() if parts else ''


def roll_call_key(user):
    if user.member_type == 'Pledge':
        return (1, 0, last_name(user), '')
    rn = (user.role_number or '').strip()
    if rn.isdigit():
        return (0, 0, int(rn), '')
    return (0, 1, rn, last_name(user))
