"""
User preferences view
"""
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.urls import reverse
from django.contrib import messages
from src.forms import UserPreferencesForm
from src.models import UserPreferences, ActivityLog, PushSubscription
from src.models.api import APIToken, DEFINED_SCOPES


@login_required
def preferences_view(request):
    """
    View for users to manage their preferences
    """
    # Get or create user preferences
    preferences, created = UserPreferences.objects.get_or_create(user=request.user)

    if request.method == 'POST':
        form = UserPreferencesForm(request.POST, instance=preferences)
        if form.is_valid():
            old_theme = preferences.theme
            form.save()

            # Log the activity
            ActivityLog.log_activity(
                action_type='preferences_updated',
                user=request.user,
                description=f'{request.user.get_display_name()} updated their preferences',
                request=request
            )

            messages.success(request, 'Your preferences have been updated successfully!')

            # Add a flag to trigger page reload if theme changed
            new_theme = request.POST.get('theme', 'light')
            if old_theme != new_theme:
                return redirect(reverse('preferences') + '?theme_changed=1')

            return redirect('preferences')
        else:
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

    context = {
        'form': form,
        'preferences': preferences,
        'theme_changed': request.GET.get('theme_changed', False),
        'vapid_public_key': getattr(settings, 'VAPID_PUBLIC_KEY', ''),
        'has_push_subscription': has_push_subscription,
        'api_token': api_token,
        'api_token_defined_scopes': DEFINED_SCOPES,
        'api_token_last_rejected': last_rejected_token,
    }

    return render(request, 'preferences.html', context)
