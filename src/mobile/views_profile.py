"""
The member's own Profile in the app (v4.4.0): the website's Profile page
(``profile``), through the same helpers (src/view/profile_view.py), so an edit
here is validated, logged and confirmed exactly as one made on the site.

* ``GET profile/`` — ``profile`` (everything the page edits), ``security`` (the
  Security card's 2FA, backup code, passkey and login summary, read-only),
  ``choices`` (pledge classes, possible bigs) and ``links`` (the website pages
  for what stays there: photo upload, password, 2FA, passkeys, sessions).
* ``PATCH profile/`` (or ``PUT``) — any of the "Account settings" fields
  (``username``, ``preferred_name``, ``email``, ``phone_number``) and the
  "Public profile" fields (``about_me``, ``pledge_class``,
  ``pledge_class_greek``, ``big_brother``, ``graduation_semester``,
  ``graduation_year``, ``instagram``, ``twitter``, ``linkedin``, ``snapchat``,
  ``facebook``, ``other_email``). A field left out keeps its value. A new email
  is only saved once the confirmation link is clicked (``email_pending``).
* ``POST profile/photo/`` (multipart ``photo``) — a new profile picture, held
  to the field's checks (JPEG, PNG or WebP, at most 5 MB). ``DELETE`` removes it.
* ``POST profile/role-history/`` ``{role_name, start_semester, end_semester}``;
  ``PUT`` / ``DELETE profile/role-history/<id>/``.
* ``POST profile/socials/`` ``{platform, handle}``; ``DELETE profile/socials/<index>/``.
* ``POST profile/initiation-chapters/`` ``{school, chapter, role_number}``;
  ``DELETE profile/initiation-chapters/<index>/``.
* ``POST profile/academics/`` ``{type, value}`` (type ``major``, ``minor`` or
  ``concentration``); ``DELETE profile/academics/<type>/<index>/``.

Every write answers ``{"profile": ...}`` (the GET's ``profile``). Refusals are
``400 {"error": "profile_invalid", "detail": <the website's message>}``.

On top of the page's own rules, the app's writes are also held to the rules
the browser form enforces and the database would (lengths, an email that is an
email, a free username, a graduation semester and year that are real), so a
bad value is a message here rather than a server error.
"""
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone
from rest_framework import exceptions
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response

from src.mobile.api import MobileAPIView
from src.mobile.views_directory import photo_path
from src.mobile.views_excuses_polls import BadRequest
from src.models import ParliamentUser, RoleHistory
from src.view import profile_view as pv

#: The "Account settings" form's fields.
ACCOUNT_FIELDS = ('username', 'preferred_name', 'email', 'phone_number')
#: The "Public profile" form's fields (big_brother is a user_id).
PUBLIC_FIELDS = ('about_me', 'pledge_class', 'pledge_class_greek', 'big_brother', 'graduation_semester',
                 'graduation_year', 'instagram', 'twitter', 'linkedin', 'snapchat', 'facebook', 'other_email')
SOCIAL_FIELDS = ('instagram', 'twitter', 'linkedin', 'snapchat', 'facebook')
GRADUATION_SEMESTERS = ('Spring', 'Fall')


# ── JSON ─────────────────────────────────────────────────────────────────────

def _iso(dt):
    return dt.isoformat() if dt else None


def _pending_email(user):
    token = (user.email_verification_tokens.filter(used=False, expires_at__gt=timezone.now())
             .order_by('-created_at').first())
    return token.new_email if token else None


def profile_json(user):
    """Everything the Profile page edits, as stored (not the directory card's display shape)."""
    from src.big_reveal import profile_big_is_locked
    from src.view.profile_card import _pledge_badge
    big = user.big_brother
    return {
        'user_id': user.user_id,
        'name': user.name,
        'display_name': user.get_display_name(),
        'photo': photo_path(user),
        'member_type': user.member_type,
        'member_status': user.member_status,
        # The page's badge rule: a pledge has no roll number yet.
        'role_number': None if user.is_pledge else (user.role_number or None),
        'is_pledge': bool(user.is_pledge),
        # account settings
        'username': user.username,
        'preferred_name': user.preferred_name or '',
        'email': user.email or '',
        'email_flagged': bool(user.email_flagged),
        'email_pending': _pending_email(user),
        'phone_number': user.phone_number or '',
        # public profile
        'about_me': user.about_me or '',
        'pledge_class': user.pledge_class or '',
        'pledge_class_greek': user.pledge_class_greek or '',
        'pledge_class_badge': _pledge_badge(user),
        'graduation_semester': user.graduation_semester or '',
        'graduation_year': user.graduation_year,
        'socials': {f: getattr(user, f) or '' for f in SOCIAL_FIELDS},
        'other_email': user.other_email or '',
        'big_brother': {'user_id': big.user_id, 'name': big.get_display_name()} if big else None,
        # v3.39.0: set by the education committee at the reveal, not editable.
        'big_locked': profile_big_is_locked(user),
        'little_brothers': [lb.get_display_name() for lb in user.little_brothers.all()],
        # additional details (deleted by index, as on the page)
        'custom_socials': [{'index': i, 'platform': s.get('platform', ''), 'handle': s.get('handle', '')}
                           for i, s in enumerate(user.custom_socials or [])],
        'initiation_chapters': [{'index': i, 'school': c.get('school', ''), 'chapter': c.get('chapter', ''),
                                 'role_number': c.get('role_number', '')}
                                for i, c in enumerate(user.initiation_chapters or [])],
        'academics': {
            'majors': list(user.majors or []),
            'minors': list(user.minors or []),
            'concentrations': list(user.concentrations or []),
        },
        'role_history': [{'id': rh.id, 'role_name': rh.role_name, 'start_semester': rh.start_semester,
                          'end_semester': rh.end_semester or ''}
                         for rh in RoleHistory.objects.filter(user=user)],
    }


def security_json(user):
    s = pv.profile_security_state(user)
    last = s['last_failed_login']
    return {
        'has_2fa': bool(s['has_2fa']),
        'backup_codes_remaining': s['backup_codes_remaining'],
        'backup_codes_warning': bool(s['backup_codes_warning']),
        'backup_codes_acknowledged': bool(user.backup_codes_acknowledged),
        'passkeys': [{'name': p.name, 'created_at': _iso(p.created_at), 'last_used_at': _iso(p.last_used_at)}
                     for p in s['passkeys']],
        # Never the raw IP, as on the page.
        'recent_logins': [{'timestamp': _iso(lg.timestamp), 'browser': lg.browser, 'os': lg.os,
                           'city': lg.city, 'region': lg.region, 'is_suspicious': bool(lg.is_suspicious)}
                          for lg in s['recent_logins']],
        'failed_logins_30d': s['failed_login_count_30d'],
        'last_failed_login': {'timestamp': _iso(last.timestamp), 'city': last.city} if last else None,
    }


def choices_json(user, big_locked):
    from src.pledge_classes import all_classes
    bigs = [] if big_locked else [
        {'user_id': m.user_id, 'name': m.get_display_name()}
        for m in ParliamentUser.objects.exclude(user_id=user.user_id).order_by('name')
        .only('user_id', 'name', 'preferred_name')
    ]
    return {
        'pledge_classes': [{'label': c['label'], 'greek': c['greek']} for c in all_classes()],
        'graduation_semesters': list(GRADUATION_SEMESTERS),
        'big_brothers': bigs,
    }


def links_json():
    profile = reverse('profile')
    return {
        'profile': profile,             # photo upload, password, passkey rename
        'passkeys': f'{profile}?open=passkeys',
        'two_factor_setup': reverse('two_factor_setup'),
        'two_factor_disable': reverse('two_factor_disable'),
        'backup_codes': reverse('two_factor_regenerate_backup_codes'),
        'sessions': reverse('session_list'),
        'preferences': reverse('preferences'),
    }


# ── Input ────────────────────────────────────────────────────────────────────

def _body(request):
    """The JSON body as the page's form would post it: every value a string."""
    data = request.data if hasattr(request.data, 'items') else {}
    out = {}
    for key, value in data.items():
        if value is None:
            value = ''
        elif isinstance(value, bool):
            raise BadRequest(f'{key} must be text.', code='profile_invalid')
        elif isinstance(value, (int, float)):
            value = str(value)
        elif not isinstance(value, str):
            raise BadRequest(f'{key} must be text.', code='profile_invalid')
        out[key] = value
    return out


def _refuse(message):
    raise BadRequest(message, code='profile_invalid')


def _check_text(user, post):
    """The page's plain-text rules (src/profile_text.py), on the page's own keys."""
    from src.profile_text import profile_text_error
    error = profile_text_error(post, user.other_email, user=user)
    if error:
        _refuse(error)


def _check_lengths(post, model=ParliamentUser):
    """What the database would refuse: a value longer than its column."""
    for key, value in post.items():
        try:
            field = model._meta.get_field(key)
        except Exception:
            continue
        limit = getattr(field, 'max_length', None)
        if limit and len(value.strip()) > limit and field.get_internal_type() == 'CharField':
            _refuse(f'{field.verbose_name.capitalize()} can be at most {limit} characters.')


def _edit_error(e):
    """A ProfileEditError as the API's error."""
    if e.status == 404:
        raise exceptions.NotFound(e.message)
    _refuse(e.message)


def _current(user, fields):
    """The member's saved values for ``fields``, as the page's form would post them back."""
    out = {}
    for f in fields:
        if f == 'big_brother':
            out[f] = user.big_brother_id or ''
        elif f == 'graduation_year':
            out[f] = str(user.graduation_year) if user.graduation_year else ''
        else:
            out[f] = getattr(user, f) or ''
    return out


# ── Views ────────────────────────────────────────────────────────────────────

class _Profile(MobileAPIView):
    web_page = 'profile'

    def done(self, user, status=200, **extra):
        user.refresh_from_db()
        return Response({'profile': profile_json(user), **extra}, status=status)


class ProfileView(_Profile):

    def get(self, request):
        user = request.user
        notice = pv.take_photo_removed_notice(user)
        profile = profile_json(user)
        return Response({
            'profile': profile,
            # Once, as the page's banner: an admin removed the photo.
            'photo_removed_by_admin': notice,
            'security': security_json(user),
            'choices': choices_json(user, profile['big_locked']),
            'links': links_json(),
        })

    def patch(self, request):
        user = request.user
        body = _body(request)
        account = {k: body[k] for k in ACCOUNT_FIELDS if k in body}
        public = {k: body[k] for k in PUBLIC_FIELDS if k in body}
        if not account and not public:
            _refuse('Nothing to save.')

        # The page posts a whole form; a field the app left out is posted back as saved.
        account_post = {**_current(user, ACCOUNT_FIELDS), **account} if account else {}
        public_post = {**_current(user, PUBLIC_FIELDS), **public} if public else {}
        _check_text(user, {**account_post, **public_post})
        self._check_account(user, account)
        self._check_public(user, public)

        pending = None
        try:
            if account:
                pending = pv.save_account_fields(request, user, account_post)['email_pending']
            if public:
                pv.save_public_profile(user, public_post)
        except pv.ProfileEditError as e:
            user.refresh_from_db()
            _edit_error(e)
        return self.done(user, email_pending=pending)

    put = patch

    @staticmethod
    def _check_account(user, account):
        from src.profile_text import member_fields_error
        error = member_fields_error(account, member=user)
        if error:
            _refuse(error)
        username = account.get('username', '')
        # The page ignores a blank username; a taken one would be a database error.
        if username and username != user.username and (
                ParliamentUser.objects.filter(username=username).exclude(user_id=user.user_id).exists()):
            _refuse('That username is taken.')

    @staticmethod
    def _check_public(user, public):
        _check_lengths(public)
        if public.get('graduation_semester', '').strip() not in ('', *GRADUATION_SEMESTERS):
            _refuse('Graduation semester must be Spring or Fall.')
        year = public.get('graduation_year', '').strip()
        if year and not (year.isdigit() and 1900 <= int(year) <= 2100):
            _refuse('Graduation year must be a year, like 2027.')
        if public.get('big_brother', '').strip() == user.user_id:
            _refuse('You can’t be your own big brother.')


class ProfilePhotoView(_Profile):
    parser_classes = [MultiPartParser]

    def post(self, request):
        """v4.4.0: a new photo, multipart ``photo`` (JPEG, PNG or WebP, at most 5 MB)."""
        upload = request.FILES.get('photo')
        if upload is None:
            _refuse('Choose a photo to upload.')
        try:
            pv.save_profile_picture(request, request.user, upload)
        except ValidationError as e:
            _refuse(' '.join(e.messages))
        return self.done(request.user)

    def delete(self, request):
        if not pv.remove_profile_picture(request, request.user):
            raise exceptions.NotFound('No profile picture to remove.')
        return self.done(request.user)


class RoleHistoryListView(_Profile):

    def post(self, request):
        body = _body(request)
        post = {'rh_role_name': body.get('role_name', ''), 'rh_start_semester': body.get('start_semester', ''),
                'rh_end_semester': body.get('end_semester', '')}
        _check_text(request.user, post)
        _check_lengths({'role_name': post['rh_role_name'], 'start_semester': post['rh_start_semester'],
                        'end_semester': post['rh_end_semester']}, model=RoleHistory)
        try:
            rh = pv.add_role_history(request.user, post)
        except pv.ProfileEditError as e:
            _edit_error(e)
        return self.done(request.user, status=201, id=rh.id)


class RoleHistoryItemView(_Profile):

    def put(self, request, rh_id):
        body = _body(request)
        post = {'rh_id': str(rh_id), 'rh_role_name': body.get('role_name', ''),
                'rh_start_semester': body.get('start_semester', ''), 'rh_end_semester': body.get('end_semester', '')}
        _check_text(request.user, post)
        _check_lengths({'role_name': post['rh_role_name'], 'start_semester': post['rh_start_semester'],
                        'end_semester': post['rh_end_semester']}, model=RoleHistory)
        try:
            pv.edit_role_history(request.user, post)
        except pv.ProfileEditError as e:
            _edit_error(e)
        return self.done(request.user)

    patch = put

    def delete(self, request, rh_id):
        if not pv.delete_role_history(request.user, {'rh_id': str(rh_id)}):
            raise exceptions.NotFound('Role history entry not found.')
        return self.done(request.user)


def _added(view, request, helper, post):
    _check_text(request.user, post)
    try:
        helper(request.user, post)
    except pv.ProfileEditError as e:
        _edit_error(e)
    return view.done(request.user, status=201)


def _removed(view, request, helper, post):
    try:
        removed = helper(request.user, post)
    except pv.ProfileEditError as e:
        _edit_error(e)
    if not removed:
        raise exceptions.NotFound('That item is no longer on your profile.')
    return view.done(request.user)


class CustomSocialListView(_Profile):

    def post(self, request):
        body = _body(request)
        return _added(self, request, pv.add_custom_social,
                      {'cs_platform': body.get('platform', ''), 'cs_handle': body.get('handle', '')})


class CustomSocialItemView(_Profile):

    def delete(self, request, index):
        return _removed(self, request, pv.delete_custom_social, {'cs_index': str(index)})


class InitiationChapterListView(_Profile):

    def post(self, request):
        body = _body(request)
        return _added(self, request, pv.add_initiation_chapter,
                      {'ic_school': body.get('school', ''), 'ic_chapter': body.get('chapter', ''),
                       'ic_role_number': body.get('role_number', '')})


class InitiationChapterItemView(_Profile):

    def delete(self, request, index):
        return _removed(self, request, pv.delete_initiation_chapter, {'ic_index': str(index)})


class AcademicListView(_Profile):

    def post(self, request):
        body = _body(request)
        return _added(self, request, pv.add_academic_item,
                      {'ai_type': body.get('type', ''), 'ai_value': body.get('value', '')})


class AcademicItemView(_Profile):

    def delete(self, request, kind, index):
        return _removed(self, request, pv.delete_academic_item, {'ai_type': kind, 'ai_index': str(index)})
