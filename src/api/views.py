"""
Read-only API viewsets for Parliament's 3.0.0 API layer.

All endpoints require authentication (token or session).
Write operations are intentionally disabled at this stage.
The entire API is gated behind the 'rest_api' feature flag (disabled by default).
"""
from django.utils import timezone

from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response

from src.models.users import ParliamentUser
from src.models.events import Event
from src.models.legislation import Legislation
from src.models_feature_flags import FeatureFlag
from .serializers import MemberSerializer, EventSerializer, LegislationSerializer


class APIEnabled(BasePermission):
    """Blocks all API access when the 'rest_api' feature flag is disabled."""
    message = 'The Parliament API is not currently enabled.'

    def has_permission(self, request, view):
        return FeatureFlag.is_feature_enabled('rest_api')


_API_PERMISSIONS = [IsAuthenticated, APIEnabled]


class MemberViewSet(viewsets.ReadOnlyModelViewSet):
    """
    GET /api/v1/members/         — active member directory
    GET /api/v1/members/{id}/    — single member detail
    GET /api/v1/members/me/      — the requesting user's own record
    """
    serializer_class = MemberSerializer
    lookup_field = 'user_id'
    permission_classes = _API_PERMISSIONS

    def get_queryset(self):
        return (
            ParliamentUser.objects
            .filter(is_active=True, member_status='Active')
            .prefetch_related('roles')
            .order_by('name')
        )

    @action(detail=False, methods=['get'], url_path='me')
    def me(self, request):
        serializer = self.get_serializer(request.user)
        return Response(serializer.data)


class EventViewSet(viewsets.ReadOnlyModelViewSet):
    """
    GET /api/v1/events/          — events visible to the requesting user
    GET /api/v1/events/{id}/     — single event detail (visibility enforced)
    GET /api/v1/events/upcoming/ — events in the next 30 days
    """
    serializer_class = EventSerializer
    permission_classes = _API_PERMISSIONS

    def _base_queryset(self):
        return (
            Event.objects
            .filter(is_active=True, archived=False)
            .select_related('created_by')
            .order_by('date_time')
        )

    def get_queryset(self):
        # Returns a real queryset; visibility is enforced in list/retrieve/upcoming.
        return self._base_queryset()

    def _visible_events(self):
        user = self.request.user
        return [e for e in self._base_queryset() if e.is_visible_to_user(user)]

    def list(self, request, *args, **kwargs):
        serializer = self.get_serializer(self._visible_events(), many=True)
        return Response(serializer.data)

    def retrieve(self, request, *args, **kwargs):
        from django.shortcuts import get_object_or_404
        from rest_framework.exceptions import PermissionDenied
        event = get_object_or_404(Event, pk=kwargs['pk'], is_active=True, archived=False)
        if not event.is_visible_to_user(request.user):
            raise PermissionDenied()
        serializer = self.get_serializer(event)
        return Response(serializer.data)

    @action(detail=False, methods=['get'], url_path='upcoming')
    def upcoming(self, request):
        now = timezone.now()
        cutoff = now + timezone.timedelta(days=30)
        filtered = [e for e in self._visible_events() if now <= e.date_time <= cutoff]
        serializer = self.get_serializer(filtered, many=True)
        return Response(serializer.data)


class LegislationViewSet(viewsets.ReadOnlyModelViewSet):
    """
    GET /api/v1/legislation/         — all non-removed legislation
    GET /api/v1/legislation/{id}/    — single item detail
    GET /api/v1/legislation/active/  — currently open for voting
    """
    serializer_class = LegislationSerializer
    permission_classes = _API_PERMISSIONS

    def get_queryset(self):
        return (
            Legislation.objects
            .exclude(status='removed')
            .filter(is_active=True)
            .select_related('posted_by')
            .prefetch_related('co_authors')
            .order_by('-created_at')
        )

    @action(detail=False, methods=['get'], url_path='active')
    def active(self, request):
        now = timezone.now()
        qs = self.get_queryset().filter(
            status='active',
            voting_closed=False,
            available_at__lte=now,
        )
        serializer = self.get_serializer(qs, many=True)
        return Response(serializer.data)
