# Page Toggle Implementation Summary

## Overview
This document summarizes the implementation of page toggles across the Parliament application. Page toggles allow administrators to enable/disable entire pages from the Admin v2 dashboard.

## Implementation Date
January 6, 2026

---

## Pages with Toggle Protection

The following pages now have the `@require_page_enabled` decorator applied:

### General Pages
1. **Home Page** (`home`)
   - File: `src/view/home.py`
   - Description: Main dashboard/home page showing recent activity and quick links

2. **User Profile** (`profile`)
   - File: `src/view/profile_view.py`
   - Description: User profile page for updating personal information

3. **Calendar** (`calendar`)
   - File: `src/view/calendar.py`
   - Description: Event calendar with subscriptions

### Legislation & Voting
4. **Voting Page** (`vote`)
   - File: `src/view/vote_view.py`
   - Description: Chapter legislation voting page

5. **Passed Legislation** (`passed_legislation`)
   - File: `src/view/passed_legislation.py`
   - Description: View all passed chapter legislation

### Documents
6. **Chapter Documents** (`chapter_documents`)
   - File: `src/view/chapter_documents.py`
   - Description: View documents published to the entire chapter

### Committees
7. **Committee Index** (`committee_index`)
   - File: `src/view/committee/committee_index.py`
   - Description: Main committee listing page

8. **Committee Home** (`committee_home`)
   - File: `src/view/committee/committee_home.py`
   - Description: Individual committee home pages

9. **Committee Documents** (`committee_documents`)
   - File: `src/view/committee/documents.py`
   - Description: Committee document viewing

### Officer Pages
10. **Officer Dashboard** (`officer_home`)
    - File: `src/view/officer/officer_home.py`
    - Description: Officer home page/dashboard

11. **User List** (`user_list`)
    - File: `src/view/officer/user_list.py`
    - Description: Officer page to view and manage users

---

## Database Status

**Total PageToggle Entries:** 18

All pages are currently **ENABLED** by default.

### Complete List of Page Toggles in Database:
- Announcements
- Attendance Page
- Calendar
- Chapter Documents
- Chat Channels
- Committee Documents
- Committee Home
- Committee Index
- Constitution & Bylaws
- Home Page
- KAI Dashboard
- Officer Dashboard
- Passed Legislation
- Robert's Rules
- Upload Legislation
- User List
- User Profile
- Voting Page

---

## How to Use Page Toggles

### Via Admin v2 Dashboard

1. Navigate to `/admin-v2/` and log in with:
   - Your user password
   - Admin v2 secret key (set in settings)

2. In the "Page Toggles" section:
   - View all pages and their current status
   - Click the toggle switch to enable/disable a page
   - Changes take effect immediately

### Behavior When Disabled

When a page is disabled:
- Users attempting to access the page will see a custom error page
- The error message can be customized per page in the database
- The page remains inaccessible until re-enabled

---

## Technical Implementation

### Decorator Pattern
```python
from src.feature_flag_decorators import require_page_enabled

@login_required
@require_page_enabled('page_name')
def my_view(request):
    # View logic here
    pass
```

### Database Model
The `PageToggle` model (defined in `src/models_feature_flags.py`) includes:
- `url_name`: Django URL name (e.g., "home", "vote")
- `display_name`: Human-readable page name
- `description`: Description of the page
- `is_enabled`: Boolean toggle status
- `disabled_message`: Custom message shown when disabled
- Metadata: created_at, updated_at, last_toggled_by, last_toggled_at

---

## Testing the Feature

### Test Plan

1. **Enable/Disable from Admin v2:**
   - Log into Admin v2 dashboard
   - Disable the "Home Page" toggle
   - Verify home page shows disabled message
   - Re-enable the toggle
   - Verify home page is accessible again

2. **Test Multiple Pages:**
   - Disable "Voting Page"
   - Attempt to access `/vote/`
   - Should see custom disabled message
   - Re-enable and verify access

3. **Test Protected Routes:**
   - Disable "Committee Index"
   - Try accessing `/committees/`
   - Verify proper error page display

### Expected Behavior
- ✅ Disabled pages show custom error message
- ✅ Enabled pages function normally
- ✅ Toggle changes take effect immediately
- ✅ No server restart required

---

## Files Modified

### View Files (Added Decorator)
1. `src/view/home.py`
2. `src/view/vote_view.py`
3. `src/view/passed_legislation.py`
4. `src/view/calendar.py`
5. `src/view/profile_view.py`
6. `src/view/chapter_documents.py`
7. `src/view/committee/committee_index.py`
8. `src/view/committee/committee_home.py`
9. `src/view/committee/documents.py`
10. `src/view/officer/officer_home.py`
11. `src/view/officer/user_list.py`

### New Files Created
- `create_page_toggles.py` - Script to populate PageToggle database entries

---

## Future Enhancements

### Potential Additional Pages to Protect
- Event management pages
- Announcement pages
- KAI report pages
- Chat system pages
- Additional committee pages

### Features to Consider
- Scheduled page toggles (enable/disable at specific times)
- Role-based toggle overrides (allow certain roles to bypass)
- Toggle history/audit log
- Bulk toggle operations

---

## Maintenance Notes

### Adding New Page Toggles

1. **Add decorator to view:**
   ```python
   from src.feature_flag_decorators import require_page_enabled

   @login_required
   @require_page_enabled('my_new_page')
   def my_view(request):
       pass
   ```

2. **Create database entry:**
   ```python
   from src.models_feature_flags import PageToggle

   PageToggle.objects.create(
       url_name='my_new_page',
       display_name='My New Page',
       description='Description of the page',
       is_enabled=True,
       disabled_message='This page is temporarily unavailable.'
   )
   ```

3. **Or use the script:**
   - Edit `create_page_toggles.py`
   - Add your page to the `page_toggles` list
   - Run: `DJANGO_SETTINGS_MODULE=Parliament.settings python3 manage.py shell < create_page_toggles.py`

---

## Support

For issues or questions about page toggles:
1. Check the Admin v2 dashboard for current status
2. Verify the PageToggle exists in the database
3. Ensure the decorator is properly applied to the view
4. Check server logs for any errors

---

*Last Updated: January 6, 2026*
