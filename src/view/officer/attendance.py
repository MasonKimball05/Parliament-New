from src.models import *
from django.utils import timezone
from src.decorators import *
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.contrib import messages

@login_required
@officer_required
@log_function_call
def attendance(request):
    # Filter committees: admins see all, chairs only see their committees
    if request.user.is_admin:
        committees = Committee.objects.all().order_by('name')
    else:
        # Show only committees where the user is a chair
        committees = Committee.objects.filter(chairs=request.user).order_by('name')

    # Only show Members, Chairs, and Officers (exclude Advisors and Pledges from attendance)
    users = ParliamentUser.objects.filter(member_type__in=['Member', 'Chair', 'Officer']).order_by('user_id')

    committee_id = request.GET.get("committee_id")
    selected_committee = None
    if committee_id:
        try:
            selected_committee = Committee.objects.get(id=committee_id)

            # Verify user has permission to take attendance for this committee
            if not request.user.is_admin and not selected_committee.is_chair(request.user):
                messages.error(request, 'You do not have permission to take attendance for this committee.')
                return redirect('attendance')

            # Filter users to members of the selected committee (excluding Advisors and Pledges)
            users = selected_committee.members.filter(member_type__in=['Member', 'Chair', 'Officer']).order_by('user_id')
        except Committee.DoesNotExist:
            selected_committee = None

    if request.method == 'POST':
        # Verify permission for POST as well (in case someone tries to submit via form manipulation)
        if selected_committee and not request.user.is_admin and not selected_committee.is_chair(request.user):
            messages.error(request, 'You do not have permission to take attendance for this committee.')
            return redirect('attendance')

        present_ids = request.POST.getlist('present')
        now = timezone.now()

        logger = logging.getLogger('function_calls')

        # Determine which users to update based on selected committee (excluding Advisors and Pledges)
        if selected_committee:
            users_to_update = selected_committee.members.filter(member_type__in=['Member', 'Chair', 'Officer'])
        else:
            users_to_update = ParliamentUser.objects.filter(member_type__in=['Member', 'Chair', 'Officer'])

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
        return redirect('officer_home')

    context = {
        'users': users,
        'committees': committees,
        'selected_committee': selected_committee,
        'today': timezone.now().date()
    }
    return render(request, 'attendance.html', context)