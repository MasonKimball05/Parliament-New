"""
A committee electing its chair from the app (11-16-26; rules in
src/committee_chair_election.py).

* ``GET  committees/<code>/chair-election/`` → the current or last election,
  the candidates (the committee's members) and what I can do.
* ``POST committees/<code>/chair-election/`` ``{action: open | vote | close |
  cancel, user_id?}`` → the same, after the action. ``vote`` needs ``user_id``.
"""
from rest_framework.response import Response

from src import committee_chair_election as ce
from src.mobile.views_chat import ChatRefused
from src.mobile.views_committees import _CommitteeView
from src.mobile.views_excuses_polls import BadRequest

ACTIONS = ('open', 'vote', 'close', 'cancel')


class ChairElectionView(_CommitteeView):

    def get(self, request, code):
        committee, _ = self.committee(request, code)
        return Response(ce.state(committee, request.user))

    def post(self, request, code):
        committee, _ = self.committee(request, code)
        data = request.data if isinstance(request.data, dict) else {}
        action = data.get('action')
        if action not in ACTIONS:
            raise BadRequest(f'action must be one of: {", ".join(ACTIONS)}', code='invalid_input')
        try:
            if action == 'open':
                ce.open_election(committee, request.user)
            else:
                election = ce.current(committee)
                if election is None:
                    raise ce.ChairElectionError('no_election', 'There’s no chair election open.', 404)
                if action == 'vote':
                    ce.vote(election, request.user, data.get('user_id'))
                elif action == 'close':
                    ce.close(election, request.user)
                else:
                    ce.cancel(election, request.user)
        except ce.ChairElectionError as exc:
            if exc.code == 'invalid_choice':
                raise BadRequest(exc.message, code='invalid_input')
            raise ChatRefused(exc.code, exc.message, exc.status)
        committee.refresh_from_db()
        return Response(ce.state(committee, request.user))
