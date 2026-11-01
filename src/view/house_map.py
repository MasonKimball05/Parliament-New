"""
House map view — shows each house's members and big/little family trees.
"""
from src.app_review import exclude_review_accounts
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.contrib import messages
from django.views.decorators.http import require_http_methods

from src.models import ParliamentUser, Role
from src.decorators import log_function_call
from src.feature_flag_decorators import require_page_enabled
from src.pledge_classes import badge_context, is_original_founder


#: Each house's dot color (the template's .house-dot-* classes; the app uses these).
HOUSE_COLORS = {
    'Smith': '#6366f1',
    'Duncan': '#f59e0b',
    'Knox': '#10b981',
    'Marshall': '#ef4444',
    'Linton': '#8b5cf6',
    'Hardin': '#0ea5e9',
    'Ryan': '#f97316',
    'Gordon': '#14b8a6',
}


def _get_historian_role():
    role, _ = Role.objects.get_or_create(
        code='HIST',
        defaults={
            'name': 'Historian and Archivist',
            'description': 'Can assign house memberships on the house map',
            'one_per_chapter': True,
            'grants_admin': False,
        }
    )
    return role


def _can_set_house(user):
    if user.member_type == 'Officer' or user.is_admin:
        return True
    if user.member_type == 'Chair':
        return user.roles.filter(name__icontains='historian').exists()
    return False


def _build_tree(member, all_little_map, house_code):
    """
    Recursively build a tree node, following littles who have no house or the same house.
    Stops at anyone with a DIFFERENT house explicitly set.
    """
    relevant_littles = [
        lb for lb in all_little_map.get(member.user_id, [])
        if not lb.house or lb.house == house_code
    ]
    badge = badge_context(member.pledge_class, member.pledge_class_greek)
    # A deleted account stays on its family line as a record (src/account_deletion.py).
    former = member.deletion_requested_at is not None
    return {
        'user_id': member.user_id,
        'name': member.get_display_name(),
        'member_type': member.member_type,
        'member_status': 'Former member' if former else member.member_status,
        'former_member': former,
        'role_number': member.role_number or '',
        # v3.15.1: per-class color for the tree node dot (None if unresolved)
        'class_color': badge['color'] if badge else '',
        'class_greek': badge['greek'] if badge else '',
        'littles': [_build_tree(lb, all_little_map, house_code) for lb in relevant_littles],
    }


def _count_tree(node):
    return 1 + sum(_count_tree(lb) for lb in node['littles'])


def _find_implied_members(house_members, member_by_id):
    """
    Walk up the big_brother chain from explicitly-assigned house members,
    collecting houseless ancestors who are implied to be in the same house.
    Returns a dict of user_id -> member for all implied + explicit members.
    """
    implied = {m.user_id: m for m in house_members}
    queue = list(house_members)
    while queue:
        current = queue.pop()
        if not current.big_brother_id:
            continue
        big = member_by_id.get(current.big_brother_id)
        if big and not big.house and big.user_id not in implied:
            implied[big.user_id] = big
            queue.append(big)
    return implied


def house_map_data():
    """
    The house map's trees and counts, shared by the web page and the app
    (src/mobile/views_house_map.py) so the two can't drift.

    Returns ``(houses, all_members, unassigned)``: one dict per house in
    HOUSE_CHOICES order with ``code``, ``label``, ``total``, ``active`` and
    ``trees``; every member row loaded (name order); and the members counted
    as unassigned.
    """
    house_choices = ParliamentUser.HOUSE_CHOICES
    house_codes = {c[0] for c in house_choices}

    # The App Review demo account (src/app_review.py) is on no house.
    all_members = list(
        exclude_review_accounts(ParliamentUser.objects.all())
        .select_related('big_brother')
        .order_by('name')
    )

    member_by_id = {m.user_id: m for m in all_members}

    # Build little_map: big_user_id -> [little_member, ...]
    all_little_map = {}
    for m in all_members:
        if m.big_brother_id:
            all_little_map.setdefault(m.big_brother_id, []).append(m)

    members_by_house = {}
    for m in all_members:
        if m.house in house_codes:
            members_by_house.setdefault(m.house, []).append(m)

    houses = []

    for code, label in house_choices:
        house_members = members_by_house.get(code, [])

        # Extend upward: include houseless ancestors of house-assigned members
        implied = _find_implied_members(house_members, member_by_id)

        # Roots: in the implied set, but whose big is NOT also implied
        roots = [
            m for m in implied.values()
            if not m.big_brother_id or m.big_brother_id not in implied
        ]
        roots.sort(key=lambda m: m.get_display_name())

        trees = [_build_tree(r, all_little_map, code) for r in roots]

        total = sum(_count_tree(t) for t in trees)
        active = sum(1 for m in house_members
                     if m.member_status == 'Active' and m.deletion_requested_at is None)

        houses.append({
            'code': code,
            'label': label,
            'total': total,
            'active': active,
            'trees': trees,
        })

    # v3.41.0: computed from the rows already loaded (no COUNT query). Leaves
    # out Removed members and the 1800s original founders — nobody knows
    # their houses, so they'd sit here forever.
    unassigned = [
        m for m in all_members
        if not m.house and m.member_status != 'Removed' and m.deletion_requested_at is None
        and not is_original_founder(m.pledge_class, m.pledge_class_greek)
    ]
    return houses, all_members, unassigned


@login_required
@require_page_enabled('house_map')
@log_function_call
@require_http_methods(['GET', 'POST'])
def house_map(request):
    historian_role = _get_historian_role()

    if request.method == 'POST' and request.user.is_admin:
        action = request.POST.get('action')
        user_id = request.POST.get('user_id', '').strip()
        try:
            target = ParliamentUser.objects.get(user_id=user_id)
            if action == 'add_historian':
                target.roles.add(historian_role)
                if target.member_type not in ('Officer', 'Chair'):
                    target.member_type = 'Chair'
                    target.save(update_fields=['member_type'])
                messages.success(request, f'{target.get_display_name()} assigned as Historian Chair.')
            elif action == 'remove_historian':
                target.roles.remove(historian_role)
                messages.success(request, f'{target.get_display_name()} removed as Historian Chair.')
        except ParliamentUser.DoesNotExist:
            messages.error(request, 'Member not found.')
        return redirect('house_map')

    house_choices = ParliamentUser.HOUSE_CHOICES
    houses, all_members, unassigned = house_map_data()
    houses_template = [
        {k: h[k] for k in ('code', 'label', 'total', 'active')} for h in houses
    ]
    houses_js = [{'code': h['code'], 'trees': h['trees']} for h in houses]

    can_set_house = _can_set_house(request.user)

    # Quick-manage panel data (house managers only): who's unassigned, plus
    # everyone pickable as a big or little. Pledges with an education-dashboard
    # pairing are locked — set_member_big refuses them too.
    unassigned_panel = None
    if can_set_house:
        from src.models import PledgeBigAssignment
        locked = set(PledgeBigAssignment.objects.values_list('pledge_id', flat=True))
        unassigned_panel = {
            'houses': [list(c) for c in house_choices],
            'unassigned': [m.user_id for m in unassigned],
            'people': [
                {
                    'id': m.user_id,
                    'name': m.get_display_name(),
                    'type': m.member_type,
                    'status': m.member_status,
                    'roll': m.role_number or '',
                    'house': m.house or '',
                    'big': m.big_brother_id or '',
                    'locked': m.user_id in locked,
                }
                for m in all_members if m.member_status != 'Removed'
            ],
        }

    historians = ParliamentUser.objects.filter(roles=historian_role, is_active=True).order_by('name')
    eligible_historians = ParliamentUser.objects.filter(
        is_active=True,
    ).exclude(
        member_type='Pledge',
    ).exclude(
        member_status__in=['Alumni', 'Removed'],
    ).exclude(
        roles=historian_role,
    ).order_by('name')

    return render(request, 'house_map.html', {
        'houses': houses_template,
        'houses_data': houses_js,
        'unassigned_count': len(unassigned),
        'unassigned_panel': unassigned_panel,
        'can_set_house': can_set_house,
        'house_choices': ParliamentUser.HOUSE_CHOICES,
        'historians': historians,
        'eligible_historians': eligible_historians,
    })
