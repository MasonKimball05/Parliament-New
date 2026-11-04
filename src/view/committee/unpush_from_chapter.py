from django.shortcuts import get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils.http import url_has_allowed_host_and_scheme
from src.committee_management import (
    CommitteeActionError, can_manage_chapter_vote, delete_chapter_vote, unpush_from_chapter,
)
from src.models import Committee, CommitteeLegislation
from src.feature_flag_decorators import require_feature_flag
import logging

__all__ = ['committee_unpush_from_chapter', 'delete_chapter_vote_link']

logger = logging.getLogger('function_calls')


@login_required
@require_feature_flag('committee_voting')
def committee_unpush_from_chapter(request, code):
    """Unpublish committee vote results from chapter documents (does NOT delete chapter vote)"""
    committee = get_object_or_404(Committee, code=code)

    # Check permissions
    if not committee.is_chair(request.user):
        messages.error(request, 'Only committee chairs can unpublish results from chapter.')
        return redirect('committee_home', code=code)

    if request.method == 'POST':
        legislation_id = request.POST.get('legislation_id')
        committee_leg = get_object_or_404(CommitteeLegislation, id=legislation_id, committee=committee)

        # Just mark as not published - don't delete chapter vote
        try:
            unpush_from_chapter(committee_leg, request.user)
        except CommitteeActionError as exc:
            messages.error(request, exc.message)
        else:
            messages.success(request, f"'{committee_leg.title}' results removed from chapter documents.")

    # Redirect back to referring page (validate to prevent open redirect)
    next_url = request.POST.get('next') or request.GET.get('next')
    if next_url and url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return redirect(next_url)
    return redirect(f'/committee/{code}/vote/')


@login_required
@require_feature_flag('committee_voting')
def delete_chapter_vote_link(request, code):
    """Delete the chapter vote linked to a committee vote"""
    committee = get_object_or_404(Committee, code=code)

    # Check permissions
    if not can_manage_chapter_vote(request.user, committee):
        messages.error(request, 'Only committee chairs or admins can delete chapter votes.')
        return redirect('committee_home', code=code)

    if request.method == 'POST':
        legislation_id = request.POST.get('legislation_id')
        committee_leg = get_object_or_404(CommitteeLegislation, id=legislation_id, committee=committee)

        try:
            delete_chapter_vote(committee, committee_leg, request.user)
        except CommitteeActionError as exc:
            messages.error(request, exc.message)
        else:
            messages.success(request, f"Chapter vote deleted.")

    # Redirect back to referring page (validate to prevent open redirect)
    next_url = request.POST.get('next') or request.GET.get('next')
    if next_url and url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return redirect(next_url)
    return redirect(f'/committee/{code}/vote/')
