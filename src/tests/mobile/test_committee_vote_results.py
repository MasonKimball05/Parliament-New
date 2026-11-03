"""
v4.4.3 — a committee vote's result page, End Vote and runoffs in the app
(views_committee_management), and recalculation under the chapter documents
page's rule (chairs or officers).

The rules and the work are the web views' (src/committee_management), so
the web pages are exercised here too: same permissions, same messages, same
activity log. CONFIDENTIALITY_MATRIX: ``CommitteeVote`` on anonymous
legislation is tallies only — no voter names on either surface.

    python manage.py test src.tests.mobile.test_committee_vote_results
"""
from django.contrib.messages import get_messages
from django.urls import reverse
from django.utils import timezone

from src.models import ActivityLog, Committee, CommitteeLegislation, CommitteeVote
from src.tests.mobile.test_committee_management import _Base, _leg
from src.tests.mobile.test_home_and_notifications import flag


def _plurality(committee, poster, **extra):
    fields = dict(vote_mode='plurality', plurality_options=['A', 'B', 'C'], plurality_runoff_enabled=True,
                  plurality_runoff_count=2)
    fields.update(extra)
    return _leg(committee, poster, **fields)


def _ballot(leg, user, choice):
    return CommitteeVote.objects.create(user=user, legislation=leg, vote_choice=choice)


class ResultTests(_Base):

    def result(self, user, leg):
        return self.get(user, f'FIN/votes/{leg.id}/result/')

    def test_who_may_look(self):
        leg = _leg(self.c, self.chair)
        for user in (self.chair, self.member, self.voter):
            self.assertEqual(self.result(user, leg).status_code, 200, user.pk)
        # An open vote: not for an outsider, an officer or an admin who isn't on the committee …
        for user in (self.outsider, self.officer, self.admin):
            r = self.result(user, leg)
            self.assertEqual((r.status_code, r.json()['error']), (403, 'no_access'), user.pk)
        # … unless they voted on it; and once closed, for anyone (the web page's rule).
        _ballot(leg, self.outsider, 'yes')
        self.assertEqual(self.result(self.outsider, leg).status_code, 200)
        leg.voting_closed = True
        leg.save()
        self.assertEqual(self.result(self.officer, leg).status_code, 200)

    def test_yes_no_names_voters_unless_anonymous(self):
        leg = _leg(self.c, self.chair, required_percentage='51')
        _ballot(leg, self.voter, 'yes')
        _ballot(leg, self.member, 'no')
        body = self.result(self.chair, leg).json()
        self.assertEqual(body['choices'], [
            {'option': 'yes', 'count': 1, 'voters': [self.voter.name]},
            {'option': 'no', 'count': 1, 'voters': [self.member.name]},
            {'option': 'abstain', 'count': 0, 'voters': []},
        ])
        self.assertEqual((body['yes_percentage'], body['required_percentage'], body['passed']), (50.0, 51, False))
        self.assertTrue(body['can_end'])
        self.assertFalse(body['can_recalculate'])

        leg.anonymous_vote = True
        leg.save()
        body = self.result(self.chair, leg).json()
        self.assertEqual([(c['count'], c['voters']) for c in body['choices']], [(1, None), (1, None), (0, None)])
        self.assertNotIn(self.voter.name, str(body))
        self.assertNotIn(self.member.name, str(body))

    def test_plurality_tie_offers_a_runoff_to_chairs_and_the_poster(self):
        leg = _plurality(self.c, self.member, voting_closed=True, anonymous_vote=True)
        _ballot(leg, self.voter, 'A')
        _ballot(leg, self.chair, 'B')
        body = self.result(self.chair, leg).json()
        self.assertEqual([(o['option'], o['count'], o['voters']) for o in body['options']],
                         [('A', 1, None), ('B', 1, None), ('C', 0, None)])
        self.assertEqual((body['has_tie'], body['tied_options'], body['winning_option']), (True, ['A', 'B'], None))
        self.assertEqual(body['top_options_for_runoff'], ['A', 'B'])
        self.assertTrue(body['can_create_runoff'])
        self.assertTrue(self.result(self.member, leg).json()['can_create_runoff'])   # the poster
        self.assertFalse(self.result(self.voter, leg).json()['can_create_runoff'])
        self.assertNotIn(self.voter.name, str(body))

    def test_web_page_matches(self):
        leg = _plurality(self.c, self.chair, voting_closed=True, anonymous_vote=True)
        _ballot(leg, self.voter, 'A')
        self.client.force_login(self.chair)
        r = self.client.get(reverse('committee_vote_result', args=['FIN', leg.id]))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context['winning_option'], 'A')
        self.assertNotContains(r, self.voter.name)
        open_leg = _leg(self.c, self.chair)
        _ballot(open_leg, self.voter, 'yes')
        r = self.client.get(reverse('committee_vote_result', args=['FIN', open_leg.id]))
        self.assertContains(r, self.voter.name)
        self.client.force_login(self.outsider)
        r = self.client.get(reverse('committee_vote_result', args=['FIN', open_leg.id]))
        self.assertRedirects(r, reverse('vote', args=['FIN']), fetch_redirect_response=False)

    def test_scoped_to_the_committee_and_flag(self):
        other = Committee.objects.create(name='Other', code='OTH')
        leg = _leg(other, self.chair, voting_closed=True)
        self.assertEqual(self.result(self.chair, leg).status_code, 404)
        flag('committee_voting', False)
        own = _leg(self.c, self.chair)
        self.assertEqual(self.result(self.chair, own).json()['error'], 'feature_disabled')

    def test_ballot_list_has_voting_history(self):
        _leg(self.c, self.chair, title='Done', voting_closed=True, passed=True, voting_ended_at=timezone.now())
        _leg(self.c, self.chair, title='Open')
        body = self.get(self.voter, 'FIN/votes/').json()
        self.assertEqual([v['title'] for v in body['votes']], ['Open'])
        self.assertEqual([(v['title'], v['passed']) for v in body['closed']], [('Done', True)])


class EndVoteTests(_Base):

    def end(self, user, leg):
        return self.post(user, f'FIN/votes/{leg.id}/end/')

    def test_chair_or_poster(self):
        leg = _leg(self.c, self.member, required_percentage='51')
        _ballot(leg, self.voter, 'yes')
        for user in (self.voter, self.officer, self.admin, self.outsider):
            r = self.end(user, leg)
            self.assertEqual((r.status_code, r.json()['error']), (403, 'no_access'), user.pk)
        r = self.end(self.member, leg)   # posted it
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['result'], 'passed')
        self.assertEqual(r.json()['message'], "Voting on 'Budget' has been ended. The vote passed.")
        leg.refresh_from_db()
        self.assertEqual((leg.voting_closed, leg.passed, leg.status), (True, True, 'passed'))
        self.assertIsNotNone(leg.voting_ended_at)
        log = ActivityLog.objects.get(action_type='vote_ended', object_id=leg.id)
        self.assertEqual(log.metadata['vote_tally'], {'yes': 1, 'no': 0, 'abstain': 0})

    def test_ends_once(self):
        leg = _leg(self.c, self.chair)
        self.assertEqual(self.end(self.chair, leg).status_code, 200)
        r = self.end(self.chair, leg)
        self.assertEqual((r.status_code, r.json()['error'], r.json()['detail']),
                         (409, 'already_closed', 'Voting has already been closed.'))
        self.assertEqual(ActivityLog.objects.filter(action_type='vote_ended').count(), 1)

    def test_anonymous_log_has_no_tally(self):
        leg = _leg(self.c, self.chair, anonymous_vote=True)
        _ballot(leg, self.voter, 'no')
        self.assertEqual(self.end(self.chair, leg).json()['result'], 'did not pass')
        self.assertNotIn('vote_tally', ActivityLog.objects.get(action_type='vote_ended').metadata)

    def test_manage_list_says_who_can_end(self):
        _leg(self.c, self.chair)
        self.assertTrue(self.get(self.chair, 'FIN/votes/manage/').json()['open'][0]['can_end'])

    def test_web_vote_page_still_ends(self):
        leg = _leg(self.c, self.chair)
        _ballot(leg, self.voter, 'yes')
        self.client.force_login(self.chair)
        r = self.client.post(reverse('vote', args=['FIN']), {'end_vote': '1', 'legislation_id': leg.id})
        self.assertRedirects(r, reverse('vote', args=['FIN']), fetch_redirect_response=False)
        leg.refresh_from_db()
        self.assertTrue(leg.voting_closed and leg.passed)
        r = self.client.post(reverse('vote', args=['FIN']), {'end_vote': '1', 'legislation_id': leg.id})
        self.assertIn('Voting has already been closed.', [str(m) for m in get_messages(r.wsgi_request)])
        self.client.force_login(self.voter)
        other = _leg(self.c, self.chair)
        self.client.post(reverse('vote', args=['FIN']), {'end_vote': '1', 'legislation_id': other.id})
        other.refresh_from_db()
        self.assertFalse(other.voting_closed)


class RunoffTests(_Base):

    def runoff(self, user, leg):
        return self.post(user, f'FIN/votes/{leg.id}/runoff/')

    def tied(self, **extra):
        leg = _plurality(self.c, self.member, voting_closed=True, **extra)
        _ballot(leg, self.voter, 'A')
        _ballot(leg, self.chair, 'B')
        return leg

    def test_creates_a_runoff(self):
        leg = self.tied(anonymous_vote=True)
        for user in (self.voter, self.officer, self.admin):
            self.assertEqual(self.runoff(user, leg).status_code, 403, user.pk)
        r = self.runoff(self.member, leg)   # the poster
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()['options'], ['A', 'B'])
        self.assertEqual(r.json()['message'], 'Runoff vote created with top 2 options: A, B')
        runoff = CommitteeLegislation.objects.get(plurality_parent=leg)
        self.assertEqual((runoff.title, runoff.plurality_options, runoff.anonymous_vote, runoff.posted_by),
                         ('Runoff: Budget', ['A', 'B'], True, self.member))
        self.assertTrue(runoff.plurality_is_runoff)
        self.assertFalse(runoff.plurality_runoff_enabled)
        self.assertTrue(ActivityLog.objects.filter(object_id=runoff.id, metadata__is_runoff=True).exists())
        r = self.runoff(self.chair, leg)
        self.assertEqual((r.status_code, r.json()['detail']),
                         (409, 'A runoff vote has already been created for this legislation.'))
        # The result page lists it and stops offering another.
        body = self.get(self.chair, f'FIN/votes/{leg.id}/result/').json()
        self.assertEqual([x['id'] for x in body['vote']['runoffs']], [runoff.id])
        self.assertFalse(body['can_create_runoff'])
        self.assertEqual(self.get(self.chair, f'FIN/votes/{runoff.id}/result/').json()['vote']['parent']['id'], leg.id)

    def test_refusals_are_the_webs(self):
        cases = [
            (_leg(self.c, self.chair, voting_closed=True), 'Runoff votes can only be created for plurality votes.'),
            (_plurality(self.c, self.chair, voting_closed=True, plurality_runoff_enabled=False),
             'Runoff voting is not enabled for this legislation.'),
            (_plurality(self.c, self.chair), 'The original vote must be closed before creating a runoff.'),
            (_plurality(self.c, self.chair, voting_closed=True, plurality_options=['A']),
             'Not enough options for a runoff vote.'),
        ]
        for leg, message in cases:
            r = self.runoff(self.chair, leg)
            self.assertEqual((r.status_code, r.json().get('detail')), (409, message), r.json())
        self.assertFalse(CommitteeLegislation.objects.filter(plurality_is_runoff=True).exists())

    def test_web_runoff_still_works(self):
        leg = self.tied()
        self.client.force_login(self.voter)
        self.client.post(reverse('create_committee_runoff', args=['FIN', leg.id]))
        self.assertFalse(CommitteeLegislation.objects.filter(plurality_parent=leg).exists())
        self.client.force_login(self.chair)
        r = self.client.post(reverse('create_committee_runoff', args=['FIN', leg.id]))
        self.assertRedirects(r, reverse('vote', args=['FIN']), fetch_redirect_response=False)
        self.assertTrue(CommitteeLegislation.objects.filter(plurality_parent=leg).exists())
        r = self.client.post(reverse('create_committee_runoff', args=['FIN', leg.id]))
        self.assertIn('A runoff vote has already been created for this legislation.',
                      [str(m) for m in get_messages(r.wsgi_request)])


class RecalculateTests(_Base):

    def test_officer_may_recalculate_like_on_the_website(self):
        leg = _leg(self.c, self.chair, voting_closed=True)
        _ballot(leg, self.voter, 'yes')
        self.assertEqual(self.post(self.member, f'FIN/votes/{leg.id}/recalculate/').status_code, 403)
        self.assertEqual(self.post(self.officer, f'FIN/votes/{leg.id}/recalculate/').json()['result'], 'passed')
        body = self.get(self.officer, f'FIN/votes/{leg.id}/result/').json()
        self.assertTrue(body['can_recalculate'])

    def test_open_vote(self):
        leg = _leg(self.c, self.chair)
        r = self.post(self.chair, f'FIN/votes/{leg.id}/recalculate/')
        self.assertEqual((r.status_code, r.json()['detail']), (409, 'Cannot recalculate - voting is still open.'))

    def test_web_standalone_recalculate_shares_the_code(self):
        leg = _leg(self.c, self.chair, voting_closed=True)
        _ballot(leg, self.voter, 'no')
        self.client.force_login(self.member)
        self.client.post(reverse('recalculate_committee_vote', args=[leg.id]))
        self.assertFalse(ActivityLog.objects.filter(object_id=leg.id).exists())
        self.client.force_login(self.officer)
        r = self.client.post(reverse('recalculate_committee_vote', args=[leg.id]))
        self.assertRedirects(r, reverse('chapter_documents'), fetch_redirect_response=False)
        leg.refresh_from_db()
        self.assertEqual((leg.passed, leg.status), (False, 'draft'))
        self.assertEqual(ActivityLog.objects.get(object_id=leg.id).metadata['action'], 'recalculate')
