"""
Chapter legislation for its authors and officers in the app (v4.4.2): the
vote page's author and officer tools, the edit and new-version pages, the
tracker's officer tools, appointment assignment and receipt checking.

* ``GET votes/manage/`` — the vote page's bills the viewer runs or watches:
  their own and, for an admin, everyone's (with the vote page's action
  buttons: Open Voting Now, Show Now, Edit, End Vote), plus each open vote's
  turnout for chairs and officers. The author's live tally, as on the page.
* ``GET|POST votes/new/`` — the "Upload Legislation" form
  (``upload_chapter_legislation``; chairs and officers). JSON, or multipart
  with a ``document`` (PDF or Word, checked as the website checks it).
* ``GET|POST votes/appointments/new/`` — the "Chair Appointment" form
  (``upload_legislation``; officers and chairs).
* ``GET|POST votes/attendance/`` — the vote page's quick attendance panel
  (``mark_attendance_quick``; chairs and officers).
* ``POST votes/receipts/verify/`` ``{receipt}`` — ``verify_vote_receipt``.
* ``POST votes/<id>/end/`` — End Vote (``end_vote``; the uploader or an
  admin). Returns the result, with whether a runoff is offered.
* ``POST votes/<id>/runoff/`` — ``create_runoff`` (the uploader or an admin).
* ``POST votes/<id>/open-now/`` ``{mode: open|show}`` — the "Now" buttons.
* ``POST legislation/<id>/reopen/`` — ``reopen_legislation`` (officers; only
  the uploader).
* ``GET|POST legislation/<id>/edit/`` — ``edit_legislation``: the form, a
  tabled or pending bill's status, co-authors; ``action: mark_voted`` and
  ``action: transfer_authorship`` as on the page.
* ``GET|POST legislation/<id>/new-version/`` — ``submit_new_version``
  (officers; only the uploader).
* ``POST legislation/passed/add/`` — the tracker's Add Legislation
  (``add_legislation``; officers and admins). JSON or multipart.
* ``POST legislation/<id>/note/`` ``{note}`` — ``update_legislation_note``.
* ``GET|POST legislation/<id>/assign/`` — ``assign_appointment`` (officers
  and chairs; admin roles admin-only).

Every permission check is the web view's, in its order; the work and its
side effects (status, ``passed``, broadcasts, activity log, the voters'
notifications, the appointment's role) are ``src/chapter_votes.py``, which
the web views call too. Ballots and receipts are untouched here: casting is
``views_votes`` (``src/ballots.py``).

Where the app is stricter than a web POST, it is only the vote page's own
button rules: End Vote is offered once voting has started and until it ends,
so the app refuses ending a vote that isn't open (``not_open``) rather than
closing (and re-announcing) it a second time.

Documents: the upload, edit, new-version and tracker endpoints take
multipart with a ``document`` (checked by ``validate_uploaded_file``, then by
``LegislationForm`` where the web view uses it). From v4.4.2 the app sends
one when a file was picked; an app build without the file picker sends JSON
and a bill that needs a file is attached on the website.
"""
from datetime import datetime, timedelta, timezone as dt_timezone

from django.core.exceptions import ValidationError
from django.http import Http404
from django.urls import reverse
from django.utils import timezone
from rest_framework import exceptions, status
from rest_framework.parsers import JSONParser, MultiPartParser
from rest_framework.response import Response

from src import chapter_votes as cv
from src.feature_flag_decorators import check_feature_enabled
from src.forms import LegislationForm
from src.mobile.api import MobileAPIView, check_web_page
from src.models import Legislation, ParliamentUser, Role
from src.permissions import user_is_officer_or_chair
from src.utils.file_validation import validate_uploaded_file

#: The vote page's attendance window (and the ballot's).
ATTENDANCE_WINDOW = timedelta(hours=3)
TRACKER_STATUSES = [('pending', 'Pending'), ('active', 'Active voting'), ('passed', 'Passed'),
                    ('failed', 'Failed'), ('tabled', 'Tabled')]
VOTE_MODES = [{'value': 'percentage', 'label': 'Yes / No'},
              {'value': 'plurality', 'label': 'Plurality'},
              {'value': 'piecewise', 'label': 'Piecewise'}]
#: The edit page's status choices for a tabled or pending bill.
EDIT_STATUSES = [('pending', 'Pending'), ('active', 'Active (reintroduce to voting)'), ('tabled', 'Tabled')]


class BadRequest(exceptions.APIException):
    status_code = 400


class Conflict(exceptions.APIException):
    status_code = 409


def _iso(dt):
    return dt.isoformat() if dt else None


def _refuse(message, code='forbidden'):
    raise exceptions.PermissionDenied(message, code=code)


def _require_voting():
    """``@require_feature_flag('legislation_voting')``."""
    if not check_feature_enabled('legislation_voting'):
        raise exceptions.PermissionDenied('Legislation voting is off.', code='feature_disabled')


def _raise(exc):
    """A ChapterVoteError as the app's error shape."""
    if exc.code == 'forbidden':
        _refuse(exc.message)
    if exc.code == 'conflict':
        raise Conflict(exc.message, code='conflict')
    raise BadRequest(exc.message, code='invalid_input')


def _legislation(legislation_id, **filters):
    leg = (Legislation.objects.select_related('posted_by', 'appointment_role', 'appointment_member')
           .filter(pk=legislation_id, **filters).first())
    if leg is None:
        raise Http404
    return leg


# ── Request bodies ──────────────────────────────────────────────────────────

class _Body:
    """The JSON or multipart body, read the same way."""

    def __init__(self, request):
        self.data = request.data if hasattr(request.data, 'get') else {}

    def get(self, name, default=None):
        value = self.data.get(name, default)
        return default if value is None else value

    def text(self, name, default=''):
        value = self.get(name, default)
        return value if isinstance(value, str) else str(value)

    def flag(self, name):
        value = self.get(name)
        return value is True or (isinstance(value, str) and value.lower() in ('true', 'on', '1'))

    def items(self, name):
        if hasattr(self.data, 'getlist'):
            values = self.data.getlist(name)
            if len(values) == 1 and isinstance(values[0], (list, tuple)):
                values = values[0]
        else:
            values = self.data.get(name) or []
        if not isinstance(values, (list, tuple)):
            raise BadRequest(f'Send {name} as a list.', code='invalid_input')
        return [v for v in values if isinstance(v, (str, int))]

    def has(self, name):
        return name in self.data


def _document(request):
    """The optional upload, checked as the web forms check it; None without one."""
    document = request.FILES.get('document') if hasattr(request, 'FILES') else None
    if document is None:
        return None
    try:
        validate_uploaded_file(document)
    except ValidationError as e:
        raise BadRequest(f'File upload error: {" ".join(e.messages)}', code='invalid_input')
    return document


def _on(flag):
    return 'on' if flag else ''


def chapter_form_data(body):
    """The app's vote fields as the vote page's upload form posts them."""
    data = {
        'title': body.text('title').strip(),
        'description': body.text('description'),
        'available_at': body.text('available_at'),
        'available_at_is_now': '1' if body.flag('available_now') else '',
        'voting_starts_at': body.text('voting_starts_at'),
        'voting_mode_choice': 'separate' if body.flag('separate_voting') else 'unified',
        'voting_ends_at': body.text('voting_ends_at'),
        'anonymous': _on(body.flag('anonymous')),
        # The form's checkbox is "remove abstain"; the app sends allow_abstain.
        'remove_abstain': _on(body.has('allow_abstain') and not body.flag('allow_abstain')),
        'required_percentage': body.text('required_percentage', '51') or '51',
        'vote_mode': body.text('vote_mode', 'percentage') or 'percentage',
        'plurality_votes_allowed': body.text('plurality_votes_allowed', '1'),
        'plurality_runoff_enabled': _on(body.flag('plurality_runoff_enabled')),
        'plurality_runoff_count': body.text('plurality_runoff_count', '2'),
        'required_number': body.text('required_number'),
    }
    options = body.items('plurality_options')
    if len(options) > cv.MAX_PLURALITY_OPTIONS:
        raise BadRequest(f'At most {cv.MAX_PLURALITY_OPTIONS} options.', code='invalid_input')
    for i, option in enumerate(options, start=1):
        data[f'plurality_option_{i}'] = str(option)
    return data


def appointment_form_data(body):
    """The app's appointment fields as the vote page's appointment form posts them."""
    data = {
        'appointment_role_id': body.text('role_id'),
        'new_role_name': body.text('new_role_name'),
        'new_role_code': body.text('new_role_code'),
        'appointment_member_id': body.text('member_id'),
        'appt_vote_mode': body.text('vote_mode', 'percentage') or 'percentage',
        'appt_available_at': body.text('available_at'),
        'appt_available_at_is_now': '1' if body.flag('available_now') else '',
        'appt_voting_starts_at': body.text('voting_starts_at'),
        'appt_voting_ends_at': body.text('voting_ends_at'),
        'appt_required_percentage': body.text('required_percentage', '51') or '51',
        'appt_required_number': body.text('required_number'),
        'appt_plurality_votes_allowed': body.text('plurality_votes_allowed', '1'),
        'appt_plurality_runoff_count': body.text('plurality_runoff_count', '2'),
        'appt_title': body.text('title'),
        'appt_description': body.text('description'),
    }
    if data['appt_available_at_is_now'] and not data['appt_available_at']:
        # The form's "Now" button fills the field too; the server's clock wins.
        data['appt_available_at'] = timezone.now().isoformat()
    # Checkboxes: present means ticked, as in the form.
    if body.flag('anonymous'):
        data['appt_anonymous'] = 'on'
    if body.has('allow_abstain') and not body.flag('allow_abstain'):
        data['appt_remove_abstain'] = 'on'
    if body.flag('plurality_runoff_enabled'):
        data['appt_plurality_runoff_enabled'] = 'on'
    options = body.items('plurality_options')
    if len(options) > cv.MAX_PLURALITY_OPTIONS:
        raise BadRequest(f'At most {cv.MAX_PLURALITY_OPTIONS} options.', code='invalid_input')
    for i, option in enumerate(options, start=1):
        data[f'appt_plurality_option_{i}'] = str(option)
    return data


# ── Shapes ──────────────────────────────────────────────────────────────────

def _thresholds():
    return [{'value': v, 'label': label} for v, label in Legislation.VOTE_THRESHOLDS]


def _tally_rows(leg, tally):
    if leg.vote_mode == 'plurality':
        return [{'option': o, 'count': tally.get(o, 0)} for o in (leg.plurality_options or [])]
    return [{'option': o, 'count': tally[o]} for o in ('yes', 'no', 'abstain')]


def _actions(leg, user):
    """The vote page's buttons for this bill (author or admin, while open)."""
    runs = cv.can_run_vote(user, leg) and not leg.voting_closed
    started = leg.voting_has_started()
    available = leg.is_available()
    return {
        'can_open_now': runs and not started,
        'can_show_now': runs and not available and bool(leg.voting_starts_at or leg.voting_manual_open),
        'can_edit': runs and not available and user == leg.posted_by,
        'can_end': runs and started,
    }


def _managed_bill(leg, user, turnout=None):
    mine = leg.posted_by_id == user.pk
    tally = cv.owner_tally(leg) if mine else None
    info = turnout.get(leg.id) if turnout else None
    return {
        'id': leg.id,
        'title': leg.title,
        'vote_mode': leg.vote_mode,
        'legislation_type': leg.legislation_type,
        'status': leg.status,
        'posted_by': leg.posted_by.name if leg.posted_by_id else None,
        'mine': mine,
        'anonymous': leg.anonymous_vote,
        'available': leg.is_available(),
        'available_at': _iso(leg.available_at),
        'voting_open': leg.voting_has_started(),
        'voting_manual_open': leg.voting_manual_open and not leg.voting_starts_at,
        'voting_starts_at': _iso(leg.get_voting_start_time()),
        'voting_ends_at': _iso(leg.voting_ends_at),
        # The author's live tally (the vote page's vote_data), counts only.
        'tally': _tally_rows(leg, tally) if tally else None,
        'total_votes': tally['total'] if tally else None,
        # Chairs and officers: ballots cast vs voting members present, and who
        # hasn't voted yet (names only, so anonymous ballots stay anonymous).
        'turnout': ({'voted': info['voted'], 'present': info['present'],
                     'not_voted': [m.get_display_name() for m in info['not_voted']]}
                    if info else None),
        **_actions(leg, user),
    }


def _result(leg, outcome):
    """A just-closed vote's result (``chapter_votes.end_vote``'s), as the result page shows it."""
    plurality = leg.vote_mode == 'plurality'
    results = leg.get_plurality_results() if plurality else []
    offered = cv.runoff_offered(leg)
    yes_pct = outcome['yes_percentage']
    return {
        'passed': outcome['passed'],
        'vote_mode': leg.vote_mode,
        'anonymous': leg.anonymous_vote,
        'total_votes': outcome['total_votes'],
        'yes': outcome['yes_votes'],
        'no': outcome['no_votes'],
        'abstain': outcome['abstain_votes'],
        'yes_percentage': round(yes_pct) if yes_pct is not None else None,
        'required_percentage': outcome['required_pct'],
        'required_number': leg.required_number if leg.vote_mode == 'piecewise' else None,
        'options': [{'option': r['option'], 'count': r['count']} for r in results] if plurality else None,
        'winner': outcome['winner'],
        'has_tie': leg.has_plurality_tie() if plurality else False,
        'runoff_offered': offered,
        'runoff_options': leg.get_top_options_for_runoff() if offered else [],
    }


def can_runoff(leg, user):
    """The result page's runoff button, for a viewer who may press it."""
    return bool(cv.can_run_vote(user, leg) and cv.runoff_offered(leg) and not cv.runoff_refusal(leg))


# ── The vote page: manage, create, attendance ───────────────────────────────

class _VotePage(MobileAPIView):
    web_page = 'vote'


class VoteManageView(_VotePage):

    def get(self, request):
        from django.db.models import Q
        user = request.user
        now = timezone.now()
        # The vote page's list: available, or the viewer's own scheduled bill.
        rows = list(Legislation.objects.filter(
            Q(available_at__lte=now) | Q(posted_by=user), voting_closed=False,
        ).exclude(status__in=Legislation.CLOSED_STATUSES)
            .select_related('posted_by').order_by('-available_at'))
        officer_panel = cv.can_upload(user)
        turnout = None
        if officer_panel:
            members = cv.attendance_panel(now - ATTENDANCE_WINDOW)
            turnout = cv.turnout(members, [leg for leg in rows if leg.voting_has_started()])
        shown = [leg for leg in rows if leg.posted_by_id == user.pk or user.is_admin or officer_panel]
        return Response({
            'voting_enabled': check_feature_enabled('legislation_voting'),
            'can_upload': cv.can_upload(user),
            'can_appoint': user_is_officer_or_chair(user),
            'can_take_attendance': officer_panel,
            'bills': [_managed_bill(leg, user, turnout) for leg in shown],
            'links': {'page': reverse('vote')},
        })


class ChapterVoteCreateView(_VotePage):
    parser_classes = [JSONParser, MultiPartParser]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        # The form posts to its own route (pledge restrictions key on it).
        check_web_page(request.user, 'upload_chapter_legislation', toggle=False)

    def get(self, request):
        return Response({
            'can_upload': cv.can_upload(request.user),
            'thresholds': _thresholds(),
            'vote_modes': VOTE_MODES,
            'max_options': cv.MAX_PLURALITY_OPTIONS,
            'description_min': 20,
            'links': {'page': reverse('vote')},
        })

    def post(self, request):
        _require_voting()
        if not cv.can_upload(request.user):
            _refuse('Only chairs and officers can upload legislation.')
        document = _document(request)
        try:
            leg, message = cv.create_chapter_legislation(
                request.user, chapter_form_data(_Body(request)), document, request._request)
        except cv.ChapterVoteError as exc:
            _raise(exc)
        leg = Legislation.objects.select_related('posted_by').get(pk=leg.pk)
        return Response({'bill': _managed_bill(leg, request.user), 'message': message},
                        status=status.HTTP_201_CREATED)


class AppointmentCreateView(MobileAPIView):
    web_page = 'upload_legislation'
    web_page_toggle = False   # @officer_required, no page toggle
    parser_classes = [JSONParser, MultiPartParser]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not user_is_officer_or_chair(request.user):
            _refuse('Officers and chairs only.')
        _require_voting()

    def get(self, request):
        return Response({
            'roles': [{'id': r.id, 'name': r.name, 'code': r.code}
                      for r in Role.objects.all().order_by('name')],
            'members': [{'id': m.pk, 'name': m.name} for m in
                        ParliamentUser.objects.filter(member_status='Active').order_by('name').only('pk', 'name')],
            'thresholds': _thresholds(),
            'vote_modes': VOTE_MODES,
            'max_options': cv.MAX_PLURALITY_OPTIONS,
            'anonymous_available': check_feature_enabled('anonymous_voting'),
            'abstain_optional': check_feature_enabled('abstain_voting'),
        })

    def post(self, request):
        try:
            leg, message, warning = cv.create_appointment(
                request.user, appointment_form_data(_Body(request)), request._request)
        except cv.ChapterVoteError as exc:
            _raise(exc)
        leg = Legislation.objects.select_related('posted_by').get(pk=leg.pk)
        return Response({'bill': _managed_bill(leg, request.user), 'message': message, 'warning': warning},
                        status=status.HTTP_201_CREATED)


class VoteAttendanceView(_VotePage):

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if request.method == 'POST':
            check_web_page(request.user, 'mark_attendance_quick', toggle=False)
        if not cv.can_upload(request.user):
            _refuse('Not allowed.')

    def get(self, request):
        members = cv.attendance_panel(timezone.now() - ATTENDANCE_WINDOW)
        return Response({
            'members': [{'id': m['member'].pk, 'name': m['member'].name, 'status': m['status']}
                        for m in members],
            'present_count': sum(1 for m in members if m['status'] in ('present', 'late')),
            'statuses': ['present', 'late', 'absent'],
        })

    def post(self, request):
        body = _Body(request)
        target_id, new_status = body.text('user_id'), body.text('status')
        if not target_id or new_status not in ('present', 'late', 'absent'):
            raise BadRequest('Invalid request.', code='invalid_input')
        target = ParliamentUser.objects.filter(user_id=target_id).first()
        if target is None:
            raise exceptions.NotFound('Unknown member.')
        cv.mark_attendance_quick(request.user, target, new_status)
        return Response({'ok': True, 'status': new_status})


class ReceiptVerifyView(MobileAPIView):
    web_page = 'verify_vote_receipt'
    web_page_toggle = False   # @login_required only

    def post(self, request):
        from src.utils.vote_receipts import RECEIPT_MAX_AGE_DAYS, verify_receipt
        receipt = _Body(request).text('receipt')
        result = verify_receipt(receipt)
        leg = result.get('legislation')
        cast_at = result.get('cast_at')
        return Response({
            'valid': bool(result.get('valid')),
            'intact': bool(result.get('intact')),
            'missing': result.get('missing', 0),
            'ballots': result.get('ballots', 0),
            'legislation': {'id': leg.id, 'title': leg.title} if leg else None,
            'is_yours': bool(result.get('valid')) and result.get('voter_pk') == request.user.pk,
            'cast_at': (datetime.fromtimestamp(cast_at, tz=dt_timezone.utc).isoformat()
                        if result.get('valid') and cast_at else None),
            'reason': result.get('reason', ''),
            'receipt_max_age_days': RECEIPT_MAX_AGE_DAYS,
        })


# ── One vote: end, runoff, open now, reopen ─────────────────────────────────

class EndVoteView(MobileAPIView):
    web_page = 'end_vote'
    web_page_toggle = False

    def post(self, request, legislation_id):
        _require_voting()
        leg = _legislation(legislation_id)
        if not cv.can_run_vote(request.user, leg):
            _refuse('Only the uploader or an admin can end the vote.')
        # The vote page offers End Vote only while voting is open.
        if leg.voting_closed:
            raise Conflict('Voting on this legislation has already ended.', code='not_open')
        if not leg.voting_has_started():
            raise Conflict('Voting on this legislation hasn’t started yet.', code='not_open')
        outcome = cv.end_vote(leg, request.user, request._request)
        return Response({
            'bill': {'id': leg.id, 'title': leg.title, 'status': leg.status},
            'result': _result(leg, outcome),
            'can_runoff': can_runoff(leg, request.user),
            'message': f'Voting ended — {"Passed" if outcome["passed"] else "Did Not Pass"}.',
        })


class RunoffView(MobileAPIView):
    web_page = 'create_runoff'
    web_page_toggle = False

    def post(self, request, legislation_id):
        _require_voting()
        leg = _legislation(legislation_id)
        if not cv.can_run_vote(request.user, leg):
            _refuse('Only the uploader or an admin can create a runoff.')
        try:
            runoff, options = cv.create_runoff(leg, request.user, request._request)
        except cv.ChapterVoteError as exc:
            _raise(exc)
        return Response({
            'runoff': {'id': runoff.id, 'title': runoff.title},
            'options': options,
            'message': f"Runoff vote created with top {len(options)} options: {', '.join(options)}",
        }, status=status.HTTP_201_CREATED)


class OpenNowView(MobileAPIView):
    web_page = 'open_legislation_now'
    web_page_toggle = False

    def post(self, request, legislation_id):
        _require_voting()
        leg = _legislation(legislation_id)
        if not cv.can_run_vote(request.user, leg):
            _refuse('Only the uploader or an admin can open this legislation.')
        try:
            level, message = cv.open_now(leg, request.user, _Body(request).text('mode'), request._request)
        except cv.ChapterVoteError as exc:
            raise Conflict(exc.message, code='not_open')
        return Response({'changed': level == 'success', 'message': message,
                         'bill': _managed_bill(leg, request.user)})


class ReopenView(MobileAPIView):
    web_page = 'reopen_legislation'
    web_page_toggle = False

    def post(self, request, legislation_id):
        # @officer_required, then the flag, then the lookup — the web's order.
        if not user_is_officer_or_chair(request.user):
            _refuse('Officers and chairs only.')
        _require_voting()
        leg = _legislation(legislation_id)
        try:
            cv.reopen(leg, request.user)
        except cv.ChapterVoteError as exc:
            _raise(exc)
        return Response({'ok': True, 'message': 'Legislation has been reopened.',
                         'bill': {'id': leg.id, 'title': leg.title, 'status': leg.status}})


# ── Edit and new version ────────────────────────────────────────────────────

#: LegislationForm's fields the app sends (the document stays on the website).
FORM_FIELDS = [f for f in LegislationForm.Meta.fields if f != 'document']
FORM_BOOLEANS = ('anonymous_vote', 'allow_abstain')
FORM_DATES = ('available_at', 'voting_starts_at', 'voting_ends_at')


class AppLegislationForm(LegislationForm):
    """
    ``LegislationForm`` without ``document``: the app can't send a file, so an
    edit keeps the bill's. Its rule — a document or 20+ characters of
    description — reads the bill's saved document instead.
    """

    class Meta(LegislationForm.Meta):
        fields = FORM_FIELDS

    def clean(self):
        cleaned = super(LegislationForm, self).clean()
        has_document = bool(self.instance and self.instance.pk and self.instance.document)
        if not has_document and len((cleaned.get('description') or '').strip()) < 20:
            raise ValidationError(
                'Please either upload a document OR provide a detailed description (at least 20 characters).')
        return cleaned


def _form_data(body, legislation=None):
    """The body as the form's data; a field left out keeps the bill's value."""
    from django.forms.models import model_to_dict
    saved = model_to_dict(legislation, fields=FORM_FIELDS) if legislation else {}
    data = {}
    for name in FORM_FIELDS:
        value = body.get(name, saved.get(name))
        if name in FORM_BOOLEANS:
            if value is True or (isinstance(value, str) and value.lower() in ('true', 'on', '1')):
                data[name] = 'on'
        elif value is None:
            data[name] = ''
        elif name in FORM_DATES and hasattr(value, 'isoformat'):
            data[name] = value.isoformat()
        else:
            data[name] = str(value)
    return data


def _form(request, legislation=None, instance=None):
    """The app form for a JSON body; the website's own form for a multipart upload."""
    body = _Body(request)
    document = _document(request)
    data = _form_data(body, legislation)
    if document is not None:
        return LegislationForm(data, {'document': document}, instance=instance)
    return AppLegislationForm(data, instance=instance)


def _form_errors(form):
    lines = []
    for field, errors in form.errors.items():
        label = form.fields[field].label if field in form.fields else None
        lines.extend(f'{label}: {e}' if label else str(e) for e in errors)
    return ' '.join(lines) or 'Please correct the error below.'


def _bill_fields(leg):
    return {
        'id': leg.id,
        'title': leg.title,
        'description': leg.description,
        'status': leg.status,
        'vote_mode': leg.vote_mode,
        'legislation_type': leg.legislation_type,
        'available_at': _iso(leg.available_at),
        'voting_starts_at': _iso(leg.voting_starts_at),
        'voting_ends_at': _iso(leg.voting_ends_at),
        'anonymous_vote': leg.anonymous_vote,
        'allow_abstain': leg.allow_abstain,
        'required_percentage': leg.required_percentage,
        'posted_by': {'id': leg.posted_by_id, 'name': leg.posted_by.name} if leg.posted_by_id else None,
        'document': ({'view': reverse('view_document', args=[leg.id])} if leg.document else None),
    }


class EditLegislationView(MobileAPIView):
    web_page = 'edit_legislation'
    web_page_toggle = False   # no @require_page_enabled
    parser_classes = [JSONParser, MultiPartParser]

    def access(self, request, legislation_id):
        _require_voting()
        leg = _legislation(legislation_id)
        try:
            return (leg, *cv.edit_access(request.user, leg))
        except cv.ChapterVoteError as exc:
            if exc.code == 'forbidden':
                _refuse(exc.message)
            raise Conflict(exc.message, code='not_editable')

    def get(self, request, legislation_id):
        leg, is_author, is_officer, is_admin = self.access(request, legislation_id)
        members = (ParliamentUser.objects.filter(member_status__in=['Active', 'Inactive'])
                   .exclude(pk=leg.posted_by_id).order_by('name').only('pk', 'name'))
        return Response({
            'bill': {**_bill_fields(leg),
                     'co_authors': [{'id': m.pk, 'name': m.name} for m in leg.co_authors.order_by('name')]},
            'members': [{'id': m.pk, 'name': m.name} for m in members],
            'thresholds': _thresholds(),
            # The page's status choice: only for a tabled or pending bill.
            'statuses': ([{'value': v, 'label': label} for v, label in EDIT_STATUSES]
                         if leg.status in ('tabled', 'pending') else None),
            'anonymous_available': check_feature_enabled('anonymous_voting'),
            'abstain_optional': check_feature_enabled('abstain_voting'),
            'can_mark_voted': bool(is_officer or is_admin),
            'can_transfer': bool(request.user == leg.posted_by or is_officer or is_admin),
            'links': {'page': reverse('edit_legislation', args=[leg.id])},
        })

    def post(self, request, legislation_id):
        leg, is_author, is_officer, is_admin = self.access(request, legislation_id)
        body = _Body(request)
        action = body.text('action', 'edit') or 'edit'
        if action == 'mark_voted':
            if not is_officer and not is_admin:
                _refuse('Officers and admins only.')
            data = {
                'mark_voted_outcome': body.text('outcome', 'passed'),
                'mark_voted_date': body.text('voted_at'),
                'mark_voted_yes': body.text('yes'),
                'mark_voted_no': body.text('no'),
                'mark_voted_abstain': body.text('abstain'),
            }
            outcome = cv.mark_voted(leg, request.user, data)
            return Response({
                'outcome': outcome,
                'message': f'Legislation marked as {outcome}.',
                # The website goes on to assign a passed appointment's role.
                'assign': bool(outcome == 'passed' and leg.legislation_type == 'appointment'
                               and leg.appointment_role_id),
                'bill': _bill_fields(leg),
            })
        if action == 'transfer_authorship':
            if not (request.user == leg.posted_by or is_officer or is_admin):
                _refuse("You don't have permission to transfer authorship.")
            try:
                message = cv.transfer_authorship(leg, body.text('transfer_to_id'))
            except cv.ChapterVoteError as exc:
                _raise(exc)
            leg.refresh_from_db()
            return Response({'message': message, 'bill': _bill_fields(leg)})
        if action != 'edit':
            raise BadRequest('Unknown action.', code='invalid_input')

        form = _form(request, leg, instance=leg)
        if not form.is_valid():
            raise BadRequest(_form_errors(form), code='invalid_input')
        co_authors = (body.items('co_author_ids') if body.has('co_author_ids')
                      else list(leg.co_authors.values_list('pk', flat=True)))
        saved, message = cv.save_edit(form, leg, body.text('status') or None, [str(c) for c in co_authors])
        return Response({'message': message, 'bill': _bill_fields(saved)})


class NewVersionView(MobileAPIView):
    web_page = 'submit_new_version'
    web_page_toggle = False   # @officer_required only
    parser_classes = [JSONParser, MultiPartParser]

    def previous(self, request, legislation_id):
        if not user_is_officer_or_chair(request.user):
            _refuse('Officers and chairs only.')
        leg = _legislation(legislation_id)
        if request.user != leg.posted_by:
            _refuse('Only the uploader can submit a new version.')
        return leg

    def get(self, request, legislation_id):
        leg = self.previous(request, legislation_id)
        return Response({'bill': _bill_fields(leg), 'thresholds': _thresholds(),
                         'links': {'page': reverse('submit_new_version', args=[leg.id])}})

    def post(self, request, legislation_id):
        leg = self.previous(request, legislation_id)
        # Unbound to the old bill, as on the website: the new version keeps
        # no document unless one is uploaded with it.
        form = _form(request)
        if not form.is_valid():
            raise BadRequest(_form_errors(form), code='invalid_input')
        new = cv.save_new_version(form, leg, request.user)
        return Response({'bill': {'id': new.id, 'title': new.title},
                         'message': 'New version of the legislation has been submitted.'},
                        status=status.HTTP_201_CREATED)


# ── Legislation Tracker: add, note; appointments ────────────────────────────

class TrackerAddView(MobileAPIView):
    web_page = 'add_legislation'
    web_page_toggle = False
    parser_classes = [JSONParser, MultiPartParser]

    def get(self, request):
        return Response({
            'can_add': cv.can_manage_tracker(request.user),
            'statuses': [{'value': v, 'label': label} for v, label in TRACKER_STATUSES],
            'thresholds': _thresholds(),
        })

    def post(self, request):
        if not cv.can_manage_tracker(request.user):
            _refuse('You do not have permission to add legislation.')
        body = _Body(request)
        data = {
            'title': body.text('title'),
            'status': body.text('status', 'pending') or 'pending',
            'description': body.text('description'),
            'include_votes': _on(body.flag('include_votes')),
            'yes_votes': body.text('yes_votes', '0') or '0',
            'no_votes': body.text('no_votes', '0') or '0',
            'abstain_votes': body.text('abstain_votes', '0') or '0',
            'required_percentage': body.text('required_percentage', '51') or '51',
        }
        try:
            leg = cv.add_tracker_legislation(request.user, data, _document(request))
        except cv.ChapterVoteError as exc:
            _raise(exc)
        return Response({'bill': {'id': leg.id, 'title': leg.title, 'status': leg.status},
                         'message': f'Legislation "{leg.title}" has been added.'},
                        status=status.HTTP_201_CREATED)


class NoteView(MobileAPIView):
    web_page = 'update_legislation_note'
    web_page_toggle = False

    def post(self, request, legislation_id):
        if not cv.can_manage_tracker(request.user):
            _refuse('Permission denied.')
        leg = _legislation(legislation_id)
        note = _Body(request).text('note')
        return Response({'ok': True, 'note': cv.update_note(leg, request.user, note)})


class AssignAppointmentView(MobileAPIView):
    web_page = 'assign_appointment'
    web_page_toggle = False   # @officer_required only

    def appointment(self, request, legislation_id):
        if not user_is_officer_or_chair(request.user):
            _refuse('Officers and chairs only.')
        leg = _legislation(legislation_id, legislation_type='appointment')
        if not leg.appointment_role_id:
            raise Conflict('This appointment has no role associated with it.', code='not_assignable')
        refusal = cv.not_assignable(leg)
        if refusal:
            raise Conflict(refusal, code='not_assignable')
        return leg

    def get(self, request, legislation_id):
        leg = self.appointment(request, legislation_id)
        plurality = leg.vote_mode == 'plurality'
        members = (ParliamentUser.objects.filter(member_status='Active').order_by('name').only('pk', 'name')
                   if plurality else [])
        nominee = leg.appointment_member
        return Response({
            'bill': {'id': leg.id, 'title': leg.title, 'vote_mode': leg.vote_mode},
            'role': {'id': leg.appointment_role.id, 'name': leg.appointment_role.name},
            'nominee': {'id': nominee.pk, 'name': nominee.name} if nominee else None,
            'is_plurality': plurality,
            'winning_option': cv.winning_option(leg) if plurality else None,
            'members': [{'id': m.pk, 'name': m.name} for m in members],
        })

    def post(self, request, legislation_id):
        leg = self.appointment(request, legislation_id)
        if leg.vote_mode == 'plurality':
            member_id = _Body(request).text('member_id').strip()
            if not member_id:
                raise BadRequest('Please select the winning member to assign.', code='invalid_input')
            member = ParliamentUser.objects.filter(user_id=member_id, member_status='Active').first()
            if member is None:
                raise Http404
        else:
            member = leg.appointment_member
            if not member:
                raise Conflict('No nominated member found for this appointment.', code='not_assignable')
        try:
            message = cv.assign_appointment(leg, request.user, member, request._request)
        except cv.ChapterVoteError as exc:
            _raise(exc)
        return Response({'ok': True, 'message': message})
