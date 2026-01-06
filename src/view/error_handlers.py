"""
Custom error handlers for 404 and 500 pages
"""
from django.shortcuts import render


def custom_404(request, exception=None):
    """
    Custom 404 page - Page Not Found
    """
    return render(request, '404.html', status=404)


def custom_500(request):
    """
    Custom 500 page - Server Error
    """
    return render(request, '500.html', status=500)
