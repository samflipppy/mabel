# Mabel v2 — Client Portal

`app.hiremabel.com` — Next.js 15, Supabase Auth, Tailwind + shadcn/ui.

The portal is what makes this worth $299 instead of $99. The owner may never
open it; the office manager lives in it, and the owner opens it when he wants
to see the money.

**Design constraints:** 16px minimum body text. WCAG AAA contrast (7:1) —
readable on a phone in sunlight. 48px touch targets. Every dollar figure is
large and unmissable. Nothing behind more than two clicks.

---

## Navigation

```
Dashboard · Calls · Leads · Customers · Mabel · Reports · Settings
```

Seven items. Not a mega-menu.

---

## 1. Dashboard

Landing screen. Answers "what happened and what needs me."

**Top strip — four cards, big numbers:**

| Calls answered | Leads captured | Emergencies | Value won |
|---|---|---|---|
| 23 this month | 14 | 3 | $14,600 |

Each card shows a sparkline and a delta vs. last month.

**Needs you** — the action list, above everything else:
- Leads with no callback in 24h, sorted by age
- Emergencies from the last 48h and whether they were answered
- Any call flagged in QA

Each row: name, phone (tap to call), what they want, how long it's been sitting.

**Recent calls** — last 10, with a play button inline.

**This week** — bar chart, calls by day, after-hours vs. business-hours split.

Empty state: *"Mabel's live and listening. Calls will show up here."*

---

## 2. Calls

Full call log. The transcript archive is a real retention feature — contractors
have never had one.

**List view:** date/time, caller, number, duration, outcome badge, QA flag if
any, play button.

**Filters:** date range, outcome, emergency only, flagged only, location.
**Search:** full-text across transcripts. `to_tsvector` index is already in the
schema — this is the feature nobody else offers. "Search for that guy who
called about the water heater" and it finds him.

**Call detail:**
- Waveform player with the transcript scrolling alongside, click a line to jump
- Speaker-labeled turns, timestamps
- Structured extraction panel: name, address, phone, job type, urgency, source
- Tool trace — what Mabel actually did during the call (created lead, texted
  owner). This builds trust; they can see the machine working.
- Actions: create lead, link to existing customer, mark as spam, flag for review
- Download recording, download transcript

---

## 3. Leads

**Board view** (default): New → Contacted → Estimate scheduled → Estimate sent →
Won / Lost. Drag on desktop, tap-to-advance on mobile.

Card shows: name, job type, value if entered, days in stage, red dot if
untouched over 24h.

**Table view** for people who prefer it. Sortable, exportable to CSV.

**Lead detail:**
- Contact block: name, address (with map link), phone (tap to call, tap to text)
- Job: type, description, urgency, source
- **Value field** — labeled *"What's this job worth?"*. Owner-entered. This is
  the number that drives every report, so make it prominent and easy.
- Status dropdown
- The full communication thread (below)
- Notes

---

## 4. Customers

Contact list with the **unified thread** — the thing that turns this from an
answering service into a system of record.

**Thread view** for one contact, chronological:
- Every call, with transcript and recording inline
- Every SMS both directions
- Every email if the integration is connected
- Estimates, photos, notes, status changes

**Pinned at top: open items.** *"Asked about a color change Apr 18 — no reply."*
This is the dropped-ball surfacing, and it's the feature owners will talk about.

**Merge handling:** when identity resolution flags a possible duplicate, a
banner appears — *"This might be the same person as Dana R. (216-555-0148)"* —
with Merge and Not the same. Merges are recorded as events and reversible.

---

## 5. Mabel — the configuration screen

This is the differentiator. Everyone else makes you email support to change
your hours.

**Tabs: Voice · Hours · Services · Emergencies · Knowledge · Team · Test**

### Voice
- Greeting text, live character count, with a preview player
- Voice picker with audio samples
- Speaking rate slider
- Keyterms — street names, neighborhoods, brand names she should recognize.
  Prefilled from their service area.

### Hours
- Weekly grid, open/close per day
- After-hours only vs. also-when-busy toggle
- Timezone (defaults from their address)
- Holiday overrides

### Services
- What they do — chips, add/remove
- What they explicitly don't do (so Mabel declines cleanly)
- Service area: ZIP multiselect with a map preview
- Out-of-area response text

### Emergencies
- The trade ruleset shown as plain-English toggles:
  *"Burst pipe or active flooding → wake me"*
  *"No hot water → next morning is fine"*
- Custom rules in free text
- Who gets the emergency text, and the on-call rotation editor
- Quiet hours override — *"never text me between 1am and 5am for anything but
  these:"*

### Knowledge
Q&A pairs Mabel can answer. *"Do you do drywall repair?"* → *"Yes, as part of
a painting job."* Sortable, toggleable. Import from their website on onboarding.

### Team
Users, roles, who gets what notification. Invite by email.

### Test
**Big button: "Call Mabel now."** Places a call to the user's phone so they can
hear their own configuration. This single feature will close deals and prevent
support tickets.

Below it: a change log. Every config edit, who made it, when, with a Revert
button. Config is versioned in the schema — use it.

---

## 6. Reports

**Monthly report** — the retention artifact, rendered in-app and as PDF:

> **October 2026**
>
> **Mabel answered 23 calls after hours.** Before Mabel, those went to voicemail.
>
> **14 became leads. You marked 5 won: $14,600.**
>
> Emergencies handled: 3
> Average response time on emergencies: 6 minutes
>
> **Where they came from:** Google 11 · Referral 6 · Truck 3 · Repeat 3
>
> **Still waiting on you:** 2 leads, oldest 9 days
>
> You paid $299. Five won jobs came to $14,600.

Slow months say so. A report that's always good news gets ignored.

**Usage** — minutes used vs. included, calls per day, cost trend. Transparent,
because surprise overage bills are how answering services lose customers.

**Lead sources** — twelve months of where calls come from. Most contractors
have never had this data and it's genuinely useful to them.

**Export** — CSV of calls, leads, or the full thread. Their data, downloadable.

---

## 7. Settings

**Account** — business name, address, timezone, logo.
**Phone** — their Mabel number, forwarding instructions with their carrier's
exact codes, and a **forwarding health indicator** (green if calls have arrived
in the last 7 days, amber if quiet, red if silent). This catches the
silent-failure churn.
**Notifications** — per-user, per-channel, quiet hours.
**Integrations** — Google Calendar, Jobber, Housecall Pro, outbound webhook.
Connect/disconnect, last sync, error state.
**Billing** — plan, next invoice, payment method, invoice history, upgrade or
downgrade, cancel. Stripe customer portal embedded.
**Data** — export everything, retention setting, delete account.

---

## Onboarding wizard

Six steps, target under 15 minutes. Shown on first login.

1. **Business** — name, trade, address, timezone (mostly prefilled from what
   Sam entered during the sale)
2. **Hours** — when they're closed
3. **Services & area** — what they do, ZIPs
4. **Emergencies** — trade ruleset preselected, they adjust
5. **Who to text** — owner's cell, confirmed by a test text
6. **Forward your phone** — their carrier's exact codes, with a **live
   verification**: "Call your business line now. We'll tell you when it reaches
   Mabel." Green check when the test call lands.

That last step is the whole onboarding. Everything else is prefilled.

---

## Mobile

The portal is responsive, not a separate app. Priorities on small screens:
Dashboard needs-you list, call playback, tap-to-call, and lead status changes.
Configuration is desktop-first — nobody edits emergency rules on a phone.
