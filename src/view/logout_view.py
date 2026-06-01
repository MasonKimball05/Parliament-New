from django.shortcuts import redirect
from django.contrib.auth import logout
from ..decorators import log_function_call
from src.view.two_factor import clear_remember_cookie


@log_function_call
def logout_view(request):
    from src.models import ActivityLog
    if request.user.is_authenticated:
        ActivityLog.log_activity(
            action_type='other',
            user=request.user,
            description=f'{request.user.name} logged out.',
            request=request,
        )
    logout(request)
    response = redirect('login')
    clear_remember_cookie(response)
    return response