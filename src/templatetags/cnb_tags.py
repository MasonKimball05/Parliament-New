"""C&B template tags (09-25-26)."""
from django import template

from src.cnb_crossrefs import link_references

register = template.Library()


@register.simple_tag
def cnb_linked(text, doc_type, structure=None):
    """Section text, escaped, with cross-references linked to their section anchors.
    References to sections that do not exist (see src/cnb_crossrefs.py) stay plain text."""
    return link_references(text, doc_type, structure)
