"""
Mason: "can we update the poll UI, it's really basic and kinda looks weak."

`templates/announcement_poll.html` was rewritten for visual polish (a
gradient header banner, status/anonymous pills, and card-style
peer-checked radio/checkbox options mirroring the pattern already
established in `templates/vote.html` for legislative voting) — but the
form's actual field names/ids/values were deliberately left untouched, so
`take_poll`'s POST handling needs no changes. This file exists to prove
that: the redesign is markup/CSS only, and submitting a poll through the
real view still records the right answer for every question type.
"""
import re

from django.test import Client, TestCase
from django.urls import reverse

from src.models import (
    Announcement, AnnouncementPoll, AnnouncementPollQuestion,
    AnnouncementPollOption, AnnouncementPollResponse, ParliamentUser,
)


def make_officer(uid='takepoll-officer'):
    return ParliamentUser.objects.create_user(
        user_id=uid, name='Poll Officer', username=uid, member_type='Officer',
        password='testpass123',
    )


def make_member(uid='takepoll-member'):
    return ParliamentUser.objects.create_user(
        user_id=uid, name='Poll Member', username=uid, member_type='Member',
        password='testpass123',
    )


def make_full_poll(officer, is_open=True, is_anonymous=False):
    announcement = Announcement.objects.create(
        title='Chapter Retreat Planning', content='...', posted_by=officer,
    )
    poll = AnnouncementPoll.objects.create(
        announcement=announcement, created_by=officer,
        title='Retreat Location Preference', description='Pick one.',
        is_open=is_open, is_anonymous=is_anonymous,
    )
    single_q = AnnouncementPollQuestion.objects.create(
        poll=poll, text='Where should we go?', question_type='single', order=0,
    )
    opt_a = AnnouncementPollOption.objects.create(question=single_q, text='Lake house', order=0)
    opt_b = AnnouncementPollOption.objects.create(question=single_q, text='Mountain cabin', order=1)

    multi_q = AnnouncementPollQuestion.objects.create(
        poll=poll, text='Which activities interest you?', question_type='multiple', order=1,
        is_required=False,
    )
    opt_c = AnnouncementPollOption.objects.create(question=multi_q, text='Hiking', order=0)
    opt_d = AnnouncementPollOption.objects.create(question=multi_q, text='Swimming', order=1)

    text_q = AnnouncementPollQuestion.objects.create(
        poll=poll, text='Anything else?', question_type='text', order=2, is_required=False,
    )
    return announcement, poll, {
        'single_q': single_q, 'opt_a': opt_a, 'opt_b': opt_b,
        'multi_q': multi_q, 'opt_c': opt_c, 'opt_d': opt_d,
        'text_q': text_q,
    }


class TakePollPageRendersTests(TestCase):
    def setUp(self):
        self.officer = make_officer()
        self.member = make_member()
        self.client = Client()
        self.client.login(username=self.member.username, password='testpass123')

    def test_open_poll_renders_title_description_and_questions(self):
        announcement, poll, parts = make_full_poll(self.officer)
        response = self.client.get(reverse('take_poll', args=[announcement.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Retreat Location Preference')
        self.assertContains(response, 'Pick one.')
        self.assertContains(response, 'Where should we go?')
        self.assertContains(response, 'Lake house')
        self.assertContains(response, 'Mountain cabin')
        # The actual submit control the view depends on must still exist.
        self.assertContains(response, f'name="q_{parts["single_q"].id}"')
        self.assertContains(response, f'value="{parts["opt_a"].id}"')

    def test_anonymous_poll_shows_anonymous_badge(self):
        announcement, poll, _ = make_full_poll(self.officer, is_anonymous=True)
        response = self.client.get(reverse('take_poll', args=[announcement.id]))
        self.assertContains(response, 'Anonymous responses')

    def test_closed_poll_shows_closed_state_not_the_form(self):
        announcement, poll, parts = make_full_poll(self.officer, is_open=False)
        response = self.client.get(reverse('take_poll', args=[announcement.id]))
        self.assertContains(response, 'no longer accepting responses')
        self.assertNotContains(response, f'name="q_{parts["single_q"].id}"')

    def test_closed_poll_the_member_already_answered_still_shows_the_closed_card(self):
        """v3.31.3: editing is only possible while the poll is still open —
        once closed, a prior respondent sees the same static closed card as
        anyone else, not the form."""
        announcement, poll, parts = make_full_poll(self.officer, is_open=False)
        AnnouncementPollResponse.objects.create(poll=poll, respondent=self.member)
        response = self.client.get(reverse('take_poll', args=[announcement.id]))
        self.assertContains(response, 'no longer accepting responses')
        self.assertContains(response, 'recorded before it closed')
        self.assertNotContains(response, f'name="q_{parts["single_q"].id}"')

    def test_already_responded_on_an_open_poll_shows_the_editable_form_prefilled(self):
        """v3.31.3: Mason asked for polls to allow a member to change their
        answer. While the poll is still open, a respondent gets the same
        form back (not a static "thanks" card), pre-filled with what they
        picked, and can resubmit to update it."""
        announcement, poll, parts = make_full_poll(self.officer)
        resp = AnnouncementPollResponse.objects.create(poll=poll, respondent=self.member)
        answer = resp.answers.create(question=parts['single_q'])
        answer.selected_options.add(parts['opt_a'])

        response = self.client.get(reverse('take_poll', args=[announcement.id]))
        html = response.content.decode()
        self.assertIn("already responded to this poll", html)
        self.assertIn(f'name="q_{parts["single_q"].id}"', html)
        self.assertIn('Update Response', html)

        # The previously-selected option's <input> is re-rendered checked;
        # the other option in the same question is not. Matched by id rather
        # than an exact string of surrounding markup/whitespace, since the
        # latter breaks on any unrelated markup tweak.
        opt_a_input = re.search(
            rf'<input[^>]*id="q{parts["single_q"].id}_opt{parts["opt_a"].id}"[^>]*>', html,
        )
        opt_b_input = re.search(
            rf'<input[^>]*id="q{parts["single_q"].id}_opt{parts["opt_b"].id}"[^>]*>', html,
        )
        self.assertIsNotNone(opt_a_input)
        self.assertIsNotNone(opt_b_input)
        self.assertIn('checked', opt_a_input.group(0))
        self.assertNotIn('checked', opt_b_input.group(0))


class TakePollSubmissionStillWorksTests(TestCase):
    """The redesign only touched markup/CSS — prove the real POST path
    (single/multiple/text answers) is unaffected end to end."""

    def setUp(self):
        self.officer = make_officer('takepoll-officer-2')
        self.member = make_member('takepoll-member-2')
        self.client = Client()
        self.client.login(username=self.member.username, password='testpass123')

    def test_submitting_all_question_types_records_the_right_answers(self):
        announcement, poll, parts = make_full_poll(self.officer)

        response = self.client.post(reverse('take_poll', args=[announcement.id]), {
            f'q_{parts["single_q"].id}': str(parts['opt_a'].id),
            f'q_{parts["multi_q"].id}': [str(parts['opt_c'].id), str(parts['opt_d'].id)],
            f'q_{parts["text_q"].id}': 'Looking forward to it!',
        })
        self.assertRedirects(response, reverse('poll_confirmation', args=[announcement.id]))

        resp = AnnouncementPollResponse.objects.get(poll=poll, respondent=self.member)
        answers = {a.question_id: a for a in resp.answers.all()}

        single_answer = answers[parts['single_q'].id]
        self.assertEqual(list(single_answer.selected_options.values_list('id', flat=True)), [parts['opt_a'].id])

        multi_answer = answers[parts['multi_q'].id]
        self.assertEqual(
            set(multi_answer.selected_options.values_list('id', flat=True)),
            {parts['opt_c'].id, parts['opt_d'].id},
        )

        text_answer = answers[parts['text_q'].id]
        self.assertEqual(text_answer.text_answer, 'Looking forward to it!')

    def test_resubmitting_updates_the_existing_response_rather_than_duplicating(self):
        """v3.31.3: a second submission while the poll is still open no
        longer gets rejected — it replaces the respondent's answers on the
        same row. `unique_together = ['poll', 'respondent']` is unchanged
        and still holds: there is still exactly one response row."""
        announcement, poll, parts = make_full_poll(self.officer)
        original = AnnouncementPollResponse.objects.create(poll=poll, respondent=self.member)
        original_pk = original.pk
        original_answer = original.answers.create(question=parts['single_q'])
        original_answer.selected_options.add(parts['opt_a'])
        self.assertIsNone(original.updated_at)

        response = self.client.post(reverse('take_poll', args=[announcement.id]), {
            f'q_{parts["single_q"].id}': str(parts['opt_b'].id),
        })
        self.assertRedirects(response, reverse('poll_confirmation', args=[announcement.id]))

        # Still exactly one response row for this (poll, respondent) pair.
        self.assertEqual(AnnouncementPollResponse.objects.filter(poll=poll, respondent=self.member).count(), 1)

        original.refresh_from_db()
        self.assertEqual(original.pk, original_pk)  # same row, not a new one
        self.assertIsNotNone(original.updated_at)

        answer = original.answers.get(question=parts['single_q'])
        self.assertEqual(list(answer.selected_options.values_list('id', flat=True)), [parts['opt_b'].id])
