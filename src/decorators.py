import logging

# Set up the logger to capture function call logs
logger = logging.getLogger('function_calls')

def log_function_call(func):
    def wrapper(request, *args, **kwargs):
        user = request.user  # Get the logged-in user
        function_name = func.__name__  # Get the function name
        action = kwargs.get('action', 'No specific action')  # Optional: You can specify an action in kwargs for more clarity

        # Log the function call details
        logger.info(f"User {user.username} called {function_name} with arguments: {args}, {kwargs}, Action: {action}")

        return func(request, *args, **kwargs)
    return wrapper
