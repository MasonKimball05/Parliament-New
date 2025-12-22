from django.contrib.auth.decorators import login_required, user_passes_test
from src.models import *
from django.shortcuts import render

@login_required
@user_passes_test(lambda u: u.member_type in ['Chair', 'Officer'])
def user_list(request):
    users = ParliamentUser.objects.all()

    user_data = []
    for user in users:
        user_data.append({
            'username': user.name,
            'id': user.user_id,
            'role': user.member_type
        })

    return render(request, 'user_list.html', {'user_data': user_data})