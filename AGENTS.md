# AGENTS.md

Read this first. Always.

Mabel answers the phone when a contractor can't. That is the product. Not a CRM. Not field software.

Violating an invariant below is a failed review. Doesn't matter what else the PR does.

Brand voice governs code comments, commit messages, docs, and error copy. She's Mabel. Never "Mabel AI." Never "the Mabel platform." Call to action is Hire Mabel.

## Invariants

### 1. No LLM ever computes, transcribes, or generates a dollar figure or a quantity that reaches a customer.

Money comes from deterministic code reading NUMERIC columns. The voice agent may discuss a job. It may never quote one. The monthly report sums values the owner entered by hand. If an LLM output can reach a money field, the design is wrong.

### 2. Every query runs inside a transaction with `SET LOCAL app.tenant_id`.

RLS with a fail-safe deny. If the setting is unset, policies match zero rows. The app connects as a non-superuser role. A separate migrator role holds BYPASSRLS and is never used by application code.

### 3. Money is `NUMERIC(12,2)`. Never float. Ever.

Grep for float in any money path after every phase.

### 4. Archive every transcript and recording to our own storage immediately post-call.

xAI's retention window is short and not fully documented. Don't depend on it. The call ends, we copy it, done.

### 5. Tenant is resolved server-side from the inbound DID.

Never from anything the model passes. The MCP server mints a short-lived tenant-scoped token. Tool handlers filter by that token's tenant, not by an argument.

### 6. Nothing irreversible happens without a human.

No auto-deletes. No auto-refunds. No auto-porting numbers. No taking an agent live.

## Voice model

Pin `grok-voice-think-fast-2.0`. Never `grok-voice-latest`. That alias moved versions and silently changed the per-minute price.

## Architecture (locked 2026-08-29)

Each client gets their own xAI Voice Agent in console.x.ai. Reason: per-shop call logs, collections/docs, and MCP connections (Jobber, Google).

We do not click templates like Customer Support per shop. Onboard creates the agent from OUR template: she is Mabel, the opening disclosure, never quote a price, never invent an arrival, pin `grok-voice-think-fast-2.0`, no voice clone, no `web_search`/`x_search`, eight tools only.

Shop packet (hours, zips, owner SMS) still lives in our database. Tenant is still resolved from the inbound DID. Store `xai_voice_agent_id` on the tenant when we have it.

Collections may hold non-price shop docs. Reject dollar-looking uploads the same way greeting notes are rejected.

See [`docs/xai-voice.md`](docs/xai-voice.md).

## How we work

- PRs on branches. The CTO bot auto-merges every PR on this repo. Sam said 2026-08-29: auto merge every time, do not ask Sam to view or confirm.
- Never deploy from a PR. Never run migrations. Draft them. Sam runs SQL.
- Never hold tenant credentials: no Telnyx keys, no xAI API key, no Jobber tokens, no Stripe keys. Not in a file, not in an env example, not in a chat message. Fail closed without keys.
- Never take an agent live from a PR.
- Never touch production data. Read-only de-identified view only.
- Never modify the live call path without Sam's sign-off on that specific change.
- Every change to the vertical rule library requires a fixture. No exceptions.
