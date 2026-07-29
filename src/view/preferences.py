"""
User preferences view
"""
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.urls import reverse
from django.contrib import messages
from django.views.decorators.http import require_POST
from src.dev_mode import dev_mode_enabled_for, set_dev_mode, user_may_use_dev_mode
from src.forms import UserPreferencesForm
from src.models import UserPreferences, ActivityLog, PushSubscription, WebAuthnCredential
from src.models.api import APIToken, DEFINED_SCOPES


@login_required
def preferences_view(request):
    """
    View for users to manage their preferences
    """
    # Get or create user preferences
    preferences, created = UserPreferences.objects.get_or_create(user=request.user)

    if request.method == 'POST':
        is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        form = UserPreferencesForm(request.POST, instance=preferences)
        if form.is_valid():
            old_theme = preferences.theme
            form.save()

            # Bust the context-processor cache so the new prefs take effect immediately
            from django.core.cache import cache
            cache.delete(f'user_prefs_{request.user.pk}')

            # Log the activity
            ActivityLog.log_activity(
                action_type='preferences_updated',
                user=request.user,
                description=f'{request.user.get_display_name()} updated their preferences',
                request=request
            )

            new_theme = request.POST.get('theme', 'light')
            theme_changed = old_theme != new_theme

            if is_ajax:
                return JsonResponse({'success': True, 'theme_changed': theme_changed})

            messages.success(request, 'Your preferences have been updated successfully!')

            # Add a flag to trigger page reload if theme changed
            if theme_changed:
                return redirect(reverse('preferences') + '?theme_changed=1')

            return redirect('preferences')
        else:
            if is_ajax:
                return JsonResponse({'error': 'There was an error updating your preferences. Please try again.'}, status=400)
            messages.error(request, 'There was an error updating your preferences. Please try again.')
    else:
        form = UserPreferencesForm(instance=preferences)

    has_push_subscription = PushSubscription.objects.filter(user=request.user).exists()

    # Fetch the user's current non-revoked, non-rejected token (if any)
    try:
        api_token = (
            APIToken.objects
            .filter(user=request.user)
            .exclude(status__in=[APIToken.STATUS_REVOKED, APIToken.STATUS_REJECTED])
            .latest('created_at')
        )
    except APIToken.DoesNotExist:
        api_token = None

    # Fetch the most recent rejection so the user can see why and try again
    last_rejected_token = (
        APIToken.objects
        .filter(user=request.user, status=APIToken.STATUS_REJECTED)
        .order_by('-created_at')
        .first()
    )

    # Show passkey nudge if user has no passkeys registered
    has_passkeys = WebAuthnCredential.objects.filter(user=request.user).exists()
    show_passkey_nudge = not has_passkeys

    context = {
        'form': form,
        'preferences': preferences,
        'theme_changed': request.GET.get('theme_changed', False),
        'vapid_public_key': getattr(settings, 'VAPID_PUBLIC_KEY', ''),
        'has_push_subscription': has_push_subscription,
        'api_token': api_token,
        'api_token_defined_scopes': DEFINED_SCOPES,
        'api_token_last_rejected': last_rejected_token,
        'show_passkey_nudge': show_passkey_nudge,
        # Developer mode. `can_use_dev_mode` drives whether the card renders at
        # all — for everyone else the section simply does not exist in the HTML,
        # which is why the toggle is not a field on UserPreferencesForm (a form
        # field would be present in the DOM and settable by a crafted POST).
        'can_use_dev_mode': user_may_use_dev_mode(request.user),
        'dev_mode_on': dev_mode_enabled_for(request.user),
    }

    return render(request, 'preferences.html', context)


@login_required
@require_POST
def toggle_dev_mode(request):
    """
    Flip developer mode for the current user.

    Deliberately a separate endpoint from `preferences_view`:

    * It is gated on the ADMIN_V2_USER_IDS allowlist, so a non-developer POSTing
      here gets a 403 rather than silently writing a preference that would then
      sit in their prefs JSON forever.
    * It keeps the flag off `UserPreferencesForm` entirely. That form rebuilds
      `prefs` wholesale on save, so anything it doesn't know about would be
      clobbered — see the explicit carry-over of the 'dev' section in its
      `save()`.
    * Both directions are logged. Turning on a debug overlay that can surface
      SQL and permission internals should leave a trail, even for you.
    """
    if not user_may_use_dev_mode(request.user):
        return JsonResponse({'error': 'Not available for this account.'}, status=403)

    enabled = request.POST.get('enabled') == '1'
    set_dev_mode(request.user, enabled)

    ActivityLog.log_activity(
        action_type='preferences_updated',
        user=request.user,
        description=(
            f'{request.user.get_display_name()} turned developer mode '
            f'{"ON" if enabled else "OFF"}'
        ),
        request=request,
    )

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({'success': True, 'enabled': enabled})

    messages.success(request, f'Developer mode {"enabled" if enabled else "disabled"}.')
    return redirect('preferences')
