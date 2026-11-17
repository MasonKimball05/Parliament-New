"""
The role transition checklist in the app (11-17-26), once a website page only
(``transition_checklist`` / ``toggle_checklist_item``,
src/view/officer/transitions.py).

* ``GET  transitions/checklist/<role_history_id>/`` — one term's handoff tasks,
  in order, with who finished each and when, and the progress.
* ``POST transitions/checklist/items/<status_id>/toggle/`` — mark one done, or
  not done again.

Who may: the website's ``_can_access_checklist`` — officers, chairs and admins,
and the incoming holder (who may not be an officer yet). Anyone else gets a 404
(the checklist belongs to another member).
"""
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import exceptions
from rest_framework.response import Response

from src.mobile.api import MobileAPIView
from src.models import RoleHistory, TransitionChecklistStatus
from src.models.users import member_defer
from src.view.officer.transitions import _can_access_checklist


def _iso(v):
    return v.isoformat() if v else None


def _progress(role_history):
    agg = TransitionChecklistStatus.objects.filter(role_history=role_history).aggregate(
        total=Count('id'), done=Count('id', filter=Q(completed_at__isnull=False)))
    return {'done': agg['done'], 'total': agg['total']}


class TransitionChecklistView(MobileAPIView):
    web_page = 'transition_checklist'
    web_page_toggle = False   # @login_required only

    def get(self, request, role_history_id):
        rh = get_object_or_404(RoleHistory.objects.select_related('user').defer(*member_defer('user')),
                               id=role_history_id)
        if not _can_access_checklist(request.user, rh):
            raise exceptions.NotFound()
        statuses = (TransitionChecklistStatus.objects.filter(role_history=rh)
                    .select_related('item', 'completed_by').defer(*member_defer('completed_by'))
                    .order_by('item__order', 'item__id'))
        return Response({
            'role_history_id': rh.id,
            'role_name': rh.role_name,
            'holder': {'user_id': rh.user.user_id, 'name': rh.user.get_display_name()},
            'start_semester': rh.start_semester or '',
            'end_semester': rh.end_semester or '',
            'is_mine': rh.user_id == request.user.pk,
            'items': [{
                'id': s.id,
                'text': s.item.text,
                'completed': s.completed_at is not None,
                'completed_by': s.completed_by.get_display_name() if s.completed_by_id else None,
                'completed_at': _iso(s.completed_at),
            } for s in statuses],
            'progress': _progress(rh),
        })


class TransitionChecklistToggleView(MobileAPIView):
    web_page = 'toggle_checklist_item'
    web_page_toggle = False

    def post(self, request, status_id):
        """toggle_checklist_item's change."""
        status = get_object_or_404(TransitionChecklistStatus.objects.select_related('role_history', 'item'),
                                   id=status_id)
        if not _can_access_checklist(request.user, status.role_history):
            raise exceptions.NotFound()
        if status.completed_at is None:
            status.completed_by = request.user
            status.completed_at = timezone.now()
        else:
            status.completed_by = None
            status.completed_at = None
        status.save(update_fields=['completed_by', 'completed_at'])
        return Response({
            'id': status.id,
            'completed': status.completed_at is not None,
            'completed_by': request.user.get_display_name() if status.completed_at else None,
            'completed_at': _iso(status.completed_at),
            'progress': _progress(status.role_history),
        })
