from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import HttpResponse
from django.core.management import call_command
from io import StringIO

@login_required
@user_passes_test(lambda u: u.member_type in ['Chair', 'Officer'])
def db_dump_view(request):
    out = StringIO()
    call_command('dump_db', stdout=out)
    output = out.getvalue()
    return HttpResponse(f"<pre>{output}</pre>")
