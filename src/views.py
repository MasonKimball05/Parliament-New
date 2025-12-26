"""
All views have been migrated to individual files in src/view/

This file is kept for backwards compatibility and may be removed in the future.

View files location:
- General views: src/view/
- Officer views: src/view/officer/
- Committee views: src/view/committee/

All URL patterns have been updated in src/urls.py to import from the individual view files.
"""

# If you need to add new views, please create them in individual files under src/view/
# and import them in src/urls.py
# While this file no longer holds any code, it is still useful to have here for the sake of testing new features
# and more easily setting up multiple functions at once to see how they work before putting them in their own files