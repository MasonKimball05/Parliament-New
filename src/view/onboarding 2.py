import json
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.views.decorators.http import require_POST

ALL_ONBOARDING_PAGES = [
    'profile', 'preferences', 'announcements', 'directory',
    'vote', 'committees', 'my_excuses', 'service_hours',
    'chats', 'calendar', 'chapter_documents',
]

# Pages pledges can see (chapter_documents excluded)
PLEDGE_ONBOARDING_PAGES = [p for p in ALL_ONBOARDING_PAGES if p != 'chapter_documents']


@login_required
def onboarding_view(request):
    user = request.user

    # One-time grandfather check for established users predating this feature.
    # Skipped when in_onboarding is set — that means they're mid-wizard (password
    # change just cleared has_default_password, which would otherwise trigger this).
    if not user.onboarding_complete and not request.session.get('in_onboarding') and not user.has_default_password and user.email:
        user.onboarding_complete = True
        user.onboarding_data = {
            'pages_visited': ALL_ONBOARDING_PAGES,
            'checklist_dismissed': True,
        }
        user.save(update_fields=['onboarding_complete', 'onboarding_data'])
        return redirect('home')

    # Determine which step to show (set by forced_password_change redirect)
    step = request.GET.get('step', 'welcome')
    valid_steps = {'welcome', 'passkey', 'guide'}
    if step not in valid_steps:
        step = 'welcome'

    # If email already exists, skip the email step on the client side
    has_email = bool(user.email)

    return render(request, 'onboarding/wizard.html', {
        'step': step,
        'has_email': has_email,
        'user': user,
        'forced_password_change_url': '/forced-password-change/',
    })


@login_required
@require_POST
def onboarding_complete_view(request):
    user = request.user
    data = user.onboarding_data or {}
    data.setdefault('pages_visited', [])
    data.setdefault('checklist_dismissed', False)
    user.onboarding_complete = True
    user.onboarding_data = data
    user.save(update_fields=['onboarding_complete', 'onboarding_data'])
    request.session.pop('in_onboarding', None)
    return redirect('home')


@login_required
@require_POST
def mark_page_visited(request):
    try:
        body = json.loads(request.body)
        page_key = body.get('page', '').strip()
    except (json.JSONDecodeError, AttributeError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    valid_pages = set(ALL_ONBOARDING_PAGES)
    if page_key not in valid_pages:
        return JsonResponse({'error': 'Unknown page'}, status=400)

    user = request.user
    data = user.onboarding_data or {}
    visited = data.get('pages_visited', [])
    if page_key not in visited:
        visited.append(page_key)
        data['pages_visited'] = visited
        user.onboarding_data = data
        user.save(update_fields=['onboarding_data'])

    return JsonResponse({'ok': True, 'pages_visited': visited})


SKIPPABLE_PROFILE_ITEMS = {'phone', 'preferred_name', 'profile_pic', 'about_me'}


@login_required
@require_POST
def skip_profile_item(request):
    try:
        body = json.loads(request.body)
        item_key = body.get('item', '').strip()
    except (json.JSONDecodeError, AttributeError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    if item_key not in SKIPPABLE_PROFILE_ITEMS:
        return JsonResponse({'error': 'Item not skippable'}, status=400)

    user = request.user
    data = user.onboarding_data or {}
    skipped = data.get('skipped_profile_items', [])
    if item_key not in skipped:
        skipped.append(item_key)
        data['skipped_profile_items'] = skipped
        user.onboarding_data = data
        user.save(update_fields=['onboarding_data'])

    return JsonResponse({'ok': True})


@login_required
@require_POST
def reset_onboarding(request):
    user = request.user
    user.onboarding_complete = False
    user.onboarding_data = {}
    user.save(update_fields=['onboarding_complete', 'onboarding_data'])
    return JsonResponse({'ok': True})


@login_required
@require_POST
def dismiss_checklist(request):
    user = request.user
    data = user.onboarding_data or {}
    data['checklist_dismissed'] = True
    user.onboarding_data = data
    user.save(update_fields=['onboarding_data'])
    return JsonResponse({'ok': True})
