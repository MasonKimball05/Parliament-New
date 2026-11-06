"""
Admin v2 Two-Factor Authentication management views.
Allows configuring 2FA policies and individual member requirements.
"""
import json
from django.contrib import messages
from django.shortcuts import redirect, render, get_object_or_404
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.db.models import Q
from django_otp import device_classes
from django_otp.plugins.otp_totp.models import TOTPDevice

from src.models import ParliamentUser, TwoFactorRequirement, ActivityLog
from src.models_feature_flags import SiteSetting
from src.view.admin_v2 import require_admin_v2_auth

#: The values SiteSetting '2fa_policy_mode' can hold (Enforce2FAMiddleware reads
#: them), with the 2FA page's labels. Also the app's choices for that setting.
TWO_FACTOR_POLICY_OPTIONS = (
    ('none', 'No 2FA Required'),
    ('admins_only', 'Admins Only'),
    ('officers_and_admins', 'Officers & Admins'),
    ('all_members', 'All Members'),
    ('custom', 'Custom (Individual Selection)'),
)


def two_factor_members(current_policy):
    """
    Every active, not-Inactive member with their 2FA state under
    ``current_policy``: (member_data, stats). v4.4.5: factored out of the
    dashboard for the app.
    """
    # Get all active members.
    #
    # v3.18.6 — `select_related('two_factor_requirement')` and `list()`.
    #
    # `two_factor_requirement` is a reverse OneToOne, so reading it in the loop
    # below cost one `SELECT … LIMIT 21` per member (47 on the live roster).
    # select_related turns that into a LEFT JOIN on the query that is already
    # running. It caches the MISS as well as the hit, so a member with no row
    # costs nothing either — which is the majority here.
    #
    # `list()` because the queryset is walked once for the device batch below
    # and again for the loop; without it that is two full roster queries.
    members = list(
        ParliamentUser.objects.filter(
            is_active=True
        ).exclude(
            member_status='Inactive'
        ).select_related(
            'two_factor_requirement'
        ).order_by('name')
    )

    # v3.18.6 — the device lookup, batched. `user_has_device(member)` walks
    # every installed django-otp device class and queries each until one hits,
    # so on this dashboard it was 47 TOTP queries plus 36 static ones (the 11
    # members WITH a TOTP device short-circuit before the static table). Now it
    # is one query per device class — two — regardless of roster size.
    #
    # `device_classes()` rather than importing TOTPDevice and StaticDevice
    # directly: it is the same enumeration `user_has_device` itself uses, so
    # adding an OTP plugin to INSTALLED_APPS cannot silently make this answer
    # differ from the un-batched version it replaced.
    member_ids = [m.pk for m in members]
    users_with_device = set()
    for device_cls in device_classes():
        users_with_device.update(
            device_cls.objects.filter(
                user_id__in=member_ids, confirmed=True,
            ).values_list('user_id', flat=True)
        )

    # Build member data with 2FA status
    member_data = []
    stats = {
        'total': 0,
        'has_2fa': 0,
        'required': 0,
        'exempt': 0,
        'policy_required': 0,
    }

    for member in members:
        stats['total'] += 1

        # Check if member has 2FA enabled (from the batch above, not a query)
        has_2fa = member.pk in users_with_device
        if has_2fa:
            stats['has_2fa'] += 1

        # Check individual requirement. Served by the select_related JOIN — no
        # query here. `getattr(..., None)` is safe rather than clever: Django
        # builds the reverse-OneToOne miss as `RelatedObjectDoesNotExist`,
        # which subclasses `(TwoFactorRequirement.DoesNotExist, AttributeError)`
        # precisely so this idiom works.
        req = getattr(member, 'two_factor_requirement', None)
        if req is not None:
            requirement_status = req.requirement
            if req.requirement == 'required':
                stats['required'] += 1
            elif req.requirement == 'exempt':
                stats['exempt'] += 1
        else:
            requirement_status = 'policy'  # Follows global policy

        # Calculate if 2FA is required for this member based on policy
        requires_2fa = False
        if requirement_status == 'required':
            requires_2fa = True
        elif requirement_status == 'exempt':
            requires_2fa = False
        else:
            # Follow policy
            if current_policy == 'none':
                requires_2fa = False
            elif current_policy == 'admins_only':
                requires_2fa = member.is_admin
            elif current_policy == 'officers_and_admins':
                requires_2fa = member.is_officer or member.is_admin
            elif current_policy == 'all_members':
                requires_2fa = True
            elif current_policy == 'custom':
                requires_2fa = False

        if requires_2fa and requirement_status == 'policy':
            stats['policy_required'] += 1

        member_data.append({
            'user': member,
            'has_2fa': has_2fa,
            'requirement_status': requirement_status,
            'requires_2fa': requires_2fa,
        })

    return member_data, stats


def filter_two_factor_members(member_data, filter_type='all', search=''):
    """The dashboard's filter (has_2fa / no_2fa / required / exempt / needs_setup) and name search."""
    # Filter handling
    if filter_type == 'has_2fa':
        member_data = [m for m in member_data if m['has_2fa']]
    elif filter_type == 'no_2fa':
        member_data = [m for m in member_data if not m['has_2fa']]
    elif filter_type == 'required':
        member_data = [m for m in member_data if m['requirement_status'] == 'required']
    elif filter_type == 'exempt':
        member_data = [m for m in member_data if m['requirement_status'] == 'exempt']
    elif filter_type == 'needs_setup':
        member_data = [m for m in member_data if m['requires_2fa'] and not m['has_2fa']]

    # Search handling
    if search:
        member_data = [m for m in member_data if search.lower() in m['user'].name.lower()]

    return member_data


@require_admin_v2_auth
def two_factor_dashboard(request):
    """
    Main 2FA management dashboard showing policy, statistics, and member list.
    """
    # Get current policy
    current_policy = SiteSetting.get_setting('2fa_policy_mode', 'none')

    member_data, stats = two_factor_members(current_policy)
    filter_type = request.GET.get('filter', 'all')
    search = request.GET.get('search', '').strip()
    member_data = filter_two_factor_members(member_data, filter_type, search)

    context = {
        'current_policy': current_policy,
        'policy_options': list(TWO_FACTOR_POLICY_OPTIONS),
        'member_data': member_data,
        'stats': stats,
        'filter': filter_type,
        'search': search,
    }

    from src.admin_console import console_enabled
    if console_enabled(request):
        from django.urls import reverse
        bulk = reverse('admin_v2_two_factor_bulk_action')
        context['bulk_actions'] = [
            {'label': 'Require 2FA', 'url': bulk, 'fields': {'action': 'require'},
             'confirm': 'Require two-factor sign-in for the selected members?'},
            {'label': 'Exempt', 'url': bulk, 'fields': {'action': 'exempt'},
             'confirm': 'Exempt the selected members from the 2FA requirement?'},
            {'label': 'Clear override (follow policy)', 'url': bulk, 'fields': {'action': 'clear'},
             'confirm': 'Clear the override for the selected members? They follow the global policy again.'},
        ]
        context['shown'] = len(member_data)
        return render(request, 'admin_v2/console/two_factor_dashboard.html', context)
    return render(request, 'admin_v2/two_factor_dashboard.html', context)


def set_two_factor_policy(request, actor, policy):
    """
    Set SiteSetting '2fa_policy_mode' (created if missing) and write the
    `other` row. False for a value not in TWO_FACTOR_POLICY_OPTIONS (nothing
    changed). v4.4.5: shared by `update_two_factor_policy` and the app.
    """
    valid_policies = [value for value, _label in TWO_FACTOR_POLICY_OPTIONS]
    if policy not in valid_policies:
        return False

    # Get or create the setting
    setting, created = SiteSetting.objects.get_or_create(
        key='2fa_policy_mode',
        defaults={
            'display_name': '2FA Policy Mode',
            'description': 'Global policy for requiring two-factor authentication',
            'category': 'security',
            'setting_type': 'string',
            'value': policy,
            'default_value': 'none',
        }
    )

    if not created:
        setting.value = policy
        setting.last_modified_by = actor.get_display_name()
        setting.save()

    # Log the change
    ActivityLog.log_activity(
        action_type='other',
        user=actor,
        description=f'Updated 2FA policy to: {policy}',
        request=request,
        object_type='SiteSetting',
        object_id=setting.id,
        object_repr=f'2fa_policy_mode={policy}'
    )
    return True


@require_admin_v2_auth
@require_POST
def update_two_factor_policy(request):
    """
    AJAX endpoint to update the global 2FA policy.
    """
    try:
        data = json.loads(request.body)
        policy = data.get('policy')

        if not set_two_factor_policy(request, request.user, policy):
            return JsonResponse({'success': False, 'error': 'Invalid policy'}, status=400)

        return JsonResponse({'success': True, 'policy': policy})

    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


def set_member_2fa_requirement(request, actor, member, requirement, reason=''):
    """
    ``requirement``: 'required', 'exempt', or 'clear' (back to the site
    policy). Writes the `other` row. False for an unknown value (nothing
    changed). v4.4.3: shared by `set_two_factor_requirement` and the app.
    """
    if requirement == 'clear':
        # Remove individual requirement
        TwoFactorRequirement.objects.filter(user=member).delete()
        action = 'Cleared 2FA requirement'
    elif requirement in ['required', 'exempt']:
        # Set individual requirement
        TwoFactorRequirement.objects.update_or_create(
            user=member,
            defaults={
                'requirement': requirement,
                'reason': reason,
                'set_by': actor,
            }
        )
        action = f'Set 2FA requirement to {requirement}'
    else:
        return False

    # Log the change
    ActivityLog.log_activity(
        action_type='other',
        user=actor,
        description=f'{action} for {member.name}',
        request=request,
        object_type='TwoFactorRequirement',
        object_id=member.user_id,
        object_repr=f'{member.name} - {requirement}'
    )
    return True


def reset_member_2fa(request, actor, member):
    """
    Remove ``member``'s authenticator (admin recovery) and write the
    `security_violation` row when there was one; returns how many were
    removed. v4.4.3: shared by `reset_user_2fa` and the app.
    """
    deleted_count = TOTPDevice.objects.filter(user=member).delete()[0]
    if deleted_count > 0:
        ActivityLog.log_activity(
            action_type='security_violation',
            user=actor,
            description=f'Admin reset 2FA for {member.name}',
            request=request,
            object_type='TOTPDevice',
            object_id=member.user_id,
            object_repr=f'{member.name}'
        )
    return deleted_count


@require_admin_v2_auth
@require_POST
def set_two_factor_requirement(request, user_id):
    """
    AJAX endpoint to set individual 2FA requirement for a member.
    """
    try:
        data = json.loads(request.body)
        requirement = data.get('requirement')
        reason = data.get('reason', '')

        member = get_object_or_404(ParliamentUser, user_id=user_id)

        if not set_member_2fa_requirement(request, request.user, member, requirement, reason):
            return JsonResponse({'success': False, 'error': 'Invalid requirement'}, status=400)

        return JsonResponse({
            'success': True,
            'member_name': member.name,
            'requirement': requirement if requirement != 'clear' else 'policy',
        })

    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


def apply_bulk_two_factor(request, action, user_ids, reason=''):
    """Require / exempt / clear the 2FA override for every member in
    ``user_ids`` (unknown ids are skipped) and write one `other` row.
    Returns how many changed, or None for an unknown action."""
    members = ParliamentUser.objects.filter(user_id__in=user_ids)
    count = 0

    if action == 'require':
        for member in members:
            TwoFactorRequirement.objects.update_or_create(
                user=member,
                defaults={
                    'requirement': 'required',
                    'reason': reason or 'Bulk action',
                    'set_by': request.user,
                }
            )
            count += 1
        action_desc = 'required 2FA for'

    elif action == 'exempt':
        for member in members:
            TwoFactorRequirement.objects.update_or_create(
                user=member,
                defaults={
                    'requirement': 'exempt',
                    'reason': reason or 'Bulk action',
                    'set_by': request.user,
                }
            )
            count += 1
        action_desc = 'exempted from 2FA'

    elif action == 'clear':
        count = TwoFactorRequirement.objects.filter(user__in=members).delete()[0]
        action_desc = 'cleared 2FA requirement for'

    else:
        return None

    # Log the bulk action
    ActivityLog.log_activity(
        action_type='other',
        user=request.user,
        description=f'Bulk action: {action_desc} {count} members',
        request=request,
    )
    return count


@require_admin_v2_auth
@require_POST
def bulk_two_factor_action(request):
    """
    AJAX endpoint for bulk 2FA requirement actions.

    The console list's Actions menu posts a plain form instead (``action``
    plus one ``ids`` per ticked row); that gets a message and a redirect back
    to the list rather than JSON.
    """
    if request.content_type != 'application/json':
        user_ids = request.POST.getlist('ids')
        if not user_ids:
            messages.error(request, 'Tick at least one member first.')
        else:
            count = apply_bulk_two_factor(request, request.POST.get('action'), user_ids,
                                          request.POST.get('reason', '').strip())
            if count is None:
                messages.error(request, 'Unknown action.')
            else:
                messages.success(request, f'Updated the 2FA requirement for {count} member(s).')
        return redirect('admin_v2_two_factor')

    try:
        data = json.loads(request.body)
        action = data.get('action')
        user_ids = data.get('user_ids', [])
        reason = data.get('reason', '')

        if not user_ids:
            return JsonResponse({'success': False, 'error': 'No users selected'}, status=400)

        count = apply_bulk_two_factor(request, action, user_ids, reason)
        if count is None:
            return JsonResponse({'success': False, 'error': 'Invalid action'}, status=400)

        return JsonResponse({
            'success': True,
            'count': count,
            'action': action,
        })

    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@require_admin_v2_auth
@require_POST
def reset_user_2fa(request, user_id):
    """
    AJAX endpoint to remove a user's 2FA device (admin recovery).
    """
    try:
        member = get_object_or_404(ParliamentUser, user_id=user_id)

        # Delete all TOTP devices for this user (and log it)
        deleted_count = reset_member_2fa(request, request.user, member)

        if deleted_count > 0:
            return JsonResponse({
                'success': True,
                'message': f'2FA has been reset for {member.name}',
            })
        else:
            return JsonResponse({
                'success': True,
                'message': f'{member.name} did not have 2FA enabled',
            })

    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)
