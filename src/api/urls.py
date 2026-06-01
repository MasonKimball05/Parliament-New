"""
API URL configuration — all routes are prefixed /api/v1/ in the root urls.py.
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework.authtoken.views import obtain_auth_token

from .views import MemberViewSet, EventViewSet, LegislationViewSet

router = DefaultRouter(trailing_slash=True)
router.register(r'members', MemberViewSet, basename='api-member')
router.register(r'events', EventViewSet, basename='api-event')
router.register(r'legislation', LegislationViewSet, basename='api-legislation')

urlpatterns = [
    path('', include(router.urls)),
    # POST /api/v1/auth/token/  → exchange username+password for a token
    path('auth/token/', obtain_auth_token, name='api-token-auth'),
]
