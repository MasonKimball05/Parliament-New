"""
``/api/mobile/v1/`` routes (mobile plan, Phase 1).

Every view here must authenticate with ``MobileTokenAuthentication`` (or be
one of the explicitly listed pre-auth endpoints, which mint nothing usable
without the full sign-in). Enforced by
``src/tests/guards/test_mobile_views_use_mobile_auth.py``.
"""
from django.urls import path

from src.mobile import (
    views_auth, views_devices, views_home, views_notifications, views_push, views_session,
)

app_name = 'mobile'

# URL names are prefixed `mobile_` so they can't collide with a web page name in
# PledgePageRestriction (which matches on the bare url_name); `mobile_auth_*`
# and `mobile_me` are on that middleware's always-allowed list.
urlpatterns = [
    # auth (src/mobile/views_auth.py)
    path('auth/login/', views_auth.LoginView.as_view(), name='mobile_auth_login'),
    path('auth/verify/', views_auth.VerifyView.as_view(), name='mobile_auth_verify'),
    path('auth/passkey/begin/', views_auth.PasskeyBeginView.as_view(), name='mobile_auth_passkey_begin'),
    path('auth/passkey/complete/', views_auth.PasskeyCompleteView.as_view(),
         name='mobile_auth_passkey_complete'),
    path('auth/refresh/', views_auth.RefreshView.as_view(), name='mobile_auth_refresh'),
    path('auth/logout/', views_auth.LogoutView.as_view(), name='mobile_auth_logout'),
    path('me/', views_auth.MeView.as_view(), name='mobile_me'),
    # web-view handoff code + websocket tickets (src/mobile/views_session.py)
    path('auth/web-session/', views_session.WebSessionCodeView.as_view(), name='mobile_auth_web_session'),
    path('ws-ticket/', views_session.WsTicketView.as_view(), name='mobile_ws_ticket'),
    # devices (src/mobile/views_devices.py) + push token (src/mobile/views_push.py)
    path('devices/', views_devices.DeviceListView.as_view(), name='mobile_devices'),
    path('devices/<uuid:public_id>/revoke/', views_devices.DeviceRevokeView.as_view(),
         name='mobile_device_revoke'),
    path('devices/push-token/', views_push.PushTokenView.as_view(), name='mobile_push_token'),
    # Phase 3 screens. Each view names the web page it mirrors (`web_page`), so
    # that page's toggle and pledge restriction apply here too.
    path('home/', views_home.HomeView.as_view(), name='mobile_home'),
    path('notifications/', views_notifications.NotificationListView.as_view(),
         name='mobile_notifications'),
    path('notifications/count/', views_notifications.NotificationCountView.as_view(),
         name='mobile_notifications_count'),
    path('notifications/<int:notification_id>/read/',
         views_notifications.NotificationReadView.as_view(), name='mobile_notification_read'),
    path('notifications/read-all/', views_notifications.NotificationReadAllView.as_view(),
         name='mobile_notifications_read_all'),
]
