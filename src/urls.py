from django.urls import path
from . import views
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from src.view.officer import *
from src.view.committee import *
from src.view.chapter_documents import chapter_documents

urlpatterns = [
    # General User Pages
    path('', views.home, name='home'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('users/', views.user_list, name='user_list'),
    path('profile/', views.profile_view, name='profile'),
    path('upload/', views.upload_legislation, name='upload_legislation'),
    path('change_password/', views.change_password, name='change_password'),
    path('chapter-documents/', chapter_documents, name='chapter_documents'),

    # Officer Pages
    path('officers/', views.officer_home, name='officer_home'),
    path('attendance/', attendance, name='attendance'),
    path('make_event/', views.make_event, name='make_event'),
    path('manage_event/', views.manage_event, name='manage_event'),
    path('user_list/', views.user_list, name='user_list'),

    # Legislation / Voting Pages
    path('vote/', views.vote_view, name='vote'),
    path('vote/end/<int:legislation_id>/', views.end_vote, name='end_vote'),
    path('passed_legislation/', views.passed_legislation, name='passed_legislation'),
    path('legislation/detail/<int:pk>/', views.PassedLegislationDetailView.as_view(), name='passed_legislation_detail'),
    path('legislation/<int:legislation_id>/', views.legislation_detail, name='legislation_detail'),
    path('legislation/history/', views.view_legislation_history, name='view_legislation_history'),
    path('legislation/<int:legislation_id>/edit/', views.edit_legislation, name='edit_legislation'),
    path('legislation/<int:legislation_id>/reopen/', views.reopen_legislation, name='reopen_legislation'),
    path('legislation/<int:legislation_id>/submit_new_version/', views.submit_new_version, name='submit_new_version'),

    # Admin Pages
    path('admin/', admin.site.urls),
    path('admin/login-as/<int:user_id>/', views.login_as_user, name='login-as'),
    path('accounts/login/', views.login_view, name='admin_login_redirect'),

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
