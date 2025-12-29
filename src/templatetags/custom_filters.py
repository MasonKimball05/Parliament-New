from django import template
register = template.Library()

@register.filter
def get_item(dictionary, key):
    return dictionary.get(key)

@register.filter
def split(value, arg):
    """Split a string by the given separator"""
    return value.split(arg)

@register.filter
def filter_by_user(queryset, user):
    """Filter a queryset by user"""
    return queryset.filter(user=user)
