"""
Event sign-ups: join, leave, and the waitlist (v4.0.0).

The one implementation behind the website's sign-up buttons
(``src/view/calendar.py`` ``event_signup`` / ``event_cancel_signup``) and the
app's ``events/<id>/signup/`` (``src/mobile/views_calendar.py``). Moved here
unchanged from the web views so the row lock, the waitlist and the visibility
rule can't drift between the two.

Both functions lock the Event row (``select_for_update``) so concurrent
sign-ups for the last slot serialise. An event the member can't see, or that
doesn't take sign-ups, raises ``Http404``: same answer as a missing event, so
ids can't be probed. A refusal the member should read raises ``SignupError``.
"""
from django.db import models, transaction
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone

from src.models import Event, EventSignup


class SignupError(Exception):
    """A refusal with a message for the member (HTTP 400 on both sides)."""


def counts(event):
    """(confirmed, waitlisted) live sign-ups for ``event``."""
    live = event.signups.filter(is_cancelled=False)
    return (live.filter(waitlist_position__isnull=True).count(),
            live.filter(waitlist_position__isnull=False).count())


def _result(event, signed_up, signup=None):
    confirmed, waitlisted = counts(event)
    return {
        'signup_count': confirmed,
        'waitlist_count': waitlisted,
        'user_signed_up': signed_up,
        'on_waitlist': bool(signup and signup.waitlist_position is not None),
        'waitlist_position': signup.waitlist_position if signup else None,
        'signup_full': event.max_signups is not None and confirmed >= event.max_signups,
    }


def _locked_event(event_id, user):
    event = get_object_or_404(
        Event.objects.select_for_update(),
        pk=event_id, is_active=True, archived=False, requires_signup=True,
    )
    # An event the member can't see on the calendar must not be
    # reachable by id either — same 404 as a nonexistent event.
    if not event.is_visible_to_user(user):
        raise Http404
    return event


def sign_up(event_id, user):
    """Sign ``user`` up (or onto the waitlist). Returns the counts dict."""
    with transaction.atomic():
        event = _locked_event(event_id, user)
        if not event.signups_open:
            raise SignupError('Sign-ups are closed for this event.')

        # Re-activate a cancelled signup or create fresh
        signup, created = EventSignup.objects.get_or_create(
            event=event,
            user=user,
            defaults={'is_cancelled': False, 'waitlist_position': None},
        )
        if not created:
            if not signup.is_cancelled:
                already_on = 'waitlist' if signup.waitlist_position is not None else 'sign-up list'
                raise SignupError(f'You are already on the {already_on}.')
            # Re-activating — reset cancellation state
            signup.is_cancelled = False
            signup.cancelled_at = None

        # Determine whether a confirmed slot is available. Leave this member's
        # own row out: get_or_create above has already saved it as confirmed,
        # so counting it took the last slot from whoever was claiming it (on
        # an N-slot event the Nth sign-up was waitlisted or refused as full;
        # fixed v4.0.0).
        confirmed_count = (event.signups
                           .filter(is_cancelled=False, waitlist_position__isnull=True)
                           .exclude(pk=signup.pk).count())
        slot_available = event.max_signups is None or confirmed_count < event.max_signups

        if slot_available:
            signup.waitlist_position = None
        elif event.allow_waitlist:
            # Place on waitlist at the next available position
            signup.waitlist_position = (
                event.signups
                .filter(is_cancelled=False, waitlist_position__isnull=False)
                .order_by('-waitlist_position')
                .values_list('waitlist_position', flat=True)
                .first() or 0
            ) + 1
        else:
            if created:
                signup.delete()
            raise SignupError('This event is full.')
        signup.save(update_fields=['is_cancelled', 'cancelled_at', 'waitlist_position'])
        return _result(event, True, signup)


def cancel(event_id, user):
    """Cancel ``user``'s sign-up, promoting the first waitlisted member."""
    with transaction.atomic():
        event = _locked_event(event_id, user)
        signup = EventSignup.objects.filter(event=event, user=user, is_cancelled=False).first()
        if not signup:
            raise SignupError('You are not signed up for this event.')

        was_confirmed = signup.waitlist_position is None
        signup.is_cancelled = True
        signup.cancelled_at = timezone.now()
        signup.save(update_fields=['is_cancelled', 'cancelled_at'])

        # If they held a confirmed slot, promote the first waitlisted person
        if was_confirmed and event.allow_waitlist:
            first_waiting = (
                EventSignup.objects
                .filter(event=event, is_cancelled=False, waitlist_position__isnull=False)
                .order_by('waitlist_position')
                .first()
            )
            if first_waiting:
                first_waiting.waitlist_position = None
                first_waiting.save(update_fields=['waitlist_position'])
                # Compact remaining waitlist positions
                EventSignup.objects.filter(
                    event=event, is_cancelled=False, waitlist_position__isnull=False,
                ).order_by('waitlist_position').update(
                    waitlist_position=models.F('waitlist_position') - 1
                )
        return _result(event, False)
