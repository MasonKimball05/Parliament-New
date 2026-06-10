"""
Read-only API viewsets for Parliament's 3.0.0 API layer.

All endpoints require authentication (APIToken) and the 'rest_api' feature flag.
Each viewset declares a ``required_scope`` that the token must carry.
Write operations are intentionally disabled at this stage.
"""
from django.db import models
from django.utils import timezone

from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from src.models.users import ParliamentUser
from src.models.events import Event, Attendance
from src.models.legislation import Legislation
from src.models.committees import Committee
from src.models.api import APIAccessLog

from .authentication import APITokenAuthentication
from .pagination import ParliamentAPIPagination
from .permissions import APIEnabled, ScopePermission
from .serializers import (
    MemberSerializer, EventSerializer, LegislationSerializer,
    CommitteeSerializer, AttendanceSerializer,
)


# ---------------------------------------------------------------------------
# Logging mixin — attached to every viewset
# ---------------------------------------------------------------------------

class APILoggingMixin:
    """
    Record an APIAccessLog entry for every API response.

    Runs in ``finalize_response`` so the HTTP status code is known.
    Exceptions here are silently swallowed — logging must never break the API.
    """

    # Maps endpoint path fragments to the serializer field used as a display identifier
    _SAMPLE_FIELD_MAP = {
        '/members/': 'display_name',
        '/events/': 'title',
        '/legislation/': 'title',
        '/committees/': 'name',
        '/attendance/': 'event_title',
    }

    def _build_response_summary(self, path, response_data):
        """
        Return {"count": N, "sample": ["Name 1", ...up to 5]} from response.data.
        Falls back gracefully if data shape is unexpected.
        """
        try:
            if not isinstance(response_data, list):
                # Detail endpoint — single record
                field = next(
                    (f for seg, f in self._SAMPLE_FIELD_MAP.items() if seg in path),
                    None,
                )
                name = response_data.get(field) if field else None
                return {'count': 1, 'sample': [name] if name else []}

            count = len(response_data)
            field = next(
                (f for seg, f in self._SAMPLE_FIELD_MAP.items() if seg in path),
                None,
            )
            if not field:
                return {'count': count, 'sample': []}

            sample = []
            for record in response_data[:5]:
                val = record.get(field)
                if val:
                    sample.append(str(val))
            return {'count': count, 'sample': sample}
        except Exception:
            return {}

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        try:
            from src.logging_utils import get_client_ip
            token = getattr(request, '_api_token', None)
            user = request.user if request.user.is_authenticated else None
            scope = getattr(self, 'required_scope', None)

            query_params = request.GET.dict() if request.GET else {}
            response_summary = {}
            if response.status_code < 400 and hasattr(response, 'data') and response.data is not None:
                response_summary = self._build_response_summary(request.path, response.data)

            APIAccessLog.objects.create(
                token=token,
                token_key_prefix=(token.key[:8] if token else ''),
                user=user,
                username=(user.username if user else ''),
                endpoint=request.path,
                method=request.method,
                ip_address=get_client_ip(request),
                response_status=response.status_code,
                scopes_used=([scope] if scope else []),
                query_params=query_params,
                response_summary=response_summary,
            )
        except Exception:
            pass  # Never let logging break an API response
        return response


# ---------------------------------------------------------------------------
# Viewsets
# ---------------------------------------------------------------------------

_API_PERMISSIONS = [IsAuthenticated, APIEnabled, ScopePermission]
_API_AUTH = [APITokenAuthentication]


class MemberViewSet(APILoggingMixin, viewsets.ReadOnlyModelViewSet):
    """
    GET /api/v1/members/         — active member directory
    GET /api/v1/members/{id}/    — single member detail
    GET /api/v1/members/me/      — the requesting user's own record
    """
    serializer_class = MemberSerializer
    lookup_field = 'user_id'
    authentication_classes = _API_AUTH
    permission_classes = _API_PERMISSIONS
    pagination_class = ParliamentAPIPagination
    required_scope = 'members:read'

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


class EventViewSet(APILoggingMixin, viewsets.ReadOnlyModelViewSet):
    """
    GET /api/v1/events/          — events visible to the requesting user
    GET /api/v1/events/{id}/     — single event detail (visibility enforced)
    GET /api/v1/events/upcoming/ — events in the next 30 days
    """
    serializer_class = EventSerializer
    authentication_classes = _API_AUTH
    permission_classes = _API_PERMISSIONS
    pagination_class = None
    required_scope = 'events:read'

    def _base_queryset(self):
        return (
            Event.objects
            .filter(is_active=True, archived=False)
            .select_related('created_by')
            .order_by('date_time')
        )

    def get_queryset(self):
        return self._base_queryset()

    def _visible_events(self):
        user = self.request.user
        return [e for e in self._base_queryset() if e.is_visible_to_user(user)]

    def list(self, request, *args, **kwargs):
        serializer = self.get_serializer(self._visible_events(), many=True)
        return Response(serializer.data)

    def retrieve(self, request, *args, **kwargs):
        from django.shortcuts import get_object_or_404
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


class LegislationViewSet(APILoggingMixin, viewsets.ReadOnlyModelViewSet):
    """
    GET /api/v1/legislation/         — all non-removed legislation
    GET /api/v1/legislation/{id}/    — single item detail
    GET /api/v1/legislation/active/  — currently open for voting
    """
    serializer_class = LegislationSerializer
    authentication_classes = _API_AUTH
    permission_classes = _API_PERMISSIONS
    pagination_class = ParliamentAPIPagination
    required_scope = 'legislation:read'

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


class CommitteeViewSet(APILoggingMixin, viewsets.ReadOnlyModelViewSet):
    """
    GET /api/v1/committees/        — all active, non-archived committees visible to the user
    GET /api/v1/committees/{id}/   — single committee detail (visibility enforced)
    GET /api/v1/committees/mine/   — committees the requesting user belongs to
    """
    serializer_class = CommitteeSerializer
    authentication_classes = _API_AUTH
    permission_classes = _API_PERMISSIONS
    pagination_class = ParliamentAPIPagination
    required_scope = 'committees:read'

    def get_queryset(self):
        return (
            Committee.objects
            .filter(is_active=True, is_archived=False)
            .prefetch_related('chairs', 'members')
            .order_by('name')
        )

    def list(self, request, *args, **kwargs):
        qs = [c for c in self.get_queryset() if c.is_visible_to(request.user)]
        serializer = self.get_serializer(qs, many=True)
        return Response(serializer.data)

    def retrieve(self, request, *args, **kwargs):
        from django.shortcuts import get_object_or_404
        committee = get_object_or_404(Committee, pk=kwargs['pk'], is_active=True, is_archived=False)
        if not committee.is_visible_to(request.user):
            raise PermissionDenied()
        serializer = self.get_serializer(committee)
        return Response(serializer.data)

    @action(detail=False, methods=['get'], url_path='mine')
    def mine(self, request):
        user = request.user
        qs = (
            Committee.objects
            .filter(is_active=True, is_archived=False)
            .filter(
                models.Q(members=user) | models.Q(chairs=user)
            )
            .prefetch_related('chairs', 'members')
            .distinct()
            .order_by('name')
        )
        serializer = self.get_serializer(qs, many=True)
        return Response(serializer.data)


class AttendanceViewSet(APILoggingMixin, viewsets.ReadOnlyModelViewSet):
    """
    GET /api/v1/attendance/        — own attendance records (most recent 100)
    GET /api/v1/attendance/{id}/   — single record (own only)

    Query params:
      ?type=event|committee         — filter by attendance_type
      ?year=2026                    — filter by year
    """
    serializer_class = AttendanceSerializer
    authentication_classes = _API_AUTH
    permission_classes = _API_PERMISSIONS
    pagination_class = None
    required_scope = 'attendance:read'

    def get_queryset(self):
        user = self.request.user
        qs = (
            Attendance.objects
            .filter(user=user)
            .select_related('event', 'committee')
            .order_by('-created_at')
        )
        attendance_type = self.request.query_params.get('type')
        if attendance_type in ('event', 'committee'):
            qs = qs.filter(attendance_type=attendance_type)
        year = self.request.query_params.get('year')
        if year and year.isdigit():
            qs = qs.filter(created_at__year=int(year))
        return qs

    def list(self, request, *args, **kwargs):
        qs = self.get_queryset()[:100]
        serializer = self.get_serializer(qs, many=True)
        return Response(serializer.data)
