from django.shortcuts import get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils.http import url_has_allowed_host_and_scheme
from src.models import Committee, CommitteeLegislation
import logging

__all__ = ['committee_unpush_from_chapter', 'delete_chapter_vote_link']

logger = logging.getLogger('function_calls')


@login_required
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

        if not committee_leg.pushed_to_chapter:
            messages.error(request, 'This item is not published to chapter.')
        else:
            # Just mark as not published - don't delete chapter vote
            committee_leg.pushed_to_chapter = False
            committee_leg.save()

            logger.info(f"{request.user.username} unpublished '{committee_leg.title}' from {committee.code} chapter documents")
            messages.success(request, f"'{committee_leg.title}' results removed from chapter documents.")

    # Redirect back to referring page (validate to prevent open redirect)
    next_url = request.POST.get('next') or request.GET.get('next')
    if next_url and url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return redirect(next_url)
    return redirect(f'/committee/{code}/vote/')


@login_required
def delete_chapter_vote_link(request, code):
    """Delete the chapter vote linked to a committee vote"""
    committee = get_object_or_404(Committee, code=code)

    # Check permissions
    if not committee.is_chair(request.user) and not request.user.is_admin:
        messages.error(request, 'Only committee chairs or admins can delete chapter votes.')
        return redirect('committee_home', code=code)

    if request.method == 'POST':
        legislation_id = request.POST.get('legislation_id')
        committee_leg = get_object_or_404(CommitteeLegislation, id=legislation_id, committee=committee)

        if not committee_leg.chapter_legislation:
            messages.error(request, 'No chapter vote exists for this item.')
        else:
            chapter_leg = committee_leg.chapter_legislation
            title = chapter_leg.title
            committee_leg.chapter_legislation = None
            committee_leg.save()
            chapter_leg.delete()

            logger.info(f"{request.user.username} deleted chapter vote '{title}' linked from {committee.code}")
            messages.success(request, f"Chapter vote deleted.")

    # Redirect back to referring page (validate to prevent open redirect)
    next_url = request.POST.get('next') or request.GET.get('next')
    if next_url and url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return redirect(next_url)
    return redirect(f'/committee/{code}/vote/')
