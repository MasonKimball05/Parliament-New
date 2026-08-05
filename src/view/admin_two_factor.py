"""
Admin v2 Two-Factor Authentication management views.
Allows configuring 2FA policies and individual member requirements.
"""
import json
from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.db.models import Q
from django_otp import device_classes
from django_otp.plugins.otp_totp.models import TOTPDevice

from src.models import ParliamentUser, TwoFactorRequirement, ActivityLog
from src.models_feature_flags import SiteSetting
from src.view.admin_v2 import require_admin_v2_auth


@require_admin_v2_auth
def two_factor_dashboard(request):
    """
    Main 2FA management dashboard showing policy, statistics, and member list.
    """
    # Get current policy
    current_policy = SiteSetting.get_setting('2fa_policy_mode', 'none')

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

    # Filter handling
    filter_type = request.GET.get('filter', 'all')
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
    search = request.GET.get('search', '').strip()
    if search:
        member_data = [m for m in member_data if search.lower() in m['user'].name.lower()]

    context = {
        'current_policy': current_policy,
        'policy_options': [
            ('none', 'No 2FA Required'),
            ('admins_only', 'Admins Only'),
            ('officers_and_admins', 'Officers & Admins'),
            ('all_members', 'All Members'),
            ('custom', 'Custom (Individual Selection)'),
        ],
        'member_data': member_data,
        'stats': stats,
        'filter': filter_type,
        'search': search,
    }

    return render(request, 'admin_v2/two_factor_dashboard.html', context)


@require_admin_v2_auth
@require_POST
def update_two_factor_policy(request):
    """
    AJAX endpoint to update the global 2FA policy.
    """
    try:
        data = json.loads(request.body)
        policy = data.get('policy')

        valid_policies = ['none', 'admins_only', 'officers_and_admins', 'all_members', 'custom']
        if policy not in valid_policies:
            return JsonResponse({'success': False, 'error': 'Invalid policy'}, status=400)

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
            setting.last_modified_by = request.user.get_display_name()
            setting.save()

        # Log the change
        ActivityLog.log_activity(
            action_type='other',
            user=request.user,
            description=f'Updated 2FA policy to: {policy}',
            request=request,
            object_type='SiteSetting',
            object_id=setting.id,
            object_repr=f'2fa_policy_mode={policy}'
        )

        return JsonResponse({'success': True, 'policy': policy})

    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


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

        if requirement == 'clear':
            # Remove individual requirement
            TwoFactorRequirement.objects.filter(user=member).delete()
            action = 'Cleared 2FA requirement'
        elif requirement in ['required', 'exempt']:
            # Set individual requirement
            req, created = TwoFactorRequirement.objects.update_or_create(
                user=member,
                defaults={
                    'requirement': requirement,
                    'reason': reason,
                    'set_by': request.user,
                }
            )
            action = f'Set 2FA requirement to {requirement}'
        else:
            return JsonResponse({'success': False, 'error': 'Invalid requirement'}, status=400)

        # Log the change
        ActivityLog.log_activity(
            action_type='other',
            user=request.user,
            description=f'{action} for {member.name}',
            request=request,
            object_type='TwoFactorRequirement',
            object_id=member.user_id,
            object_repr=f'{member.name} - {requirement}'
        )

        return JsonResponse({
            'success': True,
            'member_name': member.name,
            'requirement': requirement if requirement != 'clear' else 'policy',
        })

    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@require_admin_v2_auth
@require_POST
def bulk_two_factor_action(request):
    """
    AJAX endpoint for bulk 2FA requirement actions.
    """
    try:
        data = json.loads(request.body)
        action = data.get('action')
        user_ids = data.get('user_ids', [])
        reason = data.get('reason', '')

        if not user_ids:
            return JsonResponse({'success': False, 'error': 'No users selected'}, status=400)

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
            return JsonResponse({'success': False, 'error': 'Invalid action'}, status=400)

        # Log the bulk action
        ActivityLog.log_activity(
            action_type='other',
            user=request.user,
            description=f'Bulk action: {action_desc} {count} members',
            request=request,
        )

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

        # Delete all TOTP devices for this user
        deleted_count = TOTPDevice.objects.filter(user=member).delete()[0]

        if deleted_count > 0:
            # Log the action
            ActivityLog.log_activity(
                action_type='security_violation',
                user=request.user,
                description=f'Admin reset 2FA for {member.name}',
                request=request,
                object_type='TOTPDevice',
                object_id=member.user_id,
                object_repr=f'{member.name}'
            )

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
