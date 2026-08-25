"""
`.count` and `.exists` must not be called on querysets from a template.

WHY THIS TEST EXISTS (v3.17.5)
------------------------------
Dev mode's N+1 panel flagged `active_quarantines.count` on the admin-v2 security
dashboard as a 4× repeated query shape. Sweeping every template for the same
pattern found **~30 more**, and the worst were on *related managers inside
loops*, which is the N+1 version of the same bug.

THE THREE FACTS THAT MAKE THIS A TRAP
-------------------------------------
1. **`.count` on a queryset is a fresh `SELECT COUNT(*)` every single time.**
   The queryset result cache does not cover it. So the more places a template
   shows the same number, the more round trips it costs — `{{ x.count }}
   thing{{ x.count|pluralize }}` is *two* queries, on one line, and that idiom
   was everywhere.

2. **On a related manager with no `prefetch_related`, it is one query per
   row** — the N+1 proper. This is where the real cost was: `amendments`,
   `articles`, `members`, `songs`.

   ⚠️ **Measured correction, worth recording because CLAUDE.md's sweep list
   still says otherwise:** on a relation that IS prefetched, Django 5.2's
   `.count()` and `.exists()` *do* read the prefetch cache and cost **0
   queries** (`RelatedManager.get_queryset()` returns the cached queryset, and
   `QuerySet.count()`/`.exists()` short-circuit on `_result_cache`). So
   `prefetch_related` genuinely does fix those. Verified with
   `CaptureQueriesContext`, both directions. The remaining reason to prefer
   `|length` on a prefetched relation is consistency, not query count — but
   the reason to prefer it on a **lazy context queryset** is real.

3. **`|length` is strictly better on a context queryset the template also
   iterates**, which is nearly always. `|length` calls `len()`, which
   *evaluates and caches* the queryset — so the `{% if %}` and `{% for %}`
   that follow reuse that cache. `.count` primes nothing, so
   `{{ x.count }} thing{{ x.count|pluralize }}` plus a loop is three queries
   where one would do.

So the fix is one of three shapes:
  * a lazy context queryset       → `|length` (and the loop comes free);
  * a relation only counted       → annotate `Count(...)` in the view;
  * a relation counted AND looped → `prefetch_related`, then either spelling.

WHAT THIS TEST DOES
-------------------
Scans every template for `something.count` / `something.exists` and fails on
anything not in `ALLOWED`. The allowlist is keyed by **file and expression**, so
each entry is a decision someone made about that specific line — adding the same
name in another template does not inherit the exemption.
"""

import pathlib
import re

from django.test import SimpleTestCase

TEMPLATES = pathlib.Path(__file__).resolve().parent.parent.parent.parent / 'templates'

#: `{{ x.count }}`, `{% if x.count %}`, `{{ x.count|pluralize }}`, `x.exists`…
_CALL_RE = re.compile(r'\{[{%][^{}%]*?\b([A-Za-z_][\w.]*\.(?:count|exists))\b')

#: (template, expression) pairs that are NOT queryset methods.
#:
#: Every entry here has been checked against the view or model that supplies it.
#: If you are adding one, say which — "it looks like a dict" is how this class of
#: bug survives.
ALLOWED = {
    # APIRequestLog.response_summary is a JSONField whose documented structure is
    # {"count": N, "sample": [...]} — src/models/api.py:132-133. Dict key.
    ('admin_v2/api_token_logs.html', 'log.response_summary.count'),
    # Rows from .values(...).annotate(count=Count(...)) — dict keys, not managers.
    ('admin_v2/csp_violations.html', 'g.count'),
    ('admin_v2/honeypot_logs.html', 'ep.count'),
    ('admin_v2/honeypot_logs.html', 'ip.count'),
    ('admin_v2/page_visits.html', 'row.count'),
    ('committee/vote_result.html', 'bar.count'),
    # Both prefetched by their views (v3.17.3), so these read the prefetch cache
    # and cost nothing — see the measured note in the module docstring.
    ('committee/vote_result.html', 'legislation.runoff_votes.exists'),
    ('directory.html', 'member.roles.exists'),
    ('slating/applications_review.html', 'app.interviews.exists'),
    ('slating/my_applications.html', 'app.interviews.exists'),
    ('vote_result.html', 'bar.count'),
    ('vote_result.html', 'result.count'),
    ('slating/applications_review.html', 'data.count'),
    ('slating/period_setup.html', 'item.count'),
    # Dev-mode panel: `sh` and `f` are dicts built in src/dev_mode.py.
    ('dev/panel.html', 'sh.count'),
    ('dev/panel.html', 'f.count'),
}


def _allowed(rel_path, expression):
    # Paginator.count is a cached_property and is reached through whatever the
    # page object is called, so it is matched by suffix rather than by name.
    if expression == 'paginator.count' or expression.endswith('.paginator.count'):
        return True
    return (rel_path, expression) in ALLOWED


class NoQuerysetCountInTemplatesTests(SimpleTestCase):

    def test_no_template_calls_count_or_exists_on_a_queryset(self):
        offenders = []
        for path in sorted(TEMPLATES.rglob('*.html')):
            rel = str(path.relative_to(TEMPLATES))
            for line_no, line in enumerate(
                    path.read_text(encoding='utf-8').splitlines(), 1):
                for expression in _CALL_RE.findall(line):
                    if not _allowed(rel, expression):
                        offenders.append(f'{rel}:{line_no}  {expression}')

        self.assertEqual(
            offenders, [],
            'These call .count/.exists on what is probably a queryset. On a '
            'lazy context queryset that is a query every time and it primes no '
            'cache for the loop that follows; on an un-prefetched relation it '
            'is a query per row. Use |length, annotate Count() in the view, or '
            'add prefetch_related. If the expression is really a dict key, add '
            'it to ALLOWED with the model or view that proves it.',
        )

    def test_the_allowlist_has_no_dead_entries(self):
        """
        An allowlist that outlives the line it excuses is how the next reviewer
        concludes the check is weaker than it is.
        """
        seen = set()
        for path in sorted(TEMPLATES.rglob('*.html')):
            rel = str(path.relative_to(TEMPLATES))
            text = path.read_text(encoding='utf-8')
            for expression in _CALL_RE.findall(text):
                seen.add((rel, expression))

        dead = sorted(
            f'{rel}  {expression}'
            for rel, expression in ALLOWED
            if rel != '*' and (rel, expression) not in seen
        )
        self.assertEqual(dead, [], 'ALLOWED entries that no template uses')
