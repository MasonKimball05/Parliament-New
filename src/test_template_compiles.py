"""
Every template compiles — and `service-hours/edit/<id>/` is not a 500.

WHAT THIS FOUND (07-31-26, fixed v3.18.0)
------------------------------------------
`templates/service_hours/edit_submission.html` has been a **hard 500 since
v3.0.0 (2026-06-05)** — nearly two months. It uses two filters:

    {{ existing_responses|get_item:field.id|attr:'text_value' }}

`get_item` exists but the template has **no `{% load %}` tag at all**, and
`attr` **is not defined anywhere in the codebase**. `Invalid filter` is a
`TemplateSyntaxError` raised at PARSE time, not render time, so it does not
matter that the expression sits inside an `{% if %}` — every request to
`edit_service_submission` raised before producing a byte of output.

The route is live: `path('service-hours/edit/<int:submission_id>/', …)`. Any
member clicking "Edit" on their own service-hours submission got a 500.

WHY THE EXISTING SWEEPS MISSED IT
---------------------------------
`test_url_smoke` sweeps **zero-argument** pages; this one takes a
`submission_id`. `test_detail_route_smoke` covers parameterised routes but needs
a `ServiceHoursSubmission` row to reach this one — the same fixture-starvation
lesson v3.17.5 paid for on `/songbook/categories/`: **a page that renders zero
times fails zero times.**

THE CHEAP GUARD
---------------
Compiling every template costs about a second and needs no fixtures, no client
and no database rows. It cannot catch a runtime error, but it catches every
`TemplateSyntaxError` — unregistered filters, unbalanced blocks, misspelled tags
— across all 317 templates, including the ones no sweep can reach.

**A template that no test renders is a template that only your members test.**
"""

import pathlib

from django.template import TemplateSyntaxError
from django.template.loader import get_template
from django.test import TestCase

TEMPLATE_ROOT = pathlib.Path(__file__).resolve().parent.parent / 'templates'

#: Files under templates/ that are not templates. Each needs a reason.
_NOT_A_TEMPLATE = {
    # A copy-paste snippet documenting how to add the Subscribe button to
    # calendar.html — its header literally says "Add this to your calendar.html
    # template". It contains a bare `{% endblock %}` inside an HTML comment, so
    # it has never compiled and never needed to: nothing includes or renders it.
    # Safe to delete; kept only because deleting someone else's scratch file is
    # not this test's job.
    'calendar_subscription_snippet.html',
}


class EveryTemplateCompilesTests(TestCase):

    def test_every_template_compiles(self):
        failures = []
        checked = 0
        for path in sorted(TEMPLATE_ROOT.rglob('*.html')):
            rel = str(path.relative_to(TEMPLATE_ROOT))
            if rel in _NOT_A_TEMPLATE:
                continue
            try:
                get_template(rel)
                checked += 1
            except TemplateSyntaxError as exc:
                failures.append(f'{rel}: {exc}')
            except Exception as exc:                      # noqa: BLE001
                failures.append(f'{rel}: {type(exc).__name__}: {exc}')

        self.assertEqual(
            failures, [],
            'Templates that do not compile. Every one of these is a guaranteed '
            '500 on any page that renders them — TemplateSyntaxError is raised '
            'at parse time, so an {% if %} around the bad expression does not '
            'help.\n  ' + '\n  '.join(failures),
        )
        # Reachability, in the shape v3.17.5 established: a sweep that quietly
        # stops finding templates passes for the wrong reason.
        self.assertGreater(
            checked, 250,
            f'only {checked} templates were compiled — the sweep is not '
            f'reaching the template directory any more',
        )

    def test_the_exclusion_list_is_still_needed(self):
        """An allowlist that outlives its reason is a lie about the codebase."""
        for rel in _NOT_A_TEMPLATE:
            path = TEMPLATE_ROOT / rel
            self.assertTrue(path.exists(), f'{rel} no longer exists — drop it')
            with self.assertRaises(
                Exception,
                msg=f'{rel} compiles now — drop it from _NOT_A_TEMPLATE',
            ):
                get_template(rel)


class EditServiceSubmissionRendersTests(TestCase):
    """
    The specific page. The compile guard proves the template parses; this pins
    the route so a rename cannot quietly detach it again.
    """

    def test_the_template_parses(self):
        get_template('service_hours/edit_submission.html')

    def test_the_route_still_exists(self):
        from django.urls import reverse
        self.assertTrue(reverse('edit_service_submission', args=[1]))


class NoMultiLineHashCommentsTests(TestCase):
    """
    `{# … #}` is SINGLE-LINE ONLY. A multi-line one is not a comment.

    WHAT WENT WRONG — THREE TIMES (07-31-26)
    -----------------------------------------
    Django's hash comment is lexed line by line. Open one with `{#` and close it
    with `#}` on a later line and Django does not recognise it as a comment at
    all: **the contents are rendered into the page**, and any `{{ … }}` or
    `{% … %}` inside them is evaluated.

    This is recorded in CLAUDE.md. v3.16.3 hit it. v3.17.6's changelog says, in
    so many words, *"I made exactly that mistake writing those comments."* And
    then v3.17.5, v3.17.7 and v3.18.0 shipped **fourteen more** — two of them
    into production, where they rendered as visible paragraphs of commentary on
    `view_all_reports` and `view_all_activity`. Mason spotted them on the page.

    A rule written down three times and broken three times is not a knowledge
    problem, it is a missing test. `{% comment %} … {% endcomment %}` is the
    multi-line form; this asserts nothing reaches for the wrong one.
    """

    def test_no_template_opens_a_hash_comment_it_does_not_close(self):
        offenders = []
        for path in sorted(TEMPLATE_ROOT.rglob('*.html')):
            rel = str(path.relative_to(TEMPLATE_ROOT))
            for line_no, line in enumerate(
                    path.read_text(errors='ignore').splitlines(), 1):
                start = line.find('{#')
                if start != -1 and '#}' not in line[start:]:
                    offenders.append(f'{rel}:{line_no}  {line.strip()[:70]}')

        self.assertEqual(
            offenders, [],
            "Multi-line `{# … #}` comments. Django's hash comment is "
            'single-line only, so these RENDER INTO THE PAGE — their text is '
            'visible to users and any tags inside them are evaluated. Use '
            '`{% comment %} … {% endcomment %}` instead.\n  '
            + '\n  '.join(offenders),
        )
