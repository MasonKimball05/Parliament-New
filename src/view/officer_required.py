from django.http import HttpResponseForbidden

def officer_required(view_func):
    def wrapper(request, *args, **kwargs):
        if request.user.member_type != 'Officer':
            return HttpResponseForbidden("You do not have access to this page.")
        return view_func(request, *args, **kwargs)
    return wrapper
