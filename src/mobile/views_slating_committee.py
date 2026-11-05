"""
The slating committee's tools in the app (11-02-26): reviewing applications,
interviews, attendance, paper ballots, and the results summary / publishing.

Committee (``can_view_applications``: the committee's members, chairs and
admin, the slating manager; a site admin only while the period isn't locked):

* ``GET slating/periods/<id>/applications/?status=&position=&gpa_level=&search=``
  — ``applications_list``.
* ``GET slating/periods/<id>/applications/<app id>/`` — ``application_detail``.
* ``GET slating/periods/<id>/interviews/?status=`` — ``interview_list``.
* ``GET / POST slating/interviews/<id>/`` — ``complete_interview``: the notes
  form, and saving it ``{notes, strengths, concerns, recommendation,
  recommended_positions}``.

The period's managers (``can_manage_period``, the web's
``slating_chair_required``):

* ``POST slating/periods/<id>/applications/<app id>/review/`` —
  ``submit_review`` ``{status, review_notes, verify_gpa, unverify_gpa}``.
* ``GET / POST slating/applications/<app id>/interview/`` —
  ``schedule_interview`` ``{scheduled_at (ISO), location, interviewers}``.
* ``POST slating/periods/<id>/interviews/destroy-notes/`` —
  ``destroy_interview_notes`` ``{confirm: "DESTROY"}``.
* ``GET / POST slating/periods/<id>/attendance/`` — ``manage_attendance``
  ``{action: mark_present | mark_absent, member_id}`` (deliberation or
  voting only).
* ``GET / POST slating/periods/<id>/paper-ballots/`` —
  ``record_paper_ballots``, the web's field names (``approve`` / ``reject`` /
  ``abstain`` for a slate round; ``ballots`` and ``approve_<key>`` …  per row
  for positions), while a round is open or paused.
* ``GET slating/periods/<id>/results/summary/`` — ``results_summary``.
* ``POST slating/periods/<id>/results/publish/`` — ``publish_results``
  ``{save_to_documents}``: notifies every active member, as the web does.

Each one runs the web view's own helpers (``src/view/slating/``), so the
checks, the activity log and the notifications are the same. Period setup,
positions and transferring the admin are ``views_slating_setup.py``
(11-04-26); the form builder and importing stay on the website.
"""
import os

from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.dateparse import parse_datetime
from rest_framework import exceptions
from rest_framework.response import Response

from src.mobile.views_excuses_polls import BadRequest
from src.mobile.views_service_hours import _form_data
from src.mobile.views_slating import _iso, _SlatingBase
from src.mobile.views_votes import VoteRefused
from src.models import SlatingApplication, SlatingInterview, SlatingPeriod
from src.view.slating.applications_review import (
    application_status_counts, can_review_application, filter_applications, ranked_positions, review_sections,
    save_review,
)
from src.view.slating.apply import TIERS, tiered_position_lists
from src.view.slating.attendance import (
    ATTENDANCE_CLOSED, ATTENDANCE_STATUSES, attendance_roster, mark_all_present, mark_attendance,
)
from src.view.slating.interview_manager import (
    apps_without_interview, committee_interviewers, destroy_period_notes, period_interviews, save_completion,
    save_schedule, schedule_refusal,
)
from src.view.slating.paper_ballots import (
    PAPER_CLOSED, PaperBallotError, paper_ballots_state, round_accepts_paper, save_paper_ballots,
)
from src.view.slating.permissions import can_manage_period, can_view_applications
from src.view.slating.results import PUBLISHABLE, publish_period_results, results_summary_data


def _num(value):
    return float(value) if value is not None else None


def _flag(data, key):
    """A posted checkbox: JSON ``true`` (``on`` once through _form_data) or the web's ``on``."""
    return (data.get(key) or '').lower() in ('on', 'true', '1')


def _committee_period(request, period_id):
    period = get_object_or_404(SlatingPeriod, id=period_id)
    if not can_view_applications(request.user, period):
        raise exceptions.PermissionDenied('You do not have access to view this slating period.', code='forbidden')
    return period


def _manager_period(request, period_id):
    period = get_object_or_404(SlatingPeriod, id=period_id)
    if not can_manage_period(request.user, period):
        raise exceptions.PermissionDenied('You do not have access to manage this slating period.', code='forbidden')
    return period


def _period_head(period, user):
    return {
        'id': period.id,
        'name': period.name,
        'status': period.status,
        'status_label': period.get_status_display(),
        'vote_type': period.vote_type,
        'can_manage': can_manage_period(user, period),
    }


def _member(u):
    return {'user_id': u.user_id, 'name': u.name}


def _interview_json(interview, notes=False):
    out = {
        'id': interview.id,
        'application_id': interview.application_id,
        'applicant': interview.application.applicant.name,
        'scheduled_at': _iso(interview.scheduled_at),
        'completed_at': _iso(interview.completed_at),
        'location': interview.location,
        'interviewers': [_member(u) for u in interview.interviewers.all()],
        'recommendation': interview.recommendation,
        'recommendation_label': interview.get_recommendation_display() if interview.recommendation else '',
        'recommended_positions': [{'id': p.id, 'title': p.title} for p in interview.recommended_positions.all()],
        'notes_destroyed': interview.notes_destroyed,
    }
    if notes:
        out.update({'notes': interview.notes, 'strengths': interview.strengths, 'concerns': interview.concerns})
    return out


# ── Applications ─────────────────────────────────────────────────────────────

class SlatingApplicationsView(_SlatingBase):
    web_page = 'slating_applications'

    def get(self, request, period_id):
        period = _committee_period(request, period_id)
        q = request.query_params
        status_filter = q.get('status', '')
        position = q.get('position', '')
        gpa_level = q.get('gpa_level', '')
        search = q.get('search', '')
        if (position and not position.isdigit()) or (gpa_level and not gpa_level.isdigit()):
            raise BadRequest('position and gpa_level are numbers.', code='invalid_input')
        apps = filter_applications(period, status_filter, int(position) if position else None,
                                   int(gpa_level) if gpa_level else None, search)
        positions = period.positions.filter(is_active=True).order_by('display_order')
        titles = {p.id: p.title for p in positions}
        return Response({
            'period': _period_head(period, request.user),
            'applications': [{
                'id': a.id,
                'applicant': a.applicant.name,
                'user_id': a.applicant.user_id,
                'status': a.status,
                'status_label': a.get_status_display(),
                'submitted_at': _iso(a.submitted_at),
                'gpa_level': a.gpa_level,
                'imported': a.imported_at is not None,
                'interviewed': bool(a.interviews.all()),
                'first_choice': [titles[p] for p in a.get_first_choice_positions() if p in titles],
                # 11-05-26: as the web list ("1st: EVP · 2nd: President", roll number).
                'ranked_positions': ranked_positions(a, titles),
                'role_number': a.applicant.role_number or '',
            } for a in apps],
            'positions': [{'id': p.id, 'title': p.title} for p in positions],
            'status_counts': [{'status': k, **v} for k, v in application_status_counts(period).items()],
            'status_choices': [{'value': k, 'label': v} for k, v in SlatingApplication.STATUS_CHOICES if k != 'draft'],
            'gpa_level_choices': [{'value': k, 'label': v} for k, v in SlatingApplication.GPA_LEVEL_CHOICES],
            'filters': {'status': status_filter, 'position': position, 'gpa_level': gpa_level, 'search': search},
        })


def _answer(response):
    """What the web's detail page shows for an answer, in its order of checks."""
    if response.text_value:
        return {'text': response.text_value}
    if response.number_value:
        return {'text': str(response.number_value)}
    if response.json_value:
        value = response.json_value
        return {'text': ', '.join(str(v) for v in value) if isinstance(value, list) else str(value)}
    if response.file_value:
        return {'file_name': os.path.basename(response.file_value.name),
                'file_path': reverse('slating_response_file', args=[response.id])}
    return {'text': None}


def _application_detail(period, application, user):
    positions = period.positions.filter(is_active=True)
    tiered, _ = tiered_position_lists(application, {p.id: p for p in positions})
    can_review = can_review_application(user, period)
    applicant = application.applicant
    return {
        'period': _period_head(period, user),
        'application': {
            'id': application.id,
            'applicant': applicant.name,
            'user_id': applicant.user_id,
            'member_type': applicant.member_type,
            'member_status': applicant.member_status,
            'is_pledge': applicant.is_pledge,
            'status': application.status,
            'status_label': application.get_status_display(),
            'submitted_at': _iso(application.submitted_at),
            'reported_gpa': _num(application.reported_gpa),
            'gpa_level': application.gpa_level,
            'gpa_verified': application.gpa_verified,
            'gpa_screenshot_path': (reverse('slating_gpa_screenshot', args=[application.id])
                                    if application.gpa_screenshot else None),
            'imported_at': _iso(application.imported_at),
            'import_not_asked': application.import_not_asked or [],
            # Confidential: the web shows them only in the review panel.
            'review_notes': application.review_notes if can_review else None,
        },
        'sections': [{
            'title': title,
            'answers': [{'field_id': r.field_id, 'label': r.field.label, 'type': r.field.field_type, **_answer(r)}
                        for r in responses],
        } for title, responses in review_sections(application, user).items()],
        'preferences': {tier: [{'id': p.id, 'title': p.title} for p in tiered[tier]] for tier in TIERS},
        'interviews': [_interview_json(i) | {
            'notes': None if i.notes_destroyed else (i.notes or None)} for i in
            application.interviews.all().order_by('-scheduled_at')],
        'can_review': can_review,
        'can_manage': can_manage_period(user, period),
        'status_choices': [{'value': k, 'label': v} for k, v in SlatingApplication.STATUS_CHOICES],
    }


class SlatingApplicationDetailView(_SlatingBase):
    web_page = 'slating_app_detail'

    def get(self, request, period_id, app_id):
        period = _committee_period(request, period_id)
        application = get_object_or_404(SlatingApplication, id=app_id, period=period)
        return Response(_application_detail(period, application, request.user))


class SlatingReviewView(_SlatingBase):
    web_page = 'slating_submit_review'

    def post(self, request, period_id, app_id):
        period = _manager_period(request, period_id)
        application = get_object_or_404(SlatingApplication, id=app_id, period=period)
        data = _form_data(request)
        new_status = data.get('status') or ''
        if new_status and new_status not in dict(SlatingApplication.STATUS_CHOICES):
            raise BadRequest('That isn’t an application status.', code='invalid_input')
        notes = data.get('review_notes')
        save_review(period, application, request.user, new_status,
                    application.review_notes if notes is None else notes.strip(),
                    verify_gpa=_flag(data, 'verify_gpa'), unverify_gpa=_flag(data, 'unverify_gpa'))
        return Response(_application_detail(period, application, request.user))


# ── Interviews ───────────────────────────────────────────────────────────────

class SlatingInterviewsView(_SlatingBase):
    web_page = 'slating_interviews'

    def get(self, request, period_id):
        period = _committee_period(request, period_id)
        status_filter = request.query_params.get('status', '')
        if status_filter not in ('', 'scheduled', 'completed', 'pending'):
            raise BadRequest('status is scheduled, completed or pending.', code='invalid_input')
        return Response({
            'period': _period_head(period, request.user),
            'interviews': [_interview_json(i) for i in period_interviews(period, status_filter)],
            'without_interview': [{
                'id': a.id, 'applicant': a.applicant.name, 'is_pledge': a.applicant.is_pledge,
                'submitted_at': _iso(a.submitted_at),
            } for a in apps_without_interview(period)],
            'interviewers': [_member(u) for u in sorted(committee_interviewers(period), key=lambda u: u.name)],
            'notes_remaining': SlatingInterview.objects.filter(
                application__period=period, notes_destroyed=False).count(),
            'can_manage': can_manage_period(request.user, period),
            'status_filter': status_filter,
        })


class SlatingScheduleInterviewView(_SlatingBase):
    web_page = 'slating_schedule_interview'

    def application(self, request, app_id):
        application = get_object_or_404(SlatingApplication.objects.select_related('period', 'applicant'), id=app_id)
        refusal = schedule_refusal(request.user, application)
        if refusal:
            code, message = refusal
            if code == 'forbidden':
                raise exceptions.PermissionDenied(message, code='forbidden')
            raise VoteRefused(code, message, 409)
        return application

    def get(self, request, app_id):
        application = self.application(request, app_id)
        existing = SlatingInterview.objects.filter(application=application).first()
        return Response({
            'application_id': application.id,
            'period_id': application.period_id,
            'applicant': application.applicant.name,
            'submitted_at': _iso(application.submitted_at),
            'interviewers': [_member(u) for u in sorted(committee_interviewers(application.period),
                                                        key=lambda u: u.name)],
            'interview': _interview_json(existing) if existing else None,
        })

    def post(self, request, app_id):
        application = self.application(request, app_id)
        data = _form_data(request)
        raw = (data.get('scheduled_at') or '').strip()
        scheduled = None
        if raw:
            try:
                scheduled = parse_datetime(raw)
            except (ValueError, TypeError):
                scheduled = None
            if scheduled is None:
                raise BadRequest('Invalid date/time format.', code='invalid_input')
        interview = save_schedule(application, request.user, scheduled, (data.get('location') or '').strip(),
                                  data.getlist('interviewers'))
        return Response({'ok': True, 'interview': _interview_json(interview)})


class SlatingCompleteInterviewView(_SlatingBase):
    web_page = 'slating_complete_interview'

    def interview(self, request, interview_id):
        interview = get_object_or_404(
            SlatingInterview.objects.select_related('application__period', 'application__applicant'),
            id=interview_id)
        if not can_view_applications(request.user, interview.application.period):
            raise exceptions.PermissionDenied('Only slating committee members can complete interviews.',
                                              code='forbidden')
        return interview

    def payload(self, interview):
        period = interview.application.period
        return {
            'interview': _interview_json(interview, notes=True),
            'period_id': period.id,
            'positions': [{'id': p.id, 'title': p.title} for p in period.positions.filter(is_active=True)],
            'recommendation_choices': [{'value': k, 'label': v} for k, v in SlatingInterview.RECOMMENDATION_CHOICES],
        }

    def get(self, request, interview_id):
        return Response(self.payload(self.interview(request, interview_id)))

    def post(self, request, interview_id):
        interview = self.interview(request, interview_id)
        data = _form_data(request)
        recommendation = data.get('recommendation') or ''
        if recommendation and recommendation not in dict(SlatingInterview.RECOMMENDATION_CHOICES):
            raise BadRequest('That isn’t a recommendation.', code='invalid_input')
        positions = [p for p in data.getlist('recommended_positions') if str(p).isdigit()]
        save_completion(interview, request.user, (data.get('notes') or '').strip(),
                        (data.get('strengths') or '').strip(), (data.get('concerns') or '').strip(),
                        recommendation, positions)
        interview.refresh_from_db()
        return Response(self.payload(interview))


class SlatingDestroyNotesView(_SlatingBase):
    web_page = 'slating_destroy_notes'

    def post(self, request, period_id):
        period = _manager_period(request, period_id)
        data = _form_data(request)
        if data.get('confirm') != 'DESTROY':
            raise BadRequest('Please type DESTROY to confirm.', code='invalid_input')
        return Response({'ok': True, 'destroyed': destroy_period_notes(period, request.user)})


# ── Attendance, paper ballots, results ───────────────────────────────────────

class SlatingAttendanceView(_SlatingBase):
    web_page = 'slating_attendance'

    def period(self, request, period_id):
        period = _manager_period(request, period_id)
        if period.status not in ATTENDANCE_STATUSES:
            raise VoteRefused('attendance_closed', ATTENDANCE_CLOSED, 409)
        return period

    def payload(self, period, user):
        roster = attendance_roster(period)
        return {
            'period': _period_head(period, user) | {'quorum': period.quorum},
            'members': [{
                'user_id': e['member'].user_id,
                'name': e['member'].name,
                'member_type': e['member'].get_member_type_display(),
                'member_status': e['member'].member_status,
                'present': e['is_present'],
            } for e in roster['members_with_status']],
            **{k: roster[k] for k in ('present_count', 'total_eligible', 'quorum_met', 'quorum_needed')},
        }

    def get(self, request, period_id):
        period = self.period(request, period_id)
        return Response(self.payload(period, request.user))

    def post(self, request, period_id):
        period = self.period(request, period_id)
        data = _form_data(request)
        action = data.get('action')
        if action == 'mark_all_present':
            # 11-05-26, as the web's "Mark all listed present": ``member_ids``
            # (the list shown, after a search) or, without it, everyone eligible.
            body = request.data
            if hasattr(body, 'getlist'):   # a form post
                ids = body.getlist('member_ids') if 'member_ids' in body else None
            else:
                ids = body.get('member_ids') if isinstance(body, dict) else None
            if ids is not None and not isinstance(ids, list):
                raise BadRequest('member_ids is a list of user ids.', code='invalid_input')
            added = mark_all_present(period, request.user, ids)
            return Response({**self.payload(period, request.user), 'added': added})
        if action not in ('mark_present', 'mark_absent'):
            raise BadRequest('action is mark_present, mark_absent or mark_all_present.', code='invalid_input')
        mark_attendance(period, request.user, data.get('member_id'), action)
        return Response(self.payload(period, request.user))


def _paper_row(row, allow_abstain):
    if 'key' not in row:   # a full-slate round's one row
        return {'key': 'slate', 'position': '', 'name': 'The full slate', 'pick': False,
                'abstain_input': allow_abstain, 'counts': row['counts']}
    return {'key': row['key'], 'position': row['position'], 'name': row['name'], 'pick': row['pick'],
            'abstain_input': row['abstain_input'], 'counts': row['counts']}


class SlatingPaperBallotsView(_SlatingBase):
    web_page = 'slating_paper_ballots'

    def period(self, request, period_id):
        period = _manager_period(request, period_id)
        if not round_accepts_paper(period):
            raise VoteRefused('paper_closed', PAPER_CLOSED, 409)
        return period

    def payload(self, period, user):
        state = paper_ballots_state(period)
        return {
            'period': _period_head(period, user),
            'attempt': state['attempt'],
            'individual': state['individual'],
            'ballots': state['ballots'],
            'note': state['note'],
            'allow_abstain': state['allow_abstain'],
            'rows': [_paper_row(r, state['allow_abstain']) for r in state['rows']],
        }

    def get(self, request, period_id):
        return Response(self.payload(self.period(request, period_id), request.user))

    def post(self, request, period_id):
        period = self.period(request, period_id)
        try:
            attempt, ballots = save_paper_ballots(period, request.user, _form_data(request))
        except PaperBallotError as e:
            raise BadRequest(str(e), code='invalid_input')
        return Response({
            **self.payload(period, request.user),
            'message': f'Saved {ballots} paper ballot(s) for round {attempt}. '
                       'They\'ll be counted when you close the vote.',
        })


def _summary(period, user):
    data = results_summary_data(period)
    return {
        'period': _period_head(period, user) | {
            'required_percentage': period.required_approval_percentage,
            'results_published_at': _iso(period.results_publish_at) if period.status == 'results_published' else None,
        },
        'can_publish': period.status in PUBLISHABLE,
        'slates': [{
            'id': s.id,
            'name': s.name,
            'type_label': s.get_slate_type_display(),
            'approved_at': _iso(s.approved_at),
            'approval_percentage': _num(s.approval_percentage),
            'passed': s.passed,
        } for s in data['slates']],
        'voting_history': [{**r, 'percentage': _num(r['percentage'])} for r in data['voting_history']],
        'eligible_voters': data['eligible_voters'],
        'total_unique_voters': data['total_unique_voters'],
        'participation_rate': round(data['participation_rate'], 1),
        'present': data['present'],
        'latest_round_voted': data['latest_round_voted'],
        'app_stats': data['app_stats'],
    }


class SlatingResultsSummaryView(_SlatingBase):
    web_page = 'slating_results_summary'

    def get(self, request, period_id):
        return Response(_summary(_manager_period(request, period_id), request.user))


class SlatingPublishResultsView(_SlatingBase):
    web_page = 'slating_publish_results'

    def post(self, request, period_id):
        period = _manager_period(request, period_id)
        from src.view.slating.results import publish_refusal
        refusal = publish_refusal(period)
        if refusal:
            raise VoteRefused('not_publishable', refusal, 409)
        data = _form_data(request)
        notes = publish_period_results(period, request.user, _flag(data, 'save_to_documents'))
        return Response({**_summary(period, request.user), 'notes': [text for _, text in notes],
                         'message': 'Results published successfully!'})
