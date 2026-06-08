# Parliament REST API

The Parliament API provides read-only access to member directory, events, legislation, committees, and attendance data. It is intended for external integrations and future mobile clients.

**Base URL:** `https://am-parliament.org/api/v1/`

**Status:** The API is gated behind the `rest_api` feature flag and is **disabled by default**. An admin must enable the flag at `/officers/admin-v2/feature-flags/` before any endpoint responds.

---

## Authentication

All endpoints require authentication. The API uses token-based auth.

### Getting a Token

**Via the UI (recommended):**
Go to **Preferences → Developer API** and click **Generate Token**.

**Via the API directly:**
```
POST /api/v1/auth/token/
Content-Type: application/json

{ "username": "your_username", "password": "your_password" }
```

Response:
```json
{ "token": "9944b09199c62bcf9418ad846dd0e4bbdfc6ee4b" }
```

### Using a Token

Include the token in every request as a header:
```
Authorization: Token 9944b09199c62bcf9418ad846dd0e4bbdfc6ee4b
```

### Revoking a Token

Go to **Preferences → Developer API** and click **Revoke Token**, or:
```
POST /api/token/revoke/
Authorization: Token <your-token>
```

---

## Endpoints

### Members

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/members/` | All active members |
| `GET` | `/api/v1/members/{user_id}/` | Single member by user_id |
| `GET` | `/api/v1/members/me/` | The authenticated user's own record |

**Example response — `/api/v1/members/me/`:**
```json
{
    "user_id": "42",
    "name": "Adam Boggs",
    "preferred_name": "Adam",
    "display_name": "Adam Boggs",
    "member_type": "Member",
    "member_status": "Active",
    "roles": [{ "id": 3, "name": "Vice President of Brotherhood" }],
    "role_number": 42,
    "about_me": "Junior, Computer Science",
    "majors": ["Computer Science"],
    "minors": [],
    "concentrations": ["Cyber Security"],
    "pledge_class": "Fall 2023",
    "pledge_class_greek": "ΝΖ",
    "graduation_year": 2027,
    "graduation_semester": "Spring",
    "instagram": "adamboggs",
    "twitter": null,
    "profile_picture_url": "https://am-parliament.org/media/profile_pictures/42.jpg"
}
```

**Omitted fields:** email, phone_number, password hash, security flags, admin flags.

---

### Events

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/events/` | All events visible to the authenticated user |
| `GET` | `/api/v1/events/{id}/` | Single event (visibility enforced) |
| `GET` | `/api/v1/events/upcoming/` | Events in the next 30 days |

Visibility rules match the app: `visible_to` controls which member types can see each event.

**Example response — single event:**
```json
{
    "id": 17,
    "title": "Chapter Meeting",
    "description": "Weekly chapter meeting.",
    "date_time": "2026-06-10T19:00:00Z",
    "location": "Chapter House",
    "visible_to": "all",
    "is_recurring": true,
    "recurrence_type": "weekly",
    "recurrence_interval": 1,
    "recurrence_unit": "week",
    "recurrence_days": ["tuesday"],
    "created_by": "Mason Kimball",
    "created_at": "2026-01-05T12:00:00Z"
}
```

---

### Legislation

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/legislation/` | All non-removed legislation |
| `GET` | `/api/v1/legislation/{id}/` | Single item |
| `GET` | `/api/v1/legislation/active/` | Currently open for voting |

**Example response — single legislation item:**
```json
{
    "id": 88,
    "title": "Amendment to Standing Rules §4",
    "description": "Revises quorum threshold from 50% to 40%.",
    "status": "passed",
    "posted_by": "Mason Kimball",
    "co_authors": ["Adam Boggs"],
    "required_percentage": 66,
    "vote_mode": "percentage",
    "allow_abstain": true,
    "anonymous_vote": false,
    "available_at": "2026-05-01T19:00:00Z",
    "voting_starts_at": null,
    "voting_ends_at": null,
    "voting_ended_at": "2026-05-01T20:15:00Z",
    "voting_closed": true,
    "passed": true,
    "created_at": "2026-04-28T14:22:00Z"
}
```

---

### Committees

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/committees/` | All active, non-archived committees visible to the user |
| `GET` | `/api/v1/committees/{id}/` | Single committee (visibility enforced) |
| `GET` | `/api/v1/committees/mine/` | Committees the user is a member or chair of |

Slating committee visibility follows the same rules as the app — only members/chairs/admin can see it.

`chairs` and `members` fields are arrays of `user_id` strings.

**Example response:**
```json
{
    "id": 5,
    "code": "BROTHER",
    "name": "Brotherhood Committee",
    "is_active": true,
    "is_ad_hoc": false,
    "ad_hoc_expiration": null,
    "is_archived": false,
    "chairs": ["42"],
    "members": ["42", "17", "31", "55"],
    "created_at": "2025-09-01T00:00:00Z"
}
```

---

### Attendance

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/attendance/` | Own attendance records (most recent 100) |
| `GET` | `/api/v1/attendance/{id}/` | Single record (own only) |

**Query parameters:**

| Parameter | Values | Description |
|-----------|--------|-------------|
| `type` | `event`, `committee` | Filter by attendance type |
| `year` | e.g. `2026` | Filter by year |

Members can only retrieve their own attendance records. Officers do not get elevated access through this endpoint.

**Example response:**
```json
{
    "id": 204,
    "attendance_type": "event",
    "event": 17,
    "event_title": "Chapter Meeting",
    "committee": null,
    "committee_name": null,
    "status": "present",
    "date": "2026-06-03",
    "created_at": "2026-06-03T19:05:00Z",
    "notes": ""
}
```

---

## Error Responses

| Code | Meaning |
|------|---------|
| `401 Unauthorized` | Missing or invalid token |
| `403 Forbidden` | API feature flag is disabled, or access denied to the requested resource |
| `404 Not Found` | Resource does not exist |

When the `rest_api` feature flag is off, all `/api/v1/` endpoints return:
```json
{ "detail": "The Parliament API is not currently enabled." }
```

---

## Pagination

The DRF default router provides pagination on list endpoints. Default page size is 20. Use `?page=2` to paginate.

Attendance is capped at the most recent 100 records regardless of pagination.

---

## Rate Limiting

No API-specific rate limiting is currently configured beyond the app-level limits. Avoid polling — cache responses where possible.

---

## Enabling the API (admin only)

1. Log in as an admin
2. Go to **Admin V2 → Feature Flags**
3. Enable the **REST API** flag (`rest_api`)

To disable: toggle the flag off. All endpoints immediately return 403.
