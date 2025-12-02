from django.http import HttpResponse
from io import StringIO
from django.core.management import call_command

def db_dump_view(request):
    out = StringIO()
    call_command('dump_db', stdout=out)
    output = out.getvalue()
    return HttpResponse(f"&lt;pre&gt;{output}&lt;/pre&gt;")