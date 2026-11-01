"""
Committee chair tools, shared by the website's views (``src/view/committee/``)
and the app's (``src/mobile/views_committee_management.py``), so the two can't
drift on who may do what or on the side effects (logs, activity log, security
events).

Each function does the work and the logging; the caller decides who is asked
(its own permission check, which is the web view's and is restated next to
the matching helper here) and how to answer. Refusals a caller should show
the member are ``CommitteeActionError`` with the website's wording.

Kai: none of these tools has a rule of its own for the Kai committee. What
matters there is that Kai access follows the actual chairs list
(``src.view.kai_reports._is_kai_chair``), so adding or removing a chair here
widens or narrows who reads Kai cases — exactly as on the website, under the
same chair / VP / admin gate.
"""
import logging

from django.utils import timezone

from src.feature_flag_decorators import check_feature_enabled
from src.vote_rules import plurality_passed

logger = logging.getLogger('function_calls')
document_logger = logging.getLogger('user_actions')


class CommitteeActionError(Exception):
    """A refusal to show the member. ``code`` lets a caller tell kinds apart."""

    def __init__(self, message, code='invalid_input'):
        super().__init__(message)
        self.message = message
        self.code = code


# ── Votes ───────────────────────────────────────────────────────────────────

VOTE_MODES = ('percentage', 'plurality', 'piecewise')
MAX_PLURALITY_OPTIONS = 10


def vote_tally(legislation):
    """{option: count, ..., 'total': n} (plurality) or {yes, no, abstain, total}."""
    from src.models import CommitteeVote
    votes = CommitteeVote.objects.filter(legislation=legislation)
    if legislation.vote_mode == 'plurality':
        tally = {opt: votes.filter(vote_choice=opt).count() for opt in (legislation.plurality_options or [])}
        tally['total'] = votes.count()
    else:
        tally = {
            'yes': votes.filter(vote_choice='yes').count(),
            'no': votes.filter(vote_choice='no').count(),
            'abstain': votes.filter(vote_choice='abstain').count(),
            'total': votes.count(),
        }
    return tally


def apply_vote_result(legislation, tally):
    """
    Set ``passed`` / ``status`` from ``tally`` (not saved). Untouched when
    nobody voted, or a percentage vote had only abstentions — as before.
    """
    if not tally['total']:
        return
    if legislation.vote_mode == 'plurality':
        legislation.passed = plurality_passed(tally)  # v3.51.1: a tie doesn't pass
    elif legislation.vote_mode == 'piecewise':
        legislation.passed = tally.get('yes', 0) >= (legislation.required_number or 0)
    else:
        yes_votes, no_votes = tally.get('yes', 0), tally.get('no', 0)
        countable_votes = yes_votes + no_votes
        if not countable_votes:
            return
        legislation.passed = (yes_votes / countable_votes) * 100 >= int(legislation.required_percentage)
    legislation.status = 'passed' if legislation.passed else 'draft'


def recalculate_committee_vote(legislation, user, request):
    """
    The committee vote page's "Recalculate Result" (chairs, closed votes):
    returns ``'passed'`` / ``'did not pass'``, or None when nobody voted.
    """
    tally = vote_tally(legislation)
    if not tally['total']:
        return None
    from src.models import ActivityLog
    apply_vote_result(legislation, tally)
    legislation.save(update_fields=['passed', 'status'])
    result_text = 'passed' if legislation.passed else 'did not pass'
    logger.info(f"{user.username} recalculated vote result for '{legislation.title}' (ID: {legislation.id}) - {result_text}")
    ActivityLog.log_activity(
        action_type='vote_ended',
        user=user,
        description=f'{user.name} recalculated committee vote result for "{legislation.title}" — {result_text}',
        request=request,
        object_type='CommitteeLegislation',
        object_id=legislation.id,
        object_repr=legislation.title,
        metadata={'action': 'recalculate', 'result': result_text, 'committee': legislation.committee.code,
                  'total_votes': tally['total']},
    )
    return result_text


def _clamped_int(raw, default, low, high):
    try:
        return max(low, min(high, int(raw)))
    except (ValueError, TypeError):
        return default


def create_committee_vote(committee, user, *, title, description, available_at, document=None,
                          voting_ends_at=None, anonymous=False, remove_abstain=False,
                          required_percentage='51', vote_mode='percentage', plurality_options=(),
                          plurality_votes_allowed=1, plurality_runoff_enabled=False,
                          plurality_runoff_count=2, required_number=None):
    """
    Create a committee vote (``committee_create_vote``; chairs only). Raises
    ``CommitteeActionError`` with code ``plurality_options`` / ``required_number``
    (the web form redirects) or ``required_fields`` (it re-renders).
    """
    from src.models import CommitteeLegislation
    title = (title or '').strip() if isinstance(title, str) else ''
    description = description or ''
    vote_mode = vote_mode or 'percentage'
    if vote_mode not in VOTE_MODES:
        raise CommitteeActionError('Choose a valid vote type.', code='vote_mode')
    required_percentage = str(required_percentage or '51')
    if required_percentage not in dict(CommitteeLegislation.VOTE_THRESHOLDS):
        raise CommitteeActionError('Choose a valid passing threshold.', code='required_percentage')
    anonymous = bool(anonymous) and check_feature_enabled('anonymous_voting')
    allow_abstain = not remove_abstain and check_feature_enabled('abstain_voting')

    options = []
    votes_allowed, runoff_enabled, runoff_count = 1, False, 2
    number = None
    if vote_mode == 'plurality':
        options = [o.strip() for o in list(plurality_options)[:MAX_PLURALITY_OPTIONS]
                   if isinstance(o, str) and o.strip()]
        if len(options) < 2:
            raise CommitteeActionError('Plurality voting requires at least two options.', code='plurality_options')
        votes_allowed = min(_clamped_int(plurality_votes_allowed, 1, 1, 10), len(options))
        runoff_enabled = bool(plurality_runoff_enabled)
        if runoff_enabled:
            runoff_count = _clamped_int(plurality_runoff_count, 2, 2, len(options))
    elif vote_mode == 'piecewise':
        try:
            number = int(required_number)
        except (ValueError, TypeError):
            number = 0
        if number < 1:
            raise CommitteeActionError(
                'Piecewise voting requires a valid number of required votes (at least 1).', code='required_number')

    # Require title, available_at, and either description OR document
    if not (title and available_at and (description or document)):
        raise CommitteeActionError('Please fill in all required fields.', code='required_fields')

    legislation = CommitteeLegislation.objects.create(
        committee=committee,
        title=title,
        description=description,
        document=document if vote_mode != 'plurality' else None,
        posted_by=user,
        available_at=available_at,
        voting_ends_at=voting_ends_at,
        anonymous_vote=anonymous,
        allow_abstain=allow_abstain,
        required_percentage=required_percentage,
        vote_mode=vote_mode,
        plurality_options=options if vote_mode == 'plurality' else None,
        plurality_votes_allowed=votes_allowed,
        plurality_runoff_enabled=runoff_enabled,
        plurality_runoff_count=runoff_count,
        required_number=number,
    )
    logger.info(f"{user.username} created committee legislation titled '{title}' for {committee.code}")
    return legislation


def delete_committee_vote(legislation, user):
    """Delete a committee vote, its ballots and any chapter vote made from it (chairs or admins)."""
    from src.models import CommitteeVote
    title = legislation.title
    committee_code = legislation.committee.code
    if legislation.chapter_legislation:
        chapter_leg = legislation.chapter_legislation
        legislation.chapter_legislation = None
        legislation.save()
        chapter_leg.delete()
        logger.info(f"Also deleted linked chapter legislation for '{title}'")
    vote_count = CommitteeVote.objects.filter(legislation=legislation).count()
    CommitteeVote.objects.filter(legislation=legislation).delete()
    logger.info(f"Deleted {vote_count} votes for '{title}'")
    legislation.delete()
    logger.info(f"{user.username} deleted committee vote '{title}' from {committee_code}")
    return title


def push_to_chapter(legislation, user):
    """Publish a committee vote's results on the chapter documents page (chairs)."""
    if legislation.pushed_to_chapter:
        raise CommitteeActionError('This item has already been published to chapter.', code='already_published')
    legislation.pushed_to_chapter = True
    legislation.save()
    logger.info(f"{user.username} published '{legislation.title}' from {legislation.committee.code} to chapter documents")


def unpush_from_chapter(legislation, user):
    """Take them off it again (chairs). A chapter vote made from it stays."""
    if not legislation.pushed_to_chapter:
        raise CommitteeActionError('This item is not published to chapter.', code='not_published')
    legislation.pushed_to_chapter = False
    legislation.save()
    logger.info(f"{user.username} unpublished '{legislation.title}' from {legislation.committee.code} chapter documents")


# ── Members ─────────────────────────────────────────────────────────────────

#: role_type → the committee's m2m field (committee_add_member / _remove_member).
ROLE_FIELDS = {'member': 'members', 'chair': 'chairs', 'advisor': 'advisors', 'voter': 'voting_members'}
ROLE_LABELS = {'member': 'a member', 'chair': 'a chair', 'advisor': 'an advisor', 'voter': 'a voting member'}
ROLE_PLURALS = {'member': 'members', 'chair': 'chairs', 'advisor': 'advisors', 'voter': 'voting members'}

#: committee_manage_members' actions: action → (m2m field, add?, message).
MANAGE_ACTIONS = {
    'add_member': ('members', True, '{} has been added as a committee member.'),
    'remove_member': ('members', False, '{} has been removed from committee members.'),
    'add_advisor': ('advisors', True, '{} has been added as an advisor.'),
    'remove_advisor': ('advisors', False, '{} has been removed from advisors.'),
    'add_voter': ('voting_members', True, '{} has been added as a voting member.'),
    'remove_voter': ('voting_members', False, '{} has been removed from voting members.'),
}

PLEDGE_REFUSED = 'Pledges cannot be added to committees.'


def can_manage_roles(user, committee):
    """
    ``committee_add_member`` / ``committee_remove_member``: chairs (incl. exec
    board, via ``is_chair``), the committee's VP and site admins.
    """
    return committee.is_chair(user) or committee.is_vp(user) or user.is_admin


def add_role(committee, target, role_type):
    """Give ``target`` a role on the committee. Returns the success message."""
    if role_type not in ROLE_FIELDS:
        raise CommitteeActionError('Choose a valid role.', code='role_type')
    if target.is_pledge:
        raise CommitteeActionError(PLEDGE_REFUSED, code='pledge')
    getattr(committee, ROLE_FIELDS[role_type]).add(target)
    return f'{target.name} has been added as {ROLE_LABELS[role_type]}.'


def remove_role(committee, target, role_type):
    """Take a role away. Removing a member also drops their vote. Returns the message."""
    if role_type not in ROLE_FIELDS:
        raise CommitteeActionError('Choose a valid role.', code='role_type')
    getattr(committee, ROLE_FIELDS[role_type]).remove(target)
    if role_type == 'member' and committee.voting_members.filter(pk=target.pk).exists():
        # No reason to retain a vote on a non-member, and leaving them in
        # causes a confusing orphan state.
        committee.voting_members.remove(target)
        return f'{target.name} has been removed from members and voting members.'
    return f'{target.name} has been removed from {ROLE_PLURALS[role_type]}.'


def manage_members_action(committee, target, action):
    """One of the Manage Members page's actions (``MANAGE_ACTIONS``). Returns the message."""
    if action not in MANAGE_ACTIONS:
        raise CommitteeActionError('Choose a valid action.', code='action')
    field, adding, message = MANAGE_ACTIONS[action]
    if adding and target.is_pledge:
        raise CommitteeActionError(PLEDGE_REFUSED, code='pledge')
    related = getattr(committee, field)
    if adding:
        related.add(target)
    else:
        related.remove(target)
    return message.format(target.name)


def role_candidates(committee, role_type):
    """Who the committee page's "+ Add" lists for ``role_type`` (committee_home's eligible lists)."""
    from django.db.models import Q
    from src.constants import MemberStatus, MemberType
    from src.models import ParliamentUser
    if role_type == 'chair':
        qs = committee.members.exclude(pk__in=committee.chairs.all())
    elif role_type == 'advisor':
        qs = ParliamentUser.objects.filter(Q(member_status=MemberStatus.ACTIVE) | Q(member_type=MemberType.ADVISOR))
    elif role_type == 'voter':
        qs = (committee.members.all() | committee.chairs.all()).exclude(pk__in=committee.voting_members.all()).distinct()
    else:
        qs = ParliamentUser.objects.filter(member_status=MemberStatus.ACTIVE)
    return qs.order_by('name')


# ── Member permissions (designations) ──────────────────────────────────────

def designation_people(committee):
    """Members and advisors a chair can designate: everyone but the chairs, by name."""
    chair_ids = set(committee.chairs.values_list('pk', flat=True))
    return [m for m in (committee.members.all() | committee.advisors.all()).distinct().order_by('name')
            if m.pk not in chair_ids]


def save_designations(committee, member, values):
    """Set ``member``'s designation flags (``values``: {field: bool}, any subset of DESIGNATIONS)."""
    from src.models import CommitteePermissions
    CommitteePermissions.objects.update_or_create(
        committee=committee, user=member,
        defaults=values,
        create_defaults={**values, 'can_view_docs': True,
                         'can_vote': committee.voting_members.filter(pk=member.pk).exists()},
    )


# ── Attendance ──────────────────────────────────────────────────────────────

def can_mark_committee_attendance(user, committee):
    """``committee_attendance``: chairs (incl. exec board) and officers."""
    return committee.is_chair(user) or user.member_type == 'Officer'


def attendance_members(committee):
    return committee.members.filter(member_status='Active').order_by('name')


def todays_attendance(committee):
    """{user_id: Attendance} — today's committee attendance (local date)."""
    from src.models import Attendance
    from src.models.users import member_defer
    return {
        att.user_id: att
        for att in Attendance.objects.filter(
            committee=committee, attendance_type='committee', date=timezone.localdate(),
        ).select_related('user').defer(*member_defer('user'))
    }


def mark_committee_attendance(committee, marked_by, present_ids, request):
    """
    Today's attendance: active members in ``present_ids`` are present, everyone
    else's record for today is removed. Logs the activity. Returns how many
    are present.
    """
    from src.models import ActivityLog, Attendance
    today = timezone.localdate()
    present_ids = {str(i) for i in present_ids}
    updated_count = 0
    for member in attendance_members(committee):
        if str(member.user_id) in present_ids:
            Attendance.objects.update_or_create(
                committee=committee, user=member, date=today, attendance_type='committee',
                defaults={'status': 'present', 'marked_by': marked_by, 'marked_at': timezone.now()},
            )
            updated_count += 1
        else:
            Attendance.objects.filter(committee=committee, user=member, date=today,
                                      attendance_type='committee').delete()
    ActivityLog.log_activity(
        action_type='other',
        user=marked_by,
        description=f'Marked committee attendance for {committee.name} ({updated_count} members present)',
        request=request,
        object_type='Committee',
        object_id=committee.id,
        object_repr=str(committee),
    )
    return updated_count


# ── Documents ───────────────────────────────────────────────────────────────

def refuse_document_publish(user, document_id, committee, ip_address):
    """The security event for someone who can't manage documents trying to (un)publish."""
    from src.logging_utils import log_security_event
    log_security_event(
        event_type='UNAUTHORIZED_DOCUMENT_PUBLISH',
        user=user,
        details={'document_id': document_id, 'committee': committee.code, 'attempted_action': 'toggle_publish'},
        ip_address=ip_address,
        severity='WARNING',
    )


def refuse_document_delete(user, document, committee, ip_address):
    """The security event (and warning) for an unauthorised delete."""
    from src.logging_utils import log_security_event
    log_security_event(
        event_type='UNAUTHORIZED_DOCUMENT_DELETE',
        user=user,
        details={'document_id': document.id, 'document_title': document.title,
                 'committee': committee.code, 'committee_name': committee.name},
        ip_address=ip_address,
        severity='WARNING',
    )
    document_logger.warning(
        f"Unauthorized delete attempt: User {user.name} (ID: {user.pk}) "
        f"tried to delete document '{document.title}' (ID: {document.id}) "
        f"from committee {committee.name}"
    )


def set_document_published(document, user, committee, published):
    """Publish a committee document to the chapter documents page, or take it off."""
    from src.logging_utils import log_document_action
    old_status = document.published_to_chapter
    document.published_to_chapter = bool(published)
    document.save()
    log_document_action(
        action='PUBLISH' if document.published_to_chapter else 'UNPUBLISH',
        document=document,
        user=user,
        committee=committee,
        details={'previous_status': old_status, 'new_status': document.published_to_chapter},
    )


def delete_committee_document(document, user, committee):
    """Delete a committee document and its file. Returns its title."""
    from src.logging_utils import log_document_action
    doc_title, doc_id = document.title, document.id
    log_document_action(action='DELETE', document=document, user=user, committee=committee)
    document_logger.info(
        f"Document deleted: '{doc_title}' (ID: {doc_id}) "
        f"from committee {committee.name} by {user.name} (ID: {user.pk})"
    )
    if document.document:
        try:
            document.document.delete(save=False)
        except Exception as e:  # storage errors must not keep the record alive
            document_logger.error(f"Error deleting file for document '{doc_title}' (ID: {doc_id}): {str(e)}")
    document.delete()
    return doc_title
