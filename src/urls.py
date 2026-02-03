from django.urls import path
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth import views as auth_views
from src.view.officer import *
from src.view.officer.event_attendance import event_attendance_list, mark_event_attendance, review_excuses
from src.view.officer.chapter_minutes import (
    chapter_minutes_list, create_chapter_minutes, edit_chapter_minutes,
    save_minutes_data, save_minutes_attendance, publish_chapter_minutes,
    download_minutes_pdf, delete_chapter_minutes
)
from src.view.officer.manage_announcements import track_email_view, announcement_stats
from src.view.committee import *
from src.view.committee.committee_minutes_editor import (
    committee_minutes_list, create_committee_minutes, edit_committee_minutes,
    save_committee_minutes_data, save_committee_minutes_attendance,
    publish_committee_minutes, download_committee_minutes_pdf, delete_committee_minutes
)
from src.view.committee.manage_chat_permissions import manage_chat_permissions, add_guest_permission, update_guest_permission, remove_guest_permission
from src.view.chat import *
from src.view.submit_excuse import my_excuses, submit_excuse, cancel_excuse
from src.view.kai_reports import submit_kai_report, view_kai_reports, manage_kai_report, export_kai_reports_csv, print_kai_report, kai_dashboard, bulk_actions_kai_reports, manage_kai_templates, create_kai_template, edit_kai_template, delete_kai_template
from src.view.chapter_documents import chapter_documents
from src.view.api import dismiss_announcement_api
from src.view.notifications import notifications_page, notifications_dropdown_api, mark_notification_read, mark_all_notifications_read, delete_notification
from src.view.set_email import set_email
from src.view.upload_chapter_document import upload_chapter_document
from src.view.manage_chapter_document import manage_chapter_document
from src.view.manage_chapter_documents import manage_chapter_documents
from src.view.manage_folders import create_folder, delete_folder
from src.view.announcements import announcements_view
from src.view.calendar import calendar_view, calendar_data_api, export_calendar_ical, export_event_ical, calendar_subscription_feed, get_calendar_subscription_url, regenerate_calendar_token
from src.view.global_search import global_search
from src.view.changelog import changelog, changelog_detail
from src.view.admin_v2 import (
    admin_v2_login, admin_v2_dashboard, toggle_feature_flag,
    toggle_page, admin_v2_logout, manage_legislation, delete_legislation,
    manage_committees, toggle_committee_active,
    manage_users, toggle_user_admin, remove_user_profile_picture, manage_login_history,
    manage_announcements as admin_v2_manage_announcements_view,
    delete_announcement as admin_v2_delete_announcement_view,
    user_login_security, force_password_reset,
    add_ip_to_whitelist, add_ip_to_blacklist,
    remove_ip_from_whitelist, remove_ip_from_blacklist,
    manage_ip_whitelist, manage_ip_blacklist, manage_security_alerts,
    update_site_setting, send_test_announcement_email, preview_test_email
)
from src.view.admin_v2 import manage_events as admin_v2_manage_events, delete_event as admin_v2_delete_event
from src.view.officer.manage_events import manage_events, create_event, edit_event, delete_event
from src.view.officer.manage_members import add_member, edit_member, delete_member, initiate_pledges, get_all_roles
from src.view.home import home
from src.view.vote_view import vote_view
from src.view.change_password import change_password
from src.view.forced_password_change import forced_password_change
from src.view.view_legislation_history import view_legislation_history
from src.view.login_view import login_view
from src.view.logout_view import logout_view
from src.view.profile_view import profile_view
from src.view.preferences import preferences_view
from src.view.activity_logs import activity_logs_view, export_activity_logs
from src.view.upload_legislation import upload_legislation
from src.view.end_vote import end_vote
from src.view.delete_legislation import delete_chapter_legislation
from src.view.passed_legislation import passed_legislation, PassedLegislationDetailView
from src.view.legislation_detail import legislation_detail
from src.view.edit_legislation import edit_legislation
from src.view.reopen_legislation import reopen_legislation
from src.view.submit_new_version import submit_new_version
from src.view.login_as_view import login_as_view, login_as_user
from src.view.roberts_rules import roberts_rules
from src.view.constitution_bylaws import constitution_bylaws
from src.view.passed_resolutions import passed_resolutions
from src.view.officer_duties_detail import officer_duties_detail
from src.view.committee_details import committee_details
from src.view.kai_procedures_detail import kai_procedures_detail
from src.view.slating_elections_detail import slating_elections_detail
from src.view.advisors_detail import advisors_detail
from src.view.academic_standards_detail import academic_standards_detail
from src.view.view_document import (
    view_legislation_document, view_chapter_document,
    view_committee_document, view_passed_legislation_document,
    view_reference_document
)
from src.view.bug_report import submit_bug_report, bug_report_success, my_bug_reports, bug_tracker, bug_report_detail, bug_admin, bug_admin_update

urlpatterns = [
    # General User Pages
    path('', home, name='home'),
    path('login/', login_view, name='login'),
    path('logout/', logout_view, name='logout'),
    path('roberts-rules/', roberts_rules, name='roberts_rules'),
    path('constitution-bylaws/', constitution_bylaws, name='constitution_bylaws'),
    path('reference-document/<str:doc_slug>/', view_reference_document, name='view_reference_document'),
    path('constitution-bylaws/passed-resolutions/', passed_resolutions, name='passed_resolutions_detail'),
    path('constitution-bylaws/officer-duties/', officer_duties_detail, name='officer_duties_detail'),
    path('constitution-bylaws/committees/', committee_details, name='committee_details'),
    path('constitution-bylaws/kai-procedures/', kai_procedures_detail, name='kai_procedures_detail'),
    path('constitution-bylaws/slating-elections/', slating_elections_detail, name='slating_elections_detail'),
    path('constitution-bylaws/advisors/', advisors_detail, name='advisors_detail'),
    path('constitution-bylaws/academic-standards/', academic_standards_detail, name='academic_standards_detail'),

    # Password Reset URLs
    path('password-reset/', auth_views.PasswordResetView.as_view(template_name='registration/password_reset.html'), name='password_reset'),
    path('password-reset/done/', auth_views.PasswordResetDoneView.as_view(template_name='registration/password_reset_done.html'), name='password_reset_done'),
    path('password-reset-confirm/<uidb64>/<token>/', auth_views.PasswordResetConfirmView.as_view(template_name='registration/password_reset_confirm.html'), name='password_reset_confirm'),
    path('password-reset-complete/', auth_views.PasswordResetCompleteView.as_view(template_name='registration/password_reset_complete.html'), name='password_reset_complete'),

    path('users/', user_list, name='user_list'),
    path('profile/', profile_view, name='profile'),
    path('preferences/', preferences_view, name='preferences'),
    path('set-email/', set_email, name='set_email'),
    path('upload/', upload_legislation, name='upload_legislation'),
    path('change_password/', change_password, name='change_password'),
    path('forced-password-change/', forced_password_change, name='forced_password_change'),
    path('chapter-documents/', chapter_documents, name='chapter_documents'),
    path('chapter-documents/manage-all/', manage_chapter_documents, name='manage_chapter_documents'),
    path('chapter-documents/upload/', upload_chapter_document, name='upload_chapter_document'),
    path('chapter-documents/manage/<int:doc_id>/', manage_chapter_document, name='manage_chapter_document'),
    path('chapter-documents/view/<int:document_id>/', view_chapter_document, name='view_chapter_document'),
    path('chapter-documents/create-folder/', create_folder, name='create_folder'),
    path('chapter-documents/delete-folder/<int:folder_id>/', delete_folder, name='delete_folder'),
    path('announcements/', announcements_view, name='announcements'),
    path('calendar/', calendar_view, name='calendar'),
    path('api/calendar-data/', calendar_data_api, name='calendar_data_api'),
    path('calendar/export/', export_calendar_ical, name='export_calendar_ical'),
    path('calendar/event/<int:event_id>/export/', export_event_ical, name='export_event_ical'),
    path('calendar/subscribe/', get_calendar_subscription_url, name='get_calendar_subscription_url'),
    path('calendar/subscribe/regenerate/', regenerate_calendar_token, name='regenerate_calendar_token'),
    path('calendar/feed/<str:token>/', calendar_subscription_feed, name='calendar_subscription_feed'),
    path('search/', global_search, name='global_search'),

    # Bug Reports
    path('bug-report/', submit_bug_report, name='bug_report'),
    path('bug-report/success/<int:bug_id>/', bug_report_success, name='bug_report_success'),
    path('bug-report/my-reports/', my_bug_reports, name='my_bug_reports'),
    path('bug-tracker/', bug_tracker, name='bug_tracker'),
    path('bug-tracker/<int:bug_id>/', bug_report_detail, name='bug_report_detail'),
    path('bug-tracker/admin/', bug_admin, name='bug_admin'),
    path('bug-tracker/admin/update/<int:bug_id>/', bug_admin_update, name='bug_admin_update'),

    # Changelog / Version History
    path('changelog/', changelog, name='changelog'),
    path('changelog/<str:version>/', changelog_detail, name='changelog_detail'),

    # Member Excuse Requests
    path('excuses/', my_excuses, name='my_excuses'),
    path('excuses/submit/<int:event_id>/', submit_excuse, name='submit_excuse'),
    path('excuses/cancel/<int:excuse_id>/', cancel_excuse, name='cancel_excuse'),

    # Officer Pages
    path('officers/', officer_home, name='officer_home'),
    path('officers/upload-report/', upload_report, name='upload_report'),
    path('officers/all-events/', view_all_events, name='view_all_events'),
    path('officers/all-reports/', view_all_reports, name='view_all_reports'),
    path('officers/all-activity/', view_all_activity, name='view_all_activity'),
    path('officers/archived-events/', view_archived_events, name='view_archived_events'),
    path('officers/activity-logs/', activity_logs_view, name='activity_logs'),
    path('officers/activity-logs/export/', export_activity_logs, name='export_activity_logs'),
    # Attendance (Legacy)
    path('attendance/', attendance, name='attendance'),

    # Event-based Attendance (New System)
    path('officers/attendance/', event_attendance_list, name='event_attendance_list'),
    path('officers/attendance/event/<int:event_id>/', mark_event_attendance, name='mark_event_attendance'),
    path('officers/excuses/', review_excuses, name='review_excuses'),
    path('officers/excuses/<int:event_id>/', review_excuses, name='review_excuses'),

    # Chapter Minutes (Officer)
    path('officers/minutes/', chapter_minutes_list, name='chapter_minutes_list'),
    path('officers/minutes/create/', create_chapter_minutes, name='create_chapter_minutes'),
    path('officers/minutes/<int:minutes_id>/edit/', edit_chapter_minutes, name='edit_chapter_minutes'),
    path('officers/minutes/<int:minutes_id>/save/', save_minutes_data, name='save_minutes_data'),
    path('officers/minutes/<int:minutes_id>/save-attendance/', save_minutes_attendance, name='save_minutes_attendance'),
    path('officers/minutes/<int:minutes_id>/publish/', publish_chapter_minutes, name='publish_chapter_minutes'),
    path('officers/minutes/<int:minutes_id>/download-pdf/', download_minutes_pdf, name='download_minutes_pdf'),
    path('officers/minutes/<int:minutes_id>/delete/', delete_chapter_minutes, name='delete_chapter_minutes'),

    path('make_event/', make_event, name='make_event'),
    path('manage_event/', manage_event, name='manage_event'),
    path('user_list/', user_list, name='user_list'),
    path('user_list/export/', export_user_list, name='export_user_list'),

    # Member Management (Officer)
    path('officers/members/add/', add_member, name='add_member'),
    path('officers/members/<str:user_id>/edit/', edit_member, name='edit_member'),
    path('officers/members/<str:user_id>/delete/', delete_member, name='delete_member'),
    path('officers/members/initiate/', initiate_pledges, name='initiate_pledges'),
    path('api/roles/', get_all_roles, name='get_all_roles'),

    # Announcement Management (Officer)
    path('officers/announcements/', manage_announcements, name='manage_announcements'),
    path('officers/announcements/create/', create_announcement, name='create_announcement'),
    path('officers/announcements/<int:announcement_id>/edit/', edit_announcement, name='edit_announcement'),
    path('officers/announcements/<int:announcement_id>/delete/', delete_announcement, name='delete_announcement'),
    path('officers/announcements/<int:announcement_id>/toggle/', toggle_announcement_status, name='toggle_announcement_status'),
    path('officers/announcements/<int:announcement_id>/stats/', announcement_stats, name='announcement_stats'),

    # Announcement Email Tracking (no login required - used as tracking pixel)
    path('track/announcement/<int:announcement_id>/user/<str:user_id>/', track_email_view, name='track_email_view'),

    # Event Management (Officer)
    path('officers/events/', manage_events, name='manage_events'),
    path('officers/events/create/', create_event, name='create_event'),
    path('officers/events/<int:event_id>/edit/', edit_event, name='edit_event'),
    path('officers/events/<int:event_id>/delete/', delete_event, name='delete_event'),
    path('officers/events/<int:event_id>/archive/', archive_event, name='archive_event'),
    path('officers/events/<int:event_id>/unarchive/', unarchive_event, name='unarchive_event'),

    # Resolution Management (Officer)
    path('officers/resolutions/', manage_resolutions, name='manage_resolutions'),
    path('officers/resolutions/create/', create_resolution, name='create_resolution'),
    path('officers/resolutions/<int:resolution_id>/edit/', edit_resolution, name='edit_resolution'),
    path('officers/resolutions/<int:resolution_id>/delete/', delete_resolution, name='delete_resolution'),
    path('officers/resolutions/<int:resolution_id>/sections/', manage_section_impacts, name='manage_section_impacts'),
    path('officers/resolutions/<int:resolution_id>/sections/add/', add_section_impact, name='add_section_impact'),
    path('officers/resolutions/sections/<int:impact_id>/edit/', edit_section_impact, name='edit_section_impact'),
    path('officers/resolutions/sections/<int:impact_id>/delete/', delete_section_impact, name='delete_section_impact'),

    # Legislation / Voting Pages
    path('vote/', vote_view, name='vote'),
    path('vote/end/<int:legislation_id>/', end_vote, name='end_vote'),
    path('vote/delete/<int:legislation_id>/', delete_chapter_legislation, name='delete_chapter_legislation'),
    path('passed_legislation/', passed_legislation, name='passed_legislation'),
    path('legislation/detail/<int:pk>/', PassedLegislationDetailView.as_view(), name='passed_legislation_detail'),
    path('legislation/detail/<int:pk>/document/', view_passed_legislation_document, name='view_passed_legislation_document'),
    path('legislation/<int:legislation_id>/', legislation_detail, name='legislation_detail'),
    path('legislation/<int:legislation_id>/document/', view_legislation_document, name='view_document'),
    path('legislation/history/', view_legislation_history, name='view_legislation_history'),
    path('legislation/<int:legislation_id>/edit/', edit_legislation, name='edit_legislation'),
    path('legislation/<int:legislation_id>/reopen/', reopen_legislation, name='reopen_legislation'),
    path('legislation/<int:legislation_id>/submit_new_version/', submit_new_version, name='submit_new_version'),

    # Admin Pages
    path('admin/', admin.site.urls),
    path('admin/login-as/<int:user_id>/', login_as_user, name='login-as'),
    path('accounts/login/', login_view, name='admin_login_redirect'),

    # Committee URLs
    path('committees/', committee_index, name='committee_index'),
    path('committee/<str:code>/details/', committee_detail, name='committee_detail'),
    path('committee/<str:code>/', committee_home, name='committee_home'),
    path('committee/<str:code>/documents/', committee_documents, name='committee_documents'),
    path('committee/<str:code>/vote/', committee_vote, name='vote'),  # Keep as 'vote'
    path('committee/<str:code>/vote/<int:legislation_id>/result/', committee_vote_result, name='committee_vote_result'),
    path('committee/<str:code>/vote/<int:legislation_id>/delete/', delete_committee_vote, name='delete_committee_vote'),
    path('committee/<str:code>/manage_members/', committee_manage_members, name='manage_members'),
    path('committee/<str:code>/upload_document/', committee_upload_document, name='upload_document'),

    # New committee action URLs
    path('committee/<str:code>/add-member/', committee_add_member, name='committee_add_member'),
    path('committee/<str:code>/remove-member/', committee_remove_member, name='committee_remove_member'),
    path('committee/<str:code>/create-vote/', committee_create_vote, name='create_committee_vote'),
    path('committee/<str:code>/push-to-chapter/', committee_push_to_chapter, name='push_to_chapter'),
    path('committee/<str:code>/create-chapter-vote/<int:legislation_id>/', create_chapter_vote_from_committee, name='create_chapter_vote'),
    path('committee/<str:code>/delete-chapter-vote/', delete_chapter_vote_link, name='delete_chapter_vote_link'),
    path('committee/<str:code>/unpush-from-chapter/', committee_unpush_from_chapter, name='unpush_from_chapter'),
    path('committee/vote/<int:legislation_id>/recalculate/', recalculate_committee_vote, name='recalculate_committee_vote'),
    # Committee Minutes Editor
    path('committee/<str:code>/minutes/', committee_minutes_list, name='committee_minutes_list'),
    path('committee/<str:code>/minutes/create/', create_committee_minutes, name='create_committee_minutes'),
    path('committee/<str:code>/minutes/<int:minutes_id>/edit/', edit_committee_minutes, name='edit_committee_minutes'),
    path('committee/<str:code>/minutes/<int:minutes_id>/save/', save_committee_minutes_data, name='save_committee_minutes_data'),
    path('committee/<str:code>/minutes/<int:minutes_id>/save-attendance/', save_committee_minutes_attendance, name='save_committee_minutes_attendance'),
    path('committee/<str:code>/minutes/<int:minutes_id>/publish/', publish_committee_minutes, name='publish_committee_minutes'),
    path('committee/<str:code>/minutes/<int:minutes_id>/pdf/', download_committee_minutes_pdf, name='download_committee_minutes_pdf'),
    path('committee/<str:code>/minutes/<int:minutes_id>/delete/', delete_committee_minutes, name='delete_committee_minutes'),

    path('committee/<str:code>/documents/<int:document_id>/view/', view_committee_document, name='view_committee_document'),
    path('committee/<str:code>/documents/<int:document_id>/toggle-publish/', toggle_document_publish, name='toggle_document_publish'),
    path('committee/<str:code>/documents/<int:document_id>/delete/', delete_committee_document, name='delete_committee_document'),
    path('committee/<str:code>/attendance/', committee_attendance, name='committee_attendance'),
    path('committee/<str:code>/attendance/history/', committee_attendance_history, name='committee_attendance_history'),

    # Kai Report URLs
    path('kai/dashboard/', kai_dashboard, name='kai_dashboard'),
    path('kai/submit-report/', submit_kai_report, name='submit_kai_report'),
    path('kai/reports/', view_kai_reports, name='view_kai_reports'),
    path('kai/reports/bulk-actions/', bulk_actions_kai_reports, name='bulk_actions_kai_reports'),
    path('kai/reports/export/', export_kai_reports_csv, name='export_kai_reports_csv'),
    path('kai/reports/<int:report_id>/', manage_kai_report, name='manage_kai_report'),
    path('kai/reports/<int:report_id>/print/', print_kai_report, name='print_kai_report'),
    path('kai/templates/', manage_kai_templates, name='manage_kai_templates'),
    path('kai/templates/create/', create_kai_template, name='create_kai_template'),
    path('kai/templates/<int:template_id>/edit/', edit_kai_template, name='edit_kai_template'),
    path('kai/templates/<int:template_id>/delete/', delete_kai_template, name='delete_kai_template'),

    # Committee Chat URLs (legacy - redirects to channel chat)
    path('committee/<str:code>/chat/', committee_chat, name='committee_chat'),
    path('committee/<str:code>/chat/settings/', edit_committee_chat_settings, name='edit_committee_chat_settings'),
    path('committee/<str:code>/chat/permissions/', manage_chat_permissions, name='manage_chat_permissions'),
    path('api/committee/<str:code>/chat/messages/', get_chat_messages, name='get_chat_messages'),
    path('api/committee/<str:code>/chat/send/', send_chat_message, name='send_chat_message'),
    path('api/committee/<str:code>/chat/delete/<int:message_id>/', delete_chat_message, name='delete_chat_message'),
    path('api/committee/<str:code>/chat/active/', get_active_users, name='get_active_users'),
    path('api/committee/<str:code>/chat/permissions/add/', add_guest_permission, name='add_guest_permission'),
    path('api/committee/<str:code>/chat/permissions/<str:user_id>/update/', update_guest_permission, name='update_guest_permission'),
    path('api/committee/<str:code>/chat/permissions/<str:user_id>/remove/', remove_guest_permission, name='remove_guest_permission'),

    # New Channel-based Chat URLs
    path('chats/', chat_index, name='chat_index'),
    path('chat/committee/<str:code>/', channel_chat, name='committee_channel_chat'),  # Committee chat by code
    path('chat/<int:channel_id>/', channel_chat, name='channel_chat'),  # Fallback for non-committee channels
    path('api/channel/committee/<str:code>/messages/', get_channel_messages, name='committee_get_channel_messages'),
    path('api/channel/committee/<str:code>/send/', send_channel_message, name='committee_send_channel_message'),
    path('api/channel/committee/<str:code>/edit/<int:message_id>/', edit_channel_message, name='committee_edit_channel_message'),
    path('api/channel/committee/<str:code>/delete/<int:message_id>/', delete_channel_message, name='committee_delete_channel_message'),
    path('api/channel/committee/<str:code>/active/', get_channel_active_users, name='committee_get_channel_active_users'),
    path('api/channel/<int:channel_id>/messages/', get_channel_messages, name='get_channel_messages'),
    path('api/channel/<int:channel_id>/send/', send_channel_message, name='send_channel_message'),
    path('api/channel/<int:channel_id>/edit/<int:message_id>/', edit_channel_message, name='edit_channel_message'),
    path('api/channel/<int:channel_id>/delete/<int:message_id>/', delete_channel_message, name='delete_channel_message'),
    path('api/channel/<int:channel_id>/active/', get_channel_active_users, name='get_channel_active_users'),

    # Admin Channel Management
    path('chats/create/', create_channel, name='create_channel'),
    path('chats/<int:channel_id>/edit/', edit_channel, name='edit_channel'),
    path('chats/<int:channel_id>/delete/', delete_channel, name='delete_channel'),

    # Notifications
    path('notifications/', notifications_page, name='notifications'),

    # API Endpoints
    path('api/dismiss-announcement/<int:announcement_id>/', dismiss_announcement_api, name='dismiss_announcement_api'),
    path('api/notifications/', notifications_dropdown_api, name='notifications_api'),
    path('api/notifications/<int:notification_id>/read/', mark_notification_read, name='mark_notification_read'),
    path('api/notifications/read-all/', mark_all_notifications_read, name='mark_all_notifications_read'),
    path('api/notifications/<int:notification_id>/delete/', delete_notification, name='delete_notification'),

    # User Settings
    path('set-email/', set_email, name='set_email'),

    # Admin v2 - Advanced Administration
    path('admin-v2/', admin_v2_login, name='admin_v2_login'),
    path('admin_v2/', admin_v2_login, name='admin_v2_login'),
    path('admin-v2/dashboard/', admin_v2_dashboard, name='admin_v2_dashboard'),
    path('admin-v2/feature-flag/<int:flag_id>/toggle/', toggle_feature_flag, name='toggle_feature_flag'),
    path('admin-v2/page/<int:toggle_id>/toggle/', toggle_page, name='toggle_page'),
    path('admin-v2/setting/<int:setting_id>/update/', update_site_setting, name='update_site_setting'),
    path('admin-v2/send-test-email/', send_test_announcement_email, name='send_test_announcement_email'),
    path('admin-v2/preview-test-email/', preview_test_email, name='preview_test_email'),
    path('admin-v2/logout/', admin_v2_logout, name='admin_v2_logout'),

    # Admin v2 - Management Pages
    path('admin-v2/legislation/', manage_legislation, name='admin_v2_manage_legislation'),
    path('admin-v2/legislation/<int:legislation_id>/delete/', delete_legislation, name='admin_v2_delete_legislation'),
    path('admin-v2/events/', admin_v2_manage_events, name='admin_v2_manage_events'),
    path('admin-v2/events/<int:event_id>/delete/', admin_v2_delete_event, name='admin_v2_delete_event'),
    path('admin-v2/committees/', manage_committees, name='admin_v2_manage_committees'),
    path('admin-v2/committees/<int:committee_id>/toggle/', toggle_committee_active, name='admin_v2_toggle_committee'),
    path('admin-v2/users/', manage_users, name='admin_v2_manage_users'),
    path('admin-v2/users/<str:user_id>/toggle-admin/', toggle_user_admin, name='admin_v2_toggle_user_admin'),
    path('admin-v2/users/<str:user_id>/remove-profile-picture/', remove_user_profile_picture, name='admin_v2_remove_user_profile_picture'),
    path('admin-v2/login-history/', manage_login_history, name='admin_v2_login_history'),
    path('admin-v2/announcements/', admin_v2_manage_announcements_view, name='admin_v2_manage_announcements'),
    path('admin-v2/announcements/<int:announcement_id>/delete/', admin_v2_delete_announcement_view, name='admin_v2_delete_announcement'),

    # Admin v2 - User Login Security
    path('admin-v2/users/<str:user_id>/login-security/', user_login_security, name='admin_v2_user_login_security'),
    path('admin-v2/users/<str:user_id>/force-password-reset/', force_password_reset, name='admin_v2_force_password_reset'),

    # Admin v2 - IP Management
    path('admin-v2/ip/whitelist/', manage_ip_whitelist, name='admin_v2_ip_whitelist'),
    path('admin-v2/ip/whitelist/add/', add_ip_to_whitelist, name='admin_v2_add_ip_whitelist'),
    path('admin-v2/ip/whitelist/remove/', remove_ip_from_whitelist, name='admin_v2_remove_ip_whitelist'),
    path('admin-v2/ip/blacklist/', manage_ip_blacklist, name='admin_v2_ip_blacklist'),
    path('admin-v2/ip/blacklist/add/', add_ip_to_blacklist, name='admin_v2_add_ip_blacklist'),
    path('admin-v2/ip/blacklist/remove/', remove_ip_from_blacklist, name='admin_v2_remove_ip_blacklist'),

    # Admin v2 - Security Alerts
    path('admin-v2/security/alerts/', manage_security_alerts, name='admin_v2_security_alerts'),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

# Custom error handlers
from src.view.error_handlers import custom_404, custom_500

handler404 = custom_404
handler500 = custom_500
