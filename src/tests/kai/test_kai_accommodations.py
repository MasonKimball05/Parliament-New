"""
Kai accommodation requests — v3.28.8.

Requested by Mason: a way to submit something to the Kai Committee that is
NOT a disciplinary report. See src/models/kai_accommodations.py's docstring
for why this is a separate model from KaiReport, and src/view/kai_reports.py
for why the two forms' custom-field queries are now form_type-scoped.

These tests cover the whole new surface:
  - the model (request numbering, display_number, mark_resolved)
  - the submit view (creation, activity log, no-name audit description,
    custom-field isolation from the discipline form)
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
    Committee, KaiAccommodationFieldResponse, KaiAccommodationRequest,
    KaiAccommodationRequestActivity, KaiFormField, KaiMemberPermission,
    ParliamentUser,
)


def _member(user_id, name, member_type='Member'):
    user = ParliamentUser.objects.create_user(
        user_id=user_id, name=name, username=user_id, member_type=member_type)
    user.set_password('testpass123')
    user.save()
    return user


class KaiAccommodationRequestModelTests(TestCase):
    def setUp(self):
        self.requester = _member('acc-req-1', 'Req One')

    def test_request_number_assigned_on_first_save(self):
        req = KaiAccommodationRequest.objects.create(
            title='Need a lighter schedule', description='Finals week',
            requester=self.requester)
        self.assertTrue(re.match(r'^ACC-\d{4}-\d{3}$', req.request_number))

    def test_request_numbers_increment_within_a_year(self):
        req1 = KaiAccommodationRequest.objects.create(
            title='A', description='d', requester=self.requester)
        req2 = KaiAccommodationRequest.objects.create(
            title='B', description='d', requester=self.requester)
        year = req1.submitted_at.year
        self.assertEqual(req1.request_number, f'ACC-{year}-001')
        self.assertEqual(req2.request_number, f'ACC-{year}-002')

    def test_display_number_falls_back_to_pk_if_unassigned(self):
        req = KaiAccommodationRequest.objects.create(
            title='A', description='d', requester=self.requester)
        # `.update()`, not `.save()` — save()'s own override reassigns a
        # number whenever request_number is blank (by design, mirroring
        # KaiReport), so going through save() here would just prove that
        # behaviour again instead of testing the fallback property.
        KaiAccommodationRequest.objects.filter(pk=req.pk).update(request_number='')
        req.refresh_from_db()
        self.assertEqual(req.display_number, f'#{req.pk}')

    def test_mark_resolved_sets_status_resolver_and_timestamp(self):
        req = KaiAccommodationRequest.objects.create(
            title='A', description='d', requester=self.requester)
        resolver = _member('acc-resolver-1', 'Resolver')
        req.mark_resolved(resolver, 'approved')
        req.refresh_from_db()
        self.assertEqual(req.status, 'approved')
        self.assertEqual(req.resolved_by_id, resolver.pk)
        self.assertIsNotNone(req.resolved_at)

    def test_mark_resolved_rejects_a_non_terminal_status(self):
        req = KaiAccommodationRequest.objects.create(
            title='A', description='d', requester=self.requester)
        with self.assertRaises(AssertionError):
            req.mark_resolved(self.requester, 'pending')


class KaiFormFieldFormTypeScopingTests(TestCase):
    """
    KaiFormField.form_type discriminates which of the two forms a custom
    field belongs to. This is the field the whole toggle feature rests on —
    if it silently defaulted wrong, every existing discipline-form field
    would appear to belong to accommodation too (or vice versa).
    """

    def test_default_form_type_is_discipline(self):
        field = KaiFormField.objects.create(
            field_name='legacy_field', label='Legacy', field_type='text')
        self.assertEqual(field.form_type, 'discipline')

    def test_ordering_is_scoped_by_form_type_first(self):
        KaiFormField.objects.create(
            field_name='acc_z', label='Z', field_type='text',
            form_type='accommodation', display_order=0)
        KaiFormField.objects.create(
            field_name='dis_a', label='A', field_type='text',
            form_type='discipline', display_order=0)
        ordering = list(KaiFormField.objects.values_list('form_type', flat=True))
        # 'accommodation' sorts before 'discipline' alphabetically, and
        # Meta.ordering is ['form_type', 'section', 'display_order'].
        self.assertEqual(ordering, sorted(ordering))


class SubmitKaiAccommodationRequestViewTests(TestCase):
    def setUp(self):
        self.member = _member('acc-sub-1', 'Sub One')
        self.chair = _member('acc-chair-1', 'Chair One', member_type='Officer')
        self.committee = Committee.objects.create(
            name='Kai Committee (acc)', code='KAIACC1', is_kai_committee=True)
        self.committee.chairs.add(self.chair)
        self.client.force_login(self.member)

    def test_get_renders_the_form(self):
        resp = self.client.get(reverse('submit_kai_accommodation_request'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Request an Accommodation')

    def test_post_creates_a_request_owned_by_the_submitter(self):
        resp = self.client.post(reverse('submit_kai_accommodation_request'), {
            'title': 'Religious observance',
            'description': 'Need to miss a chapter meeting',
        })
        self.assertEqual(resp.status_code, 302)
        req = KaiAccommodationRequest.objects.get()
        self.assertEqual(req.requester_id, self.member.pk)
        self.assertEqual(req.title, 'Religious observance')

    def test_post_creates_a_created_activity_row(self):
        self.client.post(reverse('submit_kai_accommodation_request'), {
            'title': 'A', 'description': 'd',
        })
        req = KaiAccommodationRequest.objects.get()
        activity = req.activity_log.get()
        self.assertEqual(activity.action, 'created')
        self.assertEqual(activity.user_id, self.member.pk)

    def test_audit_log_description_names_no_member(self):
        """
        Mirrors submit_kai_report's own omission — see the /officers/system-logs/
        finding in CLAUDE.md's v3.25.2 entry. The requester's own name must not
        land in ActivityLog.description, which officers, chairs and advisors
        can read on that page.
        """
        from src.models import ActivityLog

        self.client.post(reverse('submit_kai_accommodation_request'), {
            'title': 'A', 'description': 'd',
        })
        log = ActivityLog.objects.filter(object_type='KaiAccommodationRequest').get()
        self.assertNotIn(self.member.name, log.description)

    def test_accommodation_custom_field_appears_on_the_form(self):
        KaiFormField.objects.create(
            field_name='acc_only', label='Accommodation Only Field',
            field_type='text', form_type='accommodation', is_active=True)
        resp = self.client.get(reverse('submit_kai_accommodation_request'))
        self.assertContains(resp, 'Accommodation Only Field')

    def test_discipline_custom_field_does_not_appear_on_the_accommodation_form(self):
        """The isolation the two form_type-scoped queries exist to guarantee."""
        KaiFormField.objects.create(
            field_name='dis_only', label='Discipline Only Field',
            field_type='text', form_type='discipline', is_active=True)
        resp = self.client.get(reverse('submit_kai_accommodation_request'))
        self.assertNotContains(resp, 'Discipline Only Field')

    def test_accommodation_custom_field_does_not_appear_on_the_discipline_form(self):
        """The other direction of the same isolation, via submit_kai_report."""
        KaiFormField.objects.create(
            field_name='acc_only_2', label='Accommodation Only Field Two',
            field_type='text', form_type='accommodation', is_active=True)
        resp = self.client.get(reverse('submit_kai_report'))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'Accommodation Only Field Two')

    def test_custom_field_response_saved_against_the_right_field(self):
        field = KaiFormField.objects.create(
            field_name='acc_notes', label='Notes', field_type='textarea',
            form_type='accommodation', is_active=True)
        self.client.post(reverse('submit_kai_accommodation_request'), {
            'title': 'A', 'description': 'd',
            f'custom_field_{field.id}': 'some detail',
        })
        req = KaiAccommodationRequest.objects.get()
        response = KaiAccommodationFieldResponse.objects.get(request=req, field=field)
        self.assertEqual(response.text_value, 'some detail')

    def test_both_submission_pages_link_to_each_other(self):
        report_resp = self.client.get(reverse('submit_kai_report'))
        self.assertContains(report_resp, reverse('submit_kai_accommodation_request'))
        acc_resp = self.client.get(reverse('submit_kai_accommodation_request'))
        self.assertContains(acc_resp, reverse('submit_kai_report'))


class ManageKaiAccommodationRequestsListViewTests(TestCase):
    def setUp(self):
        self.member = _member('acc-list-1', 'List One')
        self.chair = _member('acc-list-chair', 'Chair', member_type='Officer')
        self.committee = Committee.objects.create(
            name='Kai Committee (acc-list)', code='KAIACC2', is_kai_committee=True)
        self.committee.chairs.add(self.chair)
        self.req = KaiAccommodationRequest.objects.create(
            title='Needs review', description='d', requester=self.member)

    def test_chair_can_view_the_list(self):
        self.client.force_login(self.chair)
        resp = self.client.get(reverse('manage_kai_accommodation_requests'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Needs review')

    def test_member_with_no_permission_grant_is_blocked(self):
        self.client.force_login(self.member)
        resp = self.client.get(reverse('manage_kai_accommodation_requests'), follow=True)
        self.assertContains(resp, 'do not have permission')

    def test_member_with_explicit_grant_can_view_the_list(self):
        KaiMemberPermission.objects.create(
            committee=self.committee, user=self.member, can_view_report_list=True)
        self.client.force_login(self.member)
        resp = self.client.get(reverse('manage_kai_accommodation_requests'))
        self.assertEqual(resp.status_code, 200)

    def test_status_filter_narrows_the_list(self):
        KaiAccommodationRequest.objects.create(
            title='Approved one', description='d', requester=self.member, status='approved')
        self.client.force_login(self.chair)
        resp = self.client.get(reverse('manage_kai_accommodation_requests'), {'status': 'approved'})
        self.assertContains(resp, 'Approved one')
        self.assertNotContains(resp, 'Needs review')


class ManageKaiAccommodationRequestDetailViewTests(TestCase):
    def setUp(self):
        self.member = _member('acc-det-1', 'Det One')
        self.chair = _member('acc-det-chair', 'Chair', member_type='Officer')
        self.committee = Committee.objects.create(
            name='Kai Committee (acc-det)', code='KAIACC3', is_kai_committee=True)
        self.committee.chairs.add(self.chair)
        self.req = KaiAccommodationRequest.objects.create(
            title='Detail case', description='d', requester=self.member)

    def test_chair_can_view_the_detail_page(self):
        self.client.force_login(self.chair)
        resp = self.client.get(
            reverse('manage_kai_accommodation_request_detail', args=[self.req.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Detail case')

    def test_member_without_view_details_permission_is_blocked(self):
        self.client.force_login(self.member)
        resp = self.client.get(
            reverse('manage_kai_accommodation_request_detail', args=[self.req.id]), follow=True)
        self.assertContains(resp, 'do not have permission')

    def test_chair_can_update_status(self):
        self.client.force_login(self.chair)
        self.client.post(
            reverse('manage_kai_accommodation_request_detail', args=[self.req.id]),
            {'action': 'update_status', 'status': 'approved'})
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, 'approved')
        self.assertIsNotNone(self.req.resolved_at)
        self.assertEqual(
            KaiAccommodationRequestActivity.objects.filter(
                request=self.req, action='status_changed').count(), 1)

    def test_reviewer_without_edit_permission_cannot_update_status(self):
        KaiMemberPermission.objects.create(
            committee=self.committee, user=self.member,
            can_view_report_list=True, can_view_report_details=True,
            can_edit_open_cases=False)
        self.client.force_login(self.member)
        self.client.post(
            reverse('manage_kai_accommodation_request_detail', args=[self.req.id]),
            {'action': 'update_status', 'status': 'approved'})
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, 'pending')

    def test_chair_can_assign(self):
        self.client.force_login(self.chair)
        self.client.post(
            reverse('manage_kai_accommodation_request_detail', args=[self.req.id]),
            {'action': 'assign', 'assigned_to': str(self.chair.pk)})
        self.req.refresh_from_db()
        self.assertEqual(self.req.assigned_to_id, self.chair.pk)

    def test_chair_can_update_committee_notes(self):
        self.client.force_login(self.chair)
        self.client.post(
            reverse('manage_kai_accommodation_request_detail', args=[self.req.id]),
            {'action': 'update_notes', 'committee_notes': 'internal note'})
        self.req.refresh_from_db()
        self.assertEqual(self.req.committee_notes, 'internal note')


class KaiAccommodationFormBuilderScopingTests(TestCase):
    """
    v3.28.8: kai_form_builder now takes a `form_type` kwarg. A field added
    under one url name must not show up on the other's field list, and the
    field-name uniqueness check must stay GLOBAL (see kai_form_builder.py's
    _handle_add_field comment on why).
    """

    def setUp(self):
        self.chair = _member('acc-fb-chair', 'FB Chair', member_type='Officer')
        self.committee = Committee.objects.create(
            name='Kai Committee (acc-fb)', code='KAIACCFB', is_kai_committee=True)
        self.committee.chairs.add(self.chair)
        self.client.force_login(self.chair)

    def test_discipline_builder_only_lists_discipline_fields(self):
        KaiFormField.objects.create(
            field_name='dis_x', label='Discipline X', field_type='text',
            form_type='discipline', is_active=True)
        KaiFormField.objects.create(
            field_name='acc_x', label='Accommodation X', field_type='text',
            form_type='accommodation', is_active=True)
        resp = self.client.get(reverse('kai_form_builder'))
        self.assertContains(resp, 'Discipline X')
        self.assertNotContains(resp, 'Accommodation X')

    def test_accommodation_builder_only_lists_accommodation_fields(self):
        KaiFormField.objects.create(
            field_name='dis_y', label='Discipline Y', field_type='text',
            form_type='discipline', is_active=True)
        KaiFormField.objects.create(
            field_name='acc_y', label='Accommodation Y', field_type='text',
            form_type='accommodation', is_active=True)
        resp = self.client.get(reverse('kai_accommodation_form_builder'))
        self.assertContains(resp, 'Accommodation Y')
        self.assertNotContains(resp, 'Discipline Y')

    def test_add_field_via_accommodation_builder_is_scoped_accommodation(self):
        self.client.post(reverse('kai_accommodation_form_builder'), {
            'action': 'add_field',
            'form_type': 'accommodation',
            'field_name': 'new_acc_field',
            'label': 'New Accommodation Field',
            'field_type': 'text',
        })
        field = KaiFormField.objects.get(field_name='new_acc_field')
        self.assertEqual(field.form_type, 'accommodation')

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
        resp = self.client.post(reverse('kai_accommodation_form_builder'), {
            'action': 'add_field',
            'form_type': 'accommodation',
            'field_name': 'shared_name',
            'label': 'Duplicate attempt',
            'field_type': 'text',
        }, follow=True)
        self.assertContains(resp, 'already exists')
        self.assertEqual(KaiFormField.objects.filter(field_name='shared_name').count(), 1)

    def test_toggle_field_redirects_to_the_right_builder(self):
        field = KaiFormField.objects.create(
            field_name='acc_toggle', label='Toggle Me', field_type='text',
            form_type='accommodation', is_active=False)
        resp = self.client.post(reverse('kai_accommodation_form_builder'), {
            'action': 'toggle_field',
            'form_type': 'accommodation',
            'field_id': str(field.id),
        })
        self.assertRedirects(resp, reverse('kai_accommodation_form_builder'))
        field.refresh_from_db()
        self.assertTrue(field.is_active)

    def test_delete_field_redirects_to_the_right_builder(self):
        field = KaiFormField.objects.create(
            field_name='acc_delete', label='Delete Me', field_type='text',
            form_type='accommodation', is_active=True, is_builtin=False)
        resp = self.client.post(reverse('kai_accommodation_form_builder'), {
            'action': 'delete_field',
            'form_type': 'accommodation',
            'field_id': str(field.id),
        })
        self.assertRedirects(resp, reverse('kai_accommodation_form_builder'))
        field.refresh_from_db()
        self.assertFalse(field.is_active)


class KaiAccommodationPrivateFileServingTests(TestCase):
    """
    kai_accommodations/ was added to PRIVATE_MEDIA_PREFIXES (v3.28.8) —
    /media/ must refuse it, and the dedicated views must gate on requester-or-
    permitted-reviewer, matching serve_private_upload.py's standing rule that
    a file's access rule is the same object as its page's access rule.
    """

    def setUp(self):
        self.requester = _member('acc-file-req', 'File Requester')
        self.other_member = _member('acc-file-other', 'Other Member')
        self.chair = _member('acc-file-chair', 'File Chair', member_type='Officer')
        self.committee = Committee.objects.create(
            name='Kai Committee (acc-file)', code='KAIACCFILE', is_kai_committee=True)
        self.committee.chairs.add(self.chair)
        self.req = KaiAccommodationRequest.objects.create(
            title='File case', description='d', requester=self.requester)

    def test_media_refuses_the_kai_accommodations_prefix(self):
        resp_url = reverse('serve_media', kwargs={'path': 'kai_accommodations/probe.pdf'})
        self.client.force_login(self.requester)
        resp = self.client.get(resp_url)
        self.assertEqual(resp.status_code, 404)

    def test_requester_without_a_file_gets_404_not_500(self):
        self.client.force_login(self.requester)
        resp = self.client.get(
            reverse('kai_accommodation_attachment', args=[self.req.id]))
        self.assertEqual(resp.status_code, 404)

    def test_unrelated_member_is_refused(self):
        self.client.force_login(self.other_member)
        resp = self.client.get(
            reverse('kai_accommodation_attachment', args=[self.req.id]))
        self.assertEqual(resp.status_code, 404)

    def test_chair_with_view_details_is_not_refused_at_the_permission_check(self):
        # No file attached, so this still 404s — but via "no file", not via
        # the access predicate. Confirmed by contrast with test above: an
        # unrelated member with NO grant hits the same 404, so this test
        # exists to pin the predicate directly rather than only by response code.
        from src.view.serve_private_upload import _user_may_read_kai_accommodation

        self.assertTrue(_user_may_read_kai_accommodation(self.chair, self.req))
        self.assertTrue(_user_may_read_kai_accommodation(self.requester, self.req))
        self.assertFalse(_user_may_read_kai_accommodation(self.other_member, self.req))

    def test_permitted_reviewer_without_chair_status_is_granted(self):
        from src.view.serve_private_upload import _user_may_read_kai_accommodation

        KaiMemberPermission.objects.create(
            committee=self.committee, user=self.other_member,
            can_view_report_details=True)
        self.assertTrue(
            _user_may_read_kai_accommodation(self.other_member, self.req))
