"""
Push notification subscription management.

Endpoints:
  GET  /service-worker.js  — serve the SW with Service-Worker-Allowed: / header
  POST /push/subscribe/    — save a new PushSubscription for the logged-in user
  POST /push/unsubscribe/  — delete the subscription matching the given endpoint
"""

import json
import logging
import os

from django.conf import settings as django_settings
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_POST

from src.models import PushSubscription

logger = logging.getLogger('src')


def service_worker(request):
    """
    Serve the service worker JS from the root path so the browser grants it
    scope over the entire site.

    A SW at /static/js/service-worker.js can only control pages under
    /static/js/ — useless for push. Serving it here at /service-worker.js
    with `Service-Worker-Allowed: /` gives it full-site scope.
    """
    sw_path = os.path.join(django_settings.BASE_DIR, 'static', 'js', 'service-worker.js')
    try:
        with open(sw_path, 'r') as f:
            content = f.read()
    except FileNotFoundError:
        return HttpResponse('// service-worker.js not found', content_type='application/javascript', status=404)

    response = HttpResponse(content, content_type='application/javascript')
    response['Service-Worker-Allowed'] = '/'
    response['Cache-Control'] = 'no-cache'
    return response


@login_required
@require_POST
@csrf_protect
def push_subscribe(request):
    """
    Body (JSON):
      { endpoint, keys: { p256dh, auth } }

    Creates or updates the subscription row for this endpoint.
    """
    try:
        data = json.loads(request.body)
        endpoint = data.get('endpoint', '').strip()
        keys = data.get('keys', {})
        p256dh = keys.get('p256dh', '').strip()
        auth = keys.get('auth', '').strip()

        if not endpoint or not p256dh or not auth:
            return JsonResponse({'error': 'Missing required subscription fields'}, status=400)

        user_agent = request.META.get('HTTP_USER_AGENT', '')[:300]

        PushSubscription.objects.update_or_create(
            endpoint=endpoint,
            defaults={
                'user': request.user,
                'p256dh': p256dh,
                'auth': auth,
                'user_agent': user_agent,
            },
        )
        return JsonResponse({'status': 'subscribed'})

    except (json.JSONDecodeError, KeyError) as exc:
        logger.warning(f'[push] subscribe parse error: {exc}')
        return JsonResponse({'error': 'Invalid request body'}, status=400)
    except Exception as exc:
        logger.error(f'[push] subscribe error: {exc}')
        return JsonResponse({'error': 'Server error'}, status=500)


@login_required
@require_POST
@csrf_protect
def push_unsubscribe(request):
    """
    Body (JSON):
      { endpoint }

    Deletes the matching subscription. Safe to call even if it doesn't exist.
    """
    try:
        data = json.loads(request.body)
        endpoint = data.get('endpoint', '').strip()

        if not endpoint:
            return JsonResponse({'error': 'Missing endpoint'}, status=400)

        deleted, _ = PushSubscription.objects.filter(
            user=request.user, endpoint=endpoint
        ).delete()

        return JsonResponse({'status': 'unsubscribed', 'deleted': deleted})

    except (json.JSONDecodeError, KeyError) as exc:
        logger.warning(f'[push] unsubscribe parse error: {exc}')
        return JsonResponse({'error': 'Invalid request body'}, status=400)
    except Exception as exc:
        logger.error(f'[push] unsubscribe error: {exc}')
        return JsonResponse({'error': 'Server error'}, status=500)
