"""
Render nginx's `set_real_ip_from` block from the same Cloudflare range table
`src/utils/cloudflare_ranges.py` already uses to verify `CF-Connecting-IP` at
the application layer.

WHY THIS EXISTS
----------------
`nginx.conf` needs its own copy of the Cloudflare ranges (nginx has no way to
import a Python module), and a hand-maintained second copy is exactly the kind
of thing that drifts — one file gets updated when Cloudflare adds a range, the
other doesn't, and nobody notices until an nginx-layer check disagrees with the
Django-layer one. This command makes `cloudflare_ranges.py` the single source
and nginx.conf's block a generated artifact, the same relationship
`nginx.conf`'s CSP/media rules already have with their Django-side gates
(different layers, same fact, checked against each other rather than each
hand-maintained).

USAGE
-----
    python manage.py render_nginx_cloudflare_block

Prints the block to stdout. Paste it into `nginx.conf` between the
`# BEGIN/END cloudflare-ranges` markers already there, or redirect and diff:

    python manage.py render_nginx_cloudflare_block > /tmp/cf_block.conf
    diff /tmp/cf_block.conf <(sed -n '/BEGIN cloudflare-ranges/,/END cloudflare-ranges/p' nginx.conf)

    python manage.py render_nginx_cloudflare_block --deny-block

renders `nginx_cloudflare_only.conf` instead — an `allow <cidr>; ... deny
all;` list that, if included from the server block, refuses any connection
whose immediate peer is not a published Cloudflare edge. **Not included by
default** — see the comment at the top of `nginx_cloudflare_only.conf` for
why this is an availability decision and not a drop-in.

Deliberately does not write to `nginx.conf` directly — this project's stance
on generated security config (see `cloudflare_ranges.py`'s own docstring) is
that a diff a human reads beats a write nobody watches. Re-run this and
re-paste whenever `cloudflare_ranges.py`'s `GENERATED` date moves.
"""
from django.core.management.base import BaseCommand

from src.utils.cloudflare_ranges import CLOUDFLARE_IPV4, CLOUDFLARE_IPV6, GENERATED


class Command(BaseCommand):
    help = (
        "Render nginx's set_real_ip_from block from src/utils/cloudflare_ranges.py "
        "so the two files cannot silently drift apart."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--deny-block', action='store_true',
            help='Render the allow/deny-all block for nginx_cloudflare_only.conf '
                 'instead of the set_real_ip_from block.',
        )

    def handle(self, *args, **options):
        if options['deny_block']:
            self.stdout.write(self._render_deny_block())
            return
        self.stdout.write('\n'.join(self._render_realip_block()))

    def _render_deny_block(self):
        lines = [
            f'# nginx_cloudflare_only.conf — generated {GENERATED} from '
            'src/utils/cloudflare_ranges.py — DO NOT hand-edit; run '
            '`manage.py render_nginx_cloudflare_block --deny-block`',
        ]
        for cidr in CLOUDFLARE_IPV4:
            lines.append(f'allow {cidr};')
        for cidr in CLOUDFLARE_IPV6:
            lines.append(f'allow {cidr};')
        lines.append('allow 127.0.0.1;  # loopback, for host-side debugging/health checks')
        lines.append('deny all;')
        return '\n'.join(lines)

    def _render_realip_block(self):
        lines = [
            f'# BEGIN cloudflare-ranges (generated {GENERATED} from '
            'src/utils/cloudflare_ranges.py — DO NOT hand-edit; run '
            '`manage.py render_nginx_cloudflare_block`)',
            'real_ip_header CF-Connecting-IP;',
            'real_ip_recursive on;',
        ]
        for cidr in CLOUDFLARE_IPV4:
            lines.append(f'set_real_ip_from {cidr};')
        for cidr in CLOUDFLARE_IPV6:
            lines.append(f'set_real_ip_from {cidr};')
        lines.append(
            '# Loopback, so a health check or debug curl run ON the host '
            "itself still resolves to something real rather than being "
            "silently ignored (it is not a Cloudflare range, so without this "
            "nginx's realip module leaves $remote_addr as 127.0.0.1 anyway — "
            "this line exists to make that explicit rather than accidental)."
        )
        lines.append('set_real_ip_from 127.0.0.1;')
        lines.append('# END cloudflare-ranges')
        return lines
