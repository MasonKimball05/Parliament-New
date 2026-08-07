from django.shortcuts import redirect
from django.contrib.auth import logout
from ..decorators import log_function_call
from src.view.two_factor import clear_remember_cookie


@log_function_call
def logout_view(request):
    # v3.18.8: the manual ActivityLog write that used to sit here is gone —
    # every logout was producing TWO rows. `logout()` below fires Django's
    # `user_logged_out` signal, and `middleware/activity_logging.py:37` already
    # logs it from there.
    #
    # The signal is the right one of the two to keep, on both counts:
    #   * it categorised correctly. This one passed `action_type='other'`, so
    #     half of every logout landed under "Other / Other Action" instead of
    #     "Authentication / User Logout" — visible in the 08-06 export as two
    #     rows one second apart, distinguishable only by a trailing full stop.
    #   * it covers every path. This view is one way to log out; forced logouts
    #     from the session viewer and quarantine enforcement go through
    #     `logout()` without passing through here, and the signal catches those
    #     too. A per-view writer only ever logs the exits someone remembered.
    logout(request)
    response = redirect('login')
    clear_remember_cookie(response)
    return response