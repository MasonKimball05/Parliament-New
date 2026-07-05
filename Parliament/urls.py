"""
URL configuration for Parliament project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
import logging
from django.contrib import admin
from django.urls import path, include
from src.view.login_view import login_view
from django.conf.urls.static import static
from django.conf import settings

_logger = logging.getLogger('function_calls')


def custom_404(request, exception):
    from django.shortcuts import render
    _logger.warning(
        f"404 NOT FOUND | method={request.method} path={request.path} "
        f"user={'anonymous' if not request.user.is_authenticated else request.user.username} "
        f"referer={request.META.get('HTTP_REFERER', 'none')} | {exception}"
    )
    return render(request, '404.html', status=404)


def custom_403(request, exception=None):
    from django.shortcuts import render
    _logger.warning(
        f"403 FORBIDDEN | method={request.method} path={request.path} "
        f"user={'anonymous' if not request.user.is_authenticated else request.user.username} "
        f"referer={request.META.get('HTTP_REFERER', 'none')} | {exception}"
    )
    reason = str(exception) if exception else None
    return render(request, '403.html', {'reason': reason}, status=403)


handler404 = custom_404
handler403 = custom_403

urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/login/', login_view, name='admin_login_redirect'),
    path('', include('src.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    # Also serve files from exportable_media folder (for git-tracked media files)
    import os
    exportable_media_root = os.path.join(settings.BASE_DIR, 'exportable_media')
    urlpatterns += static('/exportable_media/', document_root=exportable_media_root)