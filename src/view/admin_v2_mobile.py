"""
Admin v2: the mobile app (v3.58.1).

Who has the app, which versions are out there, whether push is set up, and a
test push to one member. Before this the only push page in admin-v2 was the
browser (web push) subscription list; app installs had no admin view at all.

Expo delivery receipts are not stored (tasks/mobile_push.py checks them every
15 minutes and deletes the ticket rows), so "awaiting receipt" is the only
delivery number there is; errors from receipts are in the server log.
"""
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from src.mobile import push as expo
from src.models import ActivityLog, ParliamentUser
from src.models.mobile import MobileDevice, MobilePushTicket
from src.view.admin_v2 import require_admin_v2_auth


def _setup_checks():
    """(label, ok, hint) for each server-side app setting; values never shown."""
    return [
        ('Expo access token (push)', bool(getattr(settings, 'EXPO_ACCESS_TOKEN', '')),
         'EXPO_ACCESS_TOKEN in .env, with Enhanced Push Security on in Expo'),
        ('iOS team ID', bool(getattr(settings, 'MOBILE_IOS_TEAM_ID', '')), 'MOBILE_IOS_TEAM_ID'),
        ('iOS bundle ID', bool(getattr(settings, 'MOBILE_IOS_BUNDLE_ID', '')), 'MOBILE_IOS_BUNDLE_ID'),
        ('Android package', bool(getattr(settings, 'MOBILE_ANDROID_PACKAGE', '')), 'MOBILE_ANDROID_PACKAGE'),
        ('Android signing certificate', bool(getattr(settings, 'MOBILE_ANDROID_CERT_SHA256', [])),
         'MOBILE_ANDROID_CERT_SHA256 (from eas credentials)'),
    ]


@require_admin_v2_auth
def admin_v2_mobile(request):
    now = timezone.now()
    live = MobileDevice.objects.filter(revoked_at__isnull=True, refresh_expires_at__gt=now)
    stats = live.aggregate(
        total=Count('pk'),
        ios=Count('pk', filter=Q(platform='ios')),
        android=Count('pk', filter=Q(platform='android')),
        push=Count('pk', filter=~Q(push_token='')),
        week=Count('pk', filter=Q(last_seen_at__gte=now - timedelta(days=7))),
        members=Count('user', distinct=True),
    )
    versions = list(live.values('platform', 'app_version').annotate(n=Count('pk')).order_by('-n')[:12])
    recent = list(MobileDevice.objects.select_related('user').order_by('-created_at')[:25])
    push_members = (ParliamentUser.objects
                    .filter(mobile_devices__in=live.exclude(push_token=''))
                    .distinct().order_by('name').only('user_id', 'name', 'preferred_name'))
    context = {
        'stats': stats,
        'versions': versions,
        'recent': recent,
        'push_members': push_members,
        'awaiting_receipts': MobilePushTicket.objects.count(),
        'setup': _setup_checks(),
    }
    return render(request, 'admin_v2/mobile.html', context)


@require_admin_v2_auth
@require_POST
def admin_v2_mobile_push(request):
    """Send a test push to every live, push-enabled device of one member."""
    member = get_object_or_404(ParliamentUser, user_id=request.POST.get('user_id', ''))
    devices = expo.live_push_devices([member.pk])
    if not devices:
        messages.warning(request, f'{member.get_display_name()} has no signed-in app with notifications on.')
        return redirect('admin_v2_mobile')
    try:
        ok = expo.send_to_devices(
            devices, 'Test from Parliament',
            f'Sent by {request.user.get_display_name()} from the admin panel. Push works.',
            data={'url': '/notifications/', 'screen': 'notifications'})
    except expo.ExpoTransientError:
        messages.error(request, 'Expo did not answer. Try again in a minute.')
        return redirect('admin_v2_mobile')
    ActivityLog.log_activity(
        action_type='user_edited', user=request.user,
        description=f'{request.user.get_display_name()} sent a test push to {member.get_display_name()} '
                    f'({ok} of {len(devices)} device(s) accepted)',
        object_type='ParliamentUser', object_id=str(member.user_id), object_repr=member.get_display_name(),
        request=request)
    if ok:
        messages.success(request, f'Expo accepted the test push for {ok} of {len(devices)} device(s). '
                                  'It should arrive within a few seconds.')
    else:
        messages.error(request, 'Expo refused the push. The server log has the reason '
                                '(often a missing or wrong EXPO_ACCESS_TOKEN).')
    return redirect('admin_v2_mobile')
