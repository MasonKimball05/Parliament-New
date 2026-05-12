"""
Parliament application constants.

Centralizes string values used across models, views, and decorators
so that comparisons never rely on bare string literals.
"""


class MemberType:
    MEMBER = 'Member'
    CHAIR = 'Chair'
    OFFICER = 'Officer'
    ADVISOR = 'Advisor'
    PLEDGE = 'Pledge'

    ALL = (MEMBER, CHAIR, OFFICER, ADVISOR, PLEDGE)
    CAN_VOTE = (MEMBER, CHAIR, OFFICER)
    CAN_VIEW_OFFICER_PAGES = (OFFICER, CHAIR, ADVISOR)
    CAN_MANAGE_EVENTS = (OFFICER, CHAIR)


class MemberStatus:
    ACTIVE = 'Active'
    INACTIVE = 'Inactive'
    ALUMNI = 'Alumni'
    REMOVED = 'Removed'

    ALL = (ACTIVE, INACTIVE, ALUMNI, REMOVED)


class CommitteeCode:
    """
    Canonical committee code strings.

    Prefer using the boolean flags on Committee (is_kai_committee,
    is_exec_board, is_slating_committee, is_chapter_committee) over
    comparing against these strings directly — flags let a future chapter
    reassign which committee plays each role without touching code.

    These constants exist for the rare cases where a code comparison is
    unavoidable (e.g. building the DEFAULT_COMMITTEES list) and as a
    reference for what codes exist.
    """
    KAI     = 'KAI'
    EXEC    = 'EXEC'
    SLATING = 'SLATING'
    CHAPTER = 'CHAPTER'
