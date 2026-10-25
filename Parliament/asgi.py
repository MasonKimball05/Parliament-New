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
from src.middleware.two_factor import SecondFactorWebsocketMiddleware

application = ProtocolTypeRouter({
    'http': django_asgi_app,
    # v3.51.9: SecondFactorWebsocketMiddleware sits inside AuthMiddlewareStack
    # (it needs the resolved user) and treats a session that hasn't passed the
    # 2FA verify step as anonymous. Enforce2FAMiddleware is HTTP-only, so
    # before this a password-only session could open every socket.
    'websocket': AuthMiddlewareStack(
        SecondFactorWebsocketMiddleware(
            URLRouter(websocket_urlpatterns)
        )
    ),
})
