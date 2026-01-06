# Parliament v2.3 User Guide

**New Features in Version 2.3.0**

This guide covers the major new features introduced in Parliament v2.3.0: Calendar Subscriptions, Admin v2 Dashboard, and Feature Flags System.

---

## Table of Contents

1. [Calendar Subscriptions](#calendar-subscriptions)
   - [What is a Calendar Subscription?](#what-is-a-calendar-subscription)
   - [How to Subscribe](#how-to-subscribe)
   - [Supported Calendar Apps](#supported-calendar-apps)
   - [Managing Your Subscription](#managing-your-subscription)
   - [Troubleshooting](#troubleshooting-calendar)
2. [Admin v2 Dashboard](#admin-v2-dashboard) (Administrators Only)
   - [Accessing Admin v2](#accessing-admin-v2)
   - [Dashboard Overview](#dashboard-overview)
   - [Feature Flags Management](#feature-flags-management)
   - [Page Toggles](#page-toggles)
3. [Feature Flags System](#feature-flags-system) (Administrators Only)
   - [Understanding Feature Flags](#understanding-feature-flags)
   - [How to Use](#how-to-use-feature-flags)

---

## Calendar Subscriptions

### What is a Calendar Subscription?

Calendar subscriptions allow you to add Parliament events to your personal calendar app (Google Calendar, Apple Calendar, Outlook, etc.) and have them automatically update when events are added, changed, or removed.

**Key Benefits:**
-  **Always Up-to-Date**: Events automatically sync - no need to re-export
-  **Works Everywhere**: Google Calendar, Apple Calendar, Outlook, and more
-  **Permission-Based**: Only shows events you're authorized to see
-  **One-Time Setup**: Subscribe once, updates happen automatically

**How is this different from exporting?**
- **Export** (old method): Downloads a one-time snapshot. If events change, you need to re-export and re-import.
- **Subscribe** (new method): Creates a live connection. Changes automatically appear in your calendar.

---

### How to Subscribe

#### Step 1: Access the Calendar Page
1. Log in to Parliament
2. Navigate to **Calendar** from the main menu

#### Step 2: Open the Subscription Modal
1. Click the green **"Subscribe to Calendar"** button (next to Export Calendar)
2. A modal window will appear

#### Step 3: Choose Your Calendar App

**Option A: Quick Subscribe (Recommended)**

**For Google Calendar:**
1. Click the blue **"Google Calendar"** button
2. Your browser will open Google Calendar
3. Click "Add Calendar" when prompted
4. Done! Events will appear in your Google Calendar

**For Apple Calendar or Outlook:**
1. Click the gray **"Apple/Outlook"** button
2. Your calendar app will open automatically
3. Confirm the subscription when prompted
4. Done! Events will appear in your calendar

**Option B: Manual URL Copy**

If the quick buttons don't work:
1. Find the "Or copy the subscription URL" section
2. Click the **"Copy"** button
3. Follow the platform-specific instructions below

---

### Supported Calendar Apps

#### Google Calendar

**Method 1: Quick Subscribe Button** (Easiest)
- Click the "Google Calendar" button in the modal
- Confirm when Google Calendar opens

**Method 2: Manual Setup**
1. Copy the subscription URL from Parliament
2. Go to [Google Calendar](https://calendar.google.com)
3. On the left sidebar, click the **"+"** next to "Other calendars"
4. Select **"From URL"**
5. Paste the subscription URL
6. Click **"Add calendar"**

**Refresh Rate:** Google Calendar refreshes every 2-4 hours

---

#### Apple Calendar (iPhone/Mac)

**Method 1: Quick Subscribe Button** (Easiest)
- Click the "Apple/Outlook" button in the modal
- Confirm the subscription when Calendar opens

**Method 2: Manual Setup**
1. Copy the subscription URL from Parliament
2. On **Mac**: Open Calendar app ’ File ’ New Calendar Subscription ’ Paste URL
3. On **iPhone/iPad**: Settings ’ Calendar ’ Accounts ’ Add Account ’ Other ’ Add Subscribed Calendar ’ Paste URL

**Refresh Rate:** Apple Calendar refreshes every 5-15 minutes

---

#### Microsoft Outlook

**Desktop Outlook:**
1. Copy the subscription URL from Parliament
2. Open Outlook
3. Go to **Calendar** view
4. Click **"Add Calendar"** ’ **"From Internet"**
5. Paste the subscription URL
6. Click **"OK"**

**Outlook.com (Web):**
1. Copy the subscription URL from Parliament
2. Go to [Outlook.com Calendar](https://outlook.live.com/calendar)
3. Click **"Add calendar"** ’ **"Subscribe from web"**
4. Paste the subscription URL
5. Name your calendar and click **"Import"**

**Refresh Rate:** Daily by default (can be configured in Outlook settings)

---

#### Other Calendar Apps

Most calendar apps support iCal subscriptions. Look for options like:
- "Add Calendar"
- "Subscribe to Calendar"
- "From URL" or "From Web"
- "Internet Calendar"

Then paste your subscription URL from Parliament.

---

### Managing Your Subscription

#### Viewing Usage Statistics

In the subscription modal, you can see:
- **Last accessed**: When your calendar app last checked for updates
- **Access count**: Total number of times your calendar app has synced

This helps you verify that your subscription is working.

---

#### Regenerating Your Token

**When to regenerate:**
- If you accidentally shared your subscription URL publicly
- If you suspect unauthorized access
- If you want to revoke access to old subscriptions

**How to regenerate:**
1. Open the subscription modal
2. Scroll to the security warning section (yellow box)
3. Click **"Regenerate link if compromised"**
4. Confirm the action
5. Your old URL will stop working
6. A new URL will be generated
7. **Important**: You must re-subscribe in your calendar apps with the new URL

  **Warning**: Regenerating your token will break all existing subscriptions. You'll need to re-add the calendar with the new URL.

---

#### Removing a Subscription

To stop syncing Parliament events with your calendar:

**Google Calendar:**
1. Go to Google Calendar settings
2. Find "Other calendars" section
3. Find the Parliament calendar
4. Click the three dots ’ **"Remove calendar"**

**Apple Calendar:**
1. Open Calendar app
2. Find the Parliament calendar in the sidebar
3. Right-click (or Control+click) ’ **"Delete"**

**Outlook:**
1. Go to Calendar view
2. Right-click the Parliament calendar
3. Select **"Delete Calendar"**

---

### Troubleshooting (Calendar)

#### Events aren't appearing

**Check your calendar app's refresh:**
- Google Calendar: Wait 2-4 hours or manually refresh
- Apple Calendar: Wait 15 minutes or try closing and reopening the app
- Outlook: Check your sync settings or manually refresh

**Verify the subscription:**
1. Go back to Parliament's calendar subscription modal
2. Check if "Last accessed" is recent
3. If it shows "Never", your calendar app hasn't connected yet

**Try re-subscribing:**
1. Remove the old subscription from your calendar app
2. Get a fresh subscription URL from Parliament
3. Re-add the subscription

#### Some events are missing

**This is normal!** The subscription respects your permissions:
- You only see events you're authorized to view
- Officer-only events won't appear if you're not an officer
- Committee events only appear if you're a committee member

If you believe you should see an event but don't:
1. Check if the event exists in Parliament's calendar page
2. Verify you have permission to view it
3. Contact an officer if you believe there's an error

#### "Could not add calendar" error

**Solution 1**: Try the manual URL method instead of quick subscribe buttons

**Solution 2**: Check that you're using the webcal:// URL (not https://)
- Most calendar apps prefer webcal:// URLs
- The manual copy should give you the correct format

**Solution 3**: Try adding in a web browser instead of the calendar app
- For Google Calendar, use the web version first
- For Apple Calendar, try on Mac before iPhone/iPad

---

## Admin v2 Dashboard

> **Note**: This section is only relevant for administrators with proper access credentials.

### Accessing Admin v2

Admin v2 is a secure administration panel with enhanced security through dual authentication.

**Requirements:**
1. Must be logged in as an authorized administrator
2. Must know your user password
3. Must have the Admin v2 secret key (provided by system administrator)

**How to access:**
1. Navigate to `/admin-v2/` or `/admin_v2/`
2. You'll see a dual authentication form
3. Enter your **user password** (same as your normal login password)
4. Enter the **Admin v2 secret key** (get this from the system administrator)
5. Click **"Authenticate"**

**Security Features:**
- All access attempts are logged
- Failed attempts trigger security alerts
- Session-based authentication (stays logged in for your session)
- Unauthorized users cannot access even with correct password

---

### Dashboard Overview

Once logged in, you'll see the Admin v2 Dashboard with several sections:

#### Site Statistics

**Users:**
- Total user count
- Active members
- Officers, Members, and Pledges breakdown

**Legislation:**
- Total legislation count
- Draft, Passed, and Removed counts

**Events:**
- Total events
- Upcoming events
- Past events

**Committees:**
- Total committees
- Active committees

**Announcements:**
- Total announcements
- Active announcements

**Security Metrics:**
- Total login attempts
- Recent security alerts
- Activity log entries

---

### Feature Flags Management

Feature flags allow you to enable or disable specific features across the site.

#### Viewing Feature Flags

Feature flags are organized by category:
- Core Features
- Voting & Legislation
- Committees
- Events & Calendar
- Communications
- Documents
- Admin Features

Each flag shows:
-  or  (enabled/disabled status)
- Display name
- Description
- Last toggle information

#### Toggling Features

To enable or disable a feature:
1. Find the feature flag in the dashboard
2. Click the **"Toggle"** button
3. The page will refresh
4. Status will update ( becomes  or vice versa)

**Effect:** The feature will be immediately enabled or disabled site-wide for all users.

#### Example: Disabling Chats

To temporarily disable the chat system:
1. Find the **"chats"** feature flag in the Communications category
2. Click **"Toggle"** to disable it
3. All chat pages and APIs will now return "Feature Disabled" pages
4. Users cannot access any chat functionality
5. Toggle again to re-enable

---

### Page Toggles

Page toggles allow you to enable or disable entire pages with custom messages.

#### Viewing Page Toggles

Page toggles show:
-  or  (enabled/disabled status)
- Page name
- URL name
- Custom disabled message

#### Toggling Pages

To enable or disable a page:
1. Find the page toggle in the dashboard
2. Click the **"Toggle"** button
3. The page will refresh
4. Status will update

**Effect:** The page will be immediately accessible or blocked for all users.

**Example: Maintenance Mode**
1. Disable the "Home" page toggle
2. Set custom message: "Site is under maintenance. Check back in 30 minutes."
3. All users visiting the home page will see your custom message
4. Re-enable when maintenance is complete

---

### Logging Out

To log out of Admin v2:
1. Click the **"Logout"** button in the top-right
2. You'll be returned to the Admin v2 login page
3. Your Admin v2 session is cleared (but you remain logged in to Parliament)

---

## Feature Flags System

> **Note**: This section is only relevant for administrators.

### Understanding Feature Flags

Feature flags are a way to control which features are available on the site without changing code or redeploying.

**Use Cases:**
- **Gradual Rollout**: Enable new features for testing before full release
- **Maintenance Mode**: Temporarily disable features during updates
- **Emergency Shutoff**: Quickly disable problematic features
- **A/B Testing**: Enable features for specific user groups (future enhancement)

**How They Work:**
1. Code checks if a feature flag is enabled
2. If enabled: feature works normally
3. If disabled: users see a "Feature Disabled" page
4. Administrators can toggle flags without code changes

---

### How to Use Feature Flags

#### Creating New Feature Flags

Feature flags must be created in the database before they can be used. Contact your developer to add new flags.

**Required Information:**
- Internal name (e.g., "chats", "dark_mode")
- Display name (user-friendly name)
- Description
- Category

#### Recommended Flags

**Essential Flags to Create:**
- **chats**: Controls all chat functionality
- **announcements**: Controls announcements system
- **calendar_export**: Controls calendar export feature
- **calendar_subscribe**: Controls calendar subscriptions
- **committee_voting**: Controls committee voting system

**Future/Optional Flags:**
- **dark_mode**: For dark mode UI toggle
- **advanced_search**: For advanced search features
- **ai_features**: For any AI-powered features

#### Best Practices

1. **Test Before Disabling**: Verify the feature disabled page looks good
2. **Communicate Changes**: Tell users before disabling major features
3. **Document Dependencies**: Note if disabling one feature affects others
4. **Monitor Logs**: Check ActivityLog after toggling important features
5. **Have a Rollback Plan**: Know how to quickly re-enable if needed

---

## Need Help?

If you encounter issues or have questions:

**For Users:**
- Contact your chapter officers
- Check if the issue is listed in Troubleshooting sections above
- Report bugs to your system administrator

**For Administrators:**
- Review the detailed changelog: `/changelogs/v2.3.0.md`
- Check ActivityLog for error details
- Contact the developer: [Mason Kimball](https://github.com/MasonKimball05)

---

## Quick Reference

### Calendar Subscriptions
- =Í **Location**: Calendar page ’ "Subscribe to Calendar" button
- = **Refresh Rate**: Google (2-4 hrs), Apple (5-15 min), Outlook (daily)
- = **Security**: Keep subscription URL private, regenerate if compromised

### Admin v2
- =Í **Location**: `/admin-v2/` or `/admin_v2/`
- = **Access**: User password + Admin v2 secret key
- =Ê **Features**: Statistics, Feature Flags, Page Toggles

### Feature Flags
- <› **Purpose**: Enable/disable features site-wide
- <÷ **Categories**: Core, Voting, Committees, Events, Communications, Documents, Admin
- ¡ **Effect**: Immediate (no refresh needed for other users)

---

**Document Version**: 1.0
**Last Updated**: January 6, 2026
**Parliament Version**: 2.3.0
