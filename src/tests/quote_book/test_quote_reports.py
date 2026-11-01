"""
v4.4.0 — any member can report a quote (src/quote_reports.py). Unlike the
involved-party flag, a report doesn't hide the quote: officers are notified
and see it in the flagged queue; AUTO_HIDE_THRESHOLD distinct reporters hide it.
Web views and the app's endpoints (this module is the app's urlconf until the
routes are in src/mobile/urls.py).

    python manage.py test src.tests.quote_book.test_quote_reports
"""
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import include, path, reverse

from src import quote_reports
from src.chat_safety import AUTO_HIDE_THRESHOLD
from src.mobile import views_songbook as v
from src.models import ActivityLog, Notification, Quote, QuoteReport
from src.tests.mobile.sess_helpers import bearer, make_device, make_user

API = '/api/mobile/v1/'

_routes = [
    path('quote-book/', v.QuoteBookView.as_view(), name='mobile_quote_book'),
    path('quote-book/flagged/', v.QuoteFlaggedView.as_view(), name='mobile_quote_flagged'),
    path('quote-book/<int:quote_id>/flag/', v.QuoteFlagView.as_view(), name='mobile_quote_flag'),
    path('quote-book/<int:quote_id>/report/', v.QuoteReportView.as_view(), name='mobile_quote_report'),
    path('quote-book/<int:quote_id>/reports/review/', v.QuoteReportReviewView.as_view(),
         name='mobile_quote_report_review'),
]


def _urlpatterns():
    from src import urls as root
    return [path('api/mobile/v1/', include((_routes, 'mobile_qr')))] + root.urlpatterns


urlpatterns = _urlpatterns()


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'], ROOT_URLCONF=__name__)
class _Base(TestCase):

    def setUp(self):
        cache.clear()
        self.subject = make_user('qr-subject')
        self.author = make_user('qr-author')
        self.officer = make_user('qr-officer', member_type='Officer')
        self.readers = [make_user(f'qr-reader{i}') for i in range(AUTO_HIDE_THRESHOLD)]
        self.quote = Quote.objects.create(quoted_member=self.subject, text='a funny line',
                                          submitted_by=self.author)

    def web(self, user):
        c = Client()
        c.force_login(user)
        return c

    def report(self, user, reason='harassment', details=''):
        return self.web(user).post(reverse('report_quote', args=[self.quote.id]),
                                   {'reason': reason, 'details': details})


class WebQuoteReportTests(_Base):

    def test_any_member_reports_quote_stays_and_officers_notified(self):
        reader = self.readers[0]
        r = self.report(reader, details='mean')
        self.assertEqual(r.status_code, 200, r.content)
        self.quote.refresh_from_db()
        self.assertIsNone(self.quote.flagged_at)
        note = Notification.objects.get(notification_type='quote_report')
        self.assertEqual(note.recipient, self.officer)
        self.assertNotIn(reader.name, note.title + note.message)
        for user in (self.subject, self.author, reader):
            self.assertFalse(Notification.objects.filter(recipient=user).exists())
        # Officers see it, with the reporter, in the queue; the subject doesn't see who.
        page = self.web(self.officer).get(reverse('review_flagged_quotes'))
        self.assertContains(page, 'a funny line')
        self.assertContains(page, reader.name)
        self.assertContains(page, 'mean')
        self.assertNotContains(self.web(self.subject).get(reverse('review_flagged_quotes')), reader.name)
        # The book still shows the quote, and the officer's queue link counts it.
        book = self.web(self.officer).get(reverse('quote_book'))
        self.assertEqual(book.context['flagged_count'], 1)

    def test_duplicate_own_and_bad_reason_refused(self):
        self.report(self.readers[0])
        r = self.report(self.readers[0])
        self.assertEqual((r.status_code, r.json()['error']), (409, 'already_reported'))
        self.assertEqual(self.report(self.author).json()['error'], 'own_quote')
        self.assertEqual(self.report(self.readers[1], reason='meh').json()['error'], 'invalid_input')
        self.assertEqual(QuoteReport.objects.count(), 1)
        self.assertEqual(self.web(self.readers[1]).get(reverse('report_quote', args=[self.quote.id])).status_code, 405)

    def test_threshold_auto_hides_without_naming_anyone(self):
        for reader in self.readers:
            self.report(reader)
        self.quote.refresh_from_db()
        self.assertIsNotNone(self.quote.flagged_at)
        self.assertIsNone(self.quote.flagged_by)
        log = ActivityLog.objects.filter(object_type='Quote', object_id=self.quote.pk).get()
        self.assertIsNone(log.user)
        page = self.web(self.subject).get(reverse('review_flagged_quotes'))
        self.assertContains(page, 'Hidden after member reports')
        for reader in self.readers:
            self.assertNotContains(page, reader.name)

    def test_officer_dismiss_restores_auto_hidden_and_hide_flags(self):
        for reader in self.readers:
            self.report(reader)
        r = self.web(self.officer).post(reverse('review_quote_reports', args=[self.quote.id]), {'action': 'dismiss'})
        self.assertEqual(r.status_code, 302)
        self.quote.refresh_from_db()
        self.assertIsNone(self.quote.flagged_at)
        self.assertEqual(set(QuoteReport.objects.values_list('status', flat=True)), {'dismissed'})

        other = Quote.objects.create(quoted_member=self.subject, text='another', submitted_by=self.author)
        quote_reports.report_quote(other, self.readers[0], 'spam')
        self.web(self.officer).post(reverse('review_quote_reports', args=[other.id]), {'action': 'hide'})
        other.refresh_from_db()
        self.assertEqual(other.flagged_by, self.officer)
        self.assertEqual(QuoteReport.objects.get(quote=other).status, 'actioned')

    def test_non_officer_cannot_review(self):
        self.report(self.readers[0])
        for user in (self.subject, self.readers[1]):
            r = self.web(user).post(reverse('review_quote_reports', args=[self.quote.id]), {'action': 'hide'})
            self.assertEqual(r.status_code, 403)
        self.quote.refresh_from_db()
        self.assertIsNone(self.quote.flagged_at)

    def test_involved_party_flag_is_unchanged(self):
        r = self.web(self.subject).post(reverse('flag_quote', args=[self.quote.id]))
        self.assertTrue(r.json()['success'])
        self.quote.refresh_from_db()
        self.assertEqual(self.quote.flagged_by, self.subject)
        self.assertFalse(QuoteReport.objects.exists())

    def test_officer_restore_dismisses_reports(self):
        for reader in self.readers:
            self.report(reader)
        self.web(self.officer).post(reverse('restore_quote', args=[self.quote.id]))
        self.assertEqual(set(QuoteReport.objects.values_list('status', flat=True)), {'dismissed'})


class AppQuoteReportTests(_Base):

    def setUp(self):
        super().setUp()
        self.api = Client()
        self.tokens = {u.pk: make_device(u)[1] for u in [self.officer, self.subject, *self.readers]}

    def call(self, user, route, body=None, method='post'):
        kw = bearer(self.tokens[user.pk])
        if method == 'get':
            return self.api.get(API + route, **kw)
        return self.api.post(API + route, body or {}, content_type='application/json', **kw)

    def test_report_review_and_flags(self):
        book = self.call(self.readers[0], 'quote-book/', method='get').json()
        q = book['chapters'][0]['quotes'][0]
        self.assertEqual((q['can_flag'], q['can_report']), (False, True))
        r = self.call(self.readers[0], f'quote-book/{self.quote.id}/report/', {'reason': 'hate', 'details': 'x'})
        self.assertEqual(r.status_code, 201, r.content)
        r = self.call(self.readers[0], f'quote-book/{self.quote.id}/report/', {'reason': 'hate'})
        self.assertEqual((r.status_code, r.json()['error']), (409, 'already_reported'))
        r = self.call(self.readers[1], f'quote-book/{self.quote.id}/report/', {'reason': 'nope'})
        self.assertEqual((r.status_code, r.json()['error']), (400, 'invalid_input'))

        flagged = self.call(self.officer, 'quote-book/flagged/', method='get').json()['quotes']
        self.assertEqual(len(flagged), 1)
        self.assertEqual((flagged[0]['flagged_at'], flagged[0]['can_review_reports']), (None, True))
        self.assertEqual(flagged[0]['reports'][0]['reporter'], self.readers[0].get_display_name())
        # The quoted member's view never lists reports or reporters.
        mine = self.call(self.subject, 'quote-book/flagged/', method='get')
        self.assertNotIn(self.readers[0].get_display_name().encode(), mine.content)

        r = self.call(self.subject, f'quote-book/{self.quote.id}/reports/review/', {'action': 'hide'})
        self.assertEqual((r.status_code, r.json()['error']), (403, 'forbidden'))
        r = self.call(self.officer, f'quote-book/{self.quote.id}/reports/review/', {'action': 'hide'})
        self.assertEqual((r.status_code, r.json()['hidden']), (200, True))
