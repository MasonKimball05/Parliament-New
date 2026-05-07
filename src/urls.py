from django.urls import path
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth import views as auth_views
from src.view.officer import *
from src.view.officer.contact_submissions import contact_submissions_view, mark_contact_read, mark_all_contact_read
from src.view.officer.event_attendance import event_attendance_list, mark_event_attendance, review_excuses
from src.view.officer.attendance_dashboard import attendance_dashboard, member_attendance_detail
from src.view.officer.chapter_minutes import (
    chapter_minutes_list, create_chapter_minutes, edit_chapter_minutes,
    save_minutes_data, save_minutes_attendance, publish_chapter_minutes,
    download_minutes_pdf, delete_chapter_minutes
)
from src.view.officer.manage_announcements import (
    track_email_view, announcement_stats, confirm_announcement_email,
    send_announcement_emails, skip_announcement_email,
    warmup_announcement_email, cancel_warmup_announcement_email
)
from src.view.committee import *
from src.view.committee.committee_minutes_editor import (
    committee_minutes_list, create_committee_minutes, edit_committee_minutes,
    save_committee_minutes_data, save_committee_minutes_attendance,
    publish_committee_minutes, download_committee_minutes_pdf, delete_committee_minutes
)
from src.view.committee.manage_chat_permissions import manage_chat_permissions, add_guest_permission, update_guest_permission, remove_guest_permission
from src.view.chat import *
from src.view.submit_excuse import my_excuses, submit_excuse, cancel_excuse, my_attendance
from src.view.kai_reports import submit_kai_report, view_kai_reports, manage_kai_report, export_kai_reports_csv, print_kai_report, kai_dashboard, bulk_actions_kai_reports, manage_kai_templates, create_kai_template, edit_kai_template, delete_kai_template, track_kai_accused_email_view
from src.view.kai_user_dashboard import user_kai_dashboard, user_view_report, request_closure, request_drop_case
from src.view.kai_form_builder import kai_form_builder, reorder_kai_fields, get_kai_field_details
from src.view.service_user_dashboard import (
    user_service_dashboard, user_view_submission,
    submit_service_hours, edit_service_submission
)
from src.view.service_hours import (
    service_dashboard, view_service_submissions, manage_service_submission,
    bulk_actions_service, export_service_csv, manage_service_periods,
    edit_service_period, manage_member_expectations, add_service_adjustment,
    delete_service_adjustment, get_member_adjustments
)
from src.view.service_form_builder import (
    service_form_builder, reorder_service_fields, get_service_field_details
)
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
    manage_committees as admin_v2_manage_committees, toggle_committee_active,
    manage_users, toggle_user_admin, remove_user_profile_picture, manage_login_history,
    manage_announcements as admin_v2_manage_announcements_view,
    delete_announcement as admin_v2_delete_announcement_view,
    user_login_security, force_password_reset,
    add_ip_to_whitelist, add_ip_to_blacklist,
    remove_ip_from_whitelist, remove_ip_from_blacklist,
    manage_ip_whitelist, manage_ip_blacklist, manage_security_alerts,
    update_site_setting, send_test_announcement_email, preview_test_email,
    health_check, check_default_password, test_email_targeting,
    email_logs, email_log_detail, send_scheduled_announcement_email,
    security_dashboard, quarantine_management, lockdown_control,
    honeypot_logs, security_notifications_log,
    dismiss_alert, dismiss_all_alerts,
    delete_honeypot_log, clear_honeypot_logs, blacklist_all_honeypot_ips,
    manage_lockouts,
)
from src.view.admin_v2 import manage_events as admin_v2_manage_events, delete_event as admin_v2_delete_event
from src.view.notification_admin import (
    notification_dashboard, notification_schedules, notification_logs,
    create_schedule, update_schedule, toggle_schedule, delete_schedule,
    notification_log_detail
)
from src.view.officer.manage_events import manage_events, create_event, edit_event, delete_event
from src.view.officer.manage_members import add_member, edit_member, delete_member, initiate_pledges, get_all_roles, sync_officer_admins, get_admin_roles
from src.view.officer.manage_roles import manage_roles, role_detail, add_role, delete_role, assign_role_member, unassign_role_member, get_assignable_members
from src.view.home import home
from src.view.landing import landing_page, contact_submit
from src.view.vote_view import vote_view
from src.view.two_factor import (
    two_factor_setup, two_factor_qrcode, two_factor_verify,
    two_factor_disable, two_factor_dismiss,
    two_factor_backup_codes_reveal, two_factor_regenerate_backup_codes,
)
from src.view.admin_two_factor import (
    two_factor_dashboard as admin_v2_two_factor_dashboard,
    update_two_factor_policy, set_two_factor_requirement,
    bulk_two_factor_action, reset_user_2fa
)
from src.view.change_password import change_password
from src.view.forced_password_change import forced_password_change
from src.view.view_legislation_history import view_legislation_history
from src.view.login_view import login_view
from src.view.logout_view import logout_view
from src.view.profile_view import profile_view
from src.view.directory import member_directory, export_directory
from src.view.preferences import preferences_view
from src.view.session_viewer import session_list, revoke_session, revoke_all_other_sessions
from src.view.activity_logs import activity_logs_view, export_activity_logs
from src.view.upload_legislation import upload_legislation
from src.view.end_vote import end_vote, create_runoff
from src.view.delete_legislation import delete_chapter_legislation
from src.view.passed_legislation import passed_legislation, PassedLegislationDetailView, add_legislation, update_legislation_note
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
    view_reference_document,
    download_legislation_document, download_chapter_document, download_committee_document,
)
from src.view.bug_report import submit_bug_report, bug_report_success, my_bug_reports, bug_tracker, bug_report_detail, bug_admin, bug_admin_update
from src.view.debug_panel import (
    debug_request_info, debug_server_info, debug_database_info,
    debug_cache_info, debug_cache_clear, debug_session_info, debug_session_edit,
    debug_feature_flags, debug_toggle_flag, debug_error_logs, debug_clear_logs,
    debug_template_context, debug_performance_metrics, debug_users_online
)
from src.view.slating import (
    slating_dashboard, create_period, edit_period, change_period_status,
    form_builder, manage_positions, add_position, edit_position, delete_position, copy_default_positions,
    apply_view, my_applications, withdraw_application,
    applications_list, application_detail, submit_review, bulk_update_status,
    interview_list, schedule_interview, complete_interview, destroy_interview_notes,
    build_slate, approve_slate, slate_preview, copy_slate,
    slating_vote, individual_vote, close_voting,
    view_results, publish_results, results_summary,
    transfer_admin, transition_officers,
    reorder_fields, reorder_positions, period_status,
    check_eligibility, application_summary, slate_candidates,
    voting_status, toggle_field_active, toggle_position_active
)
from src.view.guide import (
    guide_index, guide_officer_hub, guide_article,
    guide_events, guide_announcements, guide_attendance, guide_chapter_minutes,
    guide_managing_members, guide_slating, guide_kai,
    guide_legislation, guide_committees,
    guide_profile, guide_calendar, guide_notifications, guide_excuses,
    guide_2fa, guide_directory, guide_search,
    guide_resolutions, guide_activity_logs, guide_kai_forms,
    tour_start, tour_advance, tour_complete, tour_skip
)
from src.view.songbook import (
    songbook_list, song_detail, song_create, song_edit, song_delete, manage_categories,
    serve_song_audio, serve_exportable_media
)
from src.view.public_songbook import public_songbook_list, public_song_detail
from src.view.csp_report import csp_report
from src.view.honeypot import (
    honeypot_wp_admin, honeypot_wp_login, honeypot_phpmyadmin, honeypot_env,
    honeypot_admin_backup, honeypot_api_export, honeypot_xmlrpc, honeypot_config,
    honeypot_shell, honeypot_setup, honeypot_git, honeypot_php_admin,
    honeypot_wp_content, honeypot_joomla, honeypot_htaccess, honeypot_aws,
    honeypot_server_status
)

urlpatterns = [
    # Public landing page
    path('', landing_page, name='landing'),
    path('contact/submit/', contact_submit, name='contact_submit'),

    # General User Pages
    path('home/', home, name='home'),
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
    path('directory/', member_directory, name='member_directory'),
    path('directory/export/', export_directory, name='export_directory'),
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
    path('chapter-documents/download/<int:document_id>/', download_chapter_document, name='download_chapter_document'),
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

    # Public songbook (no auth, no management functions)
    path('songbook/public/', public_songbook_list, name='public_songbook'),
    path('songbook/public/<int:pk>/', public_song_detail, name='public_song_detail'),

    # Songbook
    path('songbook/', songbook_list, name='songbook'),
    path('songbook/song/<int:pk>/', song_detail, name='song_detail'),
    path('songbook/song/<int:pk>/audio/', serve_song_audio, name='song_audio'),
    path('songbook/add/', song_create, name='song_create'),
    path('songbook/song/<int:pk>/edit/', song_edit, name='song_edit'),
    path('songbook/song/<int:pk>/delete/', song_delete, name='song_delete'),
    path('songbook/categories/', manage_categories, name='manage_song_categories'),
    path('exportable_media/<path:filename>', serve_exportable_media, name='serve_exportable_media'),

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

    # Guide System
    path('guide/', guide_index, name='guide_index'),
    path('guide/officers/', guide_officer_hub, name='guide_officer_hub'),
    path('guide/officers/events/', guide_events, name='guide_events'),
    path('guide/officers/announcements/', guide_announcements, name='guide_announcements'),
    path('guide/officers/attendance/', guide_attendance, name='guide_attendance'),
    path('guide/officers/chapter-minutes/', guide_chapter_minutes, name='guide_chapter_minutes'),
    path('guide/officers/managing-members/', guide_managing_members, name='guide_managing_members'),
    path('guide/officers/slating/', guide_slating, name='guide_slating'),
    path('guide/officers/kai/', guide_kai, name='guide_kai'),
    path('guide/members/legislation/', guide_legislation, name='guide_legislation'),
    path('guide/members/committees/', guide_committees, name='guide_committees'),
    path('guide/members/profile/', guide_profile, name='guide_profile'),
    path('guide/members/calendar/', guide_calendar, name='guide_calendar'),
    path('guide/members/notifications/', guide_notifications, name='guide_notifications'),
    path('guide/members/excuses/', guide_excuses, name='guide_excuses'),
    path('guide/members/2fa/', guide_2fa, name='guide_2fa'),
    path('guide/members/directory/', guide_directory, name='guide_directory'),
    path('guide/members/search/', guide_search, name='guide_search'),
    path('guide/officers/resolutions/', guide_resolutions, name='guide_resolutions'),
    path('guide/officers/activity-logs/', guide_activity_logs, name='guide_activity_logs'),
    path('guide/officers/kai-forms/', guide_kai_forms, name='guide_kai_forms'),
    path('guide/article/<slug:slug>/', guide_article, name='guide_article'),
    # Tour API endpoints
    path('guide/tour/<slug:tour_slug>/start/', tour_start, name='tour_start'),
    path('guide/tour/<slug:tour_slug>/advance/', tour_advance, name='tour_advance'),
    path('guide/tour/<slug:tour_slug>/complete/', tour_complete, name='tour_complete'),
    path('guide/tour/<slug:tour_slug>/skip/', tour_skip, name='tour_skip'),

    # Member Attendance & Excuse Requests
    path('my-attendance/', my_attendance, name='my_attendance'),
    path('excuses/', my_excuses, name='my_excuses'),
    path('excuses/submit/<int:event_id>/', submit_excuse, name='submit_excuse'),
    path('excuses/cancel/<int:excuse_id>/', cancel_excuse, name='cancel_excuse'),

    # Officer Pages
    path('officers/', officer_home, name='officer_home'),
    path('officers/edit-landing-page/', edit_landing_page, name='edit_landing_page'),
    path('officers/contact-messages/', contact_submissions_view, name='contact_submissions'),
    path('officers/contact-messages/<int:pk>/read/', mark_contact_read, name='mark_contact_read'),
    path('officers/contact-messages/mark-all-read/', mark_all_contact_read, name='mark_all_contact_read'),
    path('officers/upload-report/', upload_report, name='upload_report'),
    path('officers/all-events/', view_all_events, name='view_all_events'),
    path('officers/all-reports/', view_all_reports, name='view_all_reports'),
    path('officers/all-activity/', view_all_activity, name='view_all_activity'),
    path('officers/archived-events/', view_archived_events, name='view_archived_events'),
    path('officers/activity-logs/', activity_logs_view, name='activity_logs'),
    path('officers/activity-logs/export/', export_activity_logs, name='export_activity_logs'),
    path('officers/system-logs/', view_logs, name='view_logs'),
    # Attendance (Legacy)
    path('attendance/', attendance, name='attendance'),

    # Event-based Attendance (New System)
    path('officers/attendance/', event_attendance_list, name='event_attendance_list'),
    path('officers/attendance/dashboard/', attendance_dashboard, name='attendance_dashboard'),
    path('officers/attendance/member/<str:user_id>/', member_attendance_detail, name='officer_member_attendance'),
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
    path('officers/members/sync-admins/', sync_officer_admins, name='sync_officer_admins'),
    path('api/roles/', get_all_roles, name='get_all_roles'),
    path('api/admin-roles/', get_admin_roles, name='get_admin_roles'),

    # Role Management (Admin)
    path('officers/roles/', manage_roles, name='manage_roles'),
    path('officers/roles/add/', add_role, name='add_role'),
    path('officers/roles/<int:role_id>/', role_detail, name='role_detail'),
    path('officers/roles/<int:role_id>/delete/', delete_role, name='delete_role'),
    path('officers/roles/<int:role_id>/assign/', assign_role_member, name='assign_role_member'),
    path('officers/roles/<int:role_id>/unassign/', unassign_role_member, name='unassign_role_member'),
    path('officers/roles/<int:role_id>/members/', get_assignable_members, name='get_assignable_members'),

    # Announcement Management (Officer)
    path('officers/announcements/', manage_announcements, name='manage_announcements'),
    path('officers/announcements/create/', create_announcement, name='create_announcement'),
    path('officers/announcements/<int:announcement_id>/edit/', edit_announcement, name='edit_announcement'),
    path('officers/announcements/<int:announcement_id>/delete/', delete_announcement, name='delete_announcement'),
    path('officers/announcements/<int:announcement_id>/toggle/', toggle_announcement_status, name='toggle_announcement_status'),
    path('officers/announcements/<int:announcement_id>/stats/', announcement_stats, name='announcement_stats'),
    path('officers/announcements/<int:announcement_id>/confirm-email/', confirm_announcement_email, name='confirm_announcement_email'),
    path('officers/announcements/<int:announcement_id>/send-emails/', send_announcement_emails, name='send_announcement_emails'),
    path('officers/announcements/<int:announcement_id>/skip-email/', skip_announcement_email, name='skip_announcement_email'),
    path('officers/announcements/<int:announcement_id>/warmup-email/', warmup_announcement_email, name='warmup_announcement_email'),
    path('officers/announcements/<int:announcement_id>/cancel-warmup/', cancel_warmup_announcement_email, name='cancel_warmup_announcement_email'),

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
    path('vote/runoff/<int:legislation_id>/', create_runoff, name='create_runoff'),
    path('vote/delete/<int:legislation_id>/', delete_chapter_legislation, name='delete_chapter_legislation'),
    path('passed_legislation/', passed_legislation, name='passed_legislation'),
    path('legislation/add/', add_legislation, name='add_legislation'),
    path('legislation/detail/<int:pk>/', PassedLegislationDetailView.as_view(), name='passed_legislation_detail'),
    path('legislation/detail/<int:pk>/document/', view_passed_legislation_document, name='view_passed_legislation_document'),
    path('legislation/<int:legislation_id>/', legislation_detail, name='legislation_detail'),
    path('legislation/<int:legislation_id>/document/', view_legislation_document, name='view_document'),
    path('legislation/<int:legislation_id>/download/', download_legislation_document, name='download_legislation_document'),
    path('legislation/history/', view_legislation_history, name='view_legislation_history'),
    path('legislation/<int:legislation_id>/edit/', edit_legislation, name='edit_legislation'),
    path('legislation/<int:legislation_id>/reopen/', reopen_legislation, name='reopen_legislation'),
    path('legislation/<int:legislation_id>/note/', update_legislation_note, name='update_legislation_note'),
    path('legislation/<int:legislation_id>/submit_new_version/', submit_new_version, name='submit_new_version'),

    # Admin Pages
    path('admin/', admin.site.urls),
    path('admin/login-as/<int:user_id>/', login_as_user, name='login-as'),
    path('accounts/login/', login_view, name='admin_login_redirect'),

    # Committee URLs
    path('committees/', committee_index, name='committee_index'),
    path('committees/create/', create_committee, name='create_committee'),
    path('committees/manage/', manage_committees, name='manage_committees'),
    path('committees/<int:committee_id>/', committee_detail_api, name='committee_detail_api'),
    path('committees/<int:committee_id>/delete/', delete_committee, name='delete_committee'),
    path('committee/<str:code>/details/', committee_detail, name='committee_detail'),
    path('committee/<str:code>/', committee_home, name='committee_home'),
    path('committee/<str:code>/documents/', committee_documents, name='committee_documents'),
    path('committee/<str:code>/vote/', committee_vote, name='vote'),  # Keep as 'vote'
    path('committee/<str:code>/vote/<int:legislation_id>/result/', committee_vote_result, name='committee_vote_result'),
    path('committee/<str:code>/vote/<int:legislation_id>/runoff/', create_committee_runoff, name='create_committee_runoff'),
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
    path('committee/<str:code>/documents/<int:document_id>/download/', download_committee_document, name='download_committee_document'),
    path('committee/<str:code>/documents/<int:document_id>/toggle-publish/', toggle_document_publish, name='toggle_document_publish'),
    path('committee/<str:code>/documents/<int:document_id>/delete/', delete_committee_document, name='delete_committee_document'),
    path('committee/<str:code>/attendance/', committee_attendance, name='committee_attendance'),
    path('committee/<str:code>/attendance/history/', committee_attendance_history, name='committee_attendance_history'),

    # Kai User Dashboard URLs
    path('kai/', user_kai_dashboard, name='user_kai_dashboard'),
    path('kai/my-report/<int:report_id>/', user_view_report, name='user_view_kai_report'),
    path('kai/my-report/<int:report_id>/request-closure/', request_closure, name='kai_request_closure'),
    path('kai/my-report/<int:report_id>/request-drop/', request_drop_case, name='kai_request_drop'),

    # Kai Form Builder URLs (chair only)
    path('kai/form-builder/', kai_form_builder, name='kai_form_builder'),
    path('kai/form-builder/field/<int:field_id>/', get_kai_field_details, name='kai_get_field_details'),
    path('api/kai/reorder-fields/', reorder_kai_fields, name='kai_api_reorder_fields'),

    # Kai Report URLs (chair management)
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
    path('kai/track-email/<int:report_id>.gif', track_kai_accused_email_view, name='track_kai_accused_email'),

    # Service Hours Member URLs
    path('service-hours/', user_service_dashboard, name='user_service_dashboard'),
    path('service-hours/submit/', submit_service_hours, name='submit_service_hours'),
    path('service-hours/my-submission/<int:submission_id>/', user_view_submission, name='user_view_service_submission'),
    path('service-hours/edit/<int:submission_id>/', edit_service_submission, name='edit_service_submission'),

    # Service Hours Officer URLs (VPP only)
    path('service-hours/dashboard/', service_dashboard, name='service_dashboard'),
    path('service-hours/submissions/', view_service_submissions, name='view_service_submissions'),
    path('service-hours/submissions/<int:submission_id>/', manage_service_submission, name='manage_service_submission'),
    path('service-hours/submissions/bulk-actions/', bulk_actions_service, name='bulk_actions_service'),
    path('service-hours/submissions/export/', export_service_csv, name='export_service_csv'),
    path('service-hours/periods/', manage_service_periods, name='manage_service_periods'),
    path('service-hours/periods/<int:period_id>/edit/', edit_service_period, name='edit_service_period'),
    path('service-hours/periods/<int:period_id>/expectations/', manage_member_expectations, name='manage_member_expectations'),

    # Service Hours Adjustment URLs (VPP only)
    path('api/service-hours/adjustment/add/', add_service_adjustment, name='add_service_adjustment'),
    path('api/service-hours/adjustment/<int:adjustment_id>/delete/', delete_service_adjustment, name='delete_service_adjustment'),
    path('api/service-hours/adjustments/<int:period_id>/<int:member_id>/', get_member_adjustments, name='get_member_adjustments'),

    # Service Hours Form Builder URLs (VPP only)
    path('service-hours/form-builder/', service_form_builder, name='service_form_builder'),
    path('service-hours/form-builder/field/<int:field_id>/', get_service_field_details, name='service_get_field_details'),
    path('api/service-hours/reorder-fields/', reorder_service_fields, name='service_api_reorder_fields'),

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

    # Two-Factor Authentication
    path('accounts/two-factor/setup/', two_factor_setup, name='two_factor_setup'),
    path('accounts/two-factor/qrcode/', two_factor_qrcode, name='two_factor_qrcode'),
    path('accounts/two-factor/verify/', two_factor_verify, name='two_factor_verify'),
    path('accounts/two-factor/disable/', two_factor_disable, name='two_factor_disable'),
    path('accounts/two-factor/backup-codes/', two_factor_backup_codes_reveal, name='two_factor_backup_codes_reveal'),
    path('accounts/two-factor/backup-codes/regenerate/', two_factor_regenerate_backup_codes, name='two_factor_regenerate_backup_codes'),
    path('accounts/two-factor/dismiss/', two_factor_dismiss, name='two_factor_dismiss'),

    # Session Management
    path('account/sessions/', session_list, name='session_list'),
    path('account/sessions/<str:session_key>/revoke/', revoke_session, name='revoke_session'),
    path('account/sessions/revoke-all/', revoke_all_other_sessions, name='revoke_all_sessions'),

    # Admin v2 - Advanced Administration
    path('admin-v2/', admin_v2_login, name='admin_v2_login'),
    path('admin_v2/', admin_v2_login, name='admin_v2_login'),
    path('admin-v2/dashboard/', admin_v2_dashboard, name='admin_v2_dashboard'),
    path('admin-v2/feature-flag/<int:flag_id>/toggle/', toggle_feature_flag, name='toggle_feature_flag'),
    path('admin-v2/page/<int:toggle_id>/toggle/', toggle_page, name='toggle_page'),
    path('admin-v2/setting/<int:setting_id>/update/', update_site_setting, name='update_site_setting'),
    path('admin-v2/send-test-email/', send_test_announcement_email, name='send_test_announcement_email'),
    path('admin-v2/preview-test-email/', preview_test_email, name='preview_test_email'),
    path('admin-v2/test-email-targeting/', test_email_targeting, name='admin_v2_test_email_targeting'),
    path('admin-v2/email-logs/', email_logs, name='admin_v2_email_logs'),
    path('admin-v2/email-logs/<int:log_id>/', email_log_detail, name='admin_v2_email_log_detail'),
    path('admin-v2/email-logs/send-scheduled/<int:announcement_id>/', send_scheduled_announcement_email, name='admin_v2_send_scheduled_email'),

    # Admin v2 - Security Management
    path('admin-v2/security/', security_dashboard, name='admin_v2_security'),
    path('admin-v2/security/quarantine/', quarantine_management, name='admin_v2_quarantine'),
    path('admin-v2/security/lockdown/', lockdown_control, name='admin_v2_lockdown'),
    path('admin-v2/security/honeypot-logs/', honeypot_logs, name='admin_v2_honeypot_logs'),
    path('admin-v2/security/honeypot-logs/<int:log_id>/delete/', delete_honeypot_log, name='admin_v2_delete_honeypot_log'),
    path('admin-v2/security/honeypot-logs/clear/', clear_honeypot_logs, name='admin_v2_clear_honeypot_logs'),
    path('admin-v2/security/honeypot-logs/blacklist-all/', blacklist_all_honeypot_ips, name='admin_v2_blacklist_all_honeypot_ips'),
    path('admin-v2/security/notifications/', security_notifications_log, name='admin_v2_security_notifications'),
    path('admin-v2/security/lockouts/', manage_lockouts, name='admin_v2_lockouts'),

    path('csp-report/', csp_report, name='csp_report'),

    path('admin-v2/logout/', admin_v2_logout, name='admin_v2_logout'),

    # Admin v2 - Management Pages
    path('admin-v2/legislation/', manage_legislation, name='admin_v2_manage_legislation'),
    path('admin-v2/legislation/<int:legislation_id>/delete/', delete_legislation, name='admin_v2_delete_legislation'),
    path('admin-v2/events/', admin_v2_manage_events, name='admin_v2_manage_events'),
    path('admin-v2/events/<int:event_id>/delete/', admin_v2_delete_event, name='admin_v2_delete_event'),
    path('admin-v2/committees/', admin_v2_manage_committees, name='admin_v2_manage_committees'),
    path('admin-v2/committees/<int:committee_id>/toggle/', toggle_committee_active, name='admin_v2_toggle_committee'),
    path('admin-v2/users/', manage_users, name='admin_v2_manage_users'),
    path('admin-v2/users/<str:user_id>/toggle-admin/', toggle_user_admin, name='admin_v2_toggle_user_admin'),
    path('admin-v2/users/<str:user_id>/remove-profile-picture/', remove_user_profile_picture, name='admin_v2_remove_user_profile_picture'),
    path('api/check-default-password/<str:user_id>/', check_default_password, name='check_default_password'),
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
    path('admin-v2/security/alerts/dismiss-all/', dismiss_all_alerts, name='admin_v2_dismiss_all_alerts'),
    path('admin-v2/security/alerts/<int:alert_id>/dismiss/', dismiss_alert, name='admin_v2_dismiss_alert'),

    # Admin v2 - Two-Factor Authentication
    path('admin-v2/two-factor/', admin_v2_two_factor_dashboard, name='admin_v2_two_factor'),
    path('admin-v2/two-factor/update-policy/', update_two_factor_policy, name='admin_v2_two_factor_update_policy'),
    path('admin-v2/two-factor/set-requirement/<str:user_id>/', set_two_factor_requirement, name='admin_v2_two_factor_set_requirement'),
    path('admin-v2/two-factor/bulk-action/', bulk_two_factor_action, name='admin_v2_two_factor_bulk_action'),
    path('admin-v2/two-factor/reset-2fa/<str:user_id>/', reset_user_2fa, name='admin_v2_two_factor_reset'),

    # Admin v2 - Notifications
    path('admin-v2/notifications/', notification_dashboard, name='admin_v2_notifications'),
    path('admin-v2/notifications/schedules/', notification_schedules, name='admin_v2_notification_schedules'),
    path('admin-v2/notifications/schedules/create/', create_schedule, name='admin_v2_create_notification_schedule'),
    path('admin-v2/notifications/schedules/<int:schedule_id>/update/', update_schedule, name='admin_v2_update_notification_schedule'),
    path('admin-v2/notifications/schedules/<int:schedule_id>/toggle/', toggle_schedule, name='admin_v2_toggle_notification_schedule'),
    path('admin-v2/notifications/schedules/<int:schedule_id>/delete/', delete_schedule, name='admin_v2_delete_notification_schedule'),
    path('admin-v2/notifications/logs/', notification_logs, name='admin_v2_notification_logs'),
    path('admin-v2/notifications/logs/<int:log_id>/', notification_log_detail, name='admin_v2_notification_log_detail'),

    # Health Check API
    path('api/health-check/', health_check, name='health_check'),

    # Debug Panel API (Admin + Maintenance Mode only)
    path('api/debug/request/', debug_request_info, name='debug_request_info'),
    path('api/debug/server/', debug_server_info, name='debug_server_info'),
    path('api/debug/database/', debug_database_info, name='debug_database_info'),
    path('api/debug/cache/', debug_cache_info, name='debug_cache_info'),
    path('api/debug/cache/clear/', debug_cache_clear, name='debug_cache_clear'),
    path('api/debug/session/', debug_session_info, name='debug_session_info'),
    path('api/debug/session/edit/', debug_session_edit, name='debug_session_edit'),
    path('api/debug/flags/', debug_feature_flags, name='debug_feature_flags'),
    path('api/debug/flags/toggle/', debug_toggle_flag, name='debug_toggle_flag'),
    path('api/debug/errors/', debug_error_logs, name='debug_error_logs'),
    path('api/debug/errors/clear/', debug_clear_logs, name='debug_clear_logs'),
    path('api/debug/context/', debug_template_context, name='debug_template_context'),
    path('api/debug/performance/', debug_performance_metrics, name='debug_performance_metrics'),
    path('api/debug/users-online/', debug_users_online, name='debug_users_online'),

    # Slating System URLs
    path('slating/', slating_dashboard, name='slating_dashboard'),
    path('slating/periods/', slating_dashboard, name='slating_periods'),  # Alias
    path('slating/period/new/', create_period, name='slating_create_period'),
    path('slating/period/<int:period_id>/', edit_period, name='slating_period_detail'),
    path('slating/period/<int:period_id>/setup/', edit_period, name='slating_period_setup'),
    path('slating/period/<int:period_id>/status/', change_period_status, name='slating_change_status'),

    # Slating Form Builder
    path('slating/period/<int:period_id>/form-builder/', form_builder, name='slating_form_builder'),

    # Slating Position Management
    path('slating/period/<int:period_id>/positions/', manage_positions, name='slating_positions'),
    path('slating/period/<int:period_id>/positions/add/', add_position, name='slating_add_position'),
    path('slating/period/<int:period_id>/positions/<int:position_id>/edit/', edit_position, name='slating_edit_position'),
    path('slating/period/<int:period_id>/positions/<int:position_id>/delete/', delete_position, name='slating_delete_position'),
    path('slating/period/<int:period_id>/positions/add-defaults/', copy_default_positions, name='slating_copy_default_positions'),

    # Slating Applications (Candidate)
    path('slating/period/<int:period_id>/apply/', apply_view, name='slating_apply'),
    path('slating/my-applications/', my_applications, name='slating_my_applications'),
    path('slating/application/<int:app_id>/withdraw/', withdraw_application, name='slating_withdraw_application'),

    # Slating Applications Review (Committee)
    path('slating/period/<int:period_id>/applications/', applications_list, name='slating_applications'),
    path('slating/period/<int:period_id>/application/<int:app_id>/', application_detail, name='slating_app_detail'),
    path('slating/period/<int:period_id>/application/<int:app_id>/review/', submit_review, name='slating_submit_review'),
    path('slating/period/<int:period_id>/applications/bulk-update/', bulk_update_status, name='slating_bulk_update'),

    # Slating Interviews
    path('slating/period/<int:period_id>/interviews/', interview_list, name='slating_interviews'),
    path('slating/application/<int:app_id>/schedule-interview/', schedule_interview, name='slating_schedule_interview'),
    path('slating/interview/<int:interview_id>/complete/', complete_interview, name='slating_complete_interview'),
    path('slating/period/<int:period_id>/destroy-notes/', destroy_interview_notes, name='slating_destroy_notes'),

    # Slating Slate Building
    path('slating/period/<int:period_id>/build-slate/', build_slate, name='slating_build_slate'),
    path('slating/period/<int:period_id>/slate/approve/', approve_slate, name='slating_approve_slate'),
    path('slating/period/<int:period_id>/slate/<int:slate_id>/approve/', approve_slate, name='slating_approve_slate_id'),
    path('slating/period/<int:period_id>/slate/<int:slate_id>/preview/', slate_preview, name='slating_slate_preview'),
    path('slating/period/<int:period_id>/slate/<int:slate_id>/copy/', copy_slate, name='slating_copy_slate'),

    # Slating Voting
    path('slating/period/<int:period_id>/vote/', slating_vote, name='slating_vote'),
    path('slating/period/<int:period_id>/vote/individual/', individual_vote, name='slating_vote_individual'),
    path('slating/period/<int:period_id>/close-voting/', close_voting, name='slating_close_voting'),

    # Slating Results
    path('slating/period/<int:period_id>/results/', view_results, name='slating_results'),
    path('slating/period/<int:period_id>/results/publish/', publish_results, name='slating_publish_results'),
    path('slating/period/<int:period_id>/results/summary/', results_summary, name='slating_results_summary'),

    # Slating Admin & Transition
    path('slating/period/<int:period_id>/transfer-admin/', transfer_admin, name='slating_transfer_admin'),
    path('slating/period/<int:period_id>/transition/', transition_officers, name='slating_transition'),

    # Slating API Endpoints
    path('api/slating/period/<int:period_id>/reorder-fields/', reorder_fields, name='slating_api_reorder_fields'),
    path('api/slating/period/<int:period_id>/reorder-positions/', reorder_positions, name='slating_api_reorder_positions'),
    path('api/slating/period/<int:period_id>/status/', period_status, name='slating_api_period_status'),
    path('api/slating/period/<int:period_id>/eligibility/', check_eligibility, name='slating_api_eligibility'),
    path('api/slating/period/<int:period_id>/application/<int:app_id>/summary/', application_summary, name='slating_api_app_summary'),
    path('api/slating/period/<int:period_id>/slate/<int:slate_id>/candidates/', slate_candidates, name='slating_api_slate_candidates'),
    path('api/slating/period/<int:period_id>/voting-status/', voting_status, name='slating_api_voting_status'),
    path('api/slating/period/<int:period_id>/field/<int:field_id>/toggle/', toggle_field_active, name='slating_api_toggle_field'),
    path('api/slating/period/<int:period_id>/position/<int:position_id>/toggle/', toggle_position_active, name='slating_api_toggle_position'),

    # Honeypot (poison pill) endpoints - trap for attackers/scanners
    # Any access to these endpoints triggers immediate IP ban and alert
    path('wp-admin/', honeypot_wp_admin, name='honeypot_wp_admin'),
    path('wp-admin/<path:path>', honeypot_wp_admin, name='honeypot_wp_admin_path'),
    path('wp-login.php', honeypot_wp_login, name='honeypot_wp_login'),
    path('phpmyadmin/', honeypot_phpmyadmin, name='honeypot_phpmyadmin'),
    path('phpmyadmin/<path:path>', honeypot_phpmyadmin, name='honeypot_phpmyadmin_path'),
    path('.env', honeypot_env, name='honeypot_env'),
    path('admin/backup/', honeypot_admin_backup, name='honeypot_backup'),
    path('api/v1/users/export/', honeypot_api_export, name='honeypot_api_export'),
    path('xmlrpc.php', honeypot_xmlrpc, name='honeypot_xmlrpc'),
    path('config.php', honeypot_config, name='honeypot_config'),
    path('shell.php', honeypot_shell, name='honeypot_shell'),
    path('install.php', honeypot_setup, name='honeypot_install'),
    path('setup/', honeypot_setup, name='honeypot_setup'),
    path('setup/<path:path>', honeypot_setup, name='honeypot_setup_path'),

    # Additional honeypot endpoints — common scan/recon targets
    path('.git/config', honeypot_git, name='honeypot_git_config'),
    path('.git/', honeypot_git, name='honeypot_git_root'),
    path('.git/<path:path>', honeypot_git, name='honeypot_git_path'),
    path('admin.php', honeypot_php_admin, name='honeypot_admin_php'),
    path('login.php', honeypot_php_admin, name='honeypot_login_php'),
    path('wp-content/', honeypot_wp_content, name='honeypot_wp_content'),
    path('wp-content/<path:path>', honeypot_wp_content, name='honeypot_wp_content_path'),
    path('wp-includes/', honeypot_wp_content, name='honeypot_wp_includes'),
    path('wp-includes/<path:path>', honeypot_wp_content, name='honeypot_wp_includes_path'),
    path('administrator/', honeypot_joomla, name='honeypot_joomla'),
    path('administrator/<path:path>', honeypot_joomla, name='honeypot_joomla_path'),
    path('.htaccess', honeypot_htaccess, name='honeypot_htaccess'),
    path('.aws/credentials', honeypot_aws, name='honeypot_aws_credentials'),
    path('server-status', honeypot_server_status, name='honeypot_server_status'),
    path('server-info', honeypot_server_status, name='honeypot_server_info'),
]

if settings.DEBUG:
    import os as _os
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static('/exportable_media/', document_root=_os.path.join(settings.BASE_DIR, 'exportable_media'))

# Custom error handlers
from src.view.error_handlers import custom_404, custom_500

handler404 = custom_404
handler500 = custom_500
