from django.urls import re_path
from src import consumers

websocket_urlpatterns = [
    re_path(r'ws/chat/(?P<channel_id>\d+)/$', consumers.ChatConsumer.as_asgi()),
    re_path(r'ws/votes/$', consumers.VoteConsumer.as_asgi()),  # v3.14.0
]
