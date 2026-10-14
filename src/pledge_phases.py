"""
Pledge phases (v3.46.0).

Mason, 10-14-26: "there's no way to advance a pledges phase on the education
dashboard." Tasks (`PledgeTask.phase`) and page restrictions
(`PledgePageRestriction.allowed_phases`) were already written per phase, but
no pledge HAD a phase: `ParliamentUser.pledge_phase` was scrapped on 06-24-26,
so every pledge counted as 'all'. That meant a Phase 2 task was shown to a
brand-new pledge, and a page restricted to "Phase 1" was blocked for everyone.

Now each pledge has a phase ('1', '2' or '3', starting at '1'), set or
advanced from the education dashboard. What it unlocks:

* **Tasks:** 'All Phases' tasks plus tasks for the pledge's phase and every
  earlier one. Advancing never takes a task away.
* **Pages:** `PledgePageRestriction.is_allowed` checks the pledge's phase
  against the boxes ticked for that page, exactly as the dashboard describes.

This module is the one place that knows the phase list and its order.
"""

PHASES = ('1', '2', '3')
FIRST_PHASE = PHASES[0]
LAST_PHASE = PHASES[-1]
PHASE_CHOICES = [(p, f'Phase {p}') for p in PHASES]


def normalize(phase):
    """A valid phase string; anything else (None, '', 'all', junk) is the first phase."""
    phase = str(phase or '')
    return phase if phase in PHASES else FIRST_PHASE


def unlocked_task_phases(phase):
    """`PledgeTask.phase` values a pledge in `phase` may see: 'all' + this phase and earlier."""
    phase = normalize(phase)
    return ['all', *PHASES[:PHASES.index(phase) + 1]]


def task_unlocked(task_phase, pledge_phase):
    return (task_phase or 'all') in unlocked_task_phases(pledge_phase)


def next_phase(phase):
    """The phase after `phase`, or None if it is already the last."""
    phase = normalize(phase)
    i = PHASES.index(phase)
    return PHASES[i + 1] if i + 1 < len(PHASES) else None
