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
  on a closed vote (chairs, as on the vote page).
* ``POST committees/<code>/votes/<id>/push/`` / ``unpush/`` — publish its
  results to the chapter documents page, or take them off (chairs).
* ``POST committees/<code>/votes/<id>/delete/`` — delete it (chairs, admins).
* ``GET committees/<code>/members/candidates/?role=`` — who "+ Add" offers.
* ``POST committees/<code>/members/add/`` / ``remove/`` — a role on the
  committee (``committee_add_member`` / ``_remove_member``: chairs, the VP,
  admins).
* ``POST committees/<code>/members/manage/`` — the Manage Members page's
  actions (``committee_manage_members``: also members a chair has designated).
* ``GET|POST committees/<code>/member-permissions/`` — designations
  (``committee_member_permissions``: chairs, the VP, admins).
* ``GET|POST committees/<code>/attendance/`` — today's committee attendance
  (``committee_attendance``: chairs and officers).
* ``POST committees/<code>/documents/<id>/publish/`` / ``delete/`` —
  (``toggle_document_publish`` / ``delete_committee_document``: chairs and
  members designated to manage documents).

Every permission check, the work and its side effects (logs, activity log,
security events) are the web views', via ``src.committee_management`` and
``src.view.committee.member_permissions``. Uploading a document stays on the
website (its form takes PDFs and Word files, which the app can't pick).
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
        'can_recalculate': is_chair and leg.voting_closed,
        'can_push': is_chair and leg.voting_closed,
        'can_delete': is_chair or viewer.is_admin,
        # Making a chapter vote from it stays on the website.
        'chapter_vote_path': (reverse('create_chapter_vote', args=[committee.code, leg.id])
                              if is_chair and leg.voting_closed and leg.chapter_legislation_id is None else None),
        'result_path': reverse('committee_vote_result', args=[committee.code, leg.id]),
    }


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

    def post(self, request, code, legislation_id):
        _require_voting()
        committee = _committee(code)
        leg = self.legislation(committee, legislation_id)
        if not (committee.is_chair(request.user) and leg.voting_closed):
            _refuse('You don’t have permission to recalculate this vote.')
        result = cm.recalculate_committee_vote(leg, request.user, request._request)
        return Response({'result': result, 'vote': _one_vote(leg, request, committee)})


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
