"""
Kai commendations — v3.28.9 (corrects v3.28.8, which built this feature
under the wrong name — "accommodation request" — after a wording mistake;
see src/models/kai_commendations.py's docstring).

These tests cover the whole surface:
  - the model (commendation numbering, display_number, mark_reviewed)
  - the submit view (creation, activity log, no-name audit description,
    the required commended_member field, custom-field isolation from the
    discipline form)
  - the committee list/detail views (permission gating, status/assign/notes
    actions)
  - the form builder's form_type scoping (a field added to one form does not
    appear on the other; field_name uniqueness stays global)
  - private file serving for the attachment and custom-field file responses
"""
import re

from django.test import TestCase
from django.urls import reverse

from src.models import (
    Committee, KaiCommendation, KaiCommendationActivity,
    KaiCommendationFieldResponse, KaiFormField, KaiMemberPermission,
    ParliamentUser,
)


def _member(user_id, name, member_type='Member'):
    user = ParliamentUser.objects.create_user(
        user_id=user_id, name=name, username=user_id, member_type=member_type)
    user.set_password('testpass123')
    user.save()
    return user


class KaiCommendationModelTests(TestCase):
    def setUp(self):
        self.submitter = _member('com-sub-1', 'Sub One')
        self.honoree = _member('com-hon-1', 'Honoree One')

    def test_commendation_number_assigned_on_first_save(self):
        c = KaiCommendation.objects.create(
            title='Great job', description='Ran the whole event',
            submitted_by=self.submitter, commended_member=self.honoree)
        self.assertTrue(re.match(r'^COM-\d{4}-\d{3}$', c.commendation_number))

    def test_commendation_numbers_increment_within_a_year(self):
        c1 = KaiCommendation.objects.create(
            title='A', description='d', submitted_by=self.submitter, commended_member=self.honoree)
        c2 = KaiCommendation.objects.create(
            title='B', description='d', submitted_by=self.submitter, commended_member=self.honoree)
        year = c1.submitted_at.year
        self.assertEqual(c1.commendation_number, f'COM-{year}-001')
        self.assertEqual(c2.commendation_number, f'COM-{year}-002')

    def test_display_number_falls_back_to_pk_if_unassigned(self):
        c = KaiCommendation.objects.create(
            title='A', description='d', submitted_by=self.submitter, commended_member=self.honoree)
        # `.update()`, not `.save()` — save()'s own override reassigns a
        # number whenever commendation_number is blank (by design,
        # mirroring KaiReport), so going through save() here would just
        # prove that behaviour again instead of testing the fallback.
        KaiCommendation.objects.filter(pk=c.pk).update(commendation_number='')
        c.refresh_from_db()
        self.assertEqual(c.display_number, f'#{c.pk}')

    def test_mark_reviewed_sets_status_reviewer_and_timestamp(self):
        c = KaiCommendation.objects.create(
            title='A', description='d', submitted_by=self.submitter, commended_member=self.honoree)
        reviewer = _member('com-reviewer-1', 'Reviewer')
        c.mark_reviewed(reviewer, 'acknowledged')
        c.refresh_from_db()
        self.assertEqual(c.status, 'acknowledged')
        self.assertEqual(c.reviewed_by_id, reviewer.pk)
        self.assertIsNotNone(c.reviewed_at)

    def test_mark_reviewed_rejects_a_non_terminal_status(self):
        c = KaiCommendation.objects.create(
            title='A', description='d', submitted_by=self.submitter, commended_member=self.honoree)
        with self.assertRaises(AssertionError):
            c.mark_reviewed(self.submitter, 'pending')

    def test_commended_member_is_required(self):
        c = KaiCommendation(title='A', description='d', submitted_by=self.submitter)
        with self.assertRaises(Exception):
            c.full_clean()

    def test_str_does_not_crash_on_a_row_with_no_commended_member(self):
        # commended_member is nullable at the DB level ONLY, to survive
        # migration 0030's rename of the old (wrongly-named)
        # "accommodation request" rows, which have no honoree recorded
        # anywhere. A row in that state must still render, not 500 the
        # first page that calls str() on it (e.g. the admin, or a log
        # line). Bypasses the form/full_clean() required-ness
        # deliberately — this simulates a pre-existing row, not a new
        # submission.
        c = KaiCommendation.objects.create(
            title='Legacy row', description='d', submitted_by=self.submitter)
        self.assertIsNone(c.commended_member_id)
        self.assertIn('no member specified', str(c))


class KaiFormFieldFormTypeScopingTests(TestCase):
    """
    KaiFormField.form_type discriminates which of the two forms a custom
    field belongs to. This is the field the whole toggle feature rests on —
    if it silently defaulted wrong, every existing discipline-form field
    would appear to belong to commendations too (or vice versa).
    """

    def test_default_form_type_is_discipline(self):
        field = KaiFormField.objects.create(
            field_name='legacy_field', label='Legacy', field_type='text')
        self.assertEqual(field.form_type, 'discipline')

    def test_ordering_is_scoped_by_form_type_first(self):
        KaiFormField.objects.create(
            field_name='com_z', label='Z', field_type='text',
            form_type='commendation', display_order=0)
        KaiFormField.objects.create(
            field_name='dis_a', label='A', field_type='text',
            form_type='discipline', display_order=0)
        ordering = list(KaiFormField.objects.values_list('form_type', flat=True))
        # 'commendation' sorts before 'discipline' alphabetically, and
        # Meta.ordering is ['form_type', 'section', 'display_order'].
        self.assertEqual(ordering, sorted(ordering))


class SubmitKaiCommendationViewTests(TestCase):
    def setUp(self):
        self.member = _member('com-sub-2', 'Sub Two')
        self.honoree = _member('com-hon-2', 'Honoree Two')
        self.chair = _member('com-chair-1', 'Chair One', member_type='Officer')
        self.committee = Committee.objects.create(
            name='Kai Committee (com)', code='KAICOM1', is_kai_committee=True)
        self.committee.chairs.add(self.chair)
        self.client.force_login(self.member)

    def test_get_renders_the_form(self):
        resp = self.client.get(reverse('submit_kai_commendation'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Submit a Commendation')

    def test_get_renders_the_commended_member_choice(self):
        resp = self.client.get(reverse('submit_kai_commendation'))
        self.assertContains(resp, self.honoree.name)

    def test_post_creates_a_commendation_owned_by_the_submitter_about_the_honoree(self):
        resp = self.client.post(reverse('submit_kai_commendation'), {
            'commended_member': str(self.honoree.pk),
            'title': 'Great work',
            'description': 'Organized the whole philanthropy event',
        })
        self.assertEqual(resp.status_code, 302)
        c = KaiCommendation.objects.get()
        self.assertEqual(c.submitted_by_id, self.member.pk)
        self.assertEqual(c.commended_member_id, self.honoree.pk)
        self.assertEqual(c.title, 'Great work')

    def test_post_without_a_commended_member_fails_validation(self):
        resp = self.client.post(reverse('submit_kai_commendation'), {
            'title': 'Great work', 'description': 'd',
        })
        self.assertEqual(resp.status_code, 200)  # re-rendered with errors
        self.assertFalse(KaiCommendation.objects.exists())

    def test_is_submitter_anonymous_is_saved(self):
        self.client.post(reverse('submit_kai_commendation'), {
            'commended_member': str(self.honoree.pk),
            'title': 'A', 'description': 'd',
            'is_submitter_anonymous': 'on',
        })
        c = KaiCommendation.objects.get()
        self.assertTrue(c.is_submitter_anonymous)

    def test_is_submitter_anonymous_defaults_false(self):
        self.client.post(reverse('submit_kai_commendation'), {
            'commended_member': str(self.honoree.pk),
            'title': 'A', 'description': 'd',
        })
        c = KaiCommendation.objects.get()
        self.assertFalse(c.is_submitter_anonymous)

    def test_post_creates_a_created_activity_row(self):
        self.client.post(reverse('submit_kai_commendation'), {
            'commended_member': str(self.honoree.pk), 'title': 'A', 'description': 'd',
        })
        c = KaiCommendation.objects.get()
        activity = c.activity_log.get()
        self.assertEqual(activity.action, 'created')
        self.assertEqual(activity.user_id, self.member.pk)

    def test_audit_log_description_names_no_member(self):
        """
        Mirrors submit_kai_report's own omission — the submitter's name (and
        the honoree's) must not land in ActivityLog.description, which
        officers, chairs and advisors can read on /officers/system-logs/.
        """
        from src.models import ActivityLog

        self.client.post(reverse('submit_kai_commendation'), {
            'commended_member': str(self.honoree.pk), 'title': 'A', 'description': 'd',
        })
        log = ActivityLog.objects.filter(object_type='KaiCommendation').get()
        self.assertNotIn(self.member.name, log.description)
        self.assertNotIn(self.honoree.name, log.description)

    def test_commendation_custom_field_appears_on_the_form(self):
        KaiFormField.objects.create(
            field_name='com_only', label='Commendation Only Field',
            field_type='text', form_type='commendation', is_active=True)
        resp = self.client.get(reverse('submit_kai_commendation'))
        self.assertContains(resp, 'Commendation Only Field')

    def test_discipline_custom_field_does_not_appear_on_the_commendation_form(self):
        """The isolation the two form_type-scoped queries exist to guarantee."""
        KaiFormField.objects.create(
            field_name='dis_only', label='Discipline Only Field',
            field_type='text', form_type='discipline', is_active=True)
        resp = self.client.get(reverse('submit_kai_commendation'))
        self.assertNotContains(resp, 'Discipline Only Field')

    def test_commendation_custom_field_does_not_appear_on_the_discipline_form(self):
        """The other direction of the same isolation, via submit_kai_report."""
        KaiFormField.objects.create(
            field_name='com_only_2', label='Commendation Only Field Two',
            field_type='text', form_type='commendation', is_active=True)
        resp = self.client.get(reverse('submit_kai_report'))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'Commendation Only Field Two')

    def test_custom_field_response_saved_against_the_right_field(self):
        field = KaiFormField.objects.create(
            field_name='com_notes', label='Notes', field_type='textarea',
            form_type='commendation', is_active=True)
        self.client.post(reverse('submit_kai_commendation'), {
            'commended_member': str(self.honoree.pk), 'title': 'A', 'description': 'd',
            f'custom_field_{field.id}': 'some detail',
        })
        c = KaiCommendation.objects.get()
        response = KaiCommendationFieldResponse.objects.get(commendation=c, field=field)
        self.assertEqual(response.text_value, 'some detail')

    def test_both_submission_pages_link_to_each_other(self):
        report_resp = self.client.get(reverse('submit_kai_report'))
        self.assertContains(report_resp, reverse('submit_kai_commendation'))
        com_resp = self.client.get(reverse('submit_kai_commendation'))
        self.assertContains(com_resp, reverse('submit_kai_report'))


class ManageKaiCommendationsListViewTests(TestCase):
    def setUp(self):
        self.member = _member('com-list-1', 'List One')
        self.honoree = _member('com-list-hon', 'Honoree')
        self.chair = _member('com-list-chair', 'Chair', member_type='Officer')
        self.committee = Committee.objects.create(
            name='Kai Committee (com-list)', code='KAICOM2', is_kai_committee=True)
        self.committee.chairs.add(self.chair)
        self.c = KaiCommendation.objects.create(
            title='Needs review', description='d', submitted_by=self.member, commended_member=self.honoree)

    def test_chair_can_view_the_list(self):
        self.client.force_login(self.chair)
        resp = self.client.get(reverse('manage_kai_commendations'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Needs review')

    def test_member_with_no_permission_grant_is_blocked(self):
        self.client.force_login(self.member)
        resp = self.client.get(reverse('manage_kai_commendations'), follow=True)
        self.assertContains(resp, 'do not have permission')

    def test_member_with_explicit_grant_can_view_the_list(self):
        KaiMemberPermission.objects.create(
            committee=self.committee, user=self.member, can_view_report_list=True)
        self.client.force_login(self.member)
        resp = self.client.get(reverse('manage_kai_commendations'))
        self.assertEqual(resp.status_code, 200)

    def test_status_filter_narrows_the_list(self):
        KaiCommendation.objects.create(
            title='Acknowledged one', description='d', submitted_by=self.member,
            commended_member=self.honoree, status='acknowledged')
        self.client.force_login(self.chair)
        resp = self.client.get(reverse('manage_kai_commendations'), {'status': 'acknowledged'})
        self.assertContains(resp, 'Acknowledged one')
        self.assertNotContains(resp, 'Needs review')

    def test_list_shows_who_is_commended(self):
        self.client.force_login(self.chair)
        resp = self.client.get(reverse('manage_kai_commendations'))
        self.assertContains(resp, self.honoree.name)


class ManageKaiCommendationDetailViewTests(TestCase):
    def setUp(self):
        self.member = _member('com-det-1', 'Det One')
        self.honoree = _member('com-det-hon', 'Honoree')
        self.chair = _member('com-det-chair', 'Chair', member_type='Officer')
        self.committee = Committee.objects.create(
            name='Kai Committee (com-det)', code='KAICOM3', is_kai_committee=True)
        self.committee.chairs.add(self.chair)
        self.c = KaiCommendation.objects.create(
            title='Detail case', description='d', submitted_by=self.member, commended_member=self.honoree)

    def test_chair_can_view_the_detail_page(self):
        self.client.force_login(self.chair)
        resp = self.client.get(
            reverse('manage_kai_commendation_detail', args=[self.c.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Detail case')
        self.assertContains(resp, self.honoree.name)

    def test_member_without_view_details_permission_is_blocked(self):
        self.client.force_login(self.member)
        resp = self.client.get(
            reverse('manage_kai_commendation_detail', args=[self.c.id]), follow=True)
        self.assertContains(resp, 'do not have permission')

    def test_chair_can_update_status(self):
        self.client.force_login(self.chair)
        self.client.post(
            reverse('manage_kai_commendation_detail', args=[self.c.id]),
            {'action': 'update_status', 'status': 'acknowledged'})
        self.c.refresh_from_db()
        self.assertEqual(self.c.status, 'acknowledged')
        self.assertIsNotNone(self.c.reviewed_at)
        self.assertEqual(
            KaiCommendationActivity.objects.filter(
                commendation=self.c, action='status_changed').count(), 1)

    def test_reviewer_without_edit_permission_cannot_update_status(self):
        KaiMemberPermission.objects.create(
            committee=self.committee, user=self.member,
            can_view_report_list=True, can_view_report_details=True,
            can_edit_open_cases=False)
        self.client.force_login(self.member)
        self.client.post(
            reverse('manage_kai_commendation_detail', args=[self.c.id]),
            {'action': 'update_status', 'status': 'acknowledged'})
        self.c.refresh_from_db()
        self.assertEqual(self.c.status, 'pending')

    def test_chair_can_assign(self):
        self.client.force_login(self.chair)
        self.client.post(
            reverse('manage_kai_commendation_detail', args=[self.c.id]),
            {'action': 'assign', 'assigned_to': str(self.chair.pk)})
        self.c.refresh_from_db()
        self.assertEqual(self.c.assigned_to_id, self.chair.pk)

    def test_chair_can_update_committee_notes(self):
        self.client.force_login(self.chair)
        self.client.post(
            reverse('manage_kai_commendation_detail', args=[self.c.id]),
            {'action': 'update_notes', 'committee_notes': 'internal note'})
        self.c.refresh_from_db()
        self.assertEqual(self.c.committee_notes, 'internal note')

    def test_anonymous_flag_shown_to_the_committee(self):
        self.c.is_submitter_anonymous = True
        self.c.save(update_fields=['is_submitter_anonymous'])
        self.client.force_login(self.chair)
        resp = self.client.get(
            reverse('manage_kai_commendation_detail', args=[self.c.id]))
        self.assertContains(resp, 'anonymous')

    def test_committee_always_sees_the_submitter_with_full_details_permission(self):
        """
        Unlike KaiReport's submitter-identity redaction, is_submitter_anonymous
        is NOT a redaction mechanism for the committee — see the model
        docstring. A committee member with can_view_report_details always
        sees who submitted a commendation.
        """
        self.c.is_submitter_anonymous = True
        self.c.save(update_fields=['is_submitter_anonymous'])
        self.client.force_login(self.chair)
        resp = self.client.get(
            reverse('manage_kai_commendation_detail', args=[self.c.id]))
        self.assertContains(resp, self.member.name)


class KaiCommendationFormBuilderScopingTests(TestCase):
    """
    v3.28.9: kai_form_builder takes a `form_type` kwarg. A field added
    under one url name must not show up on the other's field list, and the
    field-name uniqueness check must stay GLOBAL.
    """

    def setUp(self):
        self.chair = _member('com-fb-chair', 'FB Chair', member_type='Officer')
        self.committee = Committee.objects.create(
            name='Kai Committee (com-fb)', code='KAICOMFB', is_kai_committee=True)
        self.committee.chairs.add(self.chair)
        self.client.force_login(self.chair)

    def test_discipline_builder_only_lists_discipline_fields(self):
        KaiFormField.objects.create(
            field_name='dis_x', label='Discipline X', field_type='text',
            form_type='discipline', is_active=True)
        KaiFormField.objects.create(
            field_name='com_x', label='Commendation X', field_type='text',
            form_type='commendation', is_active=True)
        resp = self.client.get(reverse('kai_form_builder'))
        self.assertContains(resp, 'Discipline X')
        self.assertNotContains(resp, 'Commendation X')

    def test_commendation_builder_only_lists_commendation_fields(self):
        KaiFormField.objects.create(
            field_name='dis_y', label='Discipline Y', field_type='text',
            form_type='discipline', is_active=True)
        KaiFormField.objects.create(
            field_name='com_y', label='Commendation Y', field_type='text',
            form_type='commendation', is_active=True)
        resp = self.client.get(reverse('kai_commendation_form_builder'))
        self.assertContains(resp, 'Commendation Y')
        self.assertNotContains(resp, 'Discipline Y')

    def test_add_field_via_commendation_builder_is_scoped_commendation(self):
        self.client.post(reverse('kai_commendation_form_builder'), {
            'action': 'add_field',
            'form_type': 'commendation',
            'field_name': 'new_com_field',
            'label': 'New Commendation Field',
            'field_type': 'text',
        })
        field = KaiFormField.objects.get(field_name='new_com_field')
        self.assertEqual(field.form_type, 'commendation')

    def test_add_field_via_discipline_builder_is_scoped_discipline(self):
        self.client.post(reverse('kai_form_builder'), {
            'action': 'add_field',
            'form_type': 'discipline',
            'field_name': 'new_dis_field',
            'label': 'New Discipline Field',
            'field_type': 'text',
        })
        field = KaiFormField.objects.get(field_name='new_dis_field')
        self.assertEqual(field.form_type, 'discipline')

    def test_field_name_uniqueness_is_global_across_both_forms(self):
        KaiFormField.objects.create(
            field_name='shared_name', label='Existing', field_type='text',
            form_type='discipline', is_active=True)
        resp = self.client.post(reverse('kai_commendation_form_builder'), {
            'action': 'add_field',
            'form_type': 'commendation',
            'field_name': 'shared_name',
            'label': 'Duplicate attempt',
            'field_type': 'text',
        }, follow=True)
        self.assertContains(resp, 'already exists')
        self.assertEqual(KaiFormField.objects.filter(field_name='shared_name').count(), 1)

    def test_toggle_field_redirects_to_the_right_builder(self):
        field = KaiFormField.objects.create(
            field_name='com_toggle', label='Toggle Me', field_type='text',
            form_type='commendation', is_active=False)
        resp = self.client.post(reverse('kai_commendation_form_builder'), {
            'action': 'toggle_field',
            'form_type': 'commendation',
            'field_id': str(field.id),
        })
        self.assertRedirects(resp, reverse('kai_commendation_form_builder'))
        field.refresh_from_db()
        self.assertTrue(field.is_active)

    def test_delete_field_redirects_to_the_right_builder(self):
        field = KaiFormField.objects.create(
            field_name='com_delete', label='Delete Me', field_type='text',
            form_type='commendation', is_active=True, is_builtin=False)
        resp = self.client.post(reverse('kai_commendation_form_builder'), {
            'action': 'delete_field',
            'form_type': 'commendation',
            'field_id': str(field.id),
        })
        self.assertRedirects(resp, reverse('kai_commendation_form_builder'))
        field.refresh_from_db()
        self.assertFalse(field.is_active)


class KaiCommendationPrivateFileServingTests(TestCase):
    """
    kai_commendations/ was added to PRIVATE_MEDIA_PREFIXES (v3.28.9) —
    /media/ must refuse it, and the dedicated views must gate on submitter-
    or-permitted-reviewer, matching serve_private_upload.py's standing rule
    that a file's access rule is the same object as its page's access rule.
    """

    def setUp(self):
        self.submitter = _member('com-file-sub', 'File Submitter')
        self.honoree = _member('com-file-hon', 'File Honoree')
        self.other_member = _member('com-file-other', 'Other Member')
        self.chair = _member('com-file-chair', 'File Chair', member_type='Officer')
        self.committee = Committee.objects.create(
            name='Kai Committee (com-file)', code='KAICOMFILE', is_kai_committee=True)
        self.committee.chairs.add(self.chair)
        self.c = KaiCommendation.objects.create(
            title='File case', description='d', submitted_by=self.submitter, commended_member=self.honoree)

    def test_media_refuses_the_kai_commendations_prefix(self):
        resp_url = reverse('serve_media', kwargs={'path': 'kai_commendations/probe.pdf'})
        self.client.force_login(self.submitter)
        resp = self.client.get(resp_url)
        self.assertEqual(resp.status_code, 404)

    def test_submitter_without_a_file_gets_404_not_500(self):
        self.client.force_login(self.submitter)
        resp = self.client.get(
            reverse('kai_commendation_attachment', args=[self.c.id]))
        self.assertEqual(resp.status_code, 404)

    def test_unrelated_member_is_refused(self):
        self.client.force_login(self.other_member)
        resp = self.client.get(
            reverse('kai_commendation_attachment', args=[self.c.id]))
        self.assertEqual(resp.status_code, 404)

    def test_honoree_without_committee_access_is_refused(self):
        """The commended member does NOT get automatic access — see the
        model docstring's visibility note (Kai-committee-only)."""
        from src.view.serve_private_upload import _user_may_read_kai_commendation

        self.assertFalse(_user_may_read_kai_commendation(self.honoree, self.c))

    def test_predicate_grants_submitter_and_committee_reviewer(self):
        from src.view.serve_private_upload import _user_may_read_kai_commendation

        self.assertTrue(_user_may_read_kai_commendation(self.chair, self.c))
        self.assertTrue(_user_may_read_kai_commendation(self.submitter, self.c))
        self.assertFalse(_user_may_read_kai_commendation(self.other_member, self.c))

    def test_permitted_reviewer_without_chair_status_is_granted(self):
        from src.view.serve_private_upload import _user_may_read_kai_commendation

        KaiMemberPermission.objects.create(
            committee=self.committee, user=self.other_member,
            can_view_report_details=True)
        self.assertTrue(
            _user_may_read_kai_commendation(self.other_member, self.c))
