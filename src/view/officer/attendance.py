from src.models import *
from django.utils import timezone
from src.decorators import *
from django.contrib.auth.decorators import login_required, user_passes_test
from django.shortcuts import render, redirect
from django.contrib import messages

@login_required
@user_passes_test(lambda u: u.member_type in ['Chair', 'Officer'])
@log_function_call
def attendance(request):
    committees = Committee.objects.all().order_by('name')
    users = ParliamentUser.objects.all().order_by('user_id')

    committee_id = request.GET.get("committee_id")
    selected_committee = None
    if committee_id:
        try:
            selected_committee = Committee.objects.get(id=committee_id)
            # Filter users to members of the selected committee
            users = selected_committee.members.all().order_by('user_id')
        except Committee.DoesNotExist:
            selected_committee = None

    if request.method == 'POST':
        present_ids = request.POST.getlist('present')
        now = timezone.now()

        logger = logging.getLogger('function_calls')

        # Determine which users to update based on selected committee
        users_to_update = selected_committee.members.all() if selected_committee else ParliamentUser.objects.all()

        for user in users_to_update:
            is_present = str(user.user_id) in present_ids

            Attendance.objects.update_or_create(
                user=user,
                date=now.date(),
                defaults={
                    'present': is_present,
                    'created_at': now,
                }
            )

            action = "present" if is_present else "absent"
            logger.info(f"{request.user.username} marked {user.username} as {action} on {now.date()}")

        messages.success(request, "Attendance has been updated.")
        return redirect('home')

    context = {
        'users': users,
        'committees': committees,
        'selected_committee': selected_committee
    }
    return render(request, 'attendance.html', context)