# Parliament — Officer & Admin Guide

**Last updated:** May 2026 (v2.26.0)
**Live site:** https://am-parliament.org

This guide covers how to use Parliament's admin and officer features. It is written for chapter officers and administrators — no technical background required.

---

## Table of Contents

1. [Admin v2 Dashboard](#admin-v2-dashboard)
2. [Member Management](#member-management)
3. [Events & Attendance](#events--attendance)
4. [Chapter Minutes](#chapter-minutes)
5. [Committees](#committees)
6. [Legislation & Voting](#legislation--voting)
7. [Slating & Elections](#slating--elections)
8. [Announcements](#announcements)
9. [Documents](#documents)
10. [Service Hours](#service-hours)
11. [KAI Reports](#kai-reports)
12. [Push Notifications](#push-notifications)
13. [Feature Flags & Page Toggles](#feature-flags--page-toggles)
14. [Security Tools](#security-tools)
15. [Activity Log](#activity-log)

---

## Admin v2 Dashboard

**Access:** `/admin-v2/` — Officers and admins only.

This is Parliament's main administration panel. From here you can:

- View site-wide stats (active members, recent activity, etc.)
- Manage feature flags and page toggles
- Review the activity log
- Access security tools (login history, flagged accounts, IP lists)
- Send push notifications
- Manage user sessions

> **Django Admin** (`/django-admin/`) is also available but is intended for direct database access in emergencies, not routine use. Use Admin v2 for day-to-day admin tasks.

---

## Member Management

### Member types

| Type | Description |
|------|-------------|
| `Officer` | Full site access, all officer tools |
| `Chair` | Committee chair-level access |
| `Member` | Standard member |
| `Pledge` | New member (limited access) |
| `Advisor` | Chapter advisor |
| `Alumni` | Inactive/graduated member |

### Member status

- **Active** — counts in attendance, visible in directory, can log in
- **Inactive** — hidden from most views, cannot log in

### Adding a new member

New members are added through Django Admin (`/django-admin/` → ParliamentUser → Add). Set:
- `user_id` — their member ID (e.g., `A1234`) — this is their username
- `name` — full name
- `member_type` — see table above
- `member_status` — Active
- Password — use "Reset Password" or the `reset_user_password` management command

### Changing a member's role

Go to Django Admin → ParliamentUser → find the member → update `member_type`. Role History entries are tracked automatically.

### Login as another user (for debugging)

Officers can temporarily log in as any member to troubleshoot issues. Go to the member's profile in Admin v2 or use `/staff/login-as/<user_id>/`. An amber banner appears at the top of every page as a reminder. All actions taken while impersonating are logged. Use the "Return to [your name]" button to go back.

---

## Events & Attendance

### Creating an event

1. Go to **Calendar** → **Create Event** (officers only)
2. Fill in title, date/time, location, and description
3. Set **Visible to** — leave blank for all members, or restrict to specific member types
4. Set **Requires attendance** — check this for chapter meetings and anything tracked
5. Set **Allow excuses** and optionally set an **excuse deadline**
6. Optionally link to a recurring series

### Taking attendance

Attendance can be taken from multiple places:

- **Attendance page** (`/attendance/`) — the primary officer attendance page; shows all active members with toggle buttons
- **Chapter Minutes editor** — the Attendance section in the minutes editor; saves to the same `Attendance` records
- **Vote page** — the "Attendance" tab in the officer panel on the vote page; lets you mark attendance without navigating away from a live vote

All three sync to the same underlying records. Marking someone present on one will reflect in the others.

### Attendance statuses

| Status | Meaning |
|--------|---------|
| Present | Attended |
| Late | Arrived late but attended |
| Absent | Did not attend |
| Excused | Approved excuse on file |
| Pending | Not yet marked |

### Excuse requests

Members can submit excuse requests from **My Excuses** (accessible from the member dashboard). Officers review and approve/deny from **Attendance** → **Excuse Requests**.

When an excuse is approved, the member's attendance record for that event is automatically updated to **Excused** — unless they were already marked **Present** or **Late** (approved excuse won't overwrite confirmed attendance).

### Finalizing attendance

After taking attendance for an event, mark it as finalized in the event admin. Finalized events lock further changes and prevent new excuse submissions.

### Attendance dashboard

Officers can view chapter-wide attendance trends at `/attendance/dashboard/`. Shows:
- Per-member attendance rates
- Monthly trend bars
- At-risk members (below threshold)
- Worst-to-best sorted member table

---

## Chapter Minutes

### Creating minutes

Go to **Chapter Minutes** → **Create Minutes**. Set:
- Title and date
- Start time (called to order)
- Linked event (optional — links attendance in minutes to the event's attendance records)

### Editing minutes

The minutes editor has three main sections:

**Attendance**
- Mark each member present/late/absent/excused using the radio buttons
- Click **Save Attendance** when done — this saves separately from the minutes content
- If the minutes are linked to an event, attendance here syncs to the event's records
- When you **Publish**, attendance is saved automatically before the PDF is generated

**Meeting Minutes (text)**
- Type notes directly in the text area
- Place your cursor where you want to insert a motion or section header, then use the insert buttons
- **Motion / Vote** — opens a form to record a formal motion with author, type, result, vote counts, and caucus details
- **Section Header** — adds a named section (e.g., "President's Report", "Old Business") with quick presets
- **End Section** — closes the current section

Supported Markdown formatting in the text area (reflected in the PDF):
- `**bold**`, `*italic*`, `***bold italic***`
- `~~strikethrough~~`, `` `inline code` ``
- `# Heading 1`, `## Heading 2`, `### Heading 3`
- `- bullet` or `1. numbered list`
- `> blockquote`
- `---` horizontal rule
- ` ``` ` fenced code block

**Adjourn Meeting**
- Click to set the adjournment time to now, or type it manually in the header time field

### Saving vs. publishing

- **Save Draft** — saves the minutes content (text + motions + sections). Does not affect who can see it.
- **Save Attendance** — saves attendance separately. Required before publishing if you want attendance in the PDF.
- **Publish** — generates a PDF and makes the minutes visible to members. Attendance is automatically saved before the PDF is generated. Choose visibility (all members or officers only).

### Editing after publishing

You can still edit published minutes. Changes are tracked — you'll be prompted for an edit reason. The published PDF is automatically regenerated on save. An "Edited" badge appears in the header and the edit history is recorded in the PDF footer.

### PDF preview and download

Use **Preview PDF** or **Download PDF** in the header to view/download the current PDF. This uses the current saved state — save your work before previewing.

---

## Committees

### Committee structure

Each committee has:
- A chair (manages the committee)
- Members with various roles (voting member, non-voting member, advisor)
- A document folder for agendas, minutes, reports
- A chat channel (if enabled)

### Committee minutes

Committee minutes work the same as chapter minutes — same editor, same attendance sync, same PDF generation. They can optionally be published to both the committee documents folder and the chapter documents page.

### Pushing legislation to chapter

Committee legislation that passes internally can be pushed to a chapter-wide vote. Go to the committee legislation item → **Push to Chapter Vote**.

---

## Legislation & Voting

### Creating legislation

Go to **Legislation** → **Create** (members can author; officers can also create on behalf of others). Fill in:
- Title and body text
- Vote type: **Percentage** (yes/no threshold), **Piecewise** (exact count), or **Plurality** (choose one of multiple options)
- Threshold (if percentage)
- Whether voting is anonymous

### Opening a vote

Go to the legislation detail page → **Open Vote**. Set a deadline (or leave open-ended). The vote page (`/vote/`) shows all open legislation with live tallies visible to the author.

Members can vote from the legislation detail page or from the **Vote** page during a chapter meeting. Officers running a live vote can use the Vote page's officer panel to:
- Track who has voted
- See live counts
- Take attendance (Attendance tab)
- Close the vote manually

### Closing a vote

Votes auto-close when their deadline passes (Celery beat handles this). Officers can also manually close from the Vote page. Passed legislation moves to the Passed Legislation archive.

---

## Slating & Elections

Slating is the officer election system. It is fully configurable — no code changes needed.

### Overview

A **Slating Period** defines an election cycle. Within it:
- Positions are listed with eligibility requirements (GPA, member type, etc.)
- Members apply for positions via a form
- A committee reviews applications and can schedule interviews
- A secret ballot vote is held (60% threshold, up to 3 attempts)
- Incoming officers go through a transition workflow with outgoing officers

### Setting up a slating period

Go to **Admin v2** → **Slating** → **Create Slating Period**. Configure:
- Open/close dates for applications
- Positions available
- Eligibility rules per position
- Interview schedule (optional)

### Application review

Go to **Slating** → the active period → **Applications**. You can view, sort, and annotate applications. Interview notes are confidential and can be marked for destruction after the election.

### Running the vote

Once applications close, go to **Slating** → **Start Vote**. Members vote through the normal Legislation/Vote system. Results trigger the transition workflow.

### Transition workflow

After a vote, outgoing and incoming officers complete a structured handoff in **Slating** → **Transitions**. Each officer pair has a checklist and messaging thread.

---

## Announcements

### Creating an announcement

**Announcements** → **Create Announcement** (officers only). Options:
- Title, body, expiration date
- **Pinned** — keeps announcement at the top
- **Linked documents** — attach any chapter-published documents; members see them as file links
- **Poll / Survey** — see below

### Polls and surveys

After creating an announcement, click **+ Add Poll** from the officer announcement list. Build a poll with:
- Text questions, single-choice, or multiple-choice questions
- Anonymous mode (responses not linked to users)
- Close date

Members respond from the announcement. Officers view results from **Poll Results** — includes bar charts per question, individual responses (if not anonymous), "who hasn't responded" list, and CSV export.

### Scheduling announcements

Set a **Send at** time to schedule an announcement to go out in the future. The `process_scheduled_announcements` management command handles delivery (runs on a schedule automatically).

---

## Documents

### Chapter documents

Officers can upload documents at **Documents** → **Upload**. Documents are organized into folders. Set visibility (all members, officers only, etc.).

### Document versions

Uploading a new version of an existing document creates a version history accessible from the document detail page. Members always see the latest version; older versions are archived.

### Committee documents

Each committee has its own document folder. Committee chairs manage uploads. Documents can optionally be published to the chapter folder as well.

---

## Service Hours

### For members

Members submit hours at **Service Hours** → **Submit**. Fill in:
- Activity description
- Date
- Hours
- Any custom fields the VPP has configured

### For officers (VPP)

The VPP officer dashboard at **Service Hours** → **Officer Dashboard** shows:
- All pending submissions
- Approve, reject, or request changes with a note
- Bulk approve/reject
- Set period requirements
- Override individual member requirements

---

## KAI Reports

KAI (Key Area Indicator) reports are a member reporting system.

- Members submit reports via **KAI** → **Submit Report**
- Officers view and manage reports from the **KAI Dashboard**
- Report templates define the fields (configured by officers)
- Closure requests allow members to formally close out a report period

---

## Push Notifications

Officers can send push notifications to members who have enabled them (members opt in from their preferences page).

**Send a notification:** Admin v2 → **Push Notifications** → **Send Notification**. Select recipients (all members, specific member types, or individuals) and write the message.

Members who haven't opted in will not receive the notification. Members can manage their notification preferences from **Profile** → **Preferences**.

---

## Feature Flags & Page Toggles

### Feature flags

Feature flags enable or disable entire sections of the site. Managed in **Admin v2** → **Feature Flags**.

| Flag | What it gates |
|------|--------------|
| `chats` | All chat functionality |
| `announcements` | Announcements page |
| `legislation` | Legislation and voting |
| `slating` | Officer slating and elections |
| `service_hours` | Service hours submission |
| `house_map` | House map |

> **Note:** Some flags appear in the list but toggling them has no current effect (`attendance_tracking`, `calendar_subscriptions`, `global_search`, `kai_reports`). A future developer should either enforce them or remove them.

### Page toggles

Page toggles enable or disable individual pages/URLs. Managed in **Admin v2** → **Page Toggles**. If a member hits a disabled page, they see a "Coming Soon" or "Unavailable" message.

---

## Security Tools

Accessible at **Admin v2** → **Security**. (Officers/admins only.)

### Login history

View all recent login attempts — successful and failed — with IP address, device, and timestamp. Useful for spotting unauthorized access attempts.

### Flagged accounts

Members can be flagged for review (e.g., suspicious activity). Flagged accounts can be suspended from this panel.

### IP blocklist

Add IP addresses to the blocklist to prevent them from accessing the site. Useful after a targeted attack. The blocklist is checked before authentication.

### IP allowlist

Restrict access to specific IPs (e.g., campus network only). Use with caution — this can lock out legitimate users if misconfigured.

### Honeypot

The site has a honeypot at a fake admin URL. Any access to this URL is logged and alerts officers. Access the honeypot log from the Security section.

### System lockdown

**Admin v2** → **Security** → **System Lockdown** — locks the entire site to admins only. Use only in emergencies (active attack, data breach, etc.). Unlock from the same panel.

---

## Activity Log

The activity log records every significant action on the site: logins, legislation changes, attendance marks, document uploads, admin actions, and more.

**Access:** Admin v2 → **Activity Log**

You can filter by:
- Action type (login, edit, delete, etc.)
- User
- Date range
- Object type (Legislation, Attendance, etc.)

Old log entries are automatically pruned by the `prune_activity_logs` management command (general logs kept 365 days; auth/user logs kept 730 days by default).

---

*For technical/developer information, see [HANDOFF_DEVELOPER.md](HANDOFF_DEVELOPER.md).*
