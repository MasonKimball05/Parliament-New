"""
User preferences view
"""
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.contrib import messages
from src.forms import UserPreferencesForm
from src.models import UserPreferences, ActivityLog


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
            form.save()

            # Log the activity
            ActivityLog.log_activity(
                action_type='preferences_updated',
                user=request.user,
                description=f'{request.user.get_display_name()} updated their preferences',
                request=request
            )

            messages.success(request, 'Your preferences have been updated successfully!')
            return redirect('preferences')
        else:
            messages.error(request, 'There was an error updating your preferences. Please try again.')
    else:
        form = UserPreferencesForm(instance=preferences)

    context = {
        'form': form,
        'preferences': preferences,
    }

    return render(request, 'preferences.html', context)
