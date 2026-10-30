"""
The app's Committees screens (mobile plan, Phase 3e).

* ``GET committees/`` — the member's committees with their role in each
  (``committee_index``). Admins may pass ``?all=1`` for every committee, as
  the web page's "show all".
* ``GET committees/<code>/`` — the committee dashboard (``committee_home``):
  your roles, counts, recent documents, open ballots, the committee's chat,
  and links to the parts that stay on the website (chair tools and the
  education, recruitment, slating and Kai dashboards).
* ``GET committees/<code>/members/`` — chairs, advisors and members, with who
  votes (the people section of ``committee_home``).
* ``GET committees/<code>/documents/`` — the documents this member may see
  (``committee_documents``), plus unpublished minutes for members and above.
* ``GET committees/<code>/documents/<id>/file/`` — one document's file, for
  the app's viewer (``view_committee_document``'s ``can_user_view`` check).
* ``GET committees/<code>/minutes/`` — the minutes list
  (``committee_minutes_list``); published minutes open as their document.

Who may open a committee is ``committee_home``'s rule: a member, chair,
advisor, the VP (the committee's role), its admin, or a site admin.
"""
import mimetypes
import os

from django.db.models import Count
from django.http import FileResponse
from django.urls import reverse
from rest_framework import exceptions
from rest_framework.response import Response

from src.feature_flag_decorators import check_feature_enabled
from src.mobile.api import MobileAPIView
from src.mobile.deeplinks import _committee_channel_id
from src.models import ChapterMinutes, Committee, CommitteeDocument, CommitteePermissions
from src.models.users import member_defer
from src.utils.content_disposition import apply_disposition
from src.view.committee.committee_minutes_editor import is_committee_member_or_above
from src.view.committee.member_permissions import can_designate

RECENT_DOCUMENTS = 5
DOCUMENT_LIMIT = 200
MINUTES_LIMIT = 100


def _require_committees():
    if not check_feature_enabled('committee_system'):
        raise exceptions.PermissionDenied('Committees are off.', code='feature_disabled')


def _person(user):
    return {
        'user_id': user.user_id,
        'name': user.get_display_name(),
        'photo': f'members/{user.user_id}/photo/' if user.profile_picture else None,
    }


class _Access:
    """The viewer's standing in one committee, as ``committee_home`` works it out."""

    def __init__(self, user, committee):
        self.is_member = committee.members.filter(pk=user.pk).exists()
        self.is_chair = committee.chairs.filter(pk=user.pk).exists()
        self.is_advisor = committee.advisors.filter(pk=user.pk).exists()
        self.is_voting_member = committee.voting_members.filter(pk=user.pk).exists()
        self.is_vp = bool(committee.role_id) and user.roles.filter(id=committee.role_id).exists()
        self.is_committee_admin = committee.admin_id is not None and committee.admin_id == user.pk
        self.allowed = (self.is_member or self.is_chair or self.is_advisor or self.is_vp
                        or user.is_admin or self.is_committee_admin)
        self.can_manage = self.is_chair or self.is_vp or user.is_admin or self.is_committee_admin
        # committee_minutes_list: members, chairs (incl. exec board), officers, admins.
        self.can_view_minutes = is_committee_member_or_above(user, committee, is_committee_member=self.is_member)

    def roles(self):
        return [name for name, on in (('VP', self.is_vp), ('Chair', self.is_chair),
                                      ('Advisor', self.is_advisor), ('Member', self.is_member)) if on]


def _permissions(user, committee, access):
    """``committee_home`` creates the row on a first visit; the app does the same."""
    perm, _ = CommitteePermissions.objects.get_or_create(
        committee=committee, user=user,
        defaults={'can_view_docs': True, 'can_vote': access.is_voting_member,
                  'can_upload_docs': access.is_chair, 'can_manage_members': False,
                  'can_view_results': True})
    return perm


def _kinds(c):
    return {
        'exec_board': c.is_exec_board, 'slating': c.is_slating_committee, 'kai': c.is_kai_committee,
        'recruitment': c.is_recruitment_committee, 'education': c.is_education_committee,
        'chapter': c.is_chapter_committee, 'ad_hoc': c.is_ad_hoc,
    }


def _document_json(doc, code):
    name = os.path.basename(doc.document.name or '') if doc.document else ''
    return {
        'id': doc.id,
        'title': doc.title,
        'description': doc.description or '',
        'type': doc.get_document_type_display(),
        'uploaded_at': doc.uploaded_at.isoformat() if doc.uploaded_at else None,
        'uploaded_by': doc.uploaded_by.get_display_name() if doc.uploaded_by else '',
        'file_name': name,
        'extension': os.path.splitext(name)[1].lstrip('.').lower(),
        'published_to_chapter': doc.published_to_chapter,
        'file': f'committees/{code}/documents/{doc.id}/file/',
    }


def _visible_documents(user, committee, access):
    docs = (CommitteeDocument.objects.filter(committee=committee)
            .select_related('committee', 'uploaded_by')
            .defer(*member_defer('uploaded_by'))
            .order_by('-uploaded_at'))
    return [d for d in docs if d.can_user_view(user, is_committee_member=access.is_member,
                                               is_committee_chair=access.is_chair)]


class _CommitteeView(MobileAPIView):
    web_page = 'committee_home'

    def committee(self, request, code):
        _require_committees()
        committee = Committee.objects.select_related('role').filter(code=code).first()
        if committee is None:
            raise exceptions.NotFound()
        access = _Access(request.user, committee)
        if not access.allowed:
            raise exceptions.PermissionDenied('You don’t have access to this committee.', code='no_access')
        return committee, access


class CommitteeListView(MobileAPIView):
    web_page = 'committee_index'

    def get(self, request):
        _require_committees()
        user = request.user
        show_all = request.query_params.get('all') == '1' and user.is_admin
        chair_ids = set(user.chair_roles.values_list('id', flat=True))
        advisor_ids = set(user.advisor_roles.values_list('id', flat=True))
        member_ids = set(user.committees.values_list('id', flat=True))
        voting_ids = set(user.committee_voters.values_list('id', flat=True))
        if show_all:
            rows = list(Committee.objects.select_related('role').order_by('name'))
        else:
            mine = chair_ids | advisor_ids | member_ids
            rows = [c for c in Committee.objects.select_related('role').filter(id__in=mine).order_by('name')
                    if c.is_visible_to(user)]
        rows = [c for c in rows if not c.is_archived and c.code]   # every committee page is addressed by code
        vps = Committee.vp_map(rows)
        counts = dict(Committee.objects.filter(id__in=[c.pk for c in rows])
                      .annotate(n=Count('members', distinct=True)).values_list('id', 'n'))
        out = []
        for c in rows:
            vp = vps.get(c.pk)
            out.append({
                'code': c.code,
                'name': c.name,
                'roles': [name for name, ids in (('Chair', chair_ids), ('Advisor', advisor_ids),
                                                  ('Member', member_ids)) if c.pk in ids],
                'is_voting_member': c.pk in voting_ids,
                'vp': vp.get_display_name() if vp else None,
                'member_count': counts.get(c.pk, 0),
                'kinds': _kinds(c),
            })
        return Response({'committees': out, 'show_all': show_all, 'can_show_all': user.is_admin})


class CommitteeDashboardView(_CommitteeView):

    def get(self, request, code):
        from src.mobile.views_votes import _committee_on_ballot
        user = request.user
        committee, access = self.committee(request, code)
        perm = _permissions(user, committee, access)
        vp = committee.get_vp()

        documents = _visible_documents(user, committee, access) if perm.can_view_docs else []
        can_vote = access.is_voting_member or access.is_chair or perm.can_vote
        open_votes = (_committee_on_ballot(committee).count()
                      if can_vote and check_feature_enabled('committee_voting') else None)
        people = (committee.members.all() | committee.chairs.all() | committee.advisors.all()).distinct().count()

        def web(title, path, kind):
            return {'title': title, 'path': path, 'kind': kind}

        links = []
        if committee.is_education_committee:
            links.append(web('Education dashboard', reverse('education_home', args=[code]), 'dashboard'))
        if committee.is_recruitment_committee:
            links.append(web('Recruitment dashboard', reverse('recruitment_dashboard', args=[code]), 'dashboard'))
        if committee.is_slating_committee:
            links.append(web('Slating dashboard', reverse('slating_dashboard'), 'dashboard'))
        if committee.is_kai_committee:
            links.append(web('Kai reports', reverse('view_kai_reports'), 'dashboard'))
        if access.can_manage and not committee.is_archived:
            links += [
                web('Upload a document', reverse('upload_document', args=[code]), 'tool'),
                web('Create a vote', reverse('create_committee_vote', args=[code]), 'tool'),
                web('Push to chapter', reverse('push_to_chapter', args=[code]), 'tool'),
                web('Take attendance', reverse('committee_attendance', args=[code]), 'tool'),
                web('Manage members', reverse('manage_members', args=[code]), 'tool'),
            ]
            if check_feature_enabled('chats'):
                links.append(web('Chat permissions', reverse('manage_chat_permissions', args=[code]), 'tool'))
            if can_designate(user, committee):
                links.append(web('Member permissions', reverse('committee_member_permissions', args=[code]), 'tool'))
        elif not committee.is_archived:
            # Members a chair has designated (Member Permissions page).
            if perm.can_upload_docs:
                links.append(web('Upload a document', reverse('upload_document', args=[code]), 'tool'))
            if perm.can_manage_members:
                links.append(web('Manage members', reverse('manage_members', args=[code]), 'tool'))
        if access.can_view_minutes:
            links.append(web('Write minutes', reverse('committee_minutes_list', args=[code]), 'tool'))

        return Response({
            'committee': {
                'code': committee.code,
                'name': committee.name,
                'role': committee.role.name if committee.role else None,
                'vp': _person(vp) if vp else None,
                'archived': committee.is_archived,
                'active': committee.is_active,
                'kinds': _kinds(committee),
            },
            'me': {
                'roles': access.roles(),
                'is_voting_member': access.is_voting_member,
                'can_manage': access.can_manage,
                'can_view_docs': perm.can_view_docs,
                'can_vote': can_vote,
                'can_view_minutes': access.can_view_minutes,
            },
            'stats': {
                'people': people,
                'voting_members': committee.voting_members.count(),
                'documents': len(documents),
                'open_votes': open_votes,
            },
            'recent_documents': [_document_json(d, code) for d in documents[:RECENT_DOCUMENTS]],
            'chat_channel_id': _committee_channel_id(code) if check_feature_enabled('chats') else None,
            'web_links': links,
        })


class CommitteeMembersView(_CommitteeView):

    def get(self, request, code):
        committee, _ = self.committee(request, code)
        voting = set(committee.voting_members.values_list('pk', flat=True))

        def group(qs):
            return [{**_person(u), 'votes': u.pk in voting}
                    for u in qs.defer(*member_defer()).order_by('name')]

        chairs = group(committee.chairs.all())
        chair_ids = {c['user_id'] for c in chairs}
        return Response({
            'chairs': chairs,
            'advisors': group(committee.advisors.all()),
            'members': [m for m in group(committee.members.all()) if m['user_id'] not in chair_ids],
        })


class CommitteeDocumentsView(_CommitteeView):
    web_page = 'committee_documents'

    def get(self, request, code):
        user = request.user
        committee, access = self.committee(request, code)
        if not _permissions(user, committee, access).can_view_docs:
            raise exceptions.PermissionDenied('You can’t view this committee’s documents.', code='no_access')
        docs = _visible_documents(user, committee, access)[:DOCUMENT_LIMIT]
        drafts = []
        if access.can_view_minutes:
            drafts = [{'id': m.id, 'title': m.title, 'date': m.date.isoformat(), 'status': m.status}
                      for m in ChapterMinutes.objects.filter(committee=committee).exclude(status='published')
                      .order_by('-date')]
        return Response({'documents': [_document_json(d, code) for d in docs], 'unpublished_minutes': drafts})


class CommitteeDocumentFileView(_CommitteeView):
    web_page = 'committee_documents'

    def get(self, request, code, document_id):
        committee, _ = self.committee(request, code)
        doc = CommitteeDocument.objects.filter(pk=document_id, committee=committee).first()
        if doc is None or not doc.can_user_view(request.user) or not doc.document:
            raise exceptions.NotFound()
        try:
            handle = doc.document.open('rb')
        except (FileNotFoundError, OSError):
            raise exceptions.NotFound()
        name = os.path.basename(doc.document.name)
        content_type = mimetypes.guess_type(name)[0] or 'application/octet-stream'
        response = FileResponse(handle, content_type=content_type)
        apply_disposition(response, content_type, name)   # RFC 5987 name, nosniff
        response['Cache-Control'] = 'private, no-store'
        return response


class CommitteeMinutesView(_CommitteeView):
    web_page = 'committee_minutes_list'
    web_page_toggle = False   # the web page has no @require_page_enabled

    def get(self, request, code):
        user = request.user
        committee, access = self.committee(request, code)
        if not access.can_view_minutes:
            raise exceptions.PermissionDenied('You can’t view this committee’s minutes.', code='no_access')
        rows = (ChapterMinutes.objects.filter(committee=committee)
                .select_related('published_document')
                .order_by('-date', '-start_time')[:MINUTES_LIMIT])
        out = []
        for m in rows:
            doc = m.published_document
            visible = doc is not None and doc.document and doc.can_user_view(
                user, is_committee_member=access.is_member, is_committee_chair=access.is_chair)
            out.append({
                'id': m.id,
                'title': m.title,
                'date': m.date.isoformat(),
                'start_time': m.start_time.strftime('%H:%M') if m.start_time else None,
                'status': m.status,
                'document': _document_json(doc, code) if visible else None,
                'edit_path': reverse('edit_committee_minutes', args=[code, m.id]),
            })
        return Response({'minutes': out})
