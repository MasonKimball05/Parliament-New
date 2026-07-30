"""
Smoke + N+1 sweep for the routes that take arguments.

WHY THIS EXISTS
---------------
`src/test_url_smoke.py` covers routes that reverse with **no** arguments. That
left the larger half untested: of 1,216 named routes, **684 take arguments** —
358 of those are Django's own admin (skipped), leaving ~326 app routes. Detail
pages, per-committee pages, per-report pages, the whole slating flow. None of it
was exercised, which is where a bad `defer()` name, a dead `{% url %}` with the
wrong arity, or a per-row query is most likely to hide.

HOW IT WORKS
------------
`FIXTURE` builds one real object of each kind, then `ARG_VALUES` maps a URL
parameter *name* to a value for it. A route is requested only if every one of
its parameters has a mapping; anything else is skipped and counted, so the
coverage number is honest rather than implied.

**A 404 is not a failure.** Some parameter names are genuinely ambiguous — `pk`
means a different model depending on the route, and `field_id` could be a
slating, Kai or service form field — so a route may legitimately be handed an id
of the wrong type. The assertion is on **5xx and raised exceptions**, which no
argument value can excuse: a template referencing a nonexistent URL name, a
`FieldError` from a bad `defer()`, or an unguarded attribute error will 500
whatever id you pass it.

WHAT IT DOES NOT COVER
----------------------
Only GET, only as an admin. POST-only routes report 405 and are skipped. A
permission branch that only a pledge or a non-member sees is not reached.
"""

from collections import Counter
import re

from django.test import Client, TestCase
from django.urls import NoReverseMatch, get_resolver, reverse

#: Routes we deliberately never request. Each entry needs a reason.
SKIP_ROUTES = {
    # Auth flows that consume a signed token; feeding them a fake one exercises
    # the rejection path, which their own tests already cover.
    'two_factor_recovery_confirm',
    'password_reset_confirm',
    'confirm_email_change',
    # Anonymous bearer-token feed — covered by test_pledge_permissions.
    'calendar_subscription_feed',
}

#: Parameter names we have no sensible value for, so routes using them are
#: skipped rather than guessed at.
UNMAPPED_OK = {'uidb64', 'token', 'path'}

LITERAL = re.compile(r"('[^']*'|\b\d+\b)")


class DetailRouteSmokeTests(TestCase):
    """Every argument-taking page an admin can reach must not 5xx."""

    @classmethod
    def setUpTestData(cls):
        from datetime import timedelta

        from django.utils import timezone

        from src.models import (Announcement, APIToken, Article, BugReport,
                               ChapterMinutes, ChatChannel, ChatMessage,
                               Committee, CommitteeDocument, CommitteeMinutes,
                               Event, GoverningDocument, GuideTour, KaiFormField,
                               KaiReport, KaiReportTemplate, Legislation,
                               Notification, NotificationSchedule,
                               ParliamentUser, PledgeTask, RecruitmentCandidate,
                               RecruitmentEvent, Resolution,
                               PassedResolution,
                               ResolutionSectionImpact, Role, Section, Slate,
                               SlatingApplication, SlatingInterview,
                               SlatingPeriod, SlatingPosition)

        now = timezone.now()

        cls.admin = ParliamentUser.objects.create(
            user_id='dr-admin', name='Detail Admin', username='dradmin',
            member_type='Officer', member_status='Active', is_admin=True,
        )
        cls.admin.set_password('detail-pass-12345!')
        cls.admin.save()

        cls.role = Role.objects.create(name='Detail VP', code='drvp')
        cls.admin.roles.add(cls.role)

        cls.committee = Committee.objects.create(
            name='Detail Committee', code='drc', is_active=True, role=cls.role)
        cls.committee.members.add(cls.admin)
        cls.committee.chairs.add(cls.admin)

        cls.legislation = Legislation.objects.create(
            title='Detail Bill', description='d', posted_by=cls.admin,
            available_at=now - timedelta(days=1), voting_ended_at=now,
            voting_closed=True, status='passed', passed=True,
            vote_mode='percentage', required_percentage='50')

        cls.event = Event.objects.create(
            title='Detail Event', description='d', date_time=now + timedelta(days=1),
            created_by=cls.admin, is_active=True)

        cls.announcement = Announcement.objects.create(
            title='Detail Announcement', content='c', posted_by=cls.admin,
            is_active=True)

        cls.document = CommitteeDocument.objects.create(
            committee=cls.committee, title='Detail Doc', uploaded_by=cls.admin,
            document='detail.pdf')

        cls.chapter_minutes = ChapterMinutes.objects.create(
            title='Detail Minutes', date=now.date(), start_time=now.time(),
            created_by=cls.admin)
        cls.committee_minutes = CommitteeMinutes.objects.create(
            committee=cls.committee, title='CM', date=now.date(),
            posted_by=cls.admin)

        cls.governing_doc = GoverningDocument.objects.create(
            doc_type='constitution', title='Constitution')
        cls.article = Article.objects.create(
            document=cls.governing_doc, number='I', title='Article I')
        cls.section = Section.objects.create(
            article=cls.article, number='1', content='text')
        cls.resolution = Resolution.objects.create(title='Detail Resolution')
        # ResolutionSectionImpact points at PassedResolution (the landing-page
        # model), not the C&B builder's Resolution — different models, similar
        # names.
        cls.passed_resolution = PassedResolution.objects.create(
            title='Passed Res', description='d', date_passed=now.date())
        cls.impact = ResolutionSectionImpact.objects.create(
            resolution=cls.passed_resolution, section_name='I.1')

        cls.channel = ChatChannel.objects.create(
            name='Detail Channel', access_type='open', is_active=True,
            channel_type='committee', committee=cls.committee)
        cls.message = ChatMessage.objects.create(
            channel=cls.channel, sender=cls.admin, message='hi')

        cls.task = PledgeTask.objects.create(title='Detail Task', is_active=True)

        cls.kai_report = KaiReport.objects.create(
            title='Detail Report', description='d', submitted_by=cls.admin)
        cls.kai_template = KaiReportTemplate.objects.create(
            name='T', description='d', title_template='t',
            description_template='d')
        cls.kai_field = KaiFormField.objects.create(
            field_name='f', label='F', field_type='text')

        cls.recruit_event = RecruitmentEvent.objects.create(
            event=cls.event, committee=cls.committee)
        cls.candidate = RecruitmentCandidate.objects.create(
            committee=cls.committee, name='Rush Candidate')

        cls.period = SlatingPeriod.objects.create(
            name='Detail Period', academic_term='Fall 2026')
        cls.position = SlatingPosition.objects.create(
            period=cls.period, title='President', code='pres')
        cls.application = SlatingApplication.objects.create(
            period=cls.period, applicant=cls.admin)
        cls.interview = SlatingInterview.objects.create(
            application=cls.application)
        cls.slate = Slate.objects.create(period=cls.period)

        cls.token = APIToken.objects.create(
            user=cls.admin, key='detail-key', name='Detail Token')
        cls.bug = BugReport.objects.create(description='d')
        cls.notification = Notification.objects.create(
            recipient=cls.admin, notification_type='general', title='n')
        cls.schedule = NotificationSchedule.objects.create(
            name='S', notification_type='general', message_template='m')
        cls.tour = GuideTour.objects.create(
            name='Tour', slug='detail-tour', description='d')

    def _arg_values(self):
        """URL parameter name -> a real value. See the module docstring on `pk`."""
        return {
            'code': self.committee.code,
            'committee_id': self.committee.pk,
            'user_id': self.admin.pk,
            'legislation_id': self.legislation.pk,
            'event_id': self.event.pk,
            'announcement_id': self.announcement.pk,
            'document_id': self.document.pk,
            'minutes_id': self.chapter_minutes.pk,
            'resolution_id': self.resolution.pk,
            'article_id': self.article.pk,
            'section_id': self.section.pk,
            'impact_id': self.impact.pk,
            'channel_id': self.channel.pk,
            'message_id': self.message.pk,
            'task_pk': self.task.pk,
            'report_id': self.kai_report.pk,
            'template_id': self.kai_template.pk,
            'field_id': self.kai_field.pk,
            'role_id': self.role.pk,
            'period_id': self.period.pk,
            'position_id': self.position.pk,
            'app_id': self.application.pk,
            'interview_id': self.interview.pk,
            'slate_id': self.slate.pk,
            'candidate_id': self.candidate.pk,
            'recruitment_event_id': self.recruit_event.pk,
            'token_id': self.token.pk,
            'bug_id': self.bug.pk,
            'notification_id': self.notification.pk,
            'schedule_id': self.schedule.pk,
            'tour_slug': self.tour.slug,
            'doc_type': self.governing_doc.doc_type,
            'log_id': 1,
            'submission_id': 1,
            'service_event_id': 1,
            'pk': self.legislation.pk,
            'object_id': self.legislation.pk,
        }

    def _routes_with_args(self):
        routes = []

        def walk(resolver, prefix=''):
            for key, value in resolver.reverse_dict.items():
                if not isinstance(key, str):
                    continue
                for _pattern, params in value[0]:
                    if params:
                        routes.append((prefix + key, tuple(params)))
                    break
            for namespace, (_p, sub) in getattr(resolver, 'namespace_dict', {}).items():
                walk(sub, f'{prefix}{namespace}:')

        walk(get_resolver())
        return sorted(set(routes))

    def _sweep(self):
        """GET every reversible argument-taking route; return per-route results."""
        from django.core.cache import cache
        from django.db import connection

        from src.models import IPBlacklist
        from django.test.utils import CaptureQueriesContext

        values = self._arg_values()
        client = Client()
        client.force_login(self.admin)

        results = []
        for name, params in self._routes_with_args():
            if name in SKIP_ROUTES or name.startswith('admin:'):
                continue
            if any(p in UNMAPPED_OK for p in params):
                results.append((name, None, 'skipped: unmapped parameter', []))
                continue
            if not all(p in values for p in params):
                missing = [p for p in params if p not in values]
                results.append((name, None, f'skipped: no value for {missing}', []))
                continue
            try:
                url = reverse(name, kwargs={p: values[p] for p in params})
            except NoReverseMatch as exc:
                results.append((name, None, f'NoReverseMatch: {exc}', []))
                continue
            if url.startswith('/admin/'):
                continue

            # Clear between pages: 300+ requests in one test otherwise trips the
            # app's own rate limiting, and every page after that returns 403 and
            # is silently skipped. Learned the hard way in test_url_smoke.
            cache.clear()
            # v3.17.4: the security middleware auto-blacklists an IP into the
            # DATABASE after enough suspicious requests, and a sweep of 300 URLs
            # from one client trips it. Clearing the cache is not enough — the
            # block is a row — so after ~132 pages every later page returned 403
            # and was silently skipped by the guard below. That is how the
            # `manage_announcements` N+1 (5 queries per row) hid from this test.
            # Drop any block the sweep created about itself.
            IPBlacklist.objects.all().delete()
            try:
                with CaptureQueriesContext(connection) as ctx:
                    response = client.get(url)
            except Exception as exc:               # noqa: BLE001 — the point
                results.append((name, None, f'RAISED {type(exc).__name__}: {exc}',
                                []))
                continue
            results.append((name, response.status_code, url, ctx.captured_queries))
        return results

    def test_no_detail_page_5xxs_or_raises(self):
        results = self._sweep()

        failures = [
            f'{name} ({info})' for name, status, info, _q in results
            if status is None and (info.startswith('RAISED')
                                  or info.startswith('NoReverseMatch'))
        ] + [
            f'{name} ({info}) -> {status}' for name, status, info, _q in results
            if status is not None and status >= 500
        ]
        requested = sum(1 for _n, s, _i, _q in results if s is not None)
        rendered = sum(1 for _n, s, _i, _q in results if s == 200)

        # Floors, not exact numbers. At the time of writing: 319 routes
        # considered, 284 requested, 83 rendering a full page (the rest are
        # POST-only 405s, redirects, or 404s from an ambiguous parameter name
        # getting an id of the wrong model — see the module docstring). If either
        # floor stops being met, the sweep has quietly shrunk and is hiding
        # things rather than reporting them.
        self.assertGreater(requested, 200,
                           f'only {requested} detail routes were requested')
        self.assertGreater(rendered, 60,
                           f'only {rendered} detail routes rendered a page')
        self.assertEqual(failures, [], 'detail pages that error for an admin')

    def test_no_detail_page_repeats_a_query_shape(self):
        results = self._sweep()
        offenders = []
        for name, status, url, queries in results:
            if status != 200:
                continue
            shapes = Counter(LITERAL.sub('?', q['sql']) for q in queries)
            shape, count = shapes.most_common(1)[0] if shapes else ('', 0)
            if count >= 4:
                table = re.search(r'FROM "(\w+)"', shape)
                offenders.append(
                    f'{name} ({url}): {count}× '
                    f'{table.group(1) if table else shape[:60]}')
        self.assertEqual(offenders, [], 'detail pages with a repeated query shape')
