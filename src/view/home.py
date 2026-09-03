import logging
from ..decorators import log_function_call
from ..models import ParliamentUser, Legislation, Event, Committee, Vote, Announcement, SlatingPeriod, SlatingBallot
from django.db.models import Count, F, Q

logger = logging.getLogger(__name__)
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta
from src.feature_flag_decorators import require_page_enabled
from src.models.users import member_defer, member_prefetch
from src.utils.visibility import visible_to_q
from src.utils.security_utils import get_client_ip
from src.context_processors import get_user_prefs

def committee_dashboard_links(user):
    """v3.29.0 — home-page quick links to committee management dashboards
    (Kai, Service Hours, Recruitment, Education), shown only when the user
    actually has access to that specific dashboard — a link to a 403/404
    is worse than no link at all. Slating already has its own equivalent
    (`has_slating_access`, computed inline in `home()`, tied to whether
    there's an active period) and isn't duplicated here.

    Each block below replicates the EXACT gate its own view applies —
    named in a comment — rather than one shared "is this a committee
    person" check, because the four dashboards are gated four different
    ways: a `KaiMemberPermission` grant, a Role, committee membership/
    chair/advisor/role, and chair. Module-level (not inline in `home()`)
    so it's unit-testable on its own, same reasoning as
    `transition_checklist_cards` above.

    v3.29.2 — Recruitment and Education now deliberately check ONLY
    committee-specific signals, not `is_admin`/`is_officer`. See the
    comment above the Recruitment/Education block for why: those two
    views allow a chapter-wide admin/officer bypass for management
    purposes, but this widget shouldn't advertise a dashboard as "yours"
    just because a chapter-wide role happens to also unlock it.
    """
    from src.feature_flag_decorators import check_feature_enabled, check_page_enabled
    from src.models import Committee

    links = []

    # --- Kai committee ---------------------------------------------------
    # Mirrors view_kai_reports exactly: @require_feature_flag('kai_reports')
    # + _get_kai_access(user, committee)['can_view_report_list']. Deferred
    # import — kai_reports.py doesn't import home.py, but keeping this
    # local matches how the rest of this module pulls in models it only
    # needs for one card.
    if check_feature_enabled('kai_reports'):
        kai_committee = Committee.objects.filter(is_kai_committee=True).first()
        if kai_committee is not None:
            from src.view.kai_reports import _get_kai_access
            if _get_kai_access(user, kai_committee)['can_view_report_list']:
                links.append({'label': 'Kai Committee', 'url': reverse('view_kai_reports')})

    # --- Service hours -----------------------------------------------
    # Mirrors @vpp_required exactly: admin or VPP role holder. Not
    # committee-based — there's no "service committee" model concept, this
    # dashboard is gated by Role alone.
    if user.is_admin or user.roles.filter(code__iexact='VPP').exists():
        links.append({'label': 'Service Hours', 'url': reverse('service_dashboard')})

    # --- Recruitment / Education -------------------------------------
    # Both dashboards share @require_page_enabled('committee_home') — check
    # it once rather than per-committee.
    #
    # Deliberately NARROWER than the dashboards' own access predicates
    # (v3.29.2, reported by Mason: prod showed him Recruitment and
    # Education even though he's not on either committee — he could
    # reach both only via the chapter-wide `is_admin` / `is_officer`
    # bypasses those views allow for management purposes). Those
    # bypasses are fine on the views themselves — an admin/officer
    # troubleshooting a committee they're not part of is a legitimate
    # use case reached by navigating there directly — but a HOME-PAGE
    # SHORTCUT should only appear for someone with a genuine tie to
    # THAT SPECIFIC committee, not a chapter-wide role that happens to
    # also unlock it. So this only checks the committee-specific
    # signals (chair, member, advisor, the committee's linked Role) and
    # skips `is_admin`/`is_officer` entirely, even though the
    # underlying views would still let an admin or officer through.
    if check_page_enabled('committee_home'):
        recruitment_committee = Committee.objects.filter(is_recruitment_committee=True).first()
        if recruitment_committee is not None:
            c = recruitment_committee
            has_access = (
                c.is_chair(user) or
                c.members.filter(pk=user.pk).exists() or
                c.advisors.filter(pk=user.pk).exists() or
                (c.role_id and user.roles.filter(id=c.role_id).exists())
            )
            if has_access:
                links.append({'label': 'Recruitment', 'url': reverse('recruitment_dashboard', args=[c.code])})

        # Education has no formal "member" concept beyond chairs (see
        # _education_committee_or_404) — chair is the only
        # committee-specific signal available, so that's the only one
        # checked here.
        education_committee = Committee.objects.filter(
            is_active=True, is_education_committee=True,
        ).first()
        if education_committee is not None:
            c = education_committee
            if c.chairs.filter(pk=user.pk).exists():
                links.append({'label': 'Education', 'url': reverse('education_home', args=[c.code])})

    return links


def transition_checklist_cards(user):
    """v3.14.1 — data for the home-page transition-checklist card(s).

    An incoming officer with an open term (end_semester='') and an incomplete
    handoff checklist gets a "3/8 complete" card. One grouped query (same
    aggregate pattern as toggle_checklist_item); returns [] for everyone else,
    so the common case costs a single cheap query. Module-level (not inline in
    the view) so it's unit-testable on sqlite — the full home view needs
    postgres-only JSONField lookups.
    """
    from src.models import TransitionChecklistStatus
    return list(
        TransitionChecklistStatus.objects
        .filter(role_history__user=user, role_history__end_semester='')
        .values('role_history_id', 'role_history__role_name')
        .annotate(total=Count('id'),
                  done=Count('id', filter=Q(completed_at__isnull=False)))
        .filter(done__lt=F('total'))
        .order_by('role_history__role_name')
    )


@login_required
@require_page_enabled('home')
@log_function_call
def home(request):
    # v3.18.8: was REMOTE_ADDR — the nginx socket peer, not the visitor.
    logger.info(f"User: {request.user} | Authenticated: {request.user.is_authenticated} | IP: {get_client_ip(request)} | Page accessed: home")

    now = timezone.now()
    week_ago = now - timedelta(days=7)

    # === STATISTICS ===
    # Total active members
    total_active_members = ParliamentUser.objects.filter(member_status='Active').count()

    # Active legislation count
    # v3.13.3: was filter(status='active') — but no code path ever sets
    # 'active' (open legislation is status='draft'), so this stat and the
    # pending-votes widget below were permanently empty.
    # v3.14.1: invariant moved to LegislationQuerySet.open_for_voting().
    open_legislation_qs = Legislation.objects.open_for_voting(now)
    active_legislation = open_legislation_qs.count()

    # Upcoming events (next 7 days)
    upcoming_events_count = Event.objects.filter(
        is_active=True,
        archived=False,
        date_time__gte=now,
        date_time__lte=now + timedelta(days=7)
    ).count()

    # Your active committees — evaluate to list once so the template and count()
    # below don't each fire a separate query.
    # 07-06-26: only count active, non-archived committees — inactive/archived
    # ones were inflating the "My Committees (#)" stat on the home page.
    # v3.17.5: `home_classic.html:596-608` renders four things per committee —
    # `user in committee.chairs.all`, `user in committee.advisors.all`, and
    # `committee.members.count` TWICE (once for the number, once for
    # `|pluralize`). With nothing joined that was four queries per committee.
    #
    # `distinct=True` on the Count is load-bearing here and not optional: the
    # filter joins members/chairs/advisors, so the rows multiply and a plain
    # Count would report the product rather than the member count.
    #
    # ⚠️ v3.17.7 — ANNOTATE BEFORE FILTER. THE ORDER IS THE WHOLE BUG.
    # -----------------------------------------------------------------
    # This was `.filter(...).annotate(Count('members', distinct=True))`, and
    # `distinct=True` does not save you from what that does. Django reuses the
    # filter's join for an annotation on the SAME multi-valued relation, so the
    # aggregate is computed over the rows the WHERE left standing rather than
    # over the relation. The emitted SQL made it plain:
    #
    #   COUNT(DISTINCT committee_members.member_id) …
    #   LEFT OUTER JOIN committee_members ON …
    #   WHERE (committee_members.member_id = ME OR chairs.member_id = ME OR …)
    #
    # For a committee where the OR is satisfied by the *chairs* or *advisors*
    # disjunct, every member row survives the WHERE and the count is right. For
    # a committee where the only true disjunct is `members = ME`, **exactly one
    # member row survives** — so every committee you are a plain member of
    # reported "1 member". That is the common case, so the card was wrong for
    # most members most of the time. Measured on Django 5.2.16: 1 where the
    # true count was 7; correct at 7 with the annotate moved above the filter,
    # because Django then emits a second join for the filter instead of
    # reusing one.
    #
    # `manage_committees` and `global_search` use the same annotation safely —
    # neither filters on the relation it counts. That is the distinction to
    # check, not the presence of `distinct=True`.
    user_committees = list(Committee.objects.annotate(
        member_total=Count('members', distinct=True),
    ).filter(
        Q(members=request.user) |
        Q(chairs=request.user) |
        Q(advisors=request.user),
        is_active=True,
        is_archived=False,
    ).prefetch_related(
        member_prefetch('chairs'), member_prefetch('advisors'),
    ).distinct())

    # === YOUR PENDING VOTES ===
    # Get legislation user hasn't voted on yet
    voted_legislation_ids = Vote.objects.filter(user=request.user).values_list('legislation_id', flat=True)
    pending_votes = open_legislation_qs.exclude(
        id__in=voted_legislation_ids
    ).select_related('posted_by').defer(
        *member_defer('posted_by')
    ).order_by('-available_at')[:5]

    # === UPCOMING EVENTS ===
    # Visibility: null/empty visible_to = all; otherwise member_type must be
    # listed, and 'Member' also covers Chair and Officer.
    #
    # v3.17.3: this was built inline here and was wrong in two ways —
    # `visible_to__contains` is unsupported on SQLite (so the whole home page
    # 500'd under the documented local-dev setup), and `visible_to__len=0` was
    # silently a JSON *key* lookup, so an explicitly-empty visible_to matched
    # nothing on any backend. The rule now lives in one place, next to a test
    # that compares it against the models' own is_visible_to_user().
    _vis_q = visible_to_q(request.user.member_type)

    # v3.17.3 (second pass): the four home-page card querysets below had NO
    # select_related at all, so a template that prints an author name — e.g.
    # home_modern.html:317 `{{ announcement.posted_by.get_display_name }}` —
    # lazily fetched that member per row. Dev mode showed the announcements
    # card firing THREE identical full-column `ParliamentUser` pk lookups on a
    # single home-page load, one per announcement, all for the same author.
    #
    # Worth being precise about why the v3.17.3 sweep did not catch these: that
    # sweep *narrowed existing joins*, it did not *add missing ones*. A
    # queryset with no `select_related` has no join to narrow, so it was
    # invisible to the scan. The lesson for the next pass: "every member join
    # is narrow" and "every member dereference is joined" are two different
    # properties, and only the first one was checked.
    #
    # These are `[:3]` slices, so the join is over three rows and the deferred
    # profile columns keep it cheap.
    upcoming_events = Event.objects.filter(
        is_active=True,
        archived=False,
        date_time__gte=now,
    ).filter(_vis_q).order_by('date_time')[:3]
    # NOTE: no select_related('created_by') — neither home layout renders the
    # event's author. It was added here in the first pass alongside the three
    # querysets that DO need one, and removed in the wasted-join audit that
    # followed. Adding a join reflexively "because the others have one" is the
    # mirror image of the bug this block's comment is about.

    # === RECENT ANNOUNCEMENTS ===
    announcements = Announcement.objects.filter(
        is_active=True,
    ).filter(
        Q(publish_at__isnull=True) | Q(publish_at__lte=now)
    ).filter(_vis_q).select_related('posted_by').defer(
        *member_defer('posted_by')
    ).order_by('-posted_at')[:3]

    # === RECENTLY PASSED LEGISLATION ===
    recently_passed_legislation = Legislation.objects.annotate(
        total_votes=Count('vote'),
        yes_votes=Count('vote', filter=Q(vote__vote_choice='yes'))
    ).filter(
        voting_closed=True,
        status='passed'
    ).order_by('-voting_ended_at')[:3]
    # NOTE: deliberately NOT select_related('posted_by'). The loop below turns
    # these rows into plain dicts (`legislation_previews`) and never touches the
    # author — the templates render `item.title` and `item.detail_url` only. An
    # earlier pass added the join reflexively along with the other three
    # querysets on this page; it was a wasted INNER JOIN on every home-page
    # load. Joining a relation nothing reads is the same mistake as not joining
    # one that everything reads, just quieter.

    # Pre-fetch all vote choice breakdowns for the preview items in one query
    # instead of firing per-option or per-legislation COUNTs inside the loop.
    _leg_ids = [leg.pk for leg in recently_passed_legislation]
    _vote_counts = {}  # {legislation_id: {vote_choice: count}}
    for row in (
        Vote.objects
        .filter(legislation_id__in=_leg_ids)
        .values('legislation_id', 'vote_choice')
        .annotate(n=Count('id'))
    ):
        _vote_counts.setdefault(row['legislation_id'], {})[row['vote_choice']] = row['n']

    legislation_previews = []
    for leg in recently_passed_legislation:
        # Use historical counts if set (manually entered legislation)
        yes = leg.historical_yes_votes if leg.historical_yes_votes is not None else leg.yes_votes
        total = leg.total_votes
        breakdown = _vote_counts.get(leg.pk, {})

        if leg.vote_mode == 'plurality':
            option_counts = {
                opt: breakdown.get(opt, 0)
                for opt in (leg.plurality_options or [])
            }
            winner = max(option_counts, key=option_counts.get) if option_counts else None
            legislation_previews.append({
                'title': leg.title,
                'vote_mode': 'plurality',
                'winner': winner,
                'total_votes': total,
                'detail_url': reverse('passed_legislation_detail', kwargs={'pk': leg.pk}),
            })
        elif leg.vote_mode == 'piecewise':
            legislation_previews.append({
                'title': leg.title,
                'vote_mode': 'piecewise',
                'yes_votes': yes,
                'required_yes_votes': leg.required_number,
                'total_votes': total,
                'detail_url': reverse('passed_legislation_detail', kwargs={'pk': leg.pk}),
            })
        else:
            # Percentage mode
            no = leg.historical_no_votes if leg.historical_no_votes is not None else breakdown.get('no', 0)
            countable = yes + no
            yes_pct_str = "{:.0f}%".format((yes / countable) * 100) if countable > 0 else "N/A"
            legislation_previews.append({
                'title': leg.title,
                'vote_mode': 'percentage',
                'yes_percentage': yes_pct_str,
                'yes_pct_num': round((yes / countable) * 100) if countable > 0 else 0,
                'total_votes': total,
                'detail_url': reverse('passed_legislation_detail', kwargs={'pk': leg.pk}),
            })

    # === RECENT ACTIVITY ===
    # Count new items this week — use the same ORM visibility filter already built above
    # instead of fetching all records and calling is_visible_to_user() in Python.
    new_announcements_week = Announcement.objects.filter(
        is_active=True,
        posted_at__gte=week_ago,
    ).filter(
        Q(publish_at__isnull=True) | Q(publish_at__lte=now)
    ).filter(_vis_q).count()

    new_events_week = Event.objects.filter(
        is_active=True,
        archived=False,
        created_at__gte=week_ago,
    ).filter(_vis_q).count()

    # === ACTIVE SLATING PERIOD ===
    # Show card if there's an active slating period (nominations or voting open, or results published)
    active_slating_period = SlatingPeriod.objects.filter(
        status__in=['nominations_open', 'voting_open', 'results_published']
    ).first()

    # Check if user has access to the active period's slating committee
    has_slating_access = False
    if active_slating_period and not request.user.is_pledge:
        slating_committee = active_slating_period.slating_committee
        has_slating_access = (
            request.user.is_admin or
            bool(slating_committee and (
                slating_committee.admin == request.user or
                slating_committee.members.filter(pk=request.user.pk).exists() or
                slating_committee.chairs.filter(pk=request.user.pk).exists()
            ))
        )

    # Show slating card if user has committee access OR there's an active period
    show_slating_card = has_slating_access or (active_slating_period and not request.user.is_pledge)

    # Enhanced slating data for rich card
    slating_positions = []
    slating_total_applications = 0
    user_has_applied = False
    user_has_voted = False
    slating_passed_slate = None
    slating_slate_candidates = []

    if active_slating_period:
        slating_positions = list(
            active_slating_period.positions.filter(is_active=True).order_by('display_order', 'title')
        )
        if active_slating_period.status == 'nominations_open':
            slating_total_applications = active_slating_period.applications.exclude(
                status='withdrawn'
            ).count()
            user_has_applied = active_slating_period.applications.filter(
                applicant=request.user
            ).exclude(status='withdrawn').exists()
        elif active_slating_period.status == 'voting_open':
            user_has_voted = SlatingBallot.objects.filter(
                period=active_slating_period,
                voter=request.user,
            ).exists()
        elif active_slating_period.status == 'results_published':
            slating_passed_slate = active_slating_period.slates.filter(passed=True).first()
            if slating_passed_slate:
                slating_slate_candidates = list(
                    slating_passed_slate.candidates.select_related(
                        'position', 'application__applicant'
                    ).defer(*member_defer('application__applicant')).order_by('display_order')
                )

    # === TRANSITION CHECKLIST CARD (v3.14.1, specced 07-09) ===
    transition_checklists = transition_checklist_cards(request.user)

    # === COMMITTEE DASHBOARD QUICK LINKS (v3.29.0) ===
    committee_links = committee_dashboard_links(request.user)

    context = {
        'user': request.user,
        'transition_checklists': transition_checklists,
        'committee_dashboard_links': committee_links,
        # Stats
        'total_active_members': total_active_members,
        'active_legislation': active_legislation,
        'upcoming_events_count': upcoming_events_count,
        'user_committees': user_committees,
        'user_committees_count': len(user_committees),
        # Content
        'pending_votes': pending_votes,
        'upcoming_events': upcoming_events,
        'announcements': announcements,
        'legislation_previews': legislation_previews,
        # Activity
        'new_announcements_week': new_announcements_week,
        'new_events_week': new_events_week,
        # Slating
        'active_slating_period': active_slating_period,
        'has_slating_access': has_slating_access,
        'show_slating_card': show_slating_card,
        'slating_positions': slating_positions,
        'slating_total_applications': slating_total_applications,
        'user_has_applied': user_has_applied,
        'user_has_voted': user_has_voted,
        'slating_passed_slate': slating_passed_slate,
        'slating_slate_candidates': slating_slate_candidates,
    }

    # v3.17.3: was `request.user.preferences`, a reverse one-to-one dereference
    # — i.e. a query — for a row the user_preferences context processor loads
    # and caches for this same render anyway.
    layout = getattr(get_user_prefs(request.user), 'home_layout', 'modern')
    template = 'home_classic.html' if layout == 'classic' else 'home_modern.html'
    return render(request, template, context)