"""
Semester/term label helpers.

Chapter officer terms run January-to-January (transitions typically happen in
January, or early December before winter break). There is no "Summer" term —
per Mason 07-08-26 — so labels only use Spring/Fall vocabulary, matching the
strings stored on RoleHistory.start_semester / end_semester (e.g. "Fall 2026").
"""
from django.utils import timezone


def _label(season, year):
    return f'{season} {year}'


def current_semester(when=None):
    """
    The semester label currently in progress.

    Jan–May  -> "Spring YYYY"
    Jun–Dec  -> "Fall YYYY"   (no Summer term; summer months roll into Fall)
    """
    when = when or timezone.localdate()
    season = 'Spring' if when.month <= 5 else 'Fall'
    return _label(season, when.year)


def transition_semesters(when=None):
    """
    (outgoing_end, incoming_start) labels for a role transition on `when`.

    Normal end-of-term handoffs (December or January):
      Dec YYYY -> outgoing served through "Fall YYYY", incoming starts "Spring YYYY+1"
      Jan YYYY -> outgoing served through "Fall YYYY-1", incoming starts "Spring YYYY"

    Off-cycle replacements (any other month) end and start in the semester
    currently in progress.
    """
    when = when or timezone.localdate()
    if when.month == 12:
        return _label('Fall', when.year), _label('Spring', when.year + 1)
    if when.month == 1:
        return _label('Fall', when.year - 1), _label('Spring', when.year)
    sem = current_semester(when)
    return sem, sem
