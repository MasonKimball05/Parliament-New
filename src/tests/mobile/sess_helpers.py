"""Shared setup for the p1-sess mobile tests (handoff, ws tickets, revocation, devices)."""
from src.mobile.tokens import create_device
from src.models import ParliamentUser


def make_user(uid, **extra):
    fields = dict(user_id=uid, name=f'User {uid}', username=uid,
                  member_type='Member', member_status='Active')
    fields.update(extra)
    user = ParliamentUser.objects.create(**fields)
    user.set_password('pw-' + uid)
    user.save()
    return user


def make_device(user, verified_via='password', platform='ios', name='Phone'):
    """(device, access, refresh) for a freshly signed-in install."""
    return create_device(user, verified_via, platform, device_name=name, app_version='1.0.0')


def bearer(access):
    return {'HTTP_AUTHORIZATION': f'Bearer {access}'}
