"""
A committee electing its chair in the app (11-16-26: the Education Oversight
Committee elects a chair from among its members; see src/education_oversight.py).

* Any member of the committee (or a site admin) opens an election. One can be
  open at a time.
* Every member of the committee votes once, for any member (themselves
  included), by secret ballot: totals are shown only once it closes.
* It closes on its own when every member has voted, or when whoever opened
  it, the current chair or a site admin closes it.
* Most votes wins and becomes the committee's only chair (replacing the
  last one). A tie, or no votes, elects nobody: run it again, or an officer
  sets the chair by hand on Manage committees (the override).

Only committees that ``allows`` it offer elections: the education committee,
for now.
"""
from django.db import IntegrityError, transaction
from django.utils import timezone


class ChairElectionError(Exception):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def _models():
    from src.models import CommitteeChairBallot, CommitteeChairElection, ParliamentUser
    return CommitteeChairBallot, CommitteeChairElection, ParliamentUser


def allows(committee):
    return bool(committee.is_education_committee and committee.is_active)


def electorate(committee):
    """Active members of the committee (members, voting members and chairs), by name."""
    _, _, ParliamentUser = _models()
    from django.db.models import Q
    return (ParliamentUser.objects.filter(is_active=True, member_status='Active')
            .filter(Q(committees=committee) | Q(committee_voters=committee) | Q(chair_roles=committee))
            .distinct().order_by('name'))


def on_committee(user, committee):
    return electorate(committee).filter(pk=user.pk).exists()


def current(committee):
    _, CommitteeChairElection, _ = _models()
    return CommitteeChairElection.objects.filter(committee=committee, status='open').first()


def latest(committee):
    _, CommitteeChairElection, _ = _models()
    return CommitteeChairElection.objects.filter(committee=committee).first()


def open_election(committee, user):
    _, CommitteeChairElection, _ = _models()
    if not allows(committee):
        raise ChairElectionError('not_allowed', 'This committee doesn’t elect its chair in Parliament.', 403)
    if not (on_committee(user, committee) or user.is_admin):
        raise ChairElectionError('forbidden', 'Only the committee’s members can start a chair election.', 403)
    if electorate(committee).count() < 2:
        raise ChairElectionError('too_few', 'The committee needs at least two members to elect a chair.')
    try:
        with transaction.atomic():
            election = CommitteeChairElection.objects.create(committee=committee, opened_by=user)
    except IntegrityError:
        raise ChairElectionError('already_open', 'A chair election is already open.', 409)
    _log(user, f'{user.get_display_name()} opened a chair election for {committee.name}', election)
    _notify_members(election, user)
    return election


def vote(election, user, choice_id):
    CommitteeChairBallot, _, ParliamentUser = _models()
    if election.status != 'open':
        raise ChairElectionError('closed', 'This election is closed.', 409)
    voters = electorate(election.committee)
    if not voters.filter(pk=user.pk).exists():
        raise ChairElectionError('forbidden', 'Only the committee’s members can vote.', 403)
    choice = voters.filter(pk=str(choice_id or '')).first()
    if choice is None:
        raise ChairElectionError('invalid_choice', 'Choose a member of the committee.')
    try:
        with transaction.atomic():
            CommitteeChairBallot.objects.create(election=election, voter=user, choice=choice)
    except IntegrityError:
        raise ChairElectionError('already_voted', 'You already voted in this election.', 409)
    if election.ballots.count() >= voters.count():
        close(election, None)   # everyone has voted
        election.refresh_from_db()
    return election


def can_close(election, user):
    committee = election.committee
    return bool(user.is_admin or election.opened_by_id == user.pk
                or committee.chairs.filter(pk=user.pk).exists())


def close(election, user):
    """Count it; the winner becomes the committee's chair. ``user`` None = everyone voted."""
    _, CommitteeChairElection, _ = _models()
    from collections import Counter
    if user is not None and not can_close(election, user):
        raise ChairElectionError('forbidden', 'Only whoever opened it, the chair or a site admin can close it.', 403)
    with transaction.atomic():
        locked = CommitteeChairElection.objects.select_for_update().get(pk=election.pk)
        if locked.status != 'open':
            raise ChairElectionError('closed', 'This election is already closed.', 409)
        counts = Counter(locked.ballots.values_list('choice_id', flat=True))
        ranked = counts.most_common()
        winner_id = ranked[0][0] if ranked and (len(ranked) == 1 or ranked[0][1] > ranked[1][1]) else None
        locked.status, locked.closed_at, locked.closed_by = 'closed', timezone.now(), user
        locked.totals = dict(counts)
        locked.winner_id = winner_id
        locked.save()
        committee = locked.committee
        if winner_id:
            committee.chairs.set([winner_id])   # the signal makes them a Chair member type
    if winner_id:
        from src.education_oversight import sync_name
        sync_name(committee)
        _log(user, f'{locked.winner.get_display_name()} was elected chair of {committee.name} '
                   f'({counts[winner_id]} of {sum(counts.values())} votes)', locked)
    else:
        _log(user, f'The chair election for {committee.name} closed without a winner '
                   f'({"a tie" if ranked else "no votes"})', locked)
    return locked


def cancel(election, user):
    if not can_close(election, user):
        raise ChairElectionError('forbidden', 'Only whoever opened it, the chair or a site admin can cancel it.', 403)
    if election.status != 'open':
        raise ChairElectionError('closed', 'This election is already closed.', 409)
    election.status, election.closed_at, election.closed_by = 'cancelled', timezone.now(), user
    election.save(update_fields=['status', 'closed_at', 'closed_by'])
    _log(user, f'{user.get_display_name()} cancelled the chair election for {election.committee.name}', election)
    return election


def state(committee, user):
    """The app's view of the committee's chair election."""
    election = current(committee) or latest(committee)
    voters = list(electorate(committee))
    chair = committee.chairs.order_by('name').first()
    out = {
        'allowed': allows(committee),
        'can_open': allows(committee) and current(committee) is None and (
            any(v.pk == user.pk for v in voters) or user.is_admin),
        'chair': {'user_id': chair.user_id, 'name': chair.get_display_name()} if chair else None,
        'candidates': [{'user_id': v.user_id, 'name': v.get_display_name()} for v in voters],
        'election': None,
    }
    if election is None:
        return out
    mine = election.ballots.filter(voter=user).first()
    names = {v.user_id: v.get_display_name() for v in voters}
    is_open = election.status == 'open'
    out['election'] = {
        'id': election.id,
        'status': election.status,
        'opened_at': election.opened_at.isoformat(),
        'opened_by': election.opened_by.get_display_name() if election.opened_by_id else None,
        'closed_at': election.closed_at.isoformat() if election.closed_at else None,
        'voted': election.ballots.count(),
        'electorate': len(voters),
        'my_vote': (names.get(mine.choice_id) or 'a former member') if mine else None,
        'can_vote': is_open and mine is None and any(v.pk == user.pk for v in voters),
        'can_close': is_open and can_close(election, user),
        'winner': ({'user_id': election.winner_id, 'name': election.winner.get_display_name()}
                   if election.winner_id else None),
        # Secret until it closes.
        'totals': ([{'name': names.get(uid, 'a former member'), 'votes': n}
                    for uid, n in sorted(election.totals.items(), key=lambda kv: -kv[1])]
                   if not is_open else None),
    }
    return out


def _log(user, text, election):
    from src.models import ActivityLog
    ActivityLog.log_activity(action_type='other', user=user, description=text,
                             metadata={'action': 'committee_chair_election', 'election_id': election.pk,
                                       'committee_id': election.committee_id})


def _notify_members(election, opener):
    from src.notification_service import create_notification
    for member in electorate(election.committee):
        if member.pk == opener.pk:
            continue
        create_notification(
            recipient=member, notification_type='announcement',
            title=f'Vote for the {election.committee.name} chair',
            message='A chair election is open. Cast your vote in the app.',
            link=f'/committee/{election.committee.code}/',
            push_title='Parliament', push_body=f'Vote for the {election.committee.name} chair')
