"""
v3.29.35 — remove four `FeatureFlag` rows that never gated anything.

⚠️ WHY THESE FOUR, SPECIFICALLY, AND NOT DELETED BY MIGRATION.

The 07-25-26 audit found 16 `FeatureFlag` rows seeded only by the legacy
`seed_admin_v2` command and read nowhere in `src/` or `templates/` — a
handoff hazard, since a future officer flipping one of these in the admin
would reasonably believe it changes something. Ten were wired in v3.26.0.
Of the remaining six, investigating each one for v3.29.35 found four are
not actually unwired features needing a decorator — they're exact
duplicates of controls that already exist and work under a *different*
name:

  - 'calendar'            — the real gate is `PageToggle('calendar')`,
                             via `@require_page_enabled('calendar')` on
                             `calendar_view`/`event_signup`.
  - 'chapter_documents'   — same shape, `PageToggle('chapter_documents')`
                             on `chapter_documents()`.
  - 'committee_documents' — same shape, `PageToggle('committee_documents')`
                             on `committee_documents()`.
  - 'chat_channels'       — the real chat system is gated by a
                             *different* `FeatureFlag`, `'chats'`,
                             throughout `src/view/chat/` and
                             `src/view/committee/chat.py`.

There is nothing to "wire" for these four — the pages they'd gate are
already correctly gated by something else. Leaving the row in the admin is
the same hazard as leaving it unwired: a control that reads as coverage and
provides none, just now in the other direction (it looks redundant with a
real control rather than looking like the only control). `seed_admin_v2.py`
no longer creates these four rows on a fresh install (see that file), but a
database that already ran the old seeder still has them. This command
removes them from a live DB.

The other two flags this same audit covered — 'activity_logs' and
'login_as_user' — are NOT here. Both are now genuinely wired
(`@require_feature_flag` on the views they gate) and moved to the
canonical `seed_feature_flags.py`. Deleting their rows would be wrong.

⚠️ DRY RUN BY DEFAULT. `--apply` deletes.

    python manage.py prune_dead_feature_flags            # report only
    python manage.py prune_dead_feature_flags --apply    # delete the rows

⚠️ SAFETY CHECK BEFORE DELETING, NOT JUST A HARDCODED LIST. Even though the
four names above are fixed, the command re-derives "is this name actually
read anywhere" at runtime by grepping `src/` and `templates/` for the four
call shapes `is_feature_enabled('<name>')` / `check_feature_enabled('<name>')`
/ `require_feature_flag(...'<name>'...)` / `feature_flags.<name>` before
deleting each one — if a future change wires one of these four names to a
real check, this command will refuse to touch that row and say why, rather
than silently deleting a now-live flag.
"""
import re
import subprocess

from django.conf import settings
from django.core.management.base import BaseCommand


#: The four names this command may ever touch. Deliberately not the full
#: 07-25-26 audit list — 'activity_logs' and 'login_as_user' are wired now
#: and must never be deleted by this command; the other twelve were already
#: wired in v3.26.0 and were never dead in the first place.
CANDIDATE_NAMES = ['calendar', 'chapter_documents', 'committee_documents', 'chat_channels']


def _is_read_anywhere(name, repo_root):
    """
    Re-derive "does anything actually check this flag" at runtime, rather
    than trusting the reasoning in this file's own docstring to still be
    true by the time it runs. Mirrors the check the 08-27-26 and 07-25-26
    audits both ran by hand.
    """
    patterns = [
        rf"is_feature_enabled\(\s*['\"]{re.escape(name)}['\"]",
        rf"check_feature_enabled\(\s*['\"]{re.escape(name)}['\"]",
        rf"require_feature_flag\([^)]*['\"]{re.escape(name)}['\"]",
        rf"feature_flags\.{re.escape(name)}\b",
    ]
    combined = '|'.join(patterns)
    try:
        result = subprocess.run(
            ['grep', '-rlE', combined, str(repo_root / 'src'), str(repo_root / 'templates')],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        # Can't verify — refuse to delete rather than assume it's safe.
        return True
    # Exclude this command's own file and seed_admin_v2's now-historical
    # comments (both legitimately mention the names in prose).
    hits = [
        line for line in result.stdout.splitlines()
        if 'prune_dead_feature_flags.py' not in line and 'seed_admin_v2.py' not in line
    ]
    return bool(hits)


class Command(BaseCommand):
    help = "Remove FeatureFlag rows for names that duplicate a real, differently-named control."

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply', action='store_true',
            help='Actually delete the rows. Without this, report only.')

    def handle(self, *args, **options):
        from src.models_feature_flags import FeatureFlag

        repo_root = settings.BASE_DIR
        apply_changes = options['apply']

        to_delete = []
        for name in CANDIDATE_NAMES:
            if _is_read_anywhere(name, repo_root):
                self.stdout.write(self.style.WARNING(
                    f"  SKIP '{name}' — now referenced somewhere in src/ or "
                    f"templates/. This command's reasoning may be stale; not "
                    f"deleting. Re-check by hand before running --apply again."
                ))
                continue
            try:
                flag = FeatureFlag.objects.get(name=name)
            except FeatureFlag.DoesNotExist:
                self.stdout.write(f"  '{name}' — no row exists, nothing to do.")
                continue
            to_delete.append(flag)
            self.stdout.write(
                f"  {'DELETE' if apply_changes else 'WOULD DELETE'} '{name}' "
                f"(id={flag.pk}, is_enabled={flag.is_enabled})"
            )

        if not to_delete:
            self.stdout.write(self.style.SUCCESS('Nothing to prune.'))
            return

        if apply_changes:
            for flag in to_delete:
                flag.delete()
            self.stdout.write(self.style.SUCCESS(f'Deleted {len(to_delete)} row(s).'))
        else:
            self.stdout.write(self.style.WARNING(
                f'Dry run — {len(to_delete)} row(s) would be deleted. Re-run with --apply.'
            ))
