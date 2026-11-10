"""
11-09-26: the record-interview page ticked each recommended position with
`position in interview.recommended_positions.all`, which re-ran the M2M query
once per checkbox (7 identical queries with seven positions, flagged by the
dev-mode N+1 detector). The view now passes the ids once.
"""
import re

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from src.models import SlatingInterview, SlatingPosition
from src.tests.slating.test_slating_review_fixes import _Base


class CompleteInterviewQueryTests(_Base):

    def page(self, interview):
        self.client.force_login(self.committee_member)
        with CaptureQueriesContext(connection) as ctx:
            r = self.client.get(reverse('slating_complete_interview', args=[interview.id]))
        self.assertEqual(r.status_code, 200)
        m2m = [q for q in ctx.captured_queries if 'recommended_positions' in q['sql']]
        return r.content.decode(), len(m2m)

    def checked(self, html):
        return set(int(v) for v in re.findall(
            r'name="recommended_positions" value="(\d+)"\s*checked', html))

    def test_ticks_the_recommended_positions_with_one_query(self):
        extra = [SlatingPosition.objects.create(period=self.period, title=f'Pos {i}', code=f'X{i}')
                 for i in range(5)]
        interview = SlatingInterview.objects.create(application=self.app)
        interview.recommended_positions.set([self.vp, extra[2]])
        html, queries = self.page(interview)
        self.assertEqual(self.checked(html), {self.vp.id, extra[2].id})
        self.assertEqual(queries, 1)

    def test_none_recommended(self):
        interview = SlatingInterview.objects.create(application=self.app)
        html, queries = self.page(interview)
        self.assertEqual(self.checked(html), set())
        self.assertLessEqual(queries, 1)
