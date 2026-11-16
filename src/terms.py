"""
Accepting the Terms of Use in the app (11-16-26, App Store guideline 1.2:
users agree to terms with no tolerance for objectionable content before they
see other members' content).

The app asks once after sign-in, before showing anything, and again whenever
the Terms change (their effective date, ``TERMS_EFFECTIVE_DATE`` in
src/view/legal.py, is the version). ``me/`` says whether it's needed;
``terms/accept/`` records it on the member.
"""
from django.utils import timezone


def current_version():
    from src.view.legal import TERMS_EFFECTIVE_DATE
    return TERMS_EFFECTIVE_DATE.isoformat()


def has_accepted(user):
    return bool(user.terms_accepted_at) and user.terms_version == current_version()


def accept(user, version):
    """Record acceptance of ``version``. False if it isn't the current Terms (the app showed an old copy)."""
    if version != current_version():
        return False
    user.terms_accepted_at = timezone.now()
    user.terms_version = version
    user.save(update_fields=['terms_accepted_at', 'terms_version'])
    return True
