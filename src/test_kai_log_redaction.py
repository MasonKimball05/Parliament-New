"""
v3.25.2 — `/officers/system-logs/` renders a file, and that file named Kai
reporters.

⚠️ THE TWELFTH KAI SURFACE, AND THE FIRST THAT IS NOT A DATABASE ROW.

`src/decorators.py::log_function_call` writes one line per call:

    User <username> called <view> with arguments: (), {...}, Action: ...

Thirteen views in `src/view/kai_reports.py` carry that decorator. On
`submit_kai_report` the caller **is** the reporter, so the line is
`User reporter_guy called submit_kai_report` — the single fact
`can_view_submitter_identity` exists to withhold. `/officers/system-logs/` tails
the last 200 lines of that file and renders them verbatim, behind
`officer_or_advisor_required`, which admits every officer, chair and advisor and
consults no `KaiMemberPermission` at all.

Reproduced end-to-end on 08-24-26 before the fix: a plain Officer with no Kai
permission loaded the page and read the reporter's username beside the view
name.

> **v3.18.2's rule was "enumerate the MODELS that can store it first", and a log
> file is not a model.** That is not a failure of the rule; it is the rule's
> edge. The durable form is one step out: *enumerate the things that STORE it,
> and a file is storage.* An audit description, a notification body, an email
> subject, a log line — v3.18.2 listed all four and then went looking through
> `apps.get_models()`, which can only ever find the first three.

⚠️ AND THE FIRST FIX FOR IT WAS A GUARD WRITTEN AGAINST THE INSTANCE.

It handled exactly the shape `log_function_call` emits, because that was the
line in front of me. **Enumerating the writers into `django_actions.log` — the
question that should have come first — found two more, and both are worse:**

    <username> requested closure for Kai report '<title>' (ID: 12)
    <username> requested to drop Kai report '<title>' (ID: 12)

written by `kai_user_dashboard.request_closure` and `request_drop_case`, which
are `@login_required` **party-facing** views. So the username is the submitter
or the accused, *and* the line carries the case title beside it. A third site,
`kai_reports.py`'s `[KAI EMAIL] Email queued for report: <title>`, leaked the
title alone.

All three are fixed **at source** — that is the actual fix, and
`NoKaiViewLogsAConfidentialAttributeTests` below is the general form of it, so
the next one is caught in the diff. The render-time redaction stays for the
lines already on disk, which nothing can unwrite.

WHAT IS AND IS NOT REDACTED
---------------------------
Removed on a Kai line: the actor of a `User X called <kai view>` line, any token
equal to a member's username, and any single-quoted run. Kept: the view name,
the case id, the arguments, the level and the logger. A case id names nobody and
the reader cannot open the case; an operations page emptied of operational
content is indistinguishable from a deleted one.
"""
import ast
import importlib
import logging
import os

from django.conf import settings
from django.test import Client, TestCase
from django.urls import reverse

from src.kai_audit import (REDACTED, kai_log_view_names,
                           redact_kai_log_message)
from src.models import ParliamentUser

PASSWORD = 'kai-log-redaction-pass-98765!'

#: The modules that handle Kai cases and may therefore log about them.
_KAI_VIEW_MODULES = ('src/view/kai_reports.py', 'src/view/kai_user_dashboard.py')

#: Attributes that identify a party or carry case content. `pk`, `id` and
#: `report_id` are deliberately absent: an id names nobody.
_CONFIDENTIAL_ATTRIBUTES = frozenset({
    'title', 'description', 'username', 'name', 'preferred_name', 'email',
    'submitted_by', 'targeted_to', 'chair_notes', 'committee_notes',
    'deliberation_outcome',
})

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TheViewNameSetIsDerivedNotTypedTests(TestCase):

    def test_it_contains_the_kai_views_that_carry_the_decorator(self):
        names = kai_log_view_names()
        for view in ('submit_kai_report', 'manage_kai_report', 'print_kai_report',
                     'export_kai_reports_csv', 'recuse_kai_member',
                     'appoint_kai_standin', 'end_kai_recusal'):
            self.assertIn(view, names)

    def test_it_covers_the_kai_attachment_views_from_the_other_module(self):
        """
        These live in `serve_private_upload.py`, so the module walk cannot see
        them and they are named explicitly. Both name a case.
        """
        self.assertIn('serve_kai_report_attachment', kai_log_view_names())
        self.assertIn('serve_kai_response_file', kai_log_view_names())

    def test_it_does_not_swallow_unrelated_views(self):
        """
        THE CONTROL. A set that matched everything would pass every assertion
        above while quietly emptying the page.
        """
        names = kai_log_view_names()
        for view in ('view_logs', 'home', 'directory', 'vote_view',
                     'event_attendance_list'):
            self.assertNotIn(view, names)

    def test_a_new_kai_view_is_covered_without_anyone_remembering(self):
        """
        The property the derivation buys, asserted rather than assumed: every
        public function defined in `src/view/kai_reports.py` is in the set.
        """
        import inspect

        from src.view import kai_reports

        for name, obj in vars(kai_reports).items():
            if (not name.startswith('_') and inspect.isfunction(obj)
                    and obj.__module__ == kai_reports.__name__):
                self.assertIn(name, kai_log_view_names(), f'{name} is not covered')


class NoKaiViewLogsAConfidentialAttributeTests(TestCase):
    """
    ⚠️ THE GENERAL FORM, AND THE THING THE FIRST PASS SHOULD HAVE WRITTEN.

    A redaction at render is a statement about one line shape. This is a
    statement about the *writers*: no logging call in a Kai view module may
    interpolate an attribute that identifies a party or carries case content.

    An id is fine and is what all three fixed sites now use.
    """

    def _offenders(self):
        offenders = []
        for relative in _KAI_VIEW_MODULES:
            path = os.path.join(_REPO_ROOT, relative)
            tree = ast.parse(open(path, encoding='utf-8').read(), filename=path)
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr in {'debug', 'info', 'warning',
                                               'error', 'exception', 'critical'}
                        and isinstance(node.func.value, ast.Name)
                        and 'logger' in node.func.value.id.lower()):
                    continue
                for inner in ast.walk(node):
                    if (isinstance(inner, ast.Attribute)
                            and inner.attr in _CONFIDENTIAL_ATTRIBUTES):
                        offenders.append(
                            f'{relative}:{node.lineno} logs .{inner.attr}')
        return sorted(set(offenders))

    def test_no_kai_logging_call_interpolates_a_confidential_attribute(self):
        offenders = self._offenders()
        self.assertEqual(offenders, [], '\n  ' + '\n  '.join(offenders))

    def test_the_detector_can_actually_fire(self):
        """
        ⚠️ v3.21.7's rule: before trusting a walk, ask what would have to be
        true for it to go red. Here it is, on a fixture of the exact code that
        was removed.
        """
        source = ('def request_closure(request, report_id):\n'
                  '    logger.info(f"{user.username} requested closure for '
                  'Kai report \'{report.title}\'")\n')
        tree = ast.parse(source)
        found = []
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == 'info'):
                for inner in ast.walk(node):
                    if (isinstance(inner, ast.Attribute)
                            and inner.attr in _CONFIDENTIAL_ATTRIBUTES):
                        found.append(inner.attr)
        self.assertEqual(sorted(set(found)), ['title', 'username'])


class TheMessageRedactorTests(TestCase):

    KAI_LINE = ("User reporter_guy called submit_kai_report with arguments: (), "
                "{}, Action: No specific action")

    def setUp(self):
        ParliamentUser.objects.create_user(
            user_id='P-RED001', password=PASSWORD, name='Reporter Guy',
            username='reporter_guy', member_type='Member')
        ParliamentUser.objects.create_user(
            user_id='RED-OFF', password=PASSWORD, name='Nosy Officer',
            username='nosy_officer', member_type='Officer')

    def test_it_removes_the_actor_from_a_kai_line(self):
        out = redact_kai_log_message(self.KAI_LINE)
        self.assertNotIn('reporter_guy', out)
        self.assertIn(REDACTED, out)

    def test_it_keeps_the_view_and_the_arguments(self):
        out = redact_kai_log_message(
            "User nosy_officer called manage_kai_report with arguments: (), "
            "{'report_id': 12}, Action: No specific action")
        self.assertIn('manage_kai_report', out)
        self.assertIn('report_id', out)
        self.assertIn('12', out)

    def test_it_leaves_a_non_kai_line_completely_alone(self):
        """THE CONTROL — over-redacting is a silent feature deletion."""
        line = ("User nosy_officer called event_attendance_list with arguments: "
                "(), {}, Action: No specific action")
        self.assertEqual(redact_kai_log_message(line), line)

    def test_it_works_on_a_whole_raw_log_line_not_just_the_message(self):
        """
        ⚠️ The viewer's fallback branch hands this function the **whole line**,
        timestamp and logger name included. The first draft anchored the
        pattern at the start of the string, so that branch could never have
        fired — a guard that cannot fire.
        """
        raw = ('2026-08-24 10:54:12,370 [INFO] function_calls: '
               'User reporter_guy called submit_kai_report with arguments: ()')
        out = redact_kai_log_message(raw)
        self.assertNotIn('reporter_guy', out)
        self.assertIn('2026-08-24 10:54:12,370', out)
        self.assertIn('submit_kai_report', out)

    def test_a_line_that_is_not_of_this_shape_is_returned_unchanged(self):
        for line in ('', 'Traceback (most recent call last):',
                     'GET /events/ 200', 'User nosy_officer logged in'):
            self.assertEqual(redact_kai_log_message(line), line)

    # --- the two shapes the first pass missed -------------------------------

    def test_it_redacts_the_party_and_the_title_from_a_closure_request(self):
        """
        The historical line from `kai_user_dashboard.request_closure`. The
        username here is a party to the case.
        """
        out = redact_kai_log_message(
            "reporter_guy requested closure for Kai report "
            "'Conduct at the Feb 14 formal' (ID: 12)")
        self.assertNotIn('reporter_guy', out)
        self.assertNotIn('Feb 14 formal', out)
        self.assertIn('12', out, 'the case id names nobody and should survive')

    def test_it_redacts_the_party_and_the_title_from_a_drop_request(self):
        out = redact_kai_log_message(
            "reporter_guy requested to drop Kai report 'Something' (ID: 3)")
        self.assertNotIn('reporter_guy', out)
        self.assertNotIn('Something', out)

    def test_a_quoted_string_on_a_non_kai_line_is_untouched(self):
        """
        THE CONTROL for the quote rule — it must not fire on ordinary lines.
        """
        line = "Announcement 'Chapter meeting moved' posted by nosy_officer"
        self.assertEqual(redact_kai_log_message(line), line)

    def test_a_member_who_is_not_mentioned_costs_nothing(self):
        line = 'Kai report 7 updated'
        self.assertEqual(redact_kai_log_message(line), line)

    # --- the fourth writer, found by reading the log files ------------------

    AUDIT_LINE = ('[SUCCESS] | User: System (unknown) | Action: CREATE | '
                  'Resource: KaiReport | ID: 1 | Details: {"model": "KaiReport", '
                  '"instance_id": "1", "title": "Conduct at the Feb 14 formal"}')

    def test_it_redacts_the_title_from_the_post_save_audit_line(self):
        """
        ⚠️ THE SHAPE THAT WAS ONLY FOUND BY READING THE OUTPUT.

        `src/models/activity.py`'s sender-less `post_save` receiver fires for
        every model and attaches `instance.title`, so every save of a Kai case
        wrote its title here. Note the token is `KaiReport` — no trailing word
        boundary — which is why `_KAI_MENTION` is anchored at the start only.
        """
        out = redact_kai_log_message(self.AUDIT_LINE)
        self.assertNotIn('Feb 14 formal', out)

    def test_it_keeps_the_model_and_the_id_on_that_line(self):
        """
        THE CONTROL for the JSON rule — redacting every double-quoted run would
        take the operational content with it, and the model name and id are
        exactly what an officer diagnosing a problem needs.
        """
        out = redact_kai_log_message(self.AUDIT_LINE)
        self.assertIn('"model": "KaiReport"', out)
        self.assertIn('"instance_id": "1"', out)
        self.assertIn('Action: CREATE', out)

    def test_an_audit_line_for_a_non_kai_model_is_untouched(self):
        """THE CONTROL for the widened `_KAI_MENTION`."""
        line = ('[SUCCESS] | User: nosy_officer (X) | Action: CREATE | '
                'Resource: Announcement | ID: 4 | Details: '
                '{"model": "Announcement", "title": "Chapter meeting moved"}')
        self.assertEqual(redact_kai_log_message(line), line)


class ItFailsClosedWhenTheMemberListCannotBeReadTests(TestCase):
    """
    ⚠️ THE SYSTEM-LOG PAGE IS WHAT YOU OPEN WHEN THINGS ARE BROKEN.

    Scrubbing usernames needs a query. If that query fails, the honest options
    are to render the line unscrubbed or not at all — so a Kai line whose names
    cannot be checked is **withheld**. Same reasoning as v3.18.3's note that a
    missing Kai migration must not 500 the page you would open to diagnose it:
    degrade, do not disclose, and do not disappear entirely.
    """

    def test_a_kai_line_is_withheld_when_the_member_list_is_unknown(self):
        from src.kai_audit import UNKNOWN_MEMBERS, WITHHELD

        out = redact_kai_log_message(
            "reporter_guy requested closure for Kai report 'X' (ID: 1)",
            username_pattern=UNKNOWN_MEMBERS)
        self.assertEqual(out, WITHHELD)

    def test_the_decorator_shape_still_renders_without_a_database(self):
        """
        The common case must survive an outage: that branch redacts the actor
        by pattern and returns before anything touches the database.
        """
        from src.kai_audit import UNKNOWN_MEMBERS

        out = redact_kai_log_message(
            'User reporter_guy called submit_kai_report with arguments: ()',
            username_pattern=UNKNOWN_MEMBERS)
        self.assertNotIn('reporter_guy', out)
        self.assertIn('submit_kai_report', out)

    def test_a_non_kai_line_still_renders_when_the_member_list_is_unknown(self):
        """THE CONTROL — failing closed must not empty the whole page."""
        from src.kai_audit import UNKNOWN_MEMBERS

        line = 'User nosy_officer called event_attendance_list with arguments: ()'
        self.assertEqual(
            redact_kai_log_message(line, username_pattern=UNKNOWN_MEMBERS), line)

    def test_member_usernames_returns_none_rather_than_raising(self):
        from unittest import mock

        from src import kai_audit

        with mock.patch('src.models.ParliamentUser.objects') as objects:
            objects.values_list.side_effect = RuntimeError('database is gone')
            self.assertIsNone(kai_audit.member_usernames())


class TheAuditReceiverDoesNotRecordAKaiTitleTests(TestCase):
    """
    The source half of the fourth writer: `src/models/activity.py`'s
    `post_save`/`post_delete` receivers must not attach `title`/`name` for a
    model whose visibility is decided by an in-app permission.

    ⚠️ The set is derived from the module (`models.kai`), not typed out, so a
    Kai model added later is covered by whoever adds it.
    """

    def test_kai_models_are_recognised_as_confidential(self):
        from src.models.activity import _model_is_confidential
        from src.models.kai import KaiReport, KaiReportActivity

        self.assertTrue(_model_is_confidential(KaiReport))
        self.assertTrue(_model_is_confidential(KaiReportActivity))

    def test_ordinary_models_are_not(self):
        """THE CONTROL — a predicate that said yes to everything would empty
        the audit log of every title in the project."""
        from src.models import Announcement, Event
        from src.models.activity import _model_is_confidential

        self.assertFalse(_model_is_confidential(Announcement))
        self.assertFalse(_model_is_confidential(Event))

    def test_saving_a_kai_report_logs_the_id_but_not_the_title(self):
        """The behavioural half, through the real receiver."""
        from src.models.kai import KaiReport

        submitter = ParliamentUser.objects.create_user(
            user_id='P-AUD001', password=PASSWORD, name='Submitter',
            username='audit_submitter', member_type='Member')

        with self.assertLogs('function_calls', level='INFO') as captured:
            KaiReport.objects.create(
                title='Conduct at the Feb 14 formal',
                description='...', submitted_by=submitter)

        joined = '\n'.join(captured.output)
        self.assertIn('KaiReport', joined)
        self.assertNotIn('Feb 14 formal', joined)


class ThePageDoesNotNameAKaiReporterTests(TestCase):
    """
    The end-to-end reproduction, through the real endpoint, as the real viewer.

    ⚠️ It writes its own log file and points the view at it, rather than relying
    on whatever the ambient `django_actions.log` happens to contain — a test
    that passes because the log was empty would be the fixture proving nothing,
    which is why `test_the_control_line_still_names_its_actor` is here.
    """

    def setUp(self):
        # ⚠️ `import src.view.officer.view_logs as m` binds the *function*, not
        # the module: `src/view/officer/__init__.py` re-exports `view_logs` and
        # the attribute lookup finds the re-export first.
        view_logs_module = importlib.import_module('src.view.officer.view_logs')

        self.path = os.path.join(settings.BASE_DIR, 'logs',
                                 'test_kai_log_redaction.log')
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.handler = logging.FileHandler(self.path, mode='w')
        self.handler.setFormatter(
            logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s'))
        self.function_calls = logging.getLogger('function_calls')
        self.function_calls.addHandler(self.handler)
        self.function_calls.setLevel(logging.INFO)

        self._real_path = view_logs_module.LOG_FILE_PATH
        view_logs_module.LOG_FILE_PATH = self.path
        self._module = view_logs_module

        self.reporter = ParliamentUser.objects.create_user(
            user_id='P-KLR001', password=PASSWORD, name='Reporter Guy',
            username='reporter_guy', member_type='Member')
        self.officer = ParliamentUser.objects.create_user(
            user_id='KLR-OFF', password=PASSWORD, name='Nosy Officer',
            username='nosy_officer', member_type='Officer')

    def tearDown(self):
        self._module.LOG_FILE_PATH = self._real_path
        self.function_calls.removeHandler(self.handler)
        self.handler.close()
        try:
            os.remove(self.path)
        except OSError:                                     # pragma: no cover
            pass

    def _file_and_page(self):
        self.handler.flush()
        with open(self.path, encoding='utf-8') as fh:
            written = fh.read()
        viewer = Client()
        viewer.force_login(self.officer)
        response = viewer.get(reverse('view_logs'))
        self.assertEqual(response.status_code, 200)
        return written, response.content.decode()

    def test_the_reporter_is_not_named_on_the_page(self):
        reporter = Client()
        reporter.force_login(self.reporter)
        self.assertEqual(reporter.get(reverse('submit_kai_report')).status_code, 200)

        written, page = self._file_and_page()

        # The fixture is real: the leak IS in the file.
        self.assertIn('submit_kai_report', written)
        self.assertIn('reporter_guy', written)

        # And it does not reach the page.
        self.assertIn('submit_kai_report', page)
        self.assertNotIn('reporter_guy', page)

    def test_a_historical_party_line_is_not_rendered_either(self):
        """
        The shape the first pass missed, as it exists in the log files already
        on the server.
        """
        self.function_calls.info(
            "reporter_guy requested closure for Kai report "
            "'Conduct at the Feb 14 formal' (ID: 12)")
        _written, page = self._file_and_page()
        self.assertNotIn('reporter_guy', page)
        self.assertNotIn('Feb 14 formal', page)

    def test_the_control_line_still_names_its_actor(self):
        """
        ⚠️ WITHOUT THIS, THE TESTS ABOVE PASS ON AN EMPTY PAGE. A redaction
        that removed every username would satisfy them perfectly.
        """
        self.function_calls.info(
            'User nosy_officer called event_attendance_list with arguments: (), {}')
        _written, page = self._file_and_page()
        self.assertIn('event_attendance_list', page)
        self.assertIn('nosy_officer', page)

    def test_a_plain_officer_can_still_read_the_page(self):
        """The page is not being taken away; only one column of it."""
        self.function_calls.info('User someone called home with arguments: (), {}')
        _written, page = self._file_and_page()
        self.assertIn('home', page)

    def test_an_advisor_cannot_read_the_page_at_all(self):
        """
        ⚠️ v3.25.2 NARROWED THE GATE, and this is why.

        The page had a **wider** audience than the pages whose actions it
        records: `officer_or_advisor_required` admits advisors, while
        `review_excuses` — the page `serve_excuse_document` serves — is
        `@officer_required` and deliberately does not. So an advisor could not
        open a member's excuse document but could read
        `User <member> called serve_excuse_document` in the log tail, learning
        that a named member has a medical excuse on file.

        **A raw application log inherits the audience of its narrowest line,
        not its widest reader.**
        """
        advisor = ParliamentUser.objects.create_user(
            user_id='KLR-ADV', password=PASSWORD, name='An Advisor',
            username='an_advisor', member_type='Advisor')
        viewer = Client()
        viewer.force_login(advisor)
        self.assertEqual(viewer.get(reverse('view_logs')).status_code, 403)

    def test_a_chair_can_still_read_the_page(self):
        """
        THE CONTROL for the narrowing — `officer_required` admits officers,
        chairs and admins, and this must not have quietly become admin-only.
        """
        chair = ParliamentUser.objects.create_user(
            user_id='KLR-CHR', password=PASSWORD, name='A Chair',
            username='a_chair', member_type='Chair')
        viewer = Client()
        viewer.force_login(chair)
        self.assertEqual(viewer.get(reverse('view_logs')).status_code, 200)
