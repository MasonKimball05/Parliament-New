"""
API URL configuration — all routes are prefixed /api/v1/ in the root urls.py.
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import MemberViewSet, EventViewSet, LegislationViewSet, CommitteeViewSet, AttendanceViewSet

router = DefaultRouter(trailing_slash=True)
router.register(r'members', MemberViewSet, basename='api-member')
router.register(r'events', EventViewSet, basename='api-event')
router.register(r'legislation', LegislationViewSet, basename='api-legislation')
router.register(r'committees', CommitteeViewSet, basename='api-committee')
router.register(r'attendance', AttendanceViewSet, basename='api-attendance')

urlpatterns = [
    path('', include(router.urls)),
]
