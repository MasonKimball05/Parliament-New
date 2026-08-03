"""
Grant, list or revoke a temporary emergency grant of full Kai access.

⚠️ THIS COMMAND IS THE ONLY WAY TO GET KAI ACCESS WITHOUT A KaiMemberPermission
ROW, AND THAT IS DELIBERATE.

Since v3.18.2 `user.is_admin` grants no Kai access on its own. The reasoning is
in `KaiBreakGlassGrant`'s docstring and in `_get_kai_access`; the short version
is that the standing v3.16.2 rule — *being an admin is an operational role, not
a grant of judicial access* — had been enforced in `/admin/` and not in the app.

The operational need it left behind is real: if every Kai chair graduates at
once, or a permission row is deleted by accident, someone has to be able to get
back in. This is that path, and it is intentionally awkward:

  * **It needs shell access to the box.** A stolen session cookie does not
    clear that bar; a stolen admin password does not either.
  * **It expires** — four hours by default, and `--hours` is capped.
  * **It requires a written reason**, which lands in the audit log.
  * **It announces itself** — the Kai list page shows a banner naming the
    expiry for as long as the grant is live.

Usage
-----

    python manage.py kai_break_glass grant  --user 73 --reason "..." [--hours 4]
    python manage.py kai_break_glass list   [--all]
    python manage.py kai_break_glass revoke --user 73

`--user` takes a `ParliamentUser` pk (`user_id`) or a username.
"""

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone
from datetime import timedelta

from src.models import ActivityLog, KaiBreakGlassGrant, ParliamentUser


#: Hard ceiling on a single grant. A break-glass that can be opened for a week
#: is not a break-glass, it is a role. Re-run the command if you genuinely need
#: longer — each renewal writes its own audit row, which is the point.
MAX_HOURS = 24


class Command(BaseCommand):
    help = 'Grant, list or revoke temporary emergency Kai access for a site admin.'

    def add_arguments(self, parser):
        parser.add_argument(
            'action', choices=['grant', 'list', 'revoke'],
            help='What to do.',
        )
        parser.add_argument(
            '--user', dest='user',
            help='ParliamentUser pk (user_id) or username. Required for grant/revoke.',
        )
        parser.add_argument(
            '--reason', dest='reason',
            help='Why this grant is necessary. Required for grant; it is the audit trail.',
        )
        parser.add_argument(
            '--hours', dest='hours', type=int, default=KaiBreakGlassGrant.DEFAULT_HOURS,
            help=f'Grant length in hours (default {KaiBreakGlassGrant.DEFAULT_HOURS}, max {MAX_HOURS}).',
        )
        parser.add_argument(
            '--all', dest='show_all', action='store_true',
            help='For `list`: include expired and revoked grants.',
        )

    # -- helpers ---------------------------------------------------------

    def _resolve_user(self, identifier):
        if not identifier:
            raise CommandError('--user is required for this action.')
        user = ParliamentUser.objects.filter(
            Q(pk=identifier) | Q(username=identifier)
        ).first()
        if user is None:
            raise CommandError(f'No ParliamentUser matches {identifier!r}.')
        return user

    # -- actions ---------------------------------------------------------

    def handle(self, *args, **options):
        action = options['action']
        if action == 'grant':
            return self._grant(options)
        if action == 'revoke':
            return self._revoke(options)
        return self._list(options)

    def _grant(self, options):
        user = self._resolve_user(options.get('user'))
        reason = (options.get('reason') or '').strip()
        hours = options['hours']

        if not reason:
            raise CommandError(
                '--reason is required. It is written to the audit log and is the '
                'only record of why this access was opened.'
            )
        if hours < 1 or hours > MAX_HOURS:
            raise CommandError(f'--hours must be between 1 and {MAX_HOURS}.')

        # Deliberately NOT restricted to admins in the model, but warned about
        # here: granting this to a non-admin is almost certainly a mistake, and
        # `_get_kai_access` will ignore it anyway (the break-glass branch is
        # only reached for `user.is_admin`).
        if not user.is_admin:
            self.stderr.write(self.style.WARNING(
                f'{user} is not a site admin. `_get_kai_access` only consults '
                'break-glass grants for admins, so this grant will have NO '
                'effect. Give them a KaiMemberPermission row instead.'
            ))

        existing = KaiBreakGlassGrant.active_for(user)
        if existing is not None:
            self.stdout.write(self.style.WARNING(
                f'{user} already holds an active grant until '
                f'{timezone.localtime(existing.expires_at):%Y-%m-%d %H:%M}. '
                'Issuing a second one; both will be revocable independently.'
            ))

        grant = KaiBreakGlassGrant.objects.create(
            user=user,
            granted_by=None,  # Issued from a shell; there is no request user.
            reason=reason,
            expires_at=timezone.now() + timedelta(hours=hours),
        )

        ActivityLog.log_activity(
            action_type='kai_action',
            user=None,
            description=(
                f'BREAK-GLASS: full Kai access granted to {user} for {hours}h '
                f'via manage.py kai_break_glass. Reason: {reason}'
            ),
            object_type='KaiBreakGlassGrant',
            object_id=str(grant.pk),
            object_repr=f'Break-glass grant #{grant.pk}',
            metadata={
                'action': 'break_glass_granted',
                'target_user': str(user.pk),
                'hours': hours,
                'expires_at': grant.expires_at.isoformat(),
            },
        )

        self.stdout.write(self.style.SUCCESS(
            f'Granted full Kai access to {user} until '
            f'{timezone.localtime(grant.expires_at):%Y-%m-%d %H:%M} '
            f'(grant #{grant.pk}).'
        ))
        self.stdout.write(
            'They will see a banner on the Kai list page for as long as it is live. '
            f'Revoke early with: manage.py kai_break_glass revoke --user {user.pk}'
        )

    def _revoke(self, options):
        user = self._resolve_user(options.get('user'))
        grants = KaiBreakGlassGrant.objects.filter(
            user=user, revoked_at__isnull=True, expires_at__gt=timezone.now(),
        )
        count = grants.count()
        if not count:
            self.stdout.write(f'{user} holds no active break-glass grant.')
            return

        now = timezone.now()
        for grant in grants:
            grant.revoked_at = now
            grant.save(update_fields=['revoked_at'])
            ActivityLog.log_activity(
                action_type='kai_action',
                user=None,
                description=(
                    f'BREAK-GLASS: Kai access for {user} revoked early '
                    f'(grant #{grant.pk}).'
                ),
                object_type='KaiBreakGlassGrant',
                object_id=str(grant.pk),
                object_repr=f'Break-glass grant #{grant.pk}',
                metadata={'action': 'break_glass_revoked', 'target_user': str(user.pk)},
            )
        self.stdout.write(self.style.SUCCESS(
            f'Revoked {count} active grant(s) for {user}. Access is inert immediately.'
        ))

    def _list(self, options):
        qs = KaiBreakGlassGrant.objects.select_related('user').order_by('-granted_at')
        if not options['show_all']:
            qs = qs.filter(revoked_at__isnull=True, expires_at__gt=timezone.now())

        rows = list(qs[:100])
        if not rows:
            self.stdout.write(
                'No active break-glass grants.'
                if not options['show_all'] else 'No break-glass grants on record.'
            )
            return

        for grant in rows:
            if grant.revoked_at:
                state = self.style.SUCCESS('revoked')
            elif grant.is_active:
                state = self.style.WARNING('ACTIVE')
            else:
                state = 'expired'
            self.stdout.write(
                f'#{grant.pk}  {grant.user}  [{state}]  '
                f'granted {timezone.localtime(grant.granted_at):%Y-%m-%d %H:%M}  '
                f'expires {timezone.localtime(grant.expires_at):%Y-%m-%d %H:%M}\n'
                f'        reason: {grant.reason}'
            )
