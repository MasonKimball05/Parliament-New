"""
Profile card endpoint — returns JSON for the directory popup modal.
"""
import json
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, Http404
from src.models import ParliamentUser
from src.pledge_classes import badge_context


def _pledge_badge(member):
    """Resolved {greek, color, is_founders} for the class badge, or None."""
    return badge_context(member.pledge_class, member.pledge_class_greek)


@login_required
def profile_card_json(request, user_id):
    try:
        member = (
            ParliamentUser.objects
            .select_related('big_brother')
            .prefetch_related('little_brothers', 'roles', 'role_history', 'committees', 'chair_roles')
            .get(user_id=user_id)
        )
    except ParliamentUser.DoesNotExist:
        raise Http404

    grad = ''
    if member.graduation_semester and member.graduation_year:
        grad = f'{member.graduation_semester} {member.graduation_year}'
    elif member.graduation_year:
        grad = str(member.graduation_year)

    academics = {
        'majors': list(member.majors or []),
        'minors': list(member.minors or []),
        'concentrations': list(member.concentrations or []),
    }

    socials = {}
    if member.instagram:
        socials['instagram'] = {'handle': member.instagram, 'url': f'https://instagram.com/{member.instagram}'}
    if member.twitter:
        socials['twitter'] = {'handle': member.twitter, 'url': f'https://x.com/{member.twitter}'}
    if member.linkedin:
        socials['linkedin'] = {'handle': member.linkedin, 'url': f'https://linkedin.com/in/{member.linkedin}'}
    if member.snapchat:
        socials['snapchat'] = {'handle': member.snapchat, 'url': f'https://snapchat.com/add/{member.snapchat}'}
    if member.facebook:
        socials['facebook'] = {'handle': member.facebook, 'url': f'https://facebook.com/{member.facebook}'}

    roles = [{'name': r.name, 'code': r.code} for r in member.roles.all()]

    history = [
        {
            'role': h.role_name,
            'start': h.start_semester,
            'end': h.end_semester or None,
        }
        for h in member.role_history.all()
    ]

    littles = [
        {'name': lb.get_display_name(), 'user_id': lb.user_id, 'role_number': lb.role_number or ''}
        for lb in member.little_brothers.filter(member_status='Active')
    ]

    custom_socials = [
        {'platform': s.get('platform', ''), 'handle': s.get('handle', '')}
        for s in (member.custom_socials or [])
        if s.get('platform') and s.get('handle')
    ]

    initiation_chapters = [
        {
            'school': c.get('school', ''),
            'chapter': c.get('chapter', ''),
            'role_number': c.get('role_number', ''),
        }
        for c in (member.initiation_chapters or [])
        if c.get('school') and c.get('chapter')
    ]

    # Committee memberships (member of or chair of, excluding archived)
    # Use .all() so both relations benefit from the prefetch cache rather than firing extra queries.
    committee_memberships = []
    chaired_names = set()
    for c in member.chair_roles.all():
        if c.is_active and not c.is_archived:
            committee_memberships.append({'name': c.name, 'role': 'Chair'})
            chaired_names.add(c.name)
    for c in member.committees.all():
        if c.is_active and not c.is_archived and c.name not in chaired_names:
            committee_memberships.append({'name': c.name, 'role': 'Member'})
    committee_memberships.sort(key=lambda x: x['name'])

    viewer_is_pledge = request.user.member_type == 'Pledge'

    data = {
        'user_id': '' if viewer_is_pledge else member.user_id,
        'name': member.get_display_name(),
        'full_name': member.name,
        'member_type': member.member_type,
        'member_status': member.member_status,
        'role_number': None if viewer_is_pledge else member.role_number,
        'email': member.email or '',
        'other_email': member.other_email or '',
        'phone': member.phone_number or '',
        'profile_picture_url': member.profile_picture.url if member.profile_picture else '',
        'about_me': member.about_me,
        'house': member.house or '',
        'academics': academics,
        'pledge_class': member.pledge_class,
        'pledge_class_greek': member.pledge_class_greek,
        # v3.15.0: resolved per-class badge color/greek (None if unrecognized)
        'pledge_class_badge': _pledge_badge(member),
        'graduation': grad,
        'big_brother': {
            'name': member.big_brother.get_display_name(),
            'user_id': member.big_brother.user_id,
            'role_number': member.big_brother.role_number or '',
        } if member.big_brother else None,
        'little_brothers': littles,
        'socials': socials,
        'custom_socials': custom_socials,
        'initiation_chapters': initiation_chapters,
        'roles': roles,
        'role_history': history,
        'committees': committee_memberships,
    }

    return JsonResponse(data)
