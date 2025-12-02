from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.http import HttpResponseForbidden
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm
from .models import *
from django.contrib import messages
from django.contrib.messages import get_messages
from django.views.decorators.http import require_http_methods
from django.utils import timezone
from .forms import *
from django.db.models import Count, Q
from datetime import timedelta
from django.utils.timezone import make_aware
from django.utils.dateparse import parse_datetime
from .decorators import log_function_call
from django.views.generic import TemplateView, DetailView
from django.urls import reverse
import os
from django.conf import settings
import pytz
from io import StringIO
from django.core.management import call_command
from django.http import HttpResponse
LOG_FILE_PATH = os.path.join(settings.BASE_DIR, 'logs', 'django_actions.log')

class HomeView(TemplateView):
    template_name = 'home.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Fetch the last two legislations with votes
        recent_legislation = Legislation.objects.filter(voting_closed=True).order_by('-available_at')[:2]
        recent_votes = []
        for leg in recent_legislation:
            yes_votes = Vote.objects.filter(legislation=leg, vote_choice='yes').count()
            no_votes = Vote.objects.filter(legislation=leg, vote_choice='no').count()
            abstain_votes = Vote.objects.filter(legislation=leg, vote_choice='abstain').count()
            recent_votes.append({
                'title': leg.title,
                'yes': yes_votes,
                'no': no_votes,
                'abstain': abstain_votes,
                'id': leg.id
            })
        context['recent_votes'] = recent_votes
        return context

def legislation_detail(request, legislation_id):
    legislation = get_object_or_404(Legislation, id=legislation_id)
    votes = Vote.objects.filter(legislation=legislation)

    if legislation.vote_mode == 'plurality':
        vote_result = {
            'mode': 'plurality',
            'options': {option: votes.filter(vote_choice=option).count() for option in legislation.plurality_options},
            'total': votes.count()
        }
    else:
        yes_votes = votes.filter(vote_choice='yes').count()
        no_votes = votes.filter(vote_choice='no').count()
        abstain_votes = votes.filter(vote_choice='abstain').count()
        total = votes.count()
        yes_pct = (yes_votes / total * 100) if total > 0 else 0
        vote_result = {
            'mode': 'percentage',
            'yes': yes_votes,
            'no': no_votes,
            'abstain': abstain_votes,
            'yes_percentage': "{:.0f}%".format(yes_pct),
            'required_percentage': legislation.required_percentage,
            'total': total
        }

    return render(request, 'src/legislation_detail.html', {
        'legislation': legislation,
        'vote_result': vote_result
    })


class PassedLegislationDetailView(DetailView):
    model = Legislation
    template_name = 'src/legislation_detail.html'
    context_object_name = 'legislation'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        legislation = self.object
        votes = Vote.objects.filter(legislation=legislation)
        total_votes = votes.count()

        if legislation.vote_mode == 'plurality':
            vote_counts = {option: votes.filter(vote_choice=option).count() for option in legislation.plurality_options}
            winner = max(vote_counts, key=vote_counts.get) if vote_counts else None
            context['vote_result'] = {
                'mode': 'plurality',
                'options': vote_counts,
                'winner': winner,
                'total': total_votes
            }
        else:
            yes_votes = votes.filter(vote_choice='yes').count()
            no_votes = votes.filter(vote_choice='no').count()
            abstain_votes = votes.filter(vote_choice='abstain').count()
            yes_pct = (yes_votes / total_votes * 100) if total_votes > 0 else 0
            context['vote_result'] = {
                'mode': 'percentage',
                'yes': yes_votes,
                'no': no_votes,
                'abstain': abstain_votes,
                'yes_percentage': "{:.0f}%".format(yes_pct),
                'required_percentage': legislation.required_percentage,
                'total': total_votes
            }

        return context

@log_function_call
def login_view(request):
    list(get_messages(request))  # Clear flash messages

    if request.method == 'POST':
        user_id = request.POST.get('user_id')
        username = request.POST.get('username')

        if not user_id or not username:
            messages.error(request, "Both user ID and username are required.")
            return redirect('login')

        try:
            user = ParliamentUser.objects.get(user_id=user_id)

            # Check against the username, not the name
            if user.username == username:
                login(request, user)

                logger = logging.getLogger('function_calls')
                logger.info(f"{user.name} ({user.member_type}) (user_id={user.user_id}), logged in.")

                messages.success(request, f"Welcome, {user.name}!")

                next_url = request.GET.get('next', 'home')

                print(f"User {user} ({user.user_id}) logged in, redirecting to {next_url}")

                return redirect(next_url)
            else:
                messages.error(request, "Invalid username.")
                return redirect('login')

        except ParliamentUser.DoesNotExist:
            messages.error(request, "Invalid user ID.")
            return redirect('login')

    return render(request, 'registration/login.html')


def custom_login(request):
    if request.method == 'POST':
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            return redirect('/')  # Redirect to home page after successful login
    else:
        form = AuthenticationForm()

    return render(request, 'registration/login.html', {'form': form})

def officer_required(view_func):
    def wrapper(request, *args, **kwargs):
        if request.user.member_type != 'Officer':
            return HttpResponseForbidden("You do not have access to this page.")
        return view_func(request, *args, **kwargs)
    return wrapper

@login_required
@officer_required
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

@log_function_call
def logout_view(request):
    logout(request)

    return redirect('login')

# Home page view
@login_required
@log_function_call
def home(request):
    # Get the last two passed legislations
    print(f"🔐 User: {request.user} | Authenticated: {request.user.is_authenticated}")
    logger.info(f"User: {request.user} | Authenticated: {request.user.is_authenticated} | IP: {request.META.get('REMOTE_ADDR')} | Page accessed: home")
    recently_passed_legislation = Legislation.objects.annotate(
        total_votes=Count('vote'),
        yes_votes=Count('vote', filter=Q(vote__vote_choice='yes'))
    ).filter(
        voting_closed=True,
        status='passed'
    ).order_by('-available_at')[:2]  # Change field name as per your model

    # Preparing data to display
    legislation_previews = [
        {
            'title': leg.title,
            'yes_percentage': "{:.0%}".format(leg.yes_votes / leg.total_votes) if leg.total_votes > 0 else "0%",
            'detail_url': reverse('passed_legislation_detail', kwargs={'pk': leg.pk})
        } for leg in recently_passed_legislation
    ]

    context = {
        'user': request.user,
        'legislation_previews': legislation_previews,
    }

    return render(request, 'home.html', context)


@login_required
@log_function_call
def profile_view(request):
    user = request.user

    username_form_submitted = 'username_submit' in request.POST
    password_form_submitted = 'password_submit' in request.POST

    password_form = PasswordChangeForm(user)
    username = user.username

    if request.method == 'POST':
        if username_form_submitted:
            new_username = request.POST.get('username')
            if new_username and new_username != user.username:
                # Logs new changes
                logger.info(f"{user.username} changed username to {new_username}")

                user.username = new_username
                user.save()
                messages.success(request, "Username updated successfully.")
                return redirect('profile')

        elif password_form_submitted:
            password_form = PasswordChangeForm(user, request.POST)
            if password_form.is_valid():
                # Logs new changes
                logger.info(f"{request.user.username} changed their password")

                user = password_form.save()
                update_session_auth_hash(request, user)
                messages.success(request, "Password changed successfully.")
                return redirect('profile')
            else:
                messages.error(request, "Please correct the errors below.")

    return render(request, 'profile.html', {
        'user': user,
        'password_form': password_form,
        'username': username
    })

@staff_member_required
@log_function_call
def login_as_user(request, user_id):
    user = get_object_or_404(ParliamentUser, user_id=user_id)
    login(request, user, backend='django.contrib.auth.backends.ModelBackend')

    # Optional: Add a message or log this action
    messages.info(request, f"You are now impersonating {user.username}")
    logger = logging.getLogger('function_calls')
    logger.info(f"{request.user.username} is impersonating {user.username}")

    return redirect('home')

@staff_member_required
def login_as_view(request, user_id):
    user = get_object_or_404(ParliamentUser, pk=user_id)
    login(request, user)
    return redirect('home')

@require_http_methods(["GET", "POST"])
@login_required
@log_function_call
def vote_view(request):
    user = request.user

    # Handle legislation upload
    if user.member_type in ['Chair', 'Officer'] and request.method == 'POST' and 'title' in request.POST:
        title = request.POST.get('title')
        description = request.POST.get('description')
        document = request.FILES.get('document')
        anonymous = request.POST.get('anonymous') == 'on'
        allow_abstain = not (request.POST.get('remove_abstain') == 'on')
        required_percentage = int(request.POST.get('required_percentage', 51))

        raw_available_at = request.POST.get('available_at')
        parsed_available_at = parse_datetime(raw_available_at)
        available_at = make_aware(parsed_available_at) if parsed_available_at else None

        vote_mode = request.POST.get('vote_mode', 'percentage')
        plurality_options = []

        if vote_mode == 'plurality':
            for i in range(1, 6):
                val = request.POST.get(f'plurality_option_{i}')
                if val:
                    plurality_options.append(val.strip())

            if len(plurality_options) < 2:
                messages.error(request, "Plurality voting requires at least two options.")
                return redirect('vote')

        if title and description and available_at and (document or vote_mode == 'plurality'):
            Legislation.objects.create(
                title=title,
                description=description,
                document=document if vote_mode != 'plurality' else None,
                posted_by=user,
                available_at=available_at,
                anonymous_vote=anonymous,
                allow_abstain=allow_abstain,
                required_percentage=required_percentage,
                vote_mode=vote_mode,
                plurality_options=plurality_options if vote_mode == 'plurality' else None
            )

            logger = logging.getLogger('function_calls')
            logger.info(f"{user.username} uploaded legislation titled '{title}' (mode: {vote_mode}, required %: {required_percentage})")

            messages.success(request, "Legislation uploaded successfully.")
            return redirect('vote')

    # Determine if user is present and allowed to vote
    three_hours_ago = timezone.now() - timedelta(hours=3)
    attendance = Attendance.objects.filter(
        user=user,
        created_at__gte=three_hours_ago,
        present=True
    ).order_by('-created_at').first()
    can_vote = bool(attendance)

    # Handle voting
    if request.method == 'POST' and 'vote_choice' in request.POST and can_vote:
        password = request.POST.get('password')
        auth_user = authenticate(request, username=user.username, password=password)

        if auth_user:
            legislation_id = request.POST.get('legislation_id')
            legislation = get_object_or_404(Legislation, id=legislation_id)

            if Vote.objects.filter(user=user, legislation=legislation).exists():
                messages.error(request, "You have already voted on this legislation.")
                return redirect('vote')
            if legislation.voting_closed:
                messages.error(request, "Voting on this legislation has ended.")
                return redirect('vote')

            vote_choice = request.POST.get('vote_choice')
            if legislation.vote_mode == 'plurality' and vote_choice not in legislation.plurality_options:
                messages.error(request, "Invalid vote option.")
                return redirect('vote')

            Vote.objects.create(user=user, legislation=legislation, vote_choice=vote_choice)

            logger = logging.getLogger('function_calls')
            logger.info(f"{user.username} voted '{vote_choice}' on '{legislation.title}' (ID: {legislation.id}) at {timezone.now()}")

            messages.success(request, "Your vote has been submitted.")
            return redirect('vote')
        else:
            messages.error(request, "Incorrect password.")
            return redirect('vote')

    # Gather available legislation
    available_legislation = Legislation.objects.filter(
        available_at__lte=timezone.now(),
        voting_closed=False
    )

    # Build vote data for uploader
    vote_data = {}
    for leg in available_legislation:
        if leg.posted_by == user:
            votes = Vote.objects.filter(legislation=leg)
            if leg.vote_mode == 'plurality':
                tally = {opt: votes.filter(vote_choice=opt).count() for opt in leg.plurality_options}
                tally['total'] = votes.count()
                vote_data[leg.id] = tally
            else:
                vote_data[leg.id] = {
                    'yes': votes.filter(vote_choice='yes').count(),
                    'no': votes.filter(vote_choice='no').count(),
                    'abstain': votes.filter(vote_choice='abstain').count(),
                    'total': votes.count()
                }

    return render(request, 'vote.html', {
        'profile': user,
        'can_vote': can_vote,
        'legislation': available_legislation,
        'vote_data': vote_data,
        'default_vote_mode': 'percentage',
    })


def is_chair_or_officer(user):
    return user.member_type in ['Chair', 'Officer']


@login_required
@user_passes_test(lambda u: u.member_type in ['Chair', 'Officer'])
@log_function_call
def upload_legislation(request):
    if request.user.member_type not in ['Chair', 'Officer']:
        return redirect('home')

    if request.method == 'POST':
        form = LegislationForm(request.POST, request.FILES)
        if form.is_valid():
            legislation = form.save(commit=False)
            legislation.posted_by = request.user
            legislation.save()
            return redirect('vote')
        else:
            messages.error(request, "There was an error with your submission.")
    else:
        form = LegislationForm()

    return render(request, 'vote.html', {'form': form})

@login_required
@log_function_call
def change_password(request):
    if request.method == 'POST':
        form = PasswordChangeForm(user=request.user, data=request.POST)
        if form.is_valid():
            form.save()
            update_session_auth_hash(request, form.user)  # Keeps the user logged in
            messages.success(request, "Password changed successfully.")
            return redirect('profile')
        else:
            messages.error(request, "Please correct the error below.")
    else:
        form = PasswordChangeForm(user=request.user)

    return render(request, 'change_password.html', {'form': form})


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


@login_required
@log_function_call
def end_vote(request, legislation_id):
    legislation = get_object_or_404(Legislation, id=legislation_id)

    if request.user != legislation.posted_by:
        return HttpResponseForbidden("Only the uploader can end the vote.")

    # Close voting
    legislation.voting_closed = True
    legislation.save()

    # Gather votes
    votes = Vote.objects.filter(legislation=legislation)
    vote_summary = votes.values('vote_choice').annotate(count=Count('id'))

    # Count totals
    yes_votes = votes.filter(vote_choice='yes').count()
    no_votes = votes.filter(vote_choice='no').count()
    abstain_votes = votes.filter(vote_choice='abstain').count()
    total_votes = votes.exclude(vote_choice='abstain').count()

    if legislation.vote_mode == 'plurality':
        vote_breakdown_dict = {str(option): votes.filter(vote_choice=option).count() for option in legislation.plurality_options}
        winner = max(vote_breakdown_dict, key=vote_breakdown_dict.get) if vote_breakdown_dict else None
        vote_breakdown = {'keys': list(vote_breakdown_dict.keys()), 'values': list(vote_breakdown_dict.values())}
    else:
        vote_breakdown = {
            'yes': yes_votes,
            'no': no_votes,
            'abstain': abstain_votes,
        }
        winner = None

    vote_passed = False
    required_pct = None
    yes_percentage = None
    if legislation.vote_mode == 'percentage':
        required_pct = int(legislation.required_percentage or 51)
        yes_percentage = (yes_votes / total_votes) * 100 if total_votes > 0 else 0
        vote_passed = yes_percentage >= required_pct
    elif legislation.vote_mode == 'piecewise':
        required_number = legislation.required_number or 0
        vote_passed = yes_votes >= required_number
    elif legislation.vote_mode == 'plurality':
        plurality_counts = {
            option: votes.filter(vote_choice=option).count()
            for option in legislation.plurality_options
        }
        most_voted = max(plurality_counts, key=plurality_counts.get, default=None)
        vote_passed = True if most_voted else False
        winner = most_voted

    # Update status based on vote outcome
    if vote_passed:
        legislation.status = 'passed'
    else:
        legislation.status = 'removed'
    legislation.save()

    context = {
        'legislation': legislation,
        'summary': vote_summary,
        'anonymous': legislation.anonymous_vote,
        'remove_abstain': not legislation.allow_abstain,
        'in_favor': votes.filter(vote_choice='yes'),
        'against': votes.filter(vote_choice='no'),
        'abstain': votes.filter(vote_choice='abstain'),
        'passed': vote_passed,
        'total_votes': total_votes,
        'yes_votes': yes_votes,
        'yes_percentage': f"{yes_percentage:.0f}%" if yes_percentage is not None else "N/A",
        'required_percentage': required_pct if required_pct is not None else 'N/A',
        'vote_breakdown': vote_breakdown,
        'winner': winner,
    }

    #legislation.set_passed()

    if legislation.vote_mode == 'plurality':
        context['plurality_results'] = {
            'results': [
                {
                    'option': option,
                    'count': vote_breakdown.get(option, 0),
                    'voters': [v.user.name for v in votes.filter(vote_choice=option).select_related('user')]
                }
                for option in legislation.plurality_options
            ]
        }

    return render(request, 'vote_result.html', context)

@login_required
@user_passes_test(lambda u: u.member_type in ['Chair', 'Officer'])
@log_function_call
def view_legislation_history(request):
    user = request.user

    # Fetch all legislation submitted by the logged-in user (both past and present)
    user_legislation = Legislation.objects.filter(posted_by=user).order_by('-available_at')

    legislation_history = []

    for leg in user_legislation:
        votes = Vote.objects.filter(legislation=leg)
        yes_votes = votes.filter(vote_choice='yes').count()
        no_votes = votes.filter(vote_choice='no').count()
        abstain_votes = votes.filter(vote_choice='abstain').count()
        total_votes = votes.count()

        # Calculate the yes percentage
        yes_percentage = (yes_votes / total_votes) * 100 if total_votes > 0 else 0

        # Define the passing threshold (default to 51% if not specified)
        passed = leg.set_passed()

        is_legislation_active = leg.is_available() and not leg.voting_closed

        # Adding legislation history with voting results for closed ones
        legislation_history.append({
            'legislation': leg,
            'yes_votes': yes_votes,
            'no_votes': no_votes,
            'abstain_votes': abstain_votes,
            'total_votes': total_votes,
            'yes_pct': round(yes_percentage, 2),
            'no_pct': round((no_votes / total_votes) * 100, 2) if total_votes > 0 else 0,
            'abstain_pct': round((abstain_votes / total_votes) * 100, 2) if total_votes > 0 else 0,
            'is_active': is_legislation_active,
            'voting_closed': leg.voting_closed,
            'available_at': leg.available_at,
            'voting_ended_at': leg.voting_ended_at,  # assuming you store the end time
            'anonymous_vote': leg.anonymous_vote,
            'allow_abstain': leg.allow_abstain,
            'description': leg.description,
            'title': leg.title,
            'document_url': leg.document.url if leg.document else None,
            'legislation_id': leg.id,
            'passed': passed,
        })

    return render(request, 'legislation_history.html', {'legislation_history': legislation_history})


@login_required
@user_passes_test(lambda u: u.member_type in ['Chair', 'Officer'])
@log_function_call
def reopen_legislation(request, legislation_id):
    legislation = get_object_or_404(Legislation, id=legislation_id)

    # Prevent reopening if already passed
    if legislation.status == 'passed':
        messages.error(request, "This legislation has already passed and cannot be reopened.")
        return redirect('view_legislation_history')  # Redirect to the history page after the message

    if request.user != legislation.posted_by:
        return HttpResponseForbidden("Only the uploader can reopen this legislation.")

    legislation.voting_closed = False  # Reopen the voting
    legislation.save()

    messages.success(request, "Legislation has been reopened.")
    return redirect('view_legislation_history')


@login_required
@user_passes_test(lambda u: u.member_type in ['Chair', 'Officer'])
@log_function_call
def edit_legislation(request, legislation_id):
    legislation = get_object_or_404(Legislation, id=legislation_id)

    if request.user != legislation.posted_by:
        return HttpResponseForbidden("Only the uploader can edit this legislation.")

    # Check if the legislation has passed. If it has, redirect to submit a new version.
    if legislation.status == 'passed':
        messages.error(request, "This legislation has passed and cannot be edited. Please submit a new version.")
        return redirect('submit_new_version', legislation_id=legislation.id)

    if request.method == 'POST':
        form = LegislationForm(request.POST, request.FILES, instance=legislation)
        if form.is_valid():
            form.save()
            messages.success(request, "Legislation has been updated.")
            return redirect('view_legislation_history')  # Redirect back to the history page
        else:
            messages.error(request, "Please correct the error below.")

    return render(request, 'edit_legislation.html', {'form': LegislationForm(instance=legislation)})


@login_required
@log_function_call
def passed_legislation(request):
    closed_legislation = Legislation.objects.filter(voting_closed=True)
    passed = []
    passed_legs = Legislation.objects.filter(passed=True)
    print("Passed Legislation:", passed_legs)

    for leg in closed_legislation:
        votes = Vote.objects.filter(legislation=leg)
        yes = votes.filter(vote_choice='yes').count()
        no = votes.filter(vote_choice='no').count()
        abstain = votes.filter(vote_choice='abstain').count()
        total_non_abstain = yes + no

        if total_non_abstain == 0:
            continue

        vote_passed = False
        if leg.vote_mode == 'peacewise':
            vote_passed = yes >= leg.required_yes_votes
        else:
            yes_pct = (yes / total_non_abstain) * 100
            required_pct = int(leg.required_percentage)
            vote_passed = yes_pct >= required_pct

        vote_breakdown = {
            'yes': yes,
            'no': no,
            'abstain': abstain
        }
        if leg.vote_mode == 'plurality':
            winner = max(vote_breakdown, key=vote_breakdown.get)
        else:
            winner = None


        # Determine time range for attendance window
        local_tz = pytz.timezone("America/Chicago")
        vote_end = leg.voting_ended_at or leg.available_at
        vote_start = vote_end - timedelta(hours=3)

        # Convert to local time and back to UTC to simulate attendance in UTC-6 window
        vote_start_local = vote_start.astimezone(local_tz)
        vote_end_local = vote_end.astimezone(local_tz)

        vote_start_utc = vote_start_local.astimezone(pytz.UTC)
        vote_end_utc = vote_end_local.astimezone(pytz.UTC)


        # Only get the latest attendance record per user in the window
        present_members = Attendance.objects.filter(
            present=True,
            created_at__range=(vote_start_utc, vote_end_utc)
        ).order_by('user_id', '-created_at').distinct('user_id').select_related('user')

        passed.append({
            'legislation': leg,
            'yes': yes,
            'no': no,
            'abstain': abstain,
            'yes_pct': round(yes_pct, 2),
            'no_pct': round((no / total_non_abstain) * 100, 2),
            'required_pct': required_pct,
            'required_yes_votes': getattr(leg, 'required_yes_votes', None),
            'vote_mode': leg.vote_mode,
            'vote_passed': vote_passed,
            'present_members': present_members,
            'document_url': leg.document.url if leg.document else None,
            'vote_breakdown': vote_breakdown,
            'winner': winner,
        })

        logger.info(f"{leg.title} present members: {[a.user.name for a in present_members]}")

        print("Present members for:", leg.title)
        for pm in present_members:
            print(f"- {pm.user.name} @ {pm.created_at}")

    return render(request, 'passed_legislation.html', {'passed_legislation': passed})


@login_required
@user_passes_test(lambda u: u.member_type in ['Chair', 'Officer'])
@log_function_call
def submit_new_version(request, legislation_id):
    legislation = get_object_or_404(Legislation, id=legislation_id)

    if request.user != legislation.posted_by:
        return HttpResponseForbidden("Only the uploader can submit a new version.")

    if request.method == 'POST':
        form = LegislationForm(request.POST, request.FILES)
        if form.is_valid():
            new_legislation = form.save(commit=False)
            new_legislation.posted_by = request.user
            # Optionally: mark this as a new version of the old legislation
            new_legislation.previous_version = legislation  # Assuming you have a previous_version field
            new_legislation.save()

            messages.success(request, "New version of the legislation has been submitted.")
            return redirect('view_legislation_history')  # Redirect to the history page or wherever appropriate
        else:
            messages.error(request, "Please correct the error below.")
    else:
        # Prepopulate the form with the old legislation data
        form = LegislationForm(instance=legislation)

    return render(request, 'submit_new_version.html', {'form': form, 'legislation': legislation})

@login_required
@user_passes_test(lambda u: hasattr(u, 'is_admin') and u.is_admin)
def view_logs(request):
    logs = []

    try:
        if os.path.exists(LOG_FILE_PATH):
            with open(LOG_FILE_PATH, 'r') as f:
                log_lines = f.readlines()[-200:]  # Show last 200 lines for performance
                for line in reversed(log_lines):
                    parts = line.strip().split(" - ")
                    if len(parts) >= 3:
                        timestamp, logger_name, message = parts[0], parts[1], " - ".join(parts[2:])
                        logs.append({
                            'timestamp': timestamp,
                            'logger': logger_name,
                            'message': message,
                        })
                    else:
                        logs.append({
                            'timestamp': '',
                            'logger': '',
                            'message': line.strip()
                        })
        else:
            messages.warning(request, "Log file not found.")
    except Exception as e:
        messages.error(request, f"Error reading log file: {e}")

    return render(request, 'admin/view_logs.html', {'logs': logs})

def db_dump_view(request):
    out = StringIO()
    call_command('dump_db', stdout=out)
    output = out.getvalue()
    return HttpResponse(f"&lt;pre&gt;{output}&lt;/pre&gt;")

@login_required
@user_passes_test(lambda u: u.member_type in ['Chair', 'Officer'])
def make_event(request):
    return render(request, 'make_event.html', {})

@login_required
@user_passes_test(lambda u: u.member_type in ['Chair', 'Officer'])
def manage_event(request):
    return render(request, 'manage_event.html', {})

@login_required
@user_passes_test(lambda u: u.member_type in ['Chair', 'Officer'])
def officer_home(request):
    return render(request, 'officer_home.html')
