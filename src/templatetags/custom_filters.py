import re
from django import template
from django.utils.safestring import mark_safe

register = template.Library()

@register.filter
def get_item(dictionary, key):
    if dictionary is None:
        return None
    return dictionary.get(key)

@register.filter
def dict_get(dictionary, key):
    """Alias for get_item - get a value from a dictionary by key."""
    if dictionary is None:
        return None
    return dictionary.get(key)

@register.filter
def render_description(value):
    """Convert markdown bold and line breaks to HTML"""
    if not value:
        return value
    from django.utils.html import escape
    # First escape any HTML to prevent XSS
    result = escape(str(value))
    # Convert **text** to <strong>text</strong>
    result = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', result)
    # Also handle <strong> that was already in the text (now escaped)
    result = result.replace('&lt;strong&gt;', '<strong>')
    result = result.replace('&lt;/strong&gt;', '</strong>')
    # Convert newlines to <br>
    result = result.replace('\n', '<br>')
    return mark_safe(result)

@register.filter
def split(value, arg):
    """Split a string by the given separator"""
    return value.split(arg)

@register.filter
def filter_by_user(queryset, user):
    """Filter a queryset by user"""
    return queryset.filter(user=user)


@register.filter
def dict_get_tier(prefs, position_id):
    """
    Get the preference tier for a position from the tiered preferences dict.
    Returns 'first_choice', 'second_choice', 'third_choice', 'do_not_want', or ''.
    """
    if not prefs or prefs == '':
        return ''

    # Handle legacy list format
    if isinstance(prefs, list):
        return 'first_choice' if position_id in prefs else ''

    # Must be a dict at this point
    if not isinstance(prefs, dict):
        return ''

    # Handle tiered dict format
    for tier in ['first_choice', 'second_choice', 'third_choice', 'do_not_want']:
        tier_list = prefs.get(tier, [])
        if tier_list and position_id in tier_list:
            return tier

    return ''


@register.filter
def format_phone(value):
    """Format a phone number as XXX-XXX-XXXX.

    Handles various input formats:
    - 1234567890 -> 123-456-7890
    - 123-456-7890 -> 123-456-7890 (unchanged)
    - (123) 456-7890 -> 123-456-7890
    - +1 123 456 7890 -> 123-456-7890
    """
    if not value:
        return value

    # Remove all non-digit characters
    digits = re.sub(r'\D', '', str(value))

    # Remove leading 1 if it's an 11-digit number (US country code)
    if len(digits) == 11 and digits.startswith('1'):
        digits = digits[1:]

    # Format as XXX-XXX-XXXX if we have 10 digits
    if len(digits) == 10:
        return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"

    # Return original if not a standard 10-digit number
    return value


@register.filter(needs_autoescape=True)
def linkify(value, autoescape=True):
    """Convert URLs in plain text to clickable links that open in new tabs.

    Safe against XSS: escapes the text first, then wraps detected URLs in <a> tags.
    Handles URLs with http://, https://, www., and bare domain URLs.
    """
    if not value:
        return value
    from django.utils.html import escape

    text = str(value)

    # Comprehensive URL regex pattern that catches:
    # - http:// and https:// URLs
    # - www. URLs (without protocol)
    # - Common domain URLs like example.com/path
    url_pattern = re.compile(
        r'('
        # URLs with protocol
        r'https?://[^\s<>\[\]()"\',;]+(?<![.,;:!?\)\]\'\"])'
        r'|'
        # URLs starting with www.
        r'www\.[^\s<>\[\]()"\',;]+(?<![.,;:!?\)\]\'\"])'
        r')',
        re.IGNORECASE
    )

    # Find all URLs in the original text
    urls = []
    for match in url_pattern.finditer(text):
        urls.append((match.start(), match.end(), match.group(0)))

    if not urls:
        # No URLs found, just escape and return
        return mark_safe(escape(text) if autoescape else text)

    # Build the result by escaping non-URL parts and wrapping URLs in links
    result = []
    last_end = 0

    for start, end, url in urls:
        # Escape text before this URL
        before_text = text[last_end:start]
        if autoescape:
            before_text = escape(before_text)
        result.append(before_text)

        # Create the link - add protocol if missing
        href = url
        if not url.lower().startswith(('http://', 'https://')):
            href = 'https://' + url

        # Escape the display URL for safety
        display_url = escape(url) if autoescape else url

        # Create the anchor tag
        link = (
            f'<a href="{escape(href)}" target="_blank" rel="noopener noreferrer" '
            f'class="text-blue-600 dark:text-blue-400 underline hover:text-blue-800 dark:hover:text-blue-300">'
            f'{display_url}</a>'
        )
        result.append(link)

        last_end = end

    # Escape remaining text after the last URL
    remaining_text = text[last_end:]
    if autoescape:
        remaining_text = escape(remaining_text)
    result.append(remaining_text)

    return mark_safe(''.join(result))
