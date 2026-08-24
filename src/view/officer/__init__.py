from .officer_home import *
from .attendance import *
from .user_list import *
# (`make_event` and `manage_event` — singular — were deleted in v3.25.0. Both
# rendered a template with no context and were superseded by `create_event` /
# `manage_events` in `.manage_events` below.)
from .manage_announcements import *
from .view_logs import *
from .upload_report import *
from .view_all_events import *
from .view_all_reports import *
from .view_all_activity import *
from .view_archived_events import *
from .archive_event import *
from .manage_resolutions import *
from .chapter_minutes import *
from .manage_members import *
from .edit_landing_page import edit_landing_page