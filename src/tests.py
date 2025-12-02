from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from .models import *
import random
from datetime import timedelta

class EndVoteTestCase(TestCase):
    def setUp(self):
        self.client = Client()

        self.uploader = ParliamentUser.objects.create_user(
            user_id='100', name='Uploader', username='Uploader', member_type='Chair'
        )
        self.other_user = ParliamentUser.objects.create_user(
            user_id='101', name='Other', username='Other', member_type='Member'
        )

        self.legislation = Legislation.objects.create(
            title='Test Bill',
            description='Test Description',
            document='test.pdf',
            posted_by=self.uploader,
            available_at=timezone.now()
        )

    def test_only_uploader_can_end_vote(self):
        # Login as uploader and end the vote
        self.client.force_login(self.uploader)
        response = self.client.post(reverse('end_vote', args=[self.legislation.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Test Bill')

        # Login as another user and try to end the vote
        self.client.logout()
        self.client.force_login(self.other_user)
        response = self.client.post(reverse('end_vote', args=[self.legislation.id]))
        self.assertEqual(response.status_code, 403)  # Forbidden

class VoteTallyTestCase(TestCase):
    def setUp(self):
        print("\n=== Setting up VoteTallyTestCase ===")

        # Create uploader
        self.uploader = ParliamentUser.objects.create_user(
            user_id="uploader1",
            name="Uploader",
            username="uploader",
            member_type="Chair"
        )
        print("Created uploader:", self.uploader.username)

        # Randomly decide if abstain is allowed
        self.allow_abstain = random.choice([True, False])
        print("Allow abstain:", self.allow_abstain)

        # Create legislation
        self.legislation = Legislation.objects.create(
            title="Random Vote Test Bill",
            description="Testing random votes",
            posted_by=self.uploader,
            available_at=timezone.now(),
            anonymous_vote=False,
            allow_abstain=self.allow_abstain,
            document="dummy.docx"
        )
        print("Created legislation:", self.legislation.title)

        # Create up to 15 voters and assign random votes
        self.votes_cast = {"yes": 0, "no": 0, "abstain": 0}
        choices = ["yes", "no", "abstain"] if self.allow_abstain else ["yes", "no"]

        self.total_voters = random.randint(5, 15)
        print(f"Creating {self.total_voters} voters with votes from {choices}")

        for i in range(self.total_voters):
            user = ParliamentUser.objects.create_user(
                user_id=f"user_{i}",
                name=f"User {i}",
                username=f"user_{i}",
                member_type="Member"
            )
            Attendance.objects.create(user=user, present=True)

            choice = random.choice(choices)
            self.votes_cast[choice] += 1
            Vote.objects.create(user=user, legislation=self.legislation, vote_choice=choice)
            print(f"User {user.username} voted: {choice}")

    def test_vote_summary_counts(self):
        print("\n=== Running test_vote_summary_counts ===")
        self.client.force_login(self.uploader)
        print("Logged in as uploader.")

        response = self.client.post(reverse('end_vote', args=[self.legislation.id]))
        print("POSTed to end_vote")

        self.assertEqual(response.status_code, 200)
        print("Received 200 OK response")

        # Extract vote summary from context
        context = response.context
        summary = {item['vote_choice']: item['count'] for item in context['summary']}
        print("Vote summary from context:", summary)

        for choice in ["yes", "no", "abstain"]:
            expected = self.votes_cast[choice]
            actual = summary.get(choice, 0)
            print(f"Checking {choice}: expected={expected}, actual={actual}")
            self.assertEqual(actual, expected, f"Mismatch for {choice} votes: expected {expected}, got {actual}")


class AnonymousVoteBehaviorTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        self.uploader = ParliamentUser.objects.create_user(
            user_id='uploader1',
            name='Uploader',
            username='Uploader',
            member_type='Chair'
        )

        # Login as uploader
        self.client.force_login(self.uploader)

        # Random flags
        self.is_anonymous = random.choice([True, False])
        self.allow_abstain = random.choice([True, False])
        print("=== Running test_anonymous_vote_behavior ===")
        print(f"Anonymous: {self.is_anonymous}, Allow Abstain: {self.allow_abstain}")

        # Create legislation
        self.legislation = Legislation.objects.create(
            title="Test Anonymous Vote",
            description="Testing anonymity of votes.",
            document="",
            posted_by=self.uploader,
            available_at=timezone.now() - timedelta(days=1),
            anonymous_vote=self.is_anonymous,
            allow_abstain=self.allow_abstain
        )

        # Add voters and votes
        self.voters = []
        self.choices = ['yes', 'no']
        if self.allow_abstain:
            self.choices.append('abstain')

        for i in range(10):
            voter = ParliamentUser.objects.create_user(
                user_id=f'user{i}',
                name=f'User {i}',
                username=f'User{i}',
                member_type='Member'
            )
            self.voters.append(voter)
            Attendance.objects.create(user=voter, present=True)

            vote_choice = random.choice(self.choices)
            print(f"Voter {voter.username} voting {vote_choice}")
            Vote.objects.get_or_create(user=voter, legislation=self.legislation, vote_choice=vote_choice)

    def test_vote_display_behavior(self):
        response = self.client.post(reverse('end_vote', args=[self.legislation.id]))
        self.assertEqual(response.status_code, 200)
        print("Response OK")

        context = response.context
        anonymous = context.get('anonymous')
        print(f"Anonymous in context: {anonymous}")

        if not anonymous:
            self.assertIn('in_favor', context)
            self.assertIn('against', context)
            if self.allow_abstain:
                self.assertIn('abstain', context)
            print("Vote lists present in context")
        else:
            self.assertNotIn('in_favor', context)
            self.assertNotIn('against', context)
            self.assertNotIn('abstain', context)
            print("No voter lists shown (anonymous)")

        print("Test passed.")


class ComprehensiveVoteTestCase(TestCase):
    def setUp(self):
        self.uploader = ParliamentUser.objects.create_user(
            user_id='100', name='Chair User', username='chair', member_type='Chair'
        )
        self.uploader.set_password('testpass')
        self.uploader.save()
        self.client.force_login(self.uploader)

        self.required_percent = random.choice(['51', '60', '67', '75', '100'])
        self.anonymous = random.choice([True, False])
        self.allow_abstain = random.choice([True, False])

        self.legislation = Legislation.objects.create(
            title="Unified Voting Act",
            description="A bill to unify all tests.",
            document="test_doc.pdf",
            posted_by=self.uploader,
            available_at=timezone.now(),
            anonymous_vote=self.anonymous,
            allow_abstain=self.allow_abstain,
            required_percentage=self.required_percent,
        )

        self.voters = []
        self.present_count = random.randint(5, 15)

        for i in range(self.present_count):
            user = ParliamentUser.objects.create_user(
                user_id=str(200 + i),
                name=f'Voter {i}',
                username=f'voter{i}',
                member_type='Member'
            )
            Attendance.objects.create(user=user, date=timezone.now().date(), present=True)
            self.voters.append(user)

    def test_vote_result_threshold_and_display(self):
        print(f"\n=== Running test_vote_result_threshold_and_display ===")
        print(f"Required threshold: {self.required_percent}%")
        print(f"Anonymous: {self.anonymous}, Allow Abstain: {self.allow_abstain}")

        choices = ['yes', 'no']
        if self.allow_abstain:
            choices.append('abstain')

        yes_count = 0
        for i, voter in enumerate(self.voters):
            choice = random.choice(choices)
            if choice == 'yes':
                yes_count += 1

            Vote.objects.create(user=voter, legislation=self.legislation, vote_choice=choice)
            print(f"Voter {i} voted {choice}")

        response = self.client.post(reverse('end_vote', args=[self.legislation.id]))
        self.assertEqual(response.status_code, 200)

        context = response.context
        summary = {entry['vote_choice']: entry['count'] for entry in context['summary']}
        print("Vote summary:", summary)

        total_votes = sum(summary.get(k, 0) for k in ['yes', 'no', 'abstain'] if k in summary)
        required_ratio = int(self.required_percent) / 100
        passed = summary.get('yes', 0) / total_votes >= required_ratio if total_votes else False
        print("Vote passed:", passed)

        self.assertEqual(context['anonymous'], self.anonymous)
        if not self.anonymous:
            self.assertIn('in_favor', context)
            self.assertIn('against', context)
            if self.allow_abstain:
                self.assertIn('abstain', context)

        print("=== Test completed ===")


class PiecewiseVotingTestCase(TestCase):
    def setUp(self):
        self.uploader = ParliamentUser.objects.create_user(
            user_id='900', name='Test Chair', username='testchair', member_type='Chair'
        )
        self.client.force_login(self.uploader)

    def create_legislation(self, required_number):
        return Legislation.objects.create(
            title="Piecewise Vote Test",
            description="Testing piecewise logic.",
            posted_by=self.uploader,
            available_at=timezone.now(),
            vote_mode='piecewise',
            required_number=required_number,
            document="piecewise.pdf"
        )

    def test_passes_with_enough_yes_votes(self):
        legislation = self.create_legislation(required_number=3)
        for i in range(3):
            voter = ParliamentUser.objects.create_user(
                user_id=f'p{i}', name=f'Piece Voter {i}', username=f'piece{i}', member_type='Member'
            )
            Attendance.objects.create(user=voter, present=True)
            Vote.objects.create(user=voter, legislation=legislation, vote_choice='yes')
            print(f"Voter {voter.username} voted yes")

        response = self.client.post(reverse('end_vote', args=[legislation.id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['passed'], True)
        print("=== Test completed ===")
        print("✅ test_passes_with_enough_yes_votes: Passed")

    def test_fails_with_insufficient_yes_votes(self):
        legislation = self.create_legislation(required_number=3)
        for i in range(2):
            voter = ParliamentUser.objects.create_user(
                user_id=f'f{i}', name=f'Fail Voter {i}', username=f'fail{i}', member_type='Member'
            )
            Attendance.objects.create(user=voter, present=True)
            Vote.objects.create(user=voter, legislation=legislation, vote_choice='yes')
            print(f"Voter {voter.username} voted yes")

        response = self.client.post(reverse('end_vote', args=[legislation.id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['passed'], False)
        print("Vote failed as expected")
        print("✅ test_fails_with_insufficient_yes_votes: Passed")

class PluralityTestVoting(TestCase):
    def setUp(self):
        self.uploader = ParliamentUser.objects.create_user(user_id='900', name='Test Chair', username='testchair', member_type='Chair')
        self.client.force_login(self.uploader)

    def create_legislation(self, vote_mode):
        return Legislation.objects.create(
            title="Plurality Vote Test",
            description="Testing plurality logic.",
            posted_by=self.uploader,
            available_at=timezone.now(),
            vote_mode='plurality',
            document="plurality.pdf"
        )

    def test_passes_with_highest_votes(self):
        legislation = self.create_legislation(vote_mode='plurality')
        for i in range(5):
            voter = ParliamentUser.objects.create_user(user_id=f'p{i}', name=f'Plurality Voter {i}', username=f'plurality{i}', member_type='Member')
            Attendance.objects.create(user=voter, present=True)
            Vote.objects.create(user=voter, legislation=legislation, vote_choice='yes')

        response = self.client.post(reverse('end_vote', args=[legislation.id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['passed'], True)
        print("✅ test_passes_with_highest_votes: Passed")