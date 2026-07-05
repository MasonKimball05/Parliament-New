"""
ASGI config for Parliament project.

Handles both HTTP (Django) and WebSocket (Channels) traffic.
HTTP requests are served by Django's standard ASGI application.
WebSocket connections at /ws/chat/<channel_id>/ are routed to ChatConsumer.
"""

import os

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'Parliament.settings')

# Must be called before any model imports so apps are ready
from django.core.asgi import get_asgi_application
django_asgi_app = get_asgi_application()

from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack
from src.routing import websocket_urlpatterns

application = ProtocolTypeRouter({
    'http': django_asgi_app,
    'websocket': AuthMiddlewareStack(
        URLRouter(websocket_urlpatterns)
    ),
})
