from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.core.management import call_command
from io import StringIO
from src.decorators import officer_required

@login_required
@officer_required
def db_dump_view(request):
    out = StringIO()
    call_command('dump_db', stdout=out)
    output = out.getvalue()
    return HttpResponse(f"<pre>{output}</pre>")
