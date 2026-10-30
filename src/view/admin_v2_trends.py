"""
Admin v2: trends (v4.1.0).

Three weekly counts over the last 12 weeks, one chart each (one series, one
hue, so no legend; the title names it):
* members who signed in (distinct members with a successful sign-in),
* new app sign-ins (MobileDevice rows created),
* failed sign-ins.
Weeks start on Monday in the chapter's time zone; empty weeks are 0, not gaps.
"""
from datetime import timedelta

from django.db.models import Count
from django.db.models.functions import TruncWeek
from django.shortcuts import render
from django.utils import timezone

from src.models import LoginHistory
from src.models.mobile import MobileDevice
from src.view.admin_v2 import require_admin_v2_auth

WEEKS = 12


def week_starts(today=None, weeks=WEEKS):
    today = today or timezone.localdate()
    monday = today - timedelta(days=today.weekday())
    return [monday - timedelta(weeks=n) for n in range(weeks - 1, -1, -1)]


def weekly(qs, field, starts, distinct=None):
    """{week start date: count} for every week in `starts` (missing weeks are 0)."""
    tz = timezone.get_current_timezone()
    first = timezone.make_aware(timezone.datetime.combine(starts[0], timezone.datetime.min.time()), tz)
    count = Count(distinct, distinct=True) if distinct else Count('pk')
    rows = (qs.filter(**{f'{field}__gte': first})
            .annotate(week=TruncWeek(field, tzinfo=tz)).values('week').annotate(n=count))
    found = {r['week'].date() if hasattr(r['week'], 'date') else r['week']: r['n'] for r in rows}
    return [found.get(s, 0) for s in starts]


def trend_series():
    starts = week_starts()
    labels = [s.strftime('%b %-d') for s in starts]
    series = [
        {'id': 'signins', 'title': 'Members who signed in', 'unit': 'members',
         'values': weekly(LoginHistory.objects.filter(status='success'), 'timestamp', starts, distinct='user')},
        {'id': 'app', 'title': 'New app sign-ins', 'unit': 'installs',
         'values': weekly(MobileDevice.objects.all(), 'created_at', starts)},
        {'id': 'failed', 'title': 'Failed sign-ins', 'unit': 'attempts',
         'values': weekly(LoginHistory.objects.filter(status='failed'), 'timestamp', starts)},
    ]
    for s in series:
        s['total'] = sum(s['values'])
        s['latest'] = s['values'][-1]
    return {'labels': labels, 'series': series}


@require_admin_v2_auth
def admin_v2_trends(request):
    data = trend_series()
    return render(request, 'admin_v2/trends.html', {'data': data, 'rows': list(zip(
        data['labels'], *[s['values'] for s in data['series']]))})
