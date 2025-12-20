"""
Enhanced logging utilities for Parliament application
Provides detailed, structured logging for all user actions and system events
"""
import logging
import json
from datetime import datetime
from functools import wraps
from django.http import HttpRequest

# Configure loggers
action_logger = logging.getLogger('function_calls')
security_logger = logging.getLogger('security')
admin_logger = logging.getLogger('admin_actions')


class LogContext:
    """Context manager for structured logging with consistent formatting"""

    @staticmethod
    def format_log_entry(user, action, resource_type=None, resource_id=None,
                        details=None, status='success', ip_address=None):
        """
        Create a structured log entry with consistent formatting

        Args:
            user: User object or username string
            action: Action being performed (e.g., 'CREATE', 'UPDATE', 'DELETE', 'VIEW')
            resource_type: Type of resource (e.g., 'Legislation', 'Committee', 'Document')
            resource_id: ID of the resource being acted upon
            details: Dictionary of additional details
            status: Status of the action ('success', 'failure', 'unauthorized')
            ip_address: IP address of the request

        Returns:
            Formatted log string
        """
        username = getattr(user, 'username', str(user))
        user_id = getattr(user, 'user_id', 'unknown')

        log_parts = [
            f"[{status.upper()}]",
            f"User: {username} ({user_id})",
            f"Action: {action}"
        ]

        if resource_type:
            log_parts.append(f"Resource: {resource_type}")

        if resource_id is not None:
            log_parts.append(f"ID: {resource_id}")

        if ip_address:
            log_parts.append(f"IP: {ip_address}")

        if details:
            # Format details as JSON for easy parsing
            details_str = json.dumps(details, default=str)
            log_parts.append(f"Details: {details_str}")

        return " | ".join(log_parts)


def get_client_ip(request):
    """Extract client IP address from request"""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0]
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip


def log_document_action(action, document, user, committee=None, details=None):
    """
    Specialized logging for document operations

    Args:
        action: Action performed ('UPLOAD', 'DOWNLOAD', 'PUBLISH', 'UNPUBLISH', 'DELETE')
        document: Document object or title
        user: User performing the action
        committee: Committee associated with the document
        details: Additional details
    """
    logger = logging.getLogger('function_calls')

    doc_details = details or {}
    doc_details['document_title'] = getattr(document, 'title', str(document))

    if committee:
        doc_details['committee'] = str(committee)

    if hasattr(document, 'published_to_chapter'):
        doc_details['published_to_chapter'] = document.published_to_chapter

    log_entry = LogContext.format_log_entry(
        user=user,
        action=f"DOCUMENT_{action}",
        resource_type='CommitteeDocument',
        resource_id=getattr(document, 'id', None),
        details=doc_details,
        status='success'
    )
    logger.info(log_entry)


def log_security_event(event_type, user, details=None, ip_address=None, severity='INFO'):
    """
    Log security-related events

    Args:
        event_type: Type of security event (e.g., 'LOGIN_ATTEMPT', 'UNAUTHORIZED_ACCESS')
        user: User involved in the event
        details: Additional details
        ip_address: IP address
        severity: Log level ('INFO', 'WARNING', 'ERROR', 'CRITICAL')
    """
    logger = logging.getLogger('security')

    log_entry = LogContext.format_log_entry(
        user=user,
        action=event_type,
        details=details,
        status=severity.lower(),
        ip_address=ip_address
    )

    # Log at appropriate level
    log_method = getattr(logger, severity.lower(), logger.info)
    log_method(log_entry)
