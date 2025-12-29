"""
Context processors for Parliament system
Makes data available globally across all templates
"""
from src.notifications import get_unread_announcements


def unread_announcements(request):
    """
    Add unread announcements to template context
    """
    if request.user.is_authenticated:
        announcements = get_unread_announcements(request.user)
        return {
            'unread_announcements': announcements,
            'unread_count': len(announcements)
        }
    return {
        'unread_announcements': [],
        'unread_count': 0
    }
