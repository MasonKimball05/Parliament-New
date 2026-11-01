"""
Privacy policy, terms of use and support pages (v4.4.0): public, no sign-in, because the App
Store listing links to both (its Privacy Policy URL and Support URL).

The policy describes what the code actually stores. When that changes (a new
field, a new outside service, a different retention), update
templates/legal/privacy.html and bump EFFECTIVE_DATE.
"""
from datetime import date
from urllib.parse import urlparse

from django.conf import settings
from django.shortcuts import render

EFFECTIVE_DATE = date(2026, 10, 31)


def privacy_policy(request):
    return render(request, 'legal/privacy.html', {
        'effective_date': EFFECTIVE_DATE,
        'site_host': urlparse(settings.SITE_URL).hostname,
    })


def support(request):
    return render(request, 'legal/support.html')


def terms(request):
    return render(request, 'legal/terms.html', {'effective_date': EFFECTIVE_DATE})
