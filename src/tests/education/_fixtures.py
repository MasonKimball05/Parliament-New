"""
Shared fixtures for the education-domain test suite.

Extracted from `test_education_scoring_and_meetings.py` (v3.20.0) so
`test_pledge_task_entitlement.py` and `test_quiz_analysis_threshold.py` stop
importing helpers out of a sibling *test* module — they now import from here
instead, alongside the module that originally defined them.
"""

from django.test import Client

from src.models import Committee, ParliamentUser


def make_user(uid, name='Test User', member_type='Member', **kwargs):
    user = ParliamentUser.objects.create(
        user_id=uid, username=uid, name=name,
        member_type=member_type, member_status='Active', **kwargs
    )
    user.set_password('education-test-pass-12345!')
    user.save()
    return user


class EducationFixtureMixin:
    def build(self):
        self.committee = Committee.objects.create(
            name='Education', code='EDUCATION',
            is_active=True, is_education_committee=True,
        )
        # ⚠️ PLEDGE IDS ARE NOT NUMERIC, AND ASSUMING THEY WERE SHIPPED A 500.
        #
        # `ParliamentUser.user_id` is a CharField primary key. Initiated
        # brothers carry a roll number, but a PLEDGE carries something like
        # `P-C7JKZY` until initiation (CLAUDE.md's "pledge initiation user ID"
        # note is about exactly that migration).
        #
        # The first version of this fixture used numeric ids and carried a
        # comment claiming they were realistic. They are not, and the education
        # URLs declared `<int:pledge_pk>` — so `education_pledge_detail`
        # raised `NoReverseMatch` on the real dashboard the moment a real
        # pledge existed, while every test here passed. The completion-grid
        # toggle had the same defect and had presumably never worked for a real
        # pledge either; it built its URL in JavaScript, so it 404'd quietly
        # instead of raising.
        #
        # **A fixture that is easier than production is a fixture that tests
        # something else.** These ids are now shaped like the real thing, which
        # is what makes the `<str:>` routes load-bearing here.
        self.chair = make_user('9001', 'Edu Chair', member_type='Officer')
        self.committee.chairs.add(self.chair)
        self.pledge = make_user('P-C7JKZY', 'Pledge One', member_type='Pledge')
        self.other_pledge = make_user('P-9QW2LM', 'Pledge Two', member_type='Pledge')
        self.brother = make_user('9004', 'A Brother')

        self.client = Client()
        self.client.force_login(self.chair)
