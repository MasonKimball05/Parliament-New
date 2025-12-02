from django.urls import path
from . import views
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from src.view import *

urlpatterns = [
    # General User Pages
    path('', views.home, name='home'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('users/', views.user_list, name='user_list'),
    path('profile/', views.profile_view, name='profile'),
    path('upload/', views.upload_legislation, name='upload_legislation'),
    path('change_password/', views.change_password, name='change_password'),

    # Officer Pages
    path('officers/', views.officer_home, name='officer_home'),
    path('attendance/', views.attendance, name='attendance'),
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

    # Committee Pages
    path('committee/<int:id>/', committee_home, name='committee_home'),
    path('committee/<int:id>/documents/', committee_documents, name='committee_documents'),
    path('committee/<int:id>/vote/', committee_vote, name='vote'),
    path('committee/<int:id>/manage_members/', committee_manage_members, name='manage_members'),
    path('committee/<int:id>/upload_document/', committee_upload_document, name='upload_document'),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
