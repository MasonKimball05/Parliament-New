from django.contrib.auth.decorators import login_required
from src.models import *
from django.shortcuts import render
from src.decorators import officer_or_advisor_required

@login_required
@officer_or_advisor_required
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