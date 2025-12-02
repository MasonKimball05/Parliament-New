from django.shortcuts import redirect
from django.contrib.auth import logout
from ..decorators import log_function_call

@log_function_call
def logout_view(request):
    logout(request)

    return redirect('login')