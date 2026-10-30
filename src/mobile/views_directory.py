"""
The app's Directory (mobile plan, Phase 3b).

* ``GET members/`` — everyone the web directory lists (``src/view/directory.py``):
  active members by type, advisors, and with ``?alumni=1`` the alumni (the
  1800s founders stay off, as on the web unless asked for twice). Contact
  details are the same ones the web list prints to every member.
* ``GET members/<user_id>/`` — the directory card, built by the web's own
  ``profile_card_data`` (so a pledge viewer gets the same masking).
* ``GET members/<user_id>/photo/`` — the profile picture. ``/media/`` needs a
  website session, which the app's image requests don't have.

A pledge viewer gets no roll numbers or member ids in the list either, the
card's rule applied to the list too.
"""
import hashlib
import mimetypes

from django.http import FileResponse
from rest_framework import exceptions
from rest_framework.response import Response

from src.mobile.api import MobileAPIView
from src.models import ParliamentUser
from src.pledge_classes import is_original_founder
from src.view.profile_card import card_queryset, profile_card_data

#: Display order of the groups (the web directory's sections).
GROUPS = ('Officer', 'Chair', 'Member', 'Pledge', 'Advisor')


def photo_path(m):
    """
    The member's photo URL for the app, or None. ``?v=`` changes whenever the
    picture does (10-29-26): the app keeps photos on the phone and downloads one
    again only when this path changes. The stored file name is the version;
    uploads get a fresh name each time (profile_view), and the photo view
    ignores the query.
    """
    if not m.profile_picture:
        return None
    version = hashlib.sha256(m.profile_picture.name.encode()).hexdigest()[:12]
    return f'members/{m.user_id}/photo/?v={version}'


def _row(m, viewer_is_pledge):
    return {
        'user_id': m.user_id,
        'name': m.get_display_name(),
        'full_name': m.name,
        'member_type': m.member_type,
        'member_status': m.member_status,
        'role_number': None if viewer_is_pledge else (m.role_number or None),
        'email': m.email or '',
        'phone': m.phone_number or '',
        'roles': [r.name for r in m.roles.all()],
        'photo': photo_path(m),
    }


class _DirectoryBase(MobileAPIView):
    web_page = 'member_directory'
    web_page_toggle = False   # the web page has no @require_page_enabled


class MemberListView(_DirectoryBase):

    def get(self, request):
        viewer_is_pledge = request.user.member_type == 'Pledge'
        qs = ParliamentUser.objects.prefetch_related('roles').order_by('name')
        active = list(qs.filter(member_status='Active', member_type__in=GROUPS))
        active.sort(key=lambda m: GROUPS.index(m.member_type))   # stable: name order kept
        rows = [_row(m, viewer_is_pledge) for m in active]
        if request.query_params.get('alumni') == '1':
            rows += [_row(m, viewer_is_pledge) for m in qs.filter(member_status='Alumni')
                     if not is_original_founder(m.pledge_class, m.pledge_class_greek)]
        return Response({'members': rows})


class MemberCardView(_DirectoryBase):

    def get(self, request, user_id):
        member = card_queryset().filter(user_id=user_id).first()
        if member is None:
            raise exceptions.NotFound()
        card = profile_card_data(member, request.user)
        card['photo'] = photo_path(member)
        return Response({'member': card})


class MemberPhotoView(_DirectoryBase):

    def get(self, request, user_id):
        member = ParliamentUser.objects.filter(user_id=user_id).only('profile_picture').first()
        if member is None or not member.profile_picture:
            raise exceptions.NotFound()
        try:
            handle = member.profile_picture.open('rb')
        except (FileNotFoundError, OSError):
            raise exceptions.NotFound()
        content_type = mimetypes.guess_type(member.profile_picture.name)[0] or 'application/octet-stream'
        response = FileResponse(handle, content_type=content_type)
        # A versioned path (photo_path's ?v=) never changes content.
        response['Cache-Control'] = ('private, max-age=31536000, immutable' if request.GET.get('v')
                                     else 'private, max-age=86400')
        return response
