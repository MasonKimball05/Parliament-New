"""
Bring existing Kai tags into the closed vocabulary introduced in v3.16.3.

WHY THIS EXISTS
---------------
`KaiReport.tags` was free text until 07-28-26. That was a hole through the Kai
identity redaction: tags are searchable (`_kai_search_q` searches them with no
permission gate), rendered on the report list card, and written to the CSV
export — all at `can_view_report_list` level. So a tag reading "smith-incident"
handed a name to reviewers the app deliberately denies `submitted_by` and
`targeted_to`.

v3.16.3 closed the write sites. This command closes the *existing rows*, which
the code change cannot reach.

WHY IT IS A COMMAND AND NOT A DATA MIGRATION
--------------------------------------------
Migrations run unattended on deploy. This one edits disciplinary records and
has to make judgement calls about values nobody has reviewed, so it defaults to
a dry run and prints exactly what it would do. Look at the output, then re-run
with --apply. Anything removed is recorded in the case's own activity timeline
first, which is gated on can_view_report_details — so nothing is destroyed
silently, and the removed text does not land anywhere a list-only reviewer can
read it.

USAGE
-----
    python manage.py normalize_kai_tags              # dry run (default)
    python manage.py normalize_kai_tags --apply      # write changes
    python manage.py normalize_kai_tags --apply --no-audit
"""

from django.core.management.base import BaseCommand
from django.db import transaction, OperationalError, ProgrammingError


class Command(BaseCommand):
    help = (
        'Normalize KaiReport.tags and KaiReportTemplate.suggested_tags to the '
        'closed vocabulary (KaiReport.TAG_CHOICES). Dry run unless --apply.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Actually write the changes. Without this the command only reports.',
        )
        parser.add_argument(
            '--no-audit',
            action='store_true',
            help=(
                'Skip writing a KaiReportActivity entry recording the removed values. '
                'Only use this if you have already captured them elsewhere.'
            ),
        )

    def handle(self, *args, **options):
        from src.models import KaiReport, KaiReportActivity, KaiReportTemplate

        apply_changes = options['apply']
        write_audit = not options['no_audit']

        self.stdout.write(self.style.MIGRATE_HEADING(
            'Kai tag vocabulary normalization — %s'
            % ('APPLYING CHANGES' if apply_changes else 'DRY RUN (no writes)')
        ))
        self.stdout.write('Vocabulary: %s\n' % ', '.join(KaiReport.ALLOWED_TAGS))

        # A deploy may run this before the Kai tables exist (fresh install, or a
        # migrate that hasn't happened yet). Say so plainly instead of dumping a
        # traceback into the deploy log — there is nothing to normalize either way.
        try:
            report_changes = []
            for report in KaiReport.objects.all().only('id', 'title', 'tags'):
                current = report.get_tags_list()
                if not current:
                    continue
                accepted, rejected = KaiReport.normalize_tags(current)
                if accepted == current and not rejected:
                    continue
                report_changes.append((report, current, accepted, rejected))

            template_changes = []
            for template in KaiReportTemplate.objects.all().only('id', 'name', 'suggested_tags'):
                current = list(template.suggested_tags or [])
                if not current:
                    continue
                accepted, rejected = KaiReport.normalize_tags(current)
                if accepted == current and not rejected:
                    continue
                template_changes.append((template, current, accepted, rejected))
        except (OperationalError, ProgrammingError) as exc:
            self.stdout.write(self.style.WARNING(
                'Kai tables are not present in this database (%s). Nothing to '
                'normalize — run `migrate` first if that is unexpected.' % exc
            ))
            return

        if not report_changes and not template_changes:
            self.stdout.write(self.style.SUCCESS(
                'Nothing to do — every tag already matches the vocabulary.'
            ))
            return

        # ---- Reports -------------------------------------------------------
        if report_changes:
            self.stdout.write(self.style.MIGRATE_LABEL(
                '\n%d report(s) with tags to change:' % len(report_changes)
            ))
            for report, current, accepted, rejected in report_changes:
                self.stdout.write('  Case #%s' % report.id)
                self.stdout.write('    before : %s' % ', '.join(current))
                self.stdout.write('    after  : %s' % (', '.join(accepted) or '(none)'))
                if rejected:
                    self.stdout.write(self.style.WARNING(
                        '    DROPPED: %s' % ', '.join(rejected)
                    ))

        # ---- Templates -----------------------------------------------------
        if template_changes:
            self.stdout.write(self.style.MIGRATE_LABEL(
                '\n%d template(s) with suggested_tags to change:' % len(template_changes)
            ))
            for template, current, accepted, rejected in template_changes:
                self.stdout.write('  Template "%s" (#%s)' % (template.name, template.id))
                self.stdout.write('    before : %s' % ', '.join(current))
                self.stdout.write('    after  : %s' % (', '.join(accepted) or '(none)'))
                if rejected:
                    self.stdout.write(self.style.WARNING(
                        '    DROPPED: %s' % ', '.join(rejected)
                    ))

        dropped_total = sum(len(r[3]) for r in report_changes) + \
            sum(len(t[3]) for t in template_changes)

        if not apply_changes:
            self.stdout.write(self.style.WARNING(
                '\nDRY RUN — nothing written. %d value(s) would be dropped.'
                % dropped_total
            ))
            self.stdout.write(
                'Review the DROPPED lines above. If any of them records something worth '
                'keeping, put it in the case\'s chair notes (restricted to reviewers with '
                'detail access) BEFORE re-running with --apply.'
            )
            self.stdout.write('Re-run with --apply to write these changes.')
            return

        with transaction.atomic():
            for report, current, accepted, rejected in report_changes:
                report.tags = accepted
                report.save(update_fields=['tags'])
                if rejected and write_audit:
                    # details is only rendered on the detail page, which is
                    # can_view_report_details-gated — the list page shows the
                    # action label and timestamp only. Safe place for this.
                    KaiReportActivity.objects.create(
                        report=report,
                        user=None,
                        action='tags_updated',
                        details=(
                            'Tag vocabulary normalization (v3.16.3): removed '
                            'out-of-vocabulary tag(s) %s. Remaining: %s.'
                            % (', '.join(rejected), ', '.join(accepted) or 'none')
                        ),
                    )

            for template, current, accepted, rejected in template_changes:
                template.suggested_tags = accepted
                template.save(update_fields=['suggested_tags'])

        self.stdout.write(self.style.SUCCESS(
            '\nDone. %d report(s) and %d template(s) updated; %d value(s) dropped%s.'
            % (
                len(report_changes),
                len(template_changes),
                dropped_total,
                ' (recorded in each case\'s activity timeline)' if write_audit else '',
            )
        ))
