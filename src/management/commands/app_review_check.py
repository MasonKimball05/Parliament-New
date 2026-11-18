"""
Is the live server ready for Apple's reviewer? (11-17-26)

    python manage.py app_review_check

Read-only. Checks what docs/APP_STORE_SUBMISSION.md (mobile repo) lists under
"Before you submit" and "What the reviewer must be able to see", and prints
PASS / WARN / FAIL for each. Exits 1 if anything FAILs, so it can gate a
release script.

* The review account: named in APP_REVIEW_USERNAMES, exists, can sign in (active,
  not closed or pending deletion, a usable password), a plain Member without an
  admin flag or a role (which keeps it 2FA-exempt and out of rosters).
* The public pages it links to (privacy, support, terms) aren't switched off.
* Location check-in is on (the "Always" location key needs it to be visible).
* Chat is on, and the review account can read a channel that has messages
  (to try Report and Block).
* A Songbook song has a recording (background audio needs it to be visible).
* GeoLite2 is installed (sign-ins work without it; the location is blank).
* The iOS update link is set (only after Apple gives the App Store link).
"""
import os

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

PASS, WARN, FAIL = 'PASS', 'WARN', 'FAIL'


def review_account_checks():
    """[(status, message)] for the review account(s)."""
    from src.app_review import review_usernames
    names = sorted(review_usernames())
    if not names:
        return [(FAIL, 'APP_REVIEW_USERNAMES is empty. Add APP_REVIEW_USERNAMES=appreview to .env, '
                       'restart gunicorn and the worker, then run create_app_review_account.')], []
    out, users = [], []
    User = get_user_model()
    for name in names:
        user = User.objects.filter(username__iexact=name).first()
        if user is None:
            out.append((FAIL, f'Review account "{name}" does not exist. Run: '
                              f'python manage.py create_app_review_account --username {name}'))
            continue
        problems = []
        if not user.is_active or user.member_status != 'Active':
            problems.append('not active')
        if getattr(user, 'deletion_requested_at', None) or getattr(user, 'account_deleted_at', None):
            problems.append('closed or pending deletion (a reviewer used Delete account)')
        if not user.has_usable_password():
            problems.append('no usable password')
        if user.is_admin or user.member_type != 'Member' or user.roles.exists():
            problems.append('not a plain Member (an admin flag, a role or another member type)')
        if problems:
            out.append((FAIL, f'Review account "{name}": {"; ".join(problems)}. Re-run '
                              f'create_app_review_account to reset it (and update the password in App '
                              f'Store Connect).'))
        else:
            out.append((PASS, f'Review account "{name}" can sign in.'))
            users.append(user)
    return out, users


def public_page_checks():
    from src.models_feature_flags import PageToggle
    out = []
    for url_name, label in (('privacy_policy', '/privacy/'), ('support', '/support/'), ('terms', '/terms/')):
        if PageToggle.is_page_enabled(url_name):
            out.append((PASS, f'{label} is on.'))
        else:
            out.append((FAIL, f'{label} is switched off (Admin v2 → page toggles). App Store Connect links to it.'))
    return out


def location_checkin_check():
    from src import location_checkin
    if location_checkin.enabled():
        return [(PASS, 'Location check-in is on.')]
    return [(FAIL, 'Location check-in is off (feature flags: ' + ', '.join(location_checkin.FLAGS) +
                   '). The reviewer needs to see it to accept the "Always" location permission.')]


def chat_checks(users):
    from src.models import ChatChannel, ChatMessage
    from src.models_feature_flags import FeatureFlag
    if not FeatureFlag.resolve_many(['chats'])['chats']:
        return [(FAIL, 'Chat is off (feature flag "chats"). The reviewer needs it to try Report and Block.')]
    out = [(PASS, 'Chat is on.')]
    for user in users:
        channels = list(ChatChannel.objects.filter(is_active=True))
        readable = ChatChannel.access_map(channels, user)
        ids = [c.pk for c in channels if readable[c.pk]]
        with_messages = (ChatMessage.objects.filter(channel_id__in=ids, is_deleted=False)
                         .values('channel_id').distinct().count())
        if with_messages:
            out.append((PASS, f'"{user.username}" can read {with_messages} channel(s) with messages.'))
        else:
            out.append((FAIL, f'"{user.username}" can read {len(ids)} channel(s), none with messages. '
                              f'Post something in a channel members can see.'))
    return out


def songbook_check():
    from src.models import Song
    n = Song.objects.exclude(audio_file='').exclude(audio_file__isnull=True).count()
    if n:
        return [(PASS, f'{n} song(s) have a recording.')]
    return [(FAIL, 'No Songbook song has a recording. Add one (the reviewer needs it to see why the app '
                   'plays audio in the background).')]


def geoip_check():
    path = getattr(settings, 'GEOIP_CITY_DB', '')
    if path and os.path.isfile(path):
        return [(PASS, f'GeoLite2 is installed ({path}).')]
    return [(WARN, f'GeoLite2 is not at {path or "(GEOIP_CITY_DB unset)"}. Sign-ins work; their location is '
                   f'blank. DEPLOYMENT.md, "5a. IP geolocation (GeoLite2)".')]


def update_url_check():
    from src.models_feature_flags import SiteSetting
    if SiteSetting.get_setting('mobile_ios_update_url', ''):
        return [(PASS, 'The iOS update link is set.')]
    return [(WARN, 'The iOS update link is not set (Admin v2 → Site Settings). Set it once Apple gives the '
                   'App Store link; until then "Update" opens the App Store app.')]


class Command(BaseCommand):
    help = "Check the server is ready for Apple's App Review (read-only)."

    def handle(self, *args, **options):
        account, users = review_account_checks()
        results = (account + public_page_checks() + location_checkin_check() + chat_checks(users)
                   + songbook_check() + geoip_check() + update_url_check())
        style = {PASS: self.style.SUCCESS, WARN: self.style.WARNING, FAIL: self.style.ERROR}
        for status, message in results:
            self.stdout.write(f'{style[status](status)}  {message}')
        failed = sum(1 for s, _ in results if s == FAIL)
        self.stdout.write('')
        if failed:
            self.stdout.write(self.style.ERROR(f'{failed} check(s) failed.'))
            raise SystemExit(1)
        self.stdout.write(self.style.SUCCESS('Ready for App Review.'))
