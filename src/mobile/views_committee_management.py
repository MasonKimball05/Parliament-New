"""
A committee's chair tools in the app (v4.4.0): the website's create-vote,
vote-page chair actions, push to chapter, member management, member
permissions, attendance and document publish/delete pages.

* ``GET committees/<code>/votes/manage/`` — open and recent closed votes with
  their tallies and what the viewer may do to each (``committee_vote``'s chair
  view: chairs, and admins for delete).
* ``GET|POST committees/<code>/votes/new/`` — the form's choices; create a
  vote (``committee_create_vote``; chairs). No attachment: the app has no
  document picker, so a vote needs a description.
* ``POST committees/<code>/votes/<id>/recalculate/`` — "Recalculate Result"
  on a closed vote (v4.4.3: ``recalculate_committee_vote``'s rule, chairs or
  officers; the vote page's own button is chairs only).
* ``GET committees/<code>/votes/<id>/result/`` — the result page
  (``committee_vote_result``, v4.4.3): who may look and what they see are the
  web page's (voter names only when the vote isn't anonymous).
* ``POST committees/<code>/votes/<id>/end/`` — "End Vote" (v4.4.3: chairs or
  whoever posted it).
* ``POST committees/<code>/votes/<id>/runoff/`` — ``create_committee_runoff``
  (v4.4.3: chairs or whoever posted it).
* ``POST committees/<code>/votes/<id>/push/`` / ``unpush/`` — publish its
  results to the chapter documents page, or take them off (chairs).
* ``POST committees/<code>/votes/<id>/delete/`` — delete it (chairs, admins).
* ``GET|POST committees/<code>/votes/<id>/chapter-vote/`` — the form's
  defaults and the recorded committee result; create a chapter-wide vote from
  it (``create_chapter_vote_from_committee``: chairs, admins).
* ``POST committees/<code>/votes/<id>/chapter-vote/delete/`` — delete that
  chapter vote (``delete_chapter_vote_link``: chairs, admins).
* ``GET committees/<code>/members/candidates/?role=`` — who "+ Add" offers.
* ``POST committees/<code>/members/add/`` / ``remove/`` — a role on the
  committee (``committee_add_member`` / ``_remove_member``: chairs, the VP,
  admins).
* ``POST committees/<code>/members/manage/`` — the Manage Members page's
  actions (``committee_manage_members``: also members a chair has designated).
* ``GET|POST committees/<code>/member-permissions/`` — designations
  (``committee_member_permissions``: chairs, the VP, admins).
* ``GET|POST committees/<code>/attendance/`` — today's committee attendance
  (``committee_attendance``: chairs and officers). ``can_view_history`` says
  whether the history below would open.
* ``GET committees/<code>/attendance/history/`` — every past session's records
  (``committee_attendance_history``: ``officer_required``, so officers and any
  chair), with a present count per session and each member's totals.
* ``POST committees/<code>/documents/<id>/publish/`` / ``delete/`` —
  (``toggle_document_publish`` / ``delete_committee_document``: chairs and
  members designated to manage documents).

Every permission check, the work and its side effects (logs, activity log,
security events) are the web views', via ``src.committee_management`` and
``src.view.committee.member_permissions``. Uploading and replacing a
document's file are in views_committee_documents (v4.4.2, the file picker).
None of these pages has a page toggle, so ``web_page_toggle`` is off.
"""
from django.http import Http404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import exceptions, status
from rest_framework.response import Response

from src import committee_management as cm
from src.feature_flag_decorators import check_feature_enabled
from src.logging_utils import get_client_ip
from src.mobile.api import MobileAPIView
from src.mobile.views_committees import _document_json, _person
from src.models import Committee, CommitteeDocument, CommitteeLegislation, CommitteePermissions, ParliamentUser
from src.models.users import member_defer
from src.permissions import user_is_officer_or_chair
from src.view.committee.member_permissions import (
    DESIGNATIONS, can_designate, can_manage_documents, can_manage_members,
)

CLOSED_LIMIT = 20   # the vote page's "Voting History"
CANDIDATE_LIMIT = 500


class BadRequest(exceptions.APIException):
    status_code = 400


class Conflict(exceptions.APIException):
    status_code = 409


def _refuse(message):
    raise exceptions.PermissionDenied(message, code='no_access')


def _data(request):
    return request.data if isinstance(request.data, dict) else {}


def _committee(code):
    committee = Committee.objects.select_related('role').filter(code=code).first()
    if committee is None:
        raise Http404
    return committee


def _require_voting():
    if not check_feature_enabled('committee_voting'):
        raise exceptions.PermissionDenied('Committee voting is off.', code='feature_disabled')


def _target(request):
    user_id = _data(request).get('user_id')
    target = ParliamentUser.objects.filter(pk=user_id).first() if isinstance(user_id, str) and user_id else None
    if target is None:
        raise BadRequest('User not found.', code='invalid_input')
    return target


def _when(raw, label):
    if raw in (None, ''):
        return None
    parsed = parse_datetime(raw) if isinstance(raw, str) else None
    if parsed is None:
        raise BadRequest(f'Send {label} as an ISO date and time.', code='invalid_input')
    return parsed if timezone.is_aware(parsed) else timezone.make_aware(parsed)


# ── Votes ───────────────────────────────────────────────────────────────────

def _vote_json(leg, viewer, committee, is_chair, voted_ids):
    from django.urls import reverse
    # The vote page shows an open vote's tally to chairs, its poster and anyone
    # who has voted; a closed vote's to everyone.
    show_tally = leg.voting_closed or is_chair or leg.posted_by_id == viewer.pk or leg.id in voted_ids
    tally = cm.vote_tally(leg) if show_tally else None
    if tally is None:
        results = None
    elif leg.vote_mode == 'plurality':
        results = [{'option': o, 'count': tally.get(o, 0)} for o in (leg.plurality_options or [])]
    else:
        results = [{'option': o, 'count': tally[o]} for o in ('yes', 'no', 'abstain')]
    return {
        'id': leg.id,
        'title': leg.title,
        'vote_mode': leg.vote_mode,
        'posted_by': leg.posted_by.get_display_name() if leg.posted_by else '',
        'available_at': leg.available_at.isoformat() if leg.available_at else None,
        'voting_ends_at': leg.voting_ends_at.isoformat() if leg.voting_ends_at else None,
        'voting_ended_at': leg.voting_ended_at.isoformat() if leg.voting_ended_at else None,
        'voting_closed': leg.voting_closed,
        'passed': bool(leg.passed),
        'anonymous': leg.anonymous_vote,
        'required_percentage': leg.required_percentage if leg.vote_mode == 'percentage' else None,
        'required_number': leg.required_number if leg.vote_mode == 'piecewise' else None,
        'pushed_to_chapter': leg.pushed_to_chapter,
        'has_chapter_vote': leg.chapter_legislation_id is not None,
        'results': results,
        'total_votes': tally['total'] if tally else None,
        # v4.4.3: recalculate_committee_vote's rule (chairs or officers).
        'can_recalculate': leg.voting_closed and (is_chair or viewer.member_type == 'Officer'),
        'can_end': not leg.voting_closed and (is_chair or leg.posted_by_id == viewer.pk),
        'can_push': is_chair and leg.voting_closed,
        'can_delete': is_chair or viewer.is_admin,
        # The vote page's "Create Chapter Vote" / "Delete Chapter Vote" (closed votes, chairs). The
        # path is the web page's; the app opens it natively (CommitteeChapterVoteView).
        'chapter_vote_path': _chapter_vote_path(leg, committee, is_chair),
        'can_delete_chapter_vote': is_chair and leg.voting_closed and leg.chapter_legislation_id is not None,
        'result_path': reverse('committee_vote_result', args=[committee.code, leg.id]),
    }


def _chapter_vote_path(leg, committee, is_chair):
    from django.urls import reverse
    if is_chair and leg.voting_closed and leg.chapter_legislation_id is None:
        return reverse('create_chapter_vote', args=[committee.code, leg.id])
    return None


def _one_vote(leg, request, committee):
    from src.models import CommitteeVote
    voted = set(CommitteeVote.objects.filter(user=request.user, legislation=leg).values_list('legislation_id', flat=True))
    return _vote_json(leg, request.user, committee, committee.is_chair(request.user), voted)


class _VoteToolView(MobileAPIView):
    web_page = 'vote'
    web_page_toggle = False   # committee_vote has no @require_page_enabled

    def legislation(self, committee, legislation_id):
        leg = (CommitteeLegislation.objects.select_related('posted_by')
               .filter(pk=legislation_id, committee=committee).first())
        if leg is None:
            raise Http404
        return leg


class CommitteeVoteManageView(_VoteToolView):

    def get(self, request, code):
        from src.models import CommitteeVote
        _require_voting()
        committee = _committee(code)
        user = request.user
        is_chair = committee.is_chair(user)
        if not (is_chair or user.is_admin):
            _refuse('Only committee chairs can manage votes.')
        base = (CommitteeLegislation.objects.filter(committee=committee)
                .select_related('posted_by').defer(*member_defer('posted_by')))
        open_rows = list(base.filter(available_at__lte=timezone.now(), voting_closed=False).order_by('-available_at'))
        closed_rows = list(base.filter(voting_closed=True).order_by('-voting_ended_at', '-available_at')[:CLOSED_LIMIT])
        upcoming = list(base.filter(available_at__gt=timezone.now(), voting_closed=False).order_by('available_at'))
        voted = set(CommitteeVote.objects.filter(user=user, legislation__committee=committee)
                    .values_list('legislation_id', flat=True))

        def rows(legs):
            return [_vote_json(leg, user, committee, is_chair, voted) for leg in legs]

        return Response({
            'committee': {'code': committee.code, 'name': committee.name},
            'can_create': is_chair,
            'upcoming': rows(upcoming),
            'open': rows(open_rows),
            'closed': rows(closed_rows),
        })


class CommitteeVoteCreateView(_VoteToolView):
    web_page = 'create_committee_vote'

    def committee(self, request, code):
        _require_voting()
        committee = _committee(code)
        if not committee.is_chair(request.user):
            _refuse('Only committee chairs can create votes.')
        return committee

    def get(self, request, code):
        committee = self.committee(request, code)
        return Response({
            'committee': {'code': committee.code, 'name': committee.name},
            'thresholds': [{'value': v, 'label': label} for v, label in CommitteeLegislation.VOTE_THRESHOLDS],
            'vote_modes': [{'value': 'percentage', 'label': 'Yes / No'},
                           {'value': 'plurality', 'label': 'Plurality'},
                           {'value': 'piecewise', 'label': 'Piecewise'}],
            'max_options': cm.MAX_PLURALITY_OPTIONS,
            'anonymous_available': check_feature_enabled('anonymous_voting'),
            'abstain_optional': check_feature_enabled('abstain_voting'),
        })

    def post(self, request, code):
        committee = self.committee(request, code)
        data = _data(request)
        options = data.get('plurality_options') or []
        if not isinstance(options, list):
            raise BadRequest('Send plurality_options as a list.', code='invalid_input')
        try:
            leg = cm.create_committee_vote(
                committee, request.user,
                title=data.get('title'),
                description=data.get('description') if isinstance(data.get('description'), str) else '',
                available_at=_when(data.get('available_at'), 'available_at'),
                voting_ends_at=_when(data.get('voting_ends_at'), 'voting_ends_at'),
                anonymous=data.get('anonymous') is True,
                remove_abstain=data.get('allow_abstain') is False,
                required_percentage=data.get('required_percentage') or '51',
                vote_mode=data.get('vote_mode') or 'percentage',
                plurality_options=options,
                plurality_votes_allowed=data.get('plurality_votes_allowed', 1),
                plurality_runoff_enabled=data.get('plurality_runoff_enabled') is True,
                plurality_runoff_count=data.get('plurality_runoff_count', 2),
                required_number=data.get('required_number'),
            )
        except cm.CommitteeActionError as exc:
            raise BadRequest(exc.message, code='invalid_input')
        leg = CommitteeLegislation.objects.select_related('posted_by').get(pk=leg.pk)
        return Response({'vote': _one_vote(leg, request, committee)}, status=status.HTTP_201_CREATED)


class CommitteeVoteRecalculateView(_VoteToolView):
    web_page = 'recalculate_committee_vote'

    def post(self, request, code, legislation_id):
        _require_voting()
        committee = _committee(code)
        leg = self.legislation(committee, legislation_id)
        # v4.4.3: recalculate_committee_vote's checks and messages.
        if not cm.can_recalculate_any(request.user, committee):
            _refuse('You don’t have permission to recalculate this vote.')
        if not leg.voting_closed:
            raise Conflict('Cannot recalculate - voting is still open.', code='not_closed')
        result = cm.recalculate_committee_vote(leg, request.user, request._request)
        return Response({'result': result, 'vote': _one_vote(leg, request, committee)})


class CommitteeVoteEndView(_VoteToolView):
    """The vote page's "End Vote" (v4.4.3)."""

    def post(self, request, code, legislation_id):
        _require_voting()
        committee = _committee(code)
        leg = self.legislation(committee, legislation_id)
        if not cm.can_end_vote(request.user, committee, leg):
            _refuse('You don’t have permission to end this vote.')
        try:
            result = cm.end_committee_vote(leg, request.user, request._request)
        except cm.CommitteeActionError as exc:
            raise Conflict(exc.message, code=exc.code)
        return Response({
            'result': result,
            'message': f"Voting on '{leg.title}' has been ended. The vote {result}.",
            'vote': _one_vote(leg, request, committee),
        })


class CommitteeVoteRunoffView(_VoteToolView):
    """``create_committee_runoff`` (v4.4.3)."""
    web_page = 'create_committee_runoff'

    def post(self, request, code, legislation_id):
        _require_voting()
        committee = _committee(code)
        leg = self.legislation(committee, legislation_id)
        if not cm.can_end_vote(request.user, committee, leg):
            _refuse('Only committee chairs or the vote creator can create a runoff.')
        try:
            runoff, options = cm.create_committee_runoff(leg, request.user, request._request)
        except cm.CommitteeActionError as exc:
            raise Conflict(exc.message, code=exc.code)
        return Response({
            'runoff': {'id': runoff.id, 'title': runoff.title},
            'options': options,
            'message': f"Runoff vote created with top {len(options)} options: {', '.join(options)}",
        }, status=status.HTTP_201_CREATED)


def _names(votes):
    return [v.user.name for v in votes]


class CommitteeVoteResultView(_VoteToolView):
    """
    ``committee_vote_result`` (v4.4.3). The figures are cm.vote_result, the
    web page's; ``voters`` is null throughout when the vote is anonymous.
    """
    web_page = 'committee_vote_result'

    def get(self, request, code, legislation_id):
        _require_voting()
        committee = _committee(code)
        leg = self.legislation(committee, legislation_id)
        user = request.user
        if not cm.can_view_vote_result(user, committee, leg):
            _refuse('You don’t have permission to view these results.')
        figures = cm.vote_result(leg, user)
        anonymous = leg.anonymous_vote
        parent = leg.plurality_parent
        body = {
            'committee': {'code': committee.code, 'name': committee.name},
            'vote': {
                'id': leg.id,
                'title': leg.title,
                'description': leg.description,
                'vote_mode': leg.vote_mode,
                'anonymous': anonymous,
                'posted_by': leg.posted_by.get_display_name() if leg.posted_by else '',
                'available_at': leg.available_at.isoformat() if leg.available_at else None,
                'voting_closed': leg.voting_closed,
                'voting_ended_at': leg.voting_ended_at.isoformat() if leg.voting_ended_at else None,
                'required_number': leg.required_number if leg.vote_mode == 'piecewise' else None,
                'votes_allowed': (leg.plurality_votes_allowed or 1) if leg.vote_mode == 'plurality' else 1,
                'is_runoff': leg.plurality_is_runoff,
                'parent': {'id': parent.id, 'title': parent.title} if parent and parent.committee_id == committee.id
                else None,
                'runoffs': [{'id': r.id, 'title': r.title, 'voting_closed': r.voting_closed, 'passed': bool(r.passed)}
                            for r in leg.runoff_votes.all().order_by('id')],
            },
            'total_votes': figures['total_votes'],
            'can_end': not leg.voting_closed and cm.can_end_vote(user, committee, leg),
            'can_recalculate': leg.voting_closed and cm.can_recalculate_any(user, committee),
            'can_create_runoff': bool(figures.get('can_create_runoff')),
            'chapter_vote_path': _chapter_vote_path(leg, committee, committee.is_chair(user)),
        }
        if leg.vote_mode == 'plurality':
            voters = {r['option']: r['voters'] for r in figures['plurality_results']['results']}
            body.update({
                'passed': bool(leg.passed) if leg.voting_closed else None,
                'options': [{**bar, 'voters': None if anonymous else voters.get(bar['option'], [])}
                            for bar in figures['plurality_bars']],
                'unique_voter_count': figures['unique_voter_count'],
                'winning_option': figures['winning_option'],
                'tied_options': figures['tied_options'],
                'has_tie': figures['has_tie'],
                'runoff_enabled': leg.plurality_runoff_enabled,
                'top_options_for_runoff': figures['top_options_for_runoff'],
            })
        else:
            body.update({
                'passed': bool(figures['passed']),
                'yes_percentage': figures['yes_percentage'],
                'required_percentage': figures['required_percentage'],
                'choices': [
                    {'option': option, 'count': figures[f'{option}_count'],
                     'voters': None if anonymous else _names(figures[group])}
                    for option, group in (('yes', 'in_favor'), ('no', 'against'), ('abstain', 'abstain'))
                ],
            })
        return Response(body)


class CommitteeVotePushView(_VoteToolView):
    web_page = 'push_to_chapter'
    publish = True

    def post(self, request, code, legislation_id):
        _require_voting()
        committee = _committee(code)
        if not committee.is_chair(request.user):
            _refuse('Only committee chairs can publish results to chapter.' if self.publish
                    else 'Only committee chairs can unpublish results from chapter.')
        leg = self.legislation(committee, legislation_id)
        try:
            (cm.push_to_chapter if self.publish else cm.unpush_from_chapter)(leg, request.user)
        except cm.CommitteeActionError as exc:
            raise Conflict(exc.message, code=exc.code)
        return Response({'vote': _one_vote(leg, request, committee)})


class CommitteeVoteUnpushView(CommitteeVotePushView):
    web_page = 'unpush_from_chapter'
    publish = False


class CommitteeVoteDeleteView(_VoteToolView):
    web_page = 'delete_committee_vote'

    def post(self, request, code, legislation_id):
        _require_voting()
        committee = _committee(code)
        leg = self.legislation(committee, legislation_id)
        if not (committee.is_chair(request.user) or request.user.is_admin):
            _refuse('Only committee chairs or admins can delete votes.')
        return Response({'ok': True, 'title': cm.delete_committee_vote(leg, request.user)})


# ── A chapter vote made from a committee vote ───────────────────────────────

CHAPTER_VOTE_MODES = [   # create_chapter_vote.html's labels
    {'value': 'percentage', 'label': 'Percentage (Yes/No/Abstain)'},
    {'value': 'piecewise', 'label': 'Piecewise (Fixed number of Yes votes)'},
    {'value': 'plurality', 'label': 'Plurality (Multiple options)'},
]
CHAPTER_VOTE_THRESHOLDS = [
    {'value': '51', 'label': '51% (Simple Majority)'},
    {'value': '60', 'label': '60%'},
    {'value': '67', 'label': '67% (Two-Thirds)'},
    {'value': '75', 'label': '75% (Three-Quarters)'},
    {'value': '100', 'label': '100% (Unanimous)'},
]
PIECEWISE_MESSAGE = 'Piecewise voting requires a valid number of required votes.'


def _text(data, key, default):
    value = data.get(key, default)
    if value is None:
        return default
    if not isinstance(value, str):
        raise BadRequest(f'Send {key} as text.', code='invalid_input')
    return value


class CommitteeChapterVoteView(_VoteToolView):
    """
    ``create_chapter_vote_from_committee`` and its form
    (committee/create_chapter_vote.html). Who may, the refusals, the
    validation, the description with the committee's result and the link on
    the committee vote are cm.create_chapter_vote, the web view's. The form's
    own constraints (a title, a listed mode and threshold, five option fields)
    are checked here because a JSON body isn't limited by the form.
    """
    web_page = 'create_chapter_vote'

    def chapter_vote(self, request, code, legislation_id):
        _require_voting()
        if not check_feature_enabled('legislation_voting'):
            raise exceptions.PermissionDenied('Chapter voting is off.', code='feature_disabled')
        committee = _committee(code)
        leg = self.legislation(committee, legislation_id)
        if not cm.can_manage_chapter_vote(request.user, committee):
            _refuse('Only committee chairs or admins can create chapter votes.')
        if leg.chapter_legislation_id:
            raise Conflict('A chapter vote already exists for this item.', code='already_exists')
        return committee, leg

    def get(self, request, code, legislation_id):
        committee, leg = self.chapter_vote(request, code, legislation_id)
        thresholds = [t['value'] for t in CHAPTER_VOTE_THRESHOLDS]
        return Response({
            'committee': {'code': committee.code, 'name': committee.name},
            'vote': {'id': leg.id, 'title': leg.title, 'vote_mode': leg.vote_mode,
                     'voting_closed': leg.voting_closed, 'passed': bool(leg.passed)},
            # The form's prefilled values: the committee vote's settings.
            'defaults': {
                'title': f"[{committee.code}] {leg.title}",
                'description': leg.description or '',
                'vote_mode': leg.vote_mode if leg.vote_mode in cm.VOTE_MODES else 'percentage',
                'required_percentage': leg.required_percentage if leg.required_percentage in thresholds else '51',
                'required_number': leg.required_number,
                'plurality_options': list(leg.plurality_options or [])[:cm.CHAPTER_VOTE_PLURALITY_OPTIONS],
                'anonymous': leg.anonymous_vote,
                'allow_abstain': leg.allow_abstain,
                'show_committee_result': True,
            },
            'vote_modes': CHAPTER_VOTE_MODES,
            'thresholds': CHAPTER_VOTE_THRESHOLDS,
            'max_options': cm.CHAPTER_VOTE_PLURALITY_OPTIONS,
            'anonymous_available': check_feature_enabled('anonymous_voting'),
            'abstain_optional': check_feature_enabled('abstain_voting'),
            **cm.committee_result_summary(leg),
        })

    def post(self, request, code, legislation_id):
        committee, leg = self.chapter_vote(request, code, legislation_id)
        data = _data(request)
        title = _text(data, 'title', f"[{committee.code}] {leg.title}")
        if not title.strip():
            raise BadRequest('Enter a title.', code='invalid_input')
        if len(title) > 200:
            raise BadRequest('Keep the title to 200 characters.', code='invalid_input')
        description = _text(data, 'description', leg.description or '')
        vote_mode = _text(data, 'vote_mode', 'percentage')
        if vote_mode not in cm.VOTE_MODES:
            raise BadRequest('Choose a voting mode.', code='invalid_input')
        required_percentage = _text(data, 'required_percentage', '51')
        if required_percentage not in [t['value'] for t in CHAPTER_VOTE_THRESHOLDS]:
            raise BadRequest('Choose a required percentage.', code='invalid_input')
        required_number = data.get('required_number')
        if vote_mode == 'piecewise':
            # The web view's int() would raise on anything else.
            if isinstance(required_number, bool) or not (
                    required_number in (None, '') or isinstance(required_number, int)
                    or (isinstance(required_number, str) and required_number.strip().lstrip('-').isdigit())):
                raise BadRequest(PIECEWISE_MESSAGE, code='invalid_input')
        options = data.get('plurality_options') or []
        if not isinstance(options, list) or not all(isinstance(o, str) for o in options):
            raise BadRequest('Send plurality_options as a list of text.', code='invalid_input')
        if len(options) > cm.CHAPTER_VOTE_PLURALITY_OPTIONS:
            raise BadRequest(f'Plurality voting allows up to {cm.CHAPTER_VOTE_PLURALITY_OPTIONS} options.',
                             code='invalid_input')
        try:
            chapter_leg = cm.create_chapter_vote(
                committee, leg, request.user,
                title=title,
                description=description,
                vote_mode=vote_mode,
                required_percentage=required_percentage,
                required_number=required_number,
                plurality_options=options,
                anonymous=data.get('anonymous') is True,
                remove_abstain=data.get('allow_abstain') is False,
                show_committee_result=data.get('show_committee_result') is True,
                committee_vote_summary=_text(data, 'committee_vote_summary', ''),
                available_at=_when(data.get('available_at'), 'available_at'),
            )
        except cm.CommitteeActionError as exc:
            if exc.code == 'already_exists':
                raise Conflict(exc.message, code=exc.code)
            raise BadRequest(exc.message, code='invalid_input')
        return Response({
            'chapter_vote': {'id': chapter_leg.id, 'title': chapter_leg.title},
            'message': 'Chapter vote created! Members can now vote on the Vote page.',
            'vote': _one_vote(leg, request, committee),
        }, status=status.HTTP_201_CREATED)


class CommitteeChapterVoteDeleteView(_VoteToolView):
    """``delete_chapter_vote_link``: the vote page's "Delete Chapter Vote" (chairs, admins)."""
    web_page = 'delete_chapter_vote_link'

    def post(self, request, code, legislation_id):
        _require_voting()
        committee = _committee(code)
        if not cm.can_manage_chapter_vote(request.user, committee):
            _refuse('Only committee chairs or admins can delete chapter votes.')
        leg = self.legislation(committee, legislation_id)
        try:
            title = cm.delete_chapter_vote(committee, leg, request.user)
        except cm.CommitteeActionError as exc:
            raise Conflict(exc.message, code=exc.code)
        return Response({'ok': True, 'title': title, 'message': 'Chapter vote deleted.',
                         'vote': _one_vote(leg, request, committee)})


# ── Members ─────────────────────────────────────────────────────────────────

def member_management(user, committee):
    """What the members screen may change (added to its response)."""
    return {
        # committee_home shows "+ Add" / Remove only on a live committee.
        'roles': cm.can_manage_roles(user, committee) and not committee.is_archived,
        'members': can_manage_members(user, committee),
        'permissions': can_designate(user, committee),
    }


class CommitteeMemberCandidatesView(MobileAPIView):
    web_page = 'manage_members'
    web_page_toggle = False

    def get(self, request, code):
        committee = _committee(code)
        role = request.query_params.get('role', 'member')
        if role not in cm.ROLE_FIELDS:
            raise BadRequest('Choose a valid role.', code='invalid_input')
        if cm.can_manage_roles(request.user, committee):
            qs = cm.role_candidates(committee, role)
        elif can_manage_members(request.user, committee) and role != 'chair':
            # The Manage Members page's list: active members, no pledges.
            qs = ParliamentUser.active.exclude(member_type='Pledge').order_by('name')
        else:
            _refuse('You cannot manage committee members.')
        existing = getattr(committee, cm.ROLE_FIELDS[role]).values_list('pk', flat=True)
        rows = qs.exclude(pk__in=list(existing)).exclude(member_type='Pledge').defer(*member_defer())
        return Response({'role': role, 'candidates': [
            {**_person(u), 'member_type': u.member_type} for u in rows[:CANDIDATE_LIMIT]]})


class CommitteeAddMemberView(MobileAPIView):
    web_page = 'committee_add_member'
    web_page_toggle = False
    adding = True

    def post(self, request, code):
        committee = _committee(code)
        if not cm.can_manage_roles(request.user, committee):
            _refuse('You do not have permission to manage this committee.')
        target = _target(request)
        role_type = _data(request).get('role_type')
        try:
            message = (cm.add_role if self.adding else cm.remove_role)(committee, target, role_type)
        except cm.CommitteeActionError as exc:
            raise BadRequest(exc.message, code='invalid_input')
        return Response({'ok': True, 'message': message})


class CommitteeRemoveMemberView(CommitteeAddMemberView):
    web_page = 'committee_remove_member'
    adding = False


class CommitteeManageMembersView(MobileAPIView):
    web_page = 'manage_members'
    web_page_toggle = False

    def post(self, request, code):
        committee = _committee(code)
        if not can_manage_members(request.user, committee):
            _refuse('You cannot manage committee members.')
        target = _target(request)
        try:
            message = cm.manage_members_action(committee, target, _data(request).get('action'))
        except cm.CommitteeActionError as exc:
            raise BadRequest(exc.message, code='invalid_input')
        return Response({'ok': True, 'message': message})


# ── Member permissions ──────────────────────────────────────────────────────

FIELDS = [f for f, _ in DESIGNATIONS]


class CommitteeMemberPermissionsView(MobileAPIView):
    web_page = 'committee_member_permissions'
    web_page_toggle = False

    def committee(self, request, code):
        committee = _committee(code)
        if not can_designate(request.user, committee):
            _refuse('Only the committee’s chairs can change member permissions.')
        return committee

    def payload(self, committee):
        people = cm.designation_people(committee)
        current = {p.user_id: p for p in CommitteePermissions.objects.filter(committee=committee, user__in=people)}
        return {
            'designations': [{'field': f, 'label': label} for f, label in DESIGNATIONS],
            'rows': [{**_person(m), 'flags': {f: bool(current.get(m.pk) and getattr(current[m.pk], f)) for f in FIELDS}}
                     for m in people],
        }

    def get(self, request, code):
        return Response(self.payload(self.committee(request, code)))

    def post(self, request, code):
        committee = self.committee(request, code)
        data = _data(request)
        member = next((m for m in cm.designation_people(committee) if m.pk == data.get('user_id')), None)
        if member is None:
            raise BadRequest('Choose a member or advisor of this committee (chairs can do everything).',
                             code='invalid_input')
        flags = data.get('flags')
        if (not isinstance(flags, dict) or not flags or set(flags) - set(FIELDS)
                or not all(isinstance(v, bool) for v in flags.values())):
            raise BadRequest(f'Send flags as {{field: true/false}} for {", ".join(FIELDS)}.', code='invalid_input')
        cm.save_designations(committee, member, flags)
        return Response(self.payload(committee))


# ── Attendance ──────────────────────────────────────────────────────────────

class CommitteeAttendanceView(MobileAPIView):
    web_page = 'committee_attendance'
    web_page_toggle = False

    def committee(self, request, code):
        committee = _committee(code)
        if not cm.can_mark_committee_attendance(request.user, committee):
            _refuse('Only committee chairs and officers can mark attendance.')
        return committee

    def payload(self, committee):
        existing = cm.todays_attendance(committee)
        rows = [{**_person(m), 'present': bool(existing.get(m.user_id) and existing[m.user_id].status == 'present')}
                for m in cm.attendance_members(committee)]
        return {
            'committee': {'code': committee.code, 'name': committee.name},
            'date': timezone.localdate().isoformat(),
            'members': rows,
            'present_count': sum(1 for r in rows if r['present']),
            'total_members': len(rows),
            'can_view_history': user_is_officer_or_chair(self.request.user),
        }

    def get(self, request, code):
        return Response(self.payload(self.committee(request, code)))

    def post(self, request, code):
        committee = self.committee(request, code)
        present = _data(request).get('present')
        if not isinstance(present, list) or not all(isinstance(p, str) for p in present):
            raise BadRequest('Send present as a list of user ids.', code='invalid_input')
        count = cm.mark_committee_attendance(committee, request.user, present, request._request)
        return Response({'updated': count, **self.payload(committee)})


class CommitteeAttendanceHistoryView(MobileAPIView):
    """
    The website's Attendance History page: each date's records (who, status,
    who marked it, when), newest first, from the same query
    (``cm.attendance_history``). The page counts every record as "Total
    Present"; here each session carries a count per status, and each member
    their totals across the sessions listed.
    """
    web_page = 'committee_attendance_history'
    web_page_toggle = False

    def get(self, request, code):
        if not user_is_officer_or_chair(request.user):   # @officer_required
            raise exceptions.PermissionDenied('Officers and chairs only.', code='forbidden')
        committee = _committee(code)
        sessions = []
        totals = {}
        for day, records in cm.attendance_history(committee).items():
            rows = []
            for record in records:
                person = _person(record.user)
                rows.append({
                    **person,
                    'status': record.status,
                    'status_label': record.get_status_display(),
                    'marked_by': record.marked_by.name if record.marked_by_id else None,
                    'marked_at': record.marked_at.isoformat() if record.marked_at else None,
                })
                total = totals.setdefault(record.user_id, {**person, 'present': 0, 'absent': 0,
                                                           'excused': 0, 'late': 0, 'recorded': 0})
                total['recorded'] += 1
                if record.status in ('present', 'absent', 'excused', 'late'):
                    total[record.status] += 1
            counts = {s: sum(1 for r in rows if r['status'] == s) for s in ('present', 'absent', 'excused', 'late')}
            sessions.append({'date': day.isoformat(), 'records': rows, 'counts': counts})
        members = sorted(totals.values(), key=lambda m: (-m['present'], m['name'].lower()))
        return Response({
            'committee': {'code': committee.code, 'name': committee.name},
            'sessions': sessions,
            'members': members,
            'can_mark': cm.can_mark_committee_attendance(request.user, committee),
        })


# ── Documents ───────────────────────────────────────────────────────────────

class _DocumentToolView(MobileAPIView):
    web_page_toggle = False

    def document(self, committee, document_id):
        doc = (CommitteeDocument.objects.select_related('committee', 'uploaded_by')
               .filter(pk=document_id, committee=committee).first())
        if doc is None:
            raise Http404
        return doc


class CommitteeDocumentPublishView(_DocumentToolView):
    web_page = 'toggle_document_publish'

    def post(self, request, code, document_id):
        committee = _committee(code)
        doc = self.document(committee, document_id)
        if not can_manage_documents(request.user, committee):
            cm.refuse_document_publish(request.user, document_id, committee, get_client_ip(request._request))
            _refuse('Only committee chairs can change document publish settings.')
        published = _data(request).get('published', not doc.published_to_chapter)
        if not isinstance(published, bool):
            raise BadRequest('Send published as true or false.', code='invalid_input')
        if published != doc.published_to_chapter:
            cm.set_document_published(doc, request.user, committee, published)
        return Response({'document': _document_json(doc, code)})


class CommitteeDocumentDeleteView(_DocumentToolView):
    web_page = 'delete_committee_document'

    def post(self, request, code, document_id):
        committee = _committee(code)
        doc = self.document(committee, document_id)
        if not can_manage_documents(request.user, committee):
            cm.refuse_document_delete(request.user, doc, committee, get_client_ip(request._request))
            _refuse('Only committee VPs and chairs can delete documents.')
        return Response({'ok': True, 'title': cm.delete_committee_document(doc, request.user, committee)})
