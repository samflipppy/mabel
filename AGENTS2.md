# AGENTS.md

Mabel answers the phone after hours for home service contractors. A homeowner
calls, Mabel qualifies the call, writes a lead, and texts the owner if it's an
emergency.

Read this before writing anything. Violating an invariant is a failed review
regardless of how good the rest of the change is.

---

## Runtime

- Python 3.12, `uv` for dependency management
- Postgres 16+ (managed, with PITR — not Fly unmanaged)
- FastAPI, Pydantic v2, SQLAlchemy 2.0 (Core, not ORM, for tenant-scoped queries)
- Deployed on Fly.io, one app, multiple processes

## Commands

```
uv sync                      # install
uv run pytest                # all tests
uv run pytest tests/golden   # rule library fixtures
uv run ruff check .          # lint
uv run ruff format .         # format
uv run pyright               # types
uv run alembic upgrade head  # migrations (Sam runs these, not you)
```

---

## Invariants

**1. Every tenant-scoped table has `tenant_id` and an RLS policy with FORCE.**

No exceptions. A new table without a policy is a cross-tenant leak waiting to
happen. `ENABLE ROW LEVEL SECURITY` alone is insufficient — the table owner
bypasses it. Always `FORCE`.

**2. Tenant context is set with `SET LOCAL`, never `SET`.**

`SET` persists on a pooled connection and leaks to the next request. Every
tenant-scoped query runs inside an explicit transaction that begins with
`SET LOCAL app.tenant_id`. Use `packages/db/tenant.py::tenant_scope()`. Never
write raw session code that skips it.

**3. Tenant is resolved server-side from the dialed number. Never from a model.**

The voice agent may pass anything. It is not trusted. `tenant_id` comes from
the `To` number in the SIP headers, looked up in `tenants.did_e164`, before any
session opens.

**4. No LLM output ever becomes a dollar figure or a quantity.**

Money is computed by deterministic code reading NUMERIC columns. Mabel may
discuss a job. She may never quote one. Owner-entered job values are the only
dollar amounts in the system, and they are entered by a human via SMS.

**5. Money is integer cents in BIGINT, with an explicit currency column.**

Never float. Never NUMERIC for Stripe-facing amounts — Stripe speaks cents.

**6. All timestamps are `timestamptz` in UTC. Tenant-local time is computed.**

Each tenant carries an IANA timezone. "After hours" is a computed property, not
a stored one. Never hardcode `America/New_York` even though every current
customer is in it.

**7. Every transcript and recording is copied to our own storage post-call.**

xAI retention is short and not fully documented. The call ends, we copy it. Do
not build anything that reads from xAI storage at query time.

**8. Webhooks are verified against the raw request body and are idempotent.**

Re-serializing JSON breaks the signature. Use `webhook-id` as an idempotency
key, held 10 minutes. Reject timestamps older than 300 seconds.

**9. Every change to `packages/verticals/` ships with a fixture.**

A rule change without a test is not a rule change, it's a guess.

---

## Boundaries for agents

You never:
- merge to `main` — open a PR on a branch, Sam merges
- deploy
- run a migration
- read, write, or reference production credentials
- touch `infra/` production configs
- commit anything matching `.env*`
- implement `apps/api/media/session.py` — that's Sam's, leave the TODOs

If a task appears to require any of the above, stop and ask.

## Module rules

- `packages/verticals/` and `packages/domain/` are **pure**. No I/O, no DB, no
  network. They are the most-tested code in the repo because they're the
  easiest to test.
- Nothing imports upward. `packages/` never imports from `apps/`.
- All Postgres access goes through `packages/db/`. No stray connections.
- All xAI API assumptions live in `packages/xai/client.py`. One file, one place
  to be wrong. See `docs/xai_notes.md` for what's verified vs. assumed — the
  API is sparsely documented and you will hallucinate against it if you guess.

## PR sizing

Small enough to review in ten minutes. If a change touches more than about
five files, split it.
