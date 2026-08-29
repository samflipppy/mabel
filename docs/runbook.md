# Runbook

What to do at 3am. Written for whoever is holding the pager, which for now is
Sam.

The organising principle: **a Mabel outage should leave the contractor where he
was before Mabel, not worse off.** If she cannot answer, the call falls through
to his carrier's voicemail. Almost nothing here is an emergency in the sense of
"fix it now" — most of it is "confirm it is failing safe, then fix it in the
morning".

---

## Triage: is anybody actually affected?

Three questions, in order.

1. **Are calls being answered?** `GET /health/ready` on the `web` process, and
   the `calls` table: `SELECT max(started_at) FROM calls` as `mabel_admin`. A
   gap of hours overnight is normal for one tenant and not for all of them.
2. **Is the queue moving?** `mabel_worker.queue.depth()`. A rising `ready`
   count with a healthy worker means jobs are failing and retrying; a rising
   count with `in_flight` at zero means nothing is running.
3. **Is anything queued that should have been sent?**
   `SELECT count(*) FROM notifications WHERE status='queued' AND kind='emergency'`.
   That is the only query on this page where the answer being non-zero means
   somebody is not getting a call they should be getting.

If all three are fine, whatever woke you is not affecting a customer. Go back
to bed.

---

## The media process is down

**Impact:** calls fall through to carrier voicemail. The contractor is where he
was before Mabel.

This is the failure the system is designed around, and it is not a 3am problem.

- Do not deploy `media` to fix it unless you are certain. `media` deploys drop
  in-flight calls (04-REPO.md), which turns "some calls went to voicemail" into
  "somebody was cut off mid-sentence".
- Check the xAI concurrency ceiling first: ten sessions per team. `concurrency_state()`
  returns `alert` at seven. At the limit the eleventh caller does not reach her
  at all, and the fix is a support request to xAI, not a deploy.
- If it is a crash loop, roll back rather than forward.

---

## Emergency texts are not arriving

**Impact:** a contractor is not being woken for a burst pipe. This is the one
that matters.

Work down this list:

1. `GET /webhooks/telnyx/health` returns `delivery_risk()`.
   - `no_key` — no Telnyx credential. Nothing is being sent at all.
   - `unregistered` — **the dangerous one.** The API accepts messages, returns
     ids, and carriers drop them silently. Everything looks healthy. See
     docs/BLOCKED.md #4; this needs the 10DLC campaign, which has weeks of lead
     time.
   - `ok` — the credential and registration are fine, look further down.
2. `SELECT status, error, count(*) FROM notifications WHERE kind='emergency'
   AND created_at > now() - interval '1 day' GROUP BY 1,2`. `failed` rows carry
   the reason.
3. Check delivery receipts: `SELECT after FROM audit_log WHERE action =
   'sms_delivery_receipt' ORDER BY created_at DESC LIMIT 20`. A run of
   `delivery_failed` points at carrier filtering, which points back at 10DLC.
4. Check somebody is actually on call for that tenant: a user with
   `notify_emergencies` and a `phone_e164`. `enqueue_emergency` returns False
   when there is nobody, and Mabel says "someone will call you back" rather
   than implying a truck is moving — correct behaviour, but the owner needs
   telling.

---

## A tenant says Mabel stopped working

**Almost always call forwarding.** A carrier change, a new handset, or a wrong
code, and the calls stop reaching us. He will blame Mabel before he checks his
phone.

- Settings → the forwarding indicator. Red means no calls for over fourteen
  days.
- `SELECT max(started_at) FROM calls` for that tenant.
- The `silence-watch` cron catches this daily and texts the owner, but only for
  tenants with prior traffic. A tenant who never forwarded in the first place
  never trips it — check onboarding completed step six.

---

## The queue is backing up

- `mabel_worker.queue.depth()`.
- Jobs stuck `in_flight` with an old `locked_at` are an abandoned worker. They
  self-recover after the five-minute lease; if they are not recovering, no
  worker is running.
- `SELECT kind, last_error, count(*) FROM job_queue WHERE failed_at IS NOT NULL
  GROUP BY 1,2 ORDER BY 3 DESC` — failed jobs are kept, not deleted, precisely
  so this query works.
- A job kind with no handler fails immediately rather than retrying. If that is
  what you see, a cron entry was added without a handler.

---

## Cross-tenant data appears wrong

**Stop and escalate.** This is the one thing on this page that is genuinely
urgent, and it is not a "fix it and move on".

- Do not "fix" the data first. Capture what was seen.
- Every tenant-scoped query goes through `tenant_scope()`, which sets
  `SET LOCAL app.tenant_id`, and RLS is forced on every table. For data to
  cross tenants, either a policy is missing on a new table or something is
  connecting as a role with BYPASSRLS.
- Check: `SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class
  WHERE relkind='r'` — anything tenant-scoped with `relforcerowsecurity` false
  is the bug.
- Check: `SELECT rolname, rolbypassrls FROM pg_roles` — `mabel_app` must be
  false.
- `tests/isolation/` exists to catch exactly this. If it is passing and this is
  happening, the suite has a gap worth adding to before anything else.

---

## Billing looks wrong

- Nothing in Mabel computes a customer-facing figure except from integer cents.
  `plans.py` is the source of prices, not the Stripe object.
- A tenant stuck `past_due` after paying: the `invoice.payment_succeeded`
  webhook did not arrive or was rejected. Check
  `SELECT * FROM webhook_receipts WHERE source='stripe'` and Stripe's own
  webhook log.
- Stripe retries for three days; our idempotency window is ten minutes. Every
  handler is idempotent, so a late retry is harmless — but it means a duplicate
  event id in the log is not evidence of a problem.

---

## Deploys

- `web` and `worker` are safe to deploy any time.
- **`media` drops in-flight calls.** Deploy it in the small hours, and prefer
  not deploying it at all during a storm — that is when the emergency calls
  come.
- Migrations run via `release_command`. **Agents never run them and never
  deploy.** Draft, review, then Sam runs the SQL.

---

## Things that are deliberately not automated

Each of these is irreversible, and AGENTS.md says nothing irreversible happens
without a human. If you find yourself wanting to automate one, that is the
conversation to have first.

- **Deleting a recording.** `purge_recording` clears our pointer and records
  that the object still exists. The object removal is a separate human sweep.
- **Deleting an account.** Not a button in the portal. It removes a
  contractor's entire call history.
- **Releasing a DID.** A churned tenant keeps their number until somebody
  decides otherwise. Releasing it is not recoverable.
- **Taking an agent live.** Publishing a config is a human action, every time.
- **Merging two customers.** Suggested, never automatic, and reversible when
  done.

---

## Useful queries

All of these run as `mabel_admin`, which is the only role that sees across
tenants — and is never used by application code.

```sql
-- Who has gone quiet, and for how long
SELECT t.business_name, max(c.started_at), now() - max(c.started_at) AS quiet
FROM tenants t LEFT JOIN calls c ON c.tenant_id = t.id
WHERE t.status = 'active' GROUP BY 1 ORDER BY 3 DESC NULLS FIRST;

-- Calls flagged in QA and not yet looked at
SELECT t.business_name, c.started_at, c.qa_flags
FROM calls c JOIN tenants t ON t.id = c.tenant_id
WHERE array_length(c.qa_flags,1) > 0 AND c.qa_reviewed_at IS NULL
ORDER BY c.started_at DESC;

-- Emergencies where nobody was reached
SELECT t.business_name, l.created_at, l.caller_name
FROM leads l JOIN tenants t ON t.id = l.tenant_id
WHERE l.urgency = 'emergency'
  AND NOT EXISTS (SELECT 1 FROM notifications n
                  WHERE n.lead_id = l.id AND n.status IN ('sent','delivered'))
ORDER BY l.created_at DESC;

-- Integrations that have started failing
SELECT t.business_name, i.provider, i.status, i.last_error, i.last_synced_at
FROM integrations i JOIN tenants t ON t.id = i.tenant_id
WHERE i.status <> 'connected';
```

---

## What is not built yet

`docs/BLOCKED.md` is the authoritative list. The short version, so nobody
debugs a missing account for an hour:

- No Supabase project, so no database, no auth, no storage. Recordings are not
  archived; transcripts are.
- No Telnyx account, so nothing sends and nothing rings.
- No xAI key, so no call is ever joined.
- No Stripe, so billing screens say so and nothing is charged.
- No Google, Jobber, or Housecall accounts. Integrations report as not
  connectable.
- **`tests/isolation` has never run.** It needs a Postgres, and CI is currently
  manual-only. One `workflow_dispatch` run settles whether the cross-tenant
  guarantees hold.
