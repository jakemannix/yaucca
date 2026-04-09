# Design: Scheduled Tasks + Notification Bus

**Priority**: Medium
**Status**: Proposed

## Problem

yaucca is currently passive — it only speaks when spoken to. There's no
way to:
- Get a daily briefing of overdue items without starting a session
- Receive reminders at specific times
- Be nudged about approaching deadlines

## Goals

1. Daily overdue triage that runs automatically
2. Outbound notifications via Bluesky DM (and potentially other channels)
3. Scheduled tasks that survive across sessions
4. Eventually: two-way communication (respond to DMs to update tasks)

## Design

### Scheduled Task Runner

Add a lightweight cron-like scheduler to the Modal deployment.

```python
@modal.Cron("0 8 * * *")  # 8am UTC daily
def daily_triage():
    """Query overdue items and send notification."""
    overdue = db.get_overdue_passages(before=date.today())
    upcoming = db.get_upcoming_passages(within_days=3)
    if overdue or upcoming:
        message = render_daily_briefing(overdue, upcoming)
        send_notification(message)
```

Modal supports `@modal.Cron` natively — no external scheduler needed.
Runs on the same container with access to the same SQLite volume.

### Notification Channels

#### Bluesky DM (primary)

Create a yaucca service account on Bluesky. Use AT Protocol to send DMs.

```
pip install atproto
```

```python
from atproto import Client

def send_bluesky_dm(recipient_did: str, text: str):
    client = Client()
    client.login(YAUCCA_BSKY_HANDLE, YAUCCA_BSKY_PASSWORD)
    # AT Protocol chat/DM API
    client.chat.send_message(recipient_did, text)
```

**Secrets needed:**
- `YAUCCA_BSKY_HANDLE` — e.g. `yaucca-bot.bsky.social`
- `YAUCCA_BSKY_PASSWORD` — app password
- `YAUCCA_BSKY_RECIPIENT_DID` — Jake's DID

#### Future channels
- Email (via SendGrid/SES — simple, reliable)
- SMS (via Twilio — for urgent items)
- Slack webhook
- Push notification (if yaucca ever gets a mobile app)

### Notification Types

| Type | Trigger | Channel | Example |
|------|---------|---------|---------|
| Daily briefing | 8am cron | Bluesky DM | "3 overdue items, 2 due today" |
| Deadline approaching | 24h before due | Bluesky DM | "Rental car booking due tomorrow" |
| Reminder | User-set time | Bluesky DM | "Remember to call Pete" |
| Weekly review | Sunday 6pm cron | Bluesky DM | "12 open @next items, 5 overdue" |

### Two-Way Communication (v2)

Once yaucca has a Bluesky account, it could also:
- Monitor its own DM inbox
- Parse simple commands: "done: rental car", "add: buy milk"
- Update passages via the existing API
- This turns Bluesky into a mobile GTD input channel

### Configuration

Add to `.env`:
```
YAUCCA_NOTIFY_CHANNEL=bluesky  # or email, slack, none
YAUCCA_BSKY_HANDLE=yaucca-bot.bsky.social
YAUCCA_BSKY_PASSWORD=xxxx
YAUCCA_BSKY_RECIPIENT_DID=did:plc:xxxxx
YAUCCA_NOTIFY_DAILY_HOUR=8    # local hour for daily briefing
YAUCCA_NOTIFY_TIMEZONE=America/Los_Angeles
```

## Implementation Plan

1. Create Bluesky account for yaucca
2. Add `atproto` to deploy dependencies
3. Implement `send_bluesky_dm()` utility
4. Add overdue/upcoming query helpers to db.py
5. Create `daily_triage()` Modal cron function
6. Add notification config to settings
7. Test with a real daily briefing cycle
8. v2: DM inbox monitoring for two-way commands

## Integration with Context-Aware SessionStart

The daily triage cron and the SessionStart overdue injection share the
same queries. Factor out the overdue/upcoming query logic into `db.py`
so both the cron and the hook can use it.

```
db.py
  get_overdue_passages(before: date) -> list[Passage]
  get_upcoming_passages(within_days: int) -> list[Passage]

hooks.py (SessionStart)
  uses get_overdue_passages + get_upcoming_passages

notifications.py (cron)
  uses get_overdue_passages + get_upcoming_passages
```
