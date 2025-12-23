from django.urls import path
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from src.view.officer import *
from src.view.committee import *
from src.view.chapter_documents import chapter_documents
from src.view.manage_folders import create_folder, delete_folder
from src.view.announcements import announcements_view
from src.view.calendar import calendar_view, calendar_data_api
from src.view.officer.manage_events import manage_events, create_event, edit_event, delete_event
from src.view.home import home
from src.view.vote_view import vote_view
from src.view.change_password import change_password
from src.view.forced_password_change import forced_password_change
from src.view.view_legislation_history import view_legislation_history
from src.view.login_view import login_view
from src.view.logout_view import logout_view
from src.view.profile_view import profile_view
from src.view.upload_legislation import upload_legislation
from src.view.end_vote import end_vote
from src.view.passed_legislation import passed_legislation, PassedLegislationDetailView
from src.view.legislation_detail import legislation_detail
from src.view.edit_legislation import edit_legislation
from src.view.reopen_legislation import reopen_legislation
from src.view.submit_new_version import submit_new_version
from src.view.login_as_view import login_as_view, login_as_user

urlpatterns = [
    # General User Pages
    path('', home, name='home'),
    path('login/', login_view, name='login'),
    path('logout/', logout_view, name='logout'),
    path('users/', user_list, name='user_list'),
    path('profile/', profile_view, name='profile'),
    path('upload/', upload_legislation, name='upload_legislation'),
    path('change_password/', change_password, name='change_password'),
    path('forced-password-change/', forced_password_change, name='forced_password_change'),
    path('chapter-documents/', chapter_documents, name='chapter_documents'),
    path('chapter-documents/create-folder/', create_folder, name='create_folder'),
    path('chapter-documents/delete-folder/<int:folder_id>/', delete_folder, name='delete_folder'),
    path('announcements/', announcements_view, name='announcements'),
    path('calendar/', calendar_view, name='calendar'),
    path('api/calendar-data/', calendar_data_api, name='calendar_data_api'),

    # Officer Pages
    path('officers/', officer_home, name='officer_home'),
    path('officers/upload-report/', upload_report, name='upload_report'),
    path('officers/all-events/', view_all_events, name='view_all_events'),
    path('officers/all-reports/', view_all_reports, name='view_all_reports'),
    path('officers/all-activity/', view_all_activity, name='view_all_activity'),
    path('officers/archived-events/', view_archived_events, name='view_archived_events'),
    path('attendance/', attendance, name='attendance'),
    path('make_event/', make_event, name='make_event'),
    path('manage_event/', manage_event, name='manage_event'),
    path('user_list/', user_list, name='user_list'),

    # Announcement Management (Officer)
    path('officers/announcements/', manage_announcements, name='manage_announcements'),
    path('officers/announcements/create/', create_announcement, name='create_announcement'),
    path('officers/announcements/<int:announcement_id>/edit/', edit_announcement, name='edit_announcement'),
    path('officers/announcements/<int:announcement_id>/delete/', delete_announcement, name='delete_announcement'),
    path('officers/announcements/<int:announcement_id>/toggle/', toggle_announcement_status, name='toggle_announcement_status'),

    # Event Management (Officer)
    path('officers/events/', manage_events, name='manage_events'),
    path('officers/events/create/', create_event, name='create_event'),
    path('officers/events/<int:event_id>/edit/', edit_event, name='edit_event'),
    path('officers/events/<int:event_id>/delete/', delete_event, name='delete_event'),
    path('officers/events/<int:event_id>/archive/', archive_event, name='archive_event'),
    path('officers/events/<int:event_id>/unarchive/', unarchive_event, name='unarchive_event'),

    # Legislation / Voting Pages
    path('vote/', vote_view, name='vote'),
    path('vote/end/<int:legislation_id>/', end_vote, name='end_vote'),
    path('passed_legislation/', passed_legislation, name='passed_legislation'),
    path('legislation/detail/<int:pk>/', PassedLegislationDetailView.as_view(), name='passed_legislation_detail'),
    path('legislation/<int:legislation_id>/', legislation_detail, name='legislation_detail'),
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
    path('committee/<str:code>/manage_members/', committee_manage_members, name='manage_members'),
    path('committee/<str:code>/upload_document/', committee_upload_document, name='upload_document'),

    # New committee action URLs
    path('committee/<str:code>/add-member/', committee_add_member, name='committee_add_member'),
    path('committee/<str:code>/remove-member/', committee_remove_member, name='committee_remove_member'),
    path('committee/<str:code>/create-vote/', committee_create_vote, name='create_committee_vote'),
    path('committee/<str:code>/push-to-chapter/', committee_push_to_chapter, name='push_to_chapter'),
    path('committee/<str:code>/minutes/', committee_minutes, name='minutes'),
    path('committee/<str:code>/documents/<int:document_id>/toggle-publish/', toggle_document_publish, name='toggle_document_publish'),
    path('committee/<str:code>/documents/<int:document_id>/delete/', delete_committee_document, name='delete_committee_document'),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
