"""
The Legislation Tracker in the app (v4.4.0): the website's Passed Legislation
page (``passed_legislation``, /passed-legislation/) and its detail page
(``passed_legislation_detail``, /legislation/detail/<pk>/), through the same
functions as the website (src/view/passed_legislation.py), so the app lists
exactly the bills, counts, tallies and ballots the pages do.

* ``GET legislation/passed/?status=&q=&page=`` — one page (20) of a tab, in the
  tab's order. ``status`` is one of the page's tabs (all = "All Closed",
  pending, active, passed, failed, tabled, personal; anything else is all);
  ``q`` narrows by title (app only: the web page has no search box). With the
  count under each tab (never narrowed by ``q``) and each bill's tally as the
  page shows it: yes/no/abstain (historical counts win), percentages and the
  threshold, the piecewise requirement, or the plurality breakdown and winner.
  The Personal tab (``ballots``) is the member's own ballots with their
  choices, the result and the vote receipt (or that it has expired), unpaged
  as on the page.
* ``GET legislation/passed/<id>/`` — the detail page: the bill, its results
  when voting has closed, who was present and the roll call (not for an
  anonymous ballot), exactly as ``PassedLegislationDetailView`` shows them.

Who sees what is the pages' rule. The list has ``@require_page_enabled
('passed_legislation')``; the detail page has no toggle (only sign-in), so only
the pledge restriction on ``passed_legislation_detail`` applies to it. The
list's per-bill present-members roster is shown to Officers only (the page's
``member_type == 'Officer'`` check, not admins); the detail page shows it to
every member, as the website does. The officer-only parts of the page (Add
Legislation, editing the officer note, assigning a passed appointment) stay on
the website: ``can_manage`` and each row's ``links`` point there.

A bill still on the ballot (``on_ballot``) opens in the app's vote screen
(``votes/<id>/``, src/mobile/views_votes.py), which serves only those.
"""
from urllib.parse import quote

from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from rest_framework.response import Response

from src.mobile.api import MobileAPIView
from src.models import Legislation
from src.view import passed_legislation as pl

#: The longest title search the list accepts (the model's title is 200).
SEARCH_MAX = 200


def _iso(dt):
    return dt.isoformat() if dt else None


def _is_officer(user):
    """The page's ``user.is_admin or user.member_type == 'Officer'``."""
    return bool(user.is_admin or user.member_type == 'Officer')


def _list_state(leg):
    """The list row's badge: the template reads ``status`` only."""
    if leg.status == 'passed':
        return 'passed'
    if leg.status in ('failed', 'removed'):
        return 'failed'
    if leg.status in ('tabled', 'pending'):
        return leg.status
    return 'active'


def _detail_state(leg):
    """The detail page's badge: ``status == 'passed' or passed`` first."""
    if leg.status == 'passed' or leg.passed:
        return 'passed'
    if leg.status in ('failed', 'tabled', 'pending'):
        return leg.status
    return 'active'


def _on_ballot(leg, now):
    """What ``views_votes._on_ballot`` serves, so the vote screen can open it."""
    return bool(leg.available_at and leg.available_at <= now and not leg.voting_closed
                and leg.status not in Legislation.CLOSED_STATUSES)


def _document(leg):
    if not leg.document:
        return None
    return {'view': reverse('view_document', args=[leg.id]), 'download': leg.document.url}


def _bill(item, user, now):
    leg = item['legislation']
    officer = _is_officer(user)
    plurality = item['vote_mode'] == 'plurality'
    breakdown = item['vote_breakdown'] if plurality and leg.plurality_options else None
    editable = leg.status in ('tabled', 'pending') and (officer or user.pk == leg.posted_by_id)
    unassigned = (officer and leg.legislation_type == 'appointment' and leg.status == 'passed'
                  and not leg.appointment_assigned and leg.appointment_role_id)
    return {
        'id': leg.id,
        'title': leg.title,
        'description': leg.description,
        'state': _list_state(leg),
        'status': leg.status,
        'legislation_type': leg.legislation_type,
        'vote_mode': item['vote_mode'],
        'posted_by': leg.posted_by.name if leg.posted_by_id else None,
        'co_authors': [ca.name for ca in leg.co_authors.all()],
        'available_at': _iso(leg.available_at),
        'voting_ended_at': _iso(leg.voting_ended_at),
        'anonymous': leg.anonymous_vote,
        'admin_note': leg.admin_note or '',
        'voting_closed': leg.voting_closed,
        'vote_passed': item['vote_passed'],
        'on_ballot': _on_ballot(leg, now),
        'tally': {
            'yes': item['yes'],
            'no': item['no'],
            'abstain': item['abstain'],
            'yes_pct': item['yes_pct'],
            'no_pct': item['no_pct'],
            'required_pct': item['required_pct'],
            'required_yes': item['required_yes_votes'],
            'breakdown': ([{'option': o, 'count': n} for o, n in breakdown.items()]
                          if breakdown is not None else None),
            'winner': item['winner'],
        },
        # Officers only, as on the page ("Show Present Members").
        'present_members': ([a.user.name for a in item['present_members']]
                            if user.member_type == 'Officer' else None),
        # The page's banner: a passed appointment whose role isn't assigned yet.
        'needs_assignment': ({'role': leg.appointment_role.name} if unassigned else None),
        'document': _document(leg),
        'links': {
            'detail': item['detail_url'],
            'edit': reverse('edit_legislation', args=[leg.id]) if editable else None,
            'assign': reverse('assign_appointment', args=[leg.id]) if unassigned else None,
        },
    }


def _ballot(b, verify_path):
    leg = b['legislation']
    if leg.voting_closed:
        result = 'passed' if leg.passed else 'failed'
    else:
        result = 'open'
    return {
        'legislation': {'id': leg.id, 'title': leg.title},
        'choices': b['choices'],
        'cast_at': _iso(b['cast_at']),
        'anonymous': leg.anonymous_vote,
        'result': result,
        'receipt': b['receipt'],
        'receipt_expired': b['receipt_expired'],
        'links': {
            'detail': reverse('passed_legislation_detail', kwargs={'pk': leg.id}),
            'verify': f"{verify_path}?receipt={quote(b['receipt'], safe='')}" if b['receipt'] else None,
        },
    }


class PassedLegislationView(MobileAPIView):
    web_page = 'passed_legislation'

    def get(self, request):
        from src.utils.vote_receipts import RECEIPT_MAX_AGE_DAYS
        user = request.user
        params = request.query_params
        status = params.get('status', 'all')
        if status not in pl.STATUS_FILTERS:
            status = 'all'
        q = str(params.get('q') or '').strip()[:SEARCH_MAX]
        now = timezone.now()

        all_legislation = pl.base_legislation()
        queryset = pl.filter_status(all_legislation, status, user, now)
        if q:
            queryset = queryset.filter(title__icontains=q)
        if _is_officer(user):
            # The appointment banner reads the role name per row.
            queryset = queryset.select_related('appointment_role')
        ballots = None
        if status == 'personal':
            verify = reverse('verify_vote_receipt')
            ballots = [_ballot(b, verify) for b in pl.personal_ballots(user, now)
                       if not q or q.lower() in b['legislation'].title.lower()]
        counts = pl.status_counts(all_legislation, user, now)
        paginator, page, rows = pl.legislation_page(queryset, status, params.get('page'))
        return Response({
            'status': status,
            'q': q,
            'counts': counts,
            'page': page.number,
            'pages': paginator.num_pages,
            'total': paginator.count,
            'has_next': page.has_next(),
            'bills': [_bill(item, user, now) for item in rows],
            'ballots': ballots,
            'receipt_max_age_days': RECEIPT_MAX_AGE_DAYS,
            'can_manage': _is_officer(user),
            'links': {'page': reverse('passed_legislation')},
        })


class PassedLegislationDetailView(MobileAPIView):
    web_page = 'passed_legislation_detail'
    web_page_toggle = False   # the detail page has no @require_page_enabled

    def get(self, request, legislation_id):
        leg = get_object_or_404(Legislation.objects.select_related('posted_by'), pk=legislation_id)
        ctx = pl.legislation_result(leg)
        vr = ctx['vote_result']
        result = None
        # The page shows the results card only once voting has closed.
        if leg.voting_closed:
            result = {
                'mode': vr['mode'],
                'total': vr['total'],
                'passed': vr.get('passed'),
                'yes': vr.get('yes'),
                'no': vr.get('no'),
                'abstain': vr.get('abstain'),
                'yes_pct': vr.get('yes_pct_num'),
                'required_pct': vr.get('required_percentage'),
                'required_yes': vr.get('required_yes'),
                'options': ([{'option': o, 'count': n} for o, n in vr['options'].items()]
                            if vr['mode'] == 'plurality' else None),
                'winner': vr.get('winner'),
            }
        return Response({
            'id': leg.id,
            'title': leg.title,
            'description': leg.description,
            'state': _detail_state(leg),
            'status': leg.status,
            'vote_mode': leg.vote_mode,
            'posted_by': leg.posted_by.name if leg.posted_by_id else None,
            'available_at': _iso(leg.available_at),
            'voting_ended_at': _iso(leg.voting_ended_at),
            'anonymous': leg.anonymous_vote,
            'admin_note': leg.admin_note or '',
            'voting_closed': leg.voting_closed,
            'on_ballot': _on_ballot(leg, timezone.now()),
            'document': _document(leg),
            'result': result,
            'present_members': [a.user.name for a in ctx.get('present_members') or []],
            'roll_call': [{'name': v.user.name, 'choice': v.vote_choice}
                          for v in ctx.get('individual_votes') or []],
            'links': {'page': reverse('passed_legislation_detail', kwargs={'pk': leg.id})},
        })
