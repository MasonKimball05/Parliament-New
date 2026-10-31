"""
My Work in the app (v4.4.0): the website's Legislation History page
(``view_legislation_history``, /legislation/history/), through the same
functions as the website (src/view/view_legislation_history.py), so the app
lists exactly the bills, counts and tallies the page does.

* ``GET legislation/history/?status=&q=&page=`` — one page (20) of the bills
  the member posted or co-authored, newest first. ``status`` is one of the
  page's tabs (all, active, passed, failed, tabled; anything else is all);
  ``q`` narrows by title (app only: the web page has no search box). With the
  count under each tab, how many polls and unpublished drafts the member has
  (the Polls and Drafts tabs' badges), and each bill's tally as the page shows
  it: yes/no/abstain (historical counts win when an officer entered them), the
  percentage and threshold, the plurality breakdown and winner, or the
  piecewise requirement.
* ``GET legislation/history/polls/`` — the polls the member created (the
  Polls & Surveys tab), with question and response totals.
* ``GET legislation/history/drafts/`` — the member's private drafts (the
  Drafts tab), with whether each is ready to publish and why not.

Who sees what is the page's rule and nothing more: a member's own bills (posted
or co-authored), polls (``created_by``) and drafts (``author``). The tallies
are totals only, so an anonymous ballot shows here as it does on the page, with
an "Anonymous ballot" label and no voters.

A bill still on the ballot (``on_ballot``) opens in the app's vote screen
(``votes/<id>/``, src/mobile/views_votes.py), which only serves those; any
other bill opens its web detail page (``links.detail``). Reopening a failed
bill, submitting a new version, editing a bill or a poll, and starting,
publishing or deleting a draft stay on the website: each row carries the paths
the app links there.
"""
from django.urls import reverse
from rest_framework.response import Response

from src.mobile.api import MobileAPIView
from src.models import AnnouncementPoll, Legislation, LegislationDraft
from src.models.users import member_defer
from src.view import view_legislation_history as lh
from src.view.legislation_drafts import _can_publish

#: The longest title search the list accepts (the model's title is 200).
SEARCH_MAX = 200


def _iso(dt):
    return dt.isoformat() if dt else None


def _state(item):
    """The row's status badge, in the template's order of precedence."""
    leg = item['legislation']
    if leg.status == 'passed' or item['passed']:
        return 'passed'
    if leg.status == 'tabled':
        return 'tabled'
    if leg.status == 'failed' or item['voting_closed']:
        return 'failed'
    if leg.status == 'pending':
        return 'pending'
    return 'active'


def _on_ballot(item):
    """What ``views_votes._on_ballot`` lists, so the vote screen can open it."""
    leg = item['legislation']
    return item['is_active'] and leg.status not in Legislation.CLOSED_STATUSES


def _bill(item, user):
    leg = item['legislation']
    co_authors = list(leg.co_authors.all())
    appointment = None
    if leg.legislation_type == 'appointment':
        appointment = {
            'role': leg.appointment_role.name if leg.appointment_role_id else None,
            'nominee': leg.appointment_member.name if leg.appointment_member_id else None,
            'assigned': bool(item['passed'] and leg.appointment_assigned),
        }
    breakdown = item['vote_breakdown']
    return {
        'id': leg.id,
        'title': leg.title,
        # The page leaves the description off appointments (their role and
        # nominee say it), and trims it to two lines; the app does the same.
        'description': '' if leg.legislation_type == 'appointment' else leg.description,
        'state': _state(item),
        'status': leg.status,
        'legislation_type': leg.legislation_type,
        'is_co_author': any(ca.pk == user.pk for ca in co_authors),
        'co_authors': [ca.name for ca in co_authors],
        'available_at': _iso(leg.available_at),
        'voting_ended_at': _iso(leg.voting_ended_at),
        'vote_mode': item['vote_mode'],
        'anonymous': leg.anonymous_vote,
        'admin_note': leg.admin_note,
        'appointment': appointment,
        'runoff_of': ({'id': leg.plurality_parent_id, 'title': leg.plurality_parent.title}
                      if leg.plurality_is_runoff and leg.plurality_parent_id else None),
        'runoffs': [{'id': r.id, 'title': r.title} for r in leg.runoff_votes.all()],
        'voting_closed': item['voting_closed'],
        'passed': item['passed'],
        'on_ballot': _on_ballot(item),
        'tally': {
            'yes': item['yes'],
            'no': item['no'],
            'abstain': item['abstain'],
            'total': item['total_votes'],
            'yes_pct': item['yes_pct_num'],
            'no_pct': item['no_pct_num'],
            'required_pct': item['required_pct'],
            'required_yes': item['required_yes'],
            'breakdown': ([{'option': o, 'count': n} for o, n in breakdown.items()]
                          if breakdown else None),
            'winner': item['winner'],
            'votes_allowed': leg.plurality_votes_allowed if leg.vote_mode == 'plurality' else None,
            'runoff_enabled': bool(leg.plurality_runoff_enabled),
        },
        # The page's buttons: Reopen (closed, failed) and Submit New Version
        # (passed) are web forms on the detail page; Edit while not passed.
        'can_reopen': bool(item['voting_closed'] and not item['passed']),
        'can_edit': not item['passed'],
        'links': {
            'detail': item['detail_url'],
            'document': item['document_viewer_url'],
            'edit': reverse('edit_legislation', args=[leg.id]) if not item['passed'] else None,
        },
    }


class _MyWork(MobileAPIView):
    web_page = 'view_legislation_history'
    web_page_toggle = False   # the web page has no @require_page_enabled


class LegislationHistoryView(_MyWork):

    def get(self, request):
        user = request.user
        params = request.query_params
        status = params.get('status', 'all')
        if status not in lh.STATUS_FILTERS:
            status = 'all'
        q = str(params.get('q') or '').strip()[:SEARCH_MAX]

        base_qs = lh.authored_legislation(user)
        queryset = lh.filter_status(base_qs, status)
        if q:
            queryset = queryset.filter(title__icontains=q)
        # What the template reads lazily per row (an N+1 there), joined here.
        queryset = (queryset
                    .select_related('appointment_role', 'appointment_member', 'plurality_parent')
                    .defer(*member_defer('appointment_member'))
                    .prefetch_related('runoff_votes'))
        paginator, page, rows = lh.history_page(queryset, params.get('page'))
        drafts = LegislationDraft.objects.filter(author=user, published_legislation__isnull=True).count()
        polls = AnnouncementPoll.objects.filter(created_by=user).count()
        return Response({
            'status': status,
            'q': q,
            'counts': lh.status_counts(base_qs),
            'polls_count': min(polls, lh.MY_POLLS_LIMIT),
            'drafts_count': drafts,
            'page': page.number,
            'pages': paginator.num_pages,
            'total': paginator.count,
            'has_next': page.has_next(),
            'bills': [_bill(item, user) for item in lh.history_rows(rows)],
        })


class LegislationHistoryPollsView(_MyWork):

    def get(self, request):
        return Response({'polls': [{
            'id': p.id,
            'title': p.title,
            'description': p.description,
            'announcement': {'id': p.announcement_id, 'title': p.announcement.title},
            'anonymous': p.is_anonymous,
            'open': p.is_accepting_responses(),
            'closes_at': _iso(p.closes_at),
            'created_at': _iso(p.created_at),
            'questions': p.question_total,
            'responses': p.response_total,
            'links': {
                'results': reverse('poll_results', args=[p.announcement_id]),
                'edit': reverse('create_or_edit_poll', args=[p.announcement_id]),
                'announcement': reverse('edit_announcement', args=[p.announcement_id]),
            },
        } for p in lh.my_polls(request.user)]})


class LegislationHistoryDraftsView(_MyWork):

    def get(self, request):
        user = request.user
        rows, unpublished = lh.my_drafts(user)
        drafts = []
        for row in rows:
            d = row['draft']
            bill = d.published_legislation
            drafts.append({
                'id': d.id,
                'title': d.title,
                'description': d.description,
                'published': d.is_published,
                'ready': row['ready_to_publish'],
                'not_ready_reason': '' if row['ready_to_publish'] else row['not_ready_reason'],
                'updated_at': _iso(d.updated_at),
                'planned_available_at': _iso(d.planned_available_at),
                'published_at': _iso(d.published_at),
                'bill': {'id': bill.id, 'title': bill.title} if bill else None,
                'document': ({'name': d.document_display_name or 'Document attached',
                              'path': reverse('legislation_draft_document', args=[d.id])}
                             if d.document else None),
                'links': {
                    'edit': None if d.is_published else reverse('edit_legislation_draft', args=[d.id]),
                    'bill': reverse('legislation_detail', args=[bill.id]) if bill else None,
                },
            })
        return Response({
            'drafts': drafts,
            'unpublished_count': unpublished,
            'can_publish': _can_publish(user),
            'links': {'page': reverse('view_legislation_history') + '?tab=drafts'},
        })
