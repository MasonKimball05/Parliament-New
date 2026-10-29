"""
``/api/mobile/v1/`` routes (mobile plan, Phase 1).

Every view here must authenticate with ``MobileTokenAuthentication`` (or be
one of the explicitly listed pre-auth endpoints, which mint nothing usable
without the full sign-in). Enforced by
``src/tests/guards/test_mobile_views_use_mobile_auth.py``.
"""
from django.urls import path

from src.mobile import (
    views_announcements, views_auth, views_calendar, views_chat, views_devices, views_directory,
    views_excuses_polls, views_home, views_location, views_my, views_notifications, views_push, views_session,
    views_votes,
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
    path('events/', views_calendar.EventListView.as_view(), name='mobile_events'),
    path('events/<int:event_id>/', views_calendar.EventDetailView.as_view(), name='mobile_event'),
    path('events/<int:event_id>/signup/', views_calendar.EventSignupView.as_view(),
         name='mobile_event_signup'),
    path('events/checkin-areas/', views_location.CheckinAreaListView.as_view(),
         name='mobile_checkin_areas'),
    path('events/<int:event_id>/checkin/', views_calendar.EventCheckinView.as_view(),
         name='mobile_event_checkin'),
    # Authenticated by X-Checkin-Key, not Bearer (views_location, CheckinKeyAPIView).
    path('events/<int:event_id>/checkin/location/', views_location.LocationCheckinView.as_view(),
         name='mobile_location_checkin'),
    path('events/<int:event_id>/checkin-area/', views_location.EventCheckinAreaView.as_view(),
         name='mobile_event_checkin_area'),
    path('checkin-places/', views_location.CheckinPlaceListView.as_view(), name='mobile_checkin_places'),
    path('checkin-places/<int:place_id>/', views_location.CheckinPlaceView.as_view(),
         name='mobile_checkin_place'),
    path('devices/checkin-key/', views_location.CheckinKeyView.as_view(), name='mobile_device_checkin_key'),
    path('announcements/', views_announcements.AnnouncementListView.as_view(),
         name='mobile_announcements'),
    path('announcements/<int:announcement_id>/', views_announcements.AnnouncementDetailView.as_view(),
         name='mobile_announcement'),
    path('my/attendance/', views_my.MyAttendanceView.as_view(), name='mobile_my_attendance'),
    path('my/service-hours/', views_my.MyServiceHoursView.as_view(), name='mobile_my_service_hours'),
    path('documents/', views_my.DocumentListView.as_view(), name='mobile_documents'),
    path('documents/<int:document_id>/file/', views_my.DocumentFileView.as_view(), name='mobile_document_file'),
    path('my/excuses/', views_excuses_polls.MyExcusesView.as_view(), name='mobile_my_excuses'),
    path('my/excuses/<int:excuse_id>/cancel/', views_excuses_polls.MyExcuseCancelView.as_view(),
         name='mobile_my_excuse_cancel'),
    path('polls/', views_excuses_polls.PollListView.as_view(), name='mobile_polls'),
    path('polls/<int:announcement_id>/', views_excuses_polls.PollView.as_view(), name='mobile_poll'),
    path('members/', views_directory.MemberListView.as_view(), name='mobile_members'),
    path('members/<str:user_id>/', views_directory.MemberCardView.as_view(), name='mobile_member'),
    path('members/<str:user_id>/photo/', views_directory.MemberPhotoView.as_view(),
         name='mobile_member_photo'),
    path('calendar/subscription/', views_calendar.CalendarSubscriptionView.as_view(),
         name='mobile_calendar_subscription'),
    path('votes/', views_votes.VoteListView.as_view(), name='mobile_votes'),
    path('votes/<int:legislation_id>/', views_votes.VoteDetailView.as_view(), name='mobile_vote'),
    path('votes/<int:legislation_id>/cast/', views_votes.VoteCastView.as_view(),
         name='mobile_vote_cast'),
    path('votes/reauth/passkey/begin/', views_votes.PasskeyReauthBeginView.as_view(),
         name='mobile_vote_reauth_passkey_begin'),
    path('votes/reauth/passkey/complete/', views_votes.PasskeyReauthCompleteView.as_view(),
         name='mobile_vote_reauth_passkey_complete'),
    path('committees/<str:code>/votes/', views_votes.CommitteeVoteListView.as_view(),
         name='mobile_committee_votes'),
    path('committees/<str:code>/votes/<int:legislation_id>/',
         views_votes.CommitteeVoteDetailView.as_view(), name='mobile_committee_vote'),
    path('committees/<str:code>/votes/<int:legislation_id>/cast/',
         views_votes.CommitteeVoteCastView.as_view(), name='mobile_committee_vote_cast'),
    path('chat/channels/', views_chat.ChannelListView.as_view(), name='mobile_chat_channels'),
    path('chat/channels/<int:channel_id>/messages/', views_chat.MessageListView.as_view(),
         name='mobile_chat_messages'),
    path('chat/channels/<int:channel_id>/send/', views_chat.SendView.as_view(),
         name='mobile_chat_send'),
    path('chat/channels/<int:channel_id>/messages/<int:message_id>/edit/',
         views_chat.EditView.as_view(), name='mobile_chat_edit'),
    path('chat/channels/<int:channel_id>/messages/<int:message_id>/delete/',
         views_chat.DeleteView.as_view(), name='mobile_chat_delete'),
    path('chat/channels/<int:channel_id>/read/', views_chat.ReadView.as_view(),
         name='mobile_chat_read'),
    path('chat/channels/<int:channel_id>/notification-level/',
         views_chat.NotificationLevelView.as_view(), name='mobile_chat_notification_level'),
]
