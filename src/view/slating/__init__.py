"""
Slating System Views

This module provides views for the modular officer slating system.
"""

from .dashboard import slating_dashboard
from .period_setup import create_period, edit_period, change_period_status
from .form_builder import form_builder
from .position_manager import manage_positions, add_position, edit_position, delete_position, copy_default_positions
from .apply import apply_view, my_applications, withdraw_application
from .applications_review import applications_list, application_detail, submit_review, bulk_update_status
from .interview_manager import interview_list, schedule_interview, complete_interview, destroy_interview_notes
from .slate_builder import build_slate, approve_slate, slate_preview, copy_slate
from .vote import slating_vote, individual_vote, close_voting
from .results import view_results, publish_results, results_summary
from .transfer_admin import transfer_admin
from .transition import transition_officers
from .api import (
    reorder_fields, reorder_positions, period_status,
    check_eligibility, application_summary, slate_candidates,
    voting_status, toggle_field_active, toggle_position_active
)

__all__ = [
    # Dashboard
    'slating_dashboard',

    # Period Management
    'create_period',
    'edit_period',
    'change_period_status',

    # Form Builder
    'form_builder',

    # Position Management
    'manage_positions',
    'add_position',
    'edit_position',
    'delete_position',
    'copy_default_positions',

    # Application Flow
    'apply_view',
    'my_applications',
    'withdraw_application',

    # Application Review
    'applications_list',
    'application_detail',
    'submit_review',
    'bulk_update_status',

    # Interview Management
    'interview_list',
    'schedule_interview',
    'complete_interview',
    'destroy_interview_notes',

    # Slate Building
    'build_slate',
    'approve_slate',
    'slate_preview',
    'copy_slate',

    # Voting
    'slating_vote',
    'individual_vote',
    'close_voting',

    # Results
    'view_results',
    'publish_results',
    'results_summary',

    # Admin Transfer
    'transfer_admin',

    # Officer Transition
    'transition_officers',

    # API
    'reorder_fields',
    'reorder_positions',
    'period_status',
    'check_eligibility',
    'application_summary',
    'slate_candidates',
    'voting_status',
    'toggle_field_active',
    'toggle_position_active',
]
