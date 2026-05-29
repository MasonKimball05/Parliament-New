"""
User preferences view
"""
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.contrib import messages
from src.forms import UserPreferencesForm
from src.models import UserPreferences, ActivityLog, PushSubscription


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
                return redirect('preferences' + '?theme_changed=1')

            return redirect('preferences')
        else:
            messages.error(request, 'There was an error updating your preferences. Please try again.')
    else:
        form = UserPreferencesForm(instance=preferences)

    has_push_subscription = PushSubscription.objects.filter(user=request.user).exists()

    context = {
        'form': form,
        'preferences': preferences,
        'theme_changed': request.GET.get('theme_changed', False),
        'vapid_public_key': getattr(settings, 'VAPID_PUBLIC_KEY', ''),
        'has_push_subscription': has_push_subscription,
    }

    return render(request, 'preferences.html', context)
