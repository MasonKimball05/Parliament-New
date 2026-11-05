"""Slating template filters (11-05-26)."""
from django import template

register = template.Library()


@register.filter
def ballot_fingerprint(rows):
    """``{{ candidates|ballot_fingerprint }}`` — see src/view/slating/api.py ``ballot_fingerprint``."""
    from src.view.slating.api import ballot_fingerprint as fingerprint
    return fingerprint(rows or [])
