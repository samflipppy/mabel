# Mabel vertical rules

This is the moat. Each trade gets a versioned JSON rule set: what counts as an emergency, what Mabel has to collect, and what she must never say. An LLM does not decide any of that.

Plumbing v3 is verified. HVAC, electrical, and restoration are drafts (`verified: false`). Sam verifies a draft before it goes live. Never take an agent live from a bot.

## Layout

```
packages/verticals/
  plumbing/v3.json
  hvac/v1.json
  electrical/v1.json
  restoration/v1.json
  fixtures/                 # one JSON file per call scenario
  schema.json
  mabel_verticals/          # deterministic matcher, no model
```

## How fixtures run

A fixture is an input call scenario and the outcome the rules must produce:

- `expect.trigger` — emergency code, or `null`
- `expect.escalate` — text the owner now, or wait
- `expect.notify` — `now` or `recap_7am`
- `expect.capture_gaps` — required fields still empty

Matching is phrase matching against the caller's words plus the captured problem. Conditions like outdoor temperature live on the trigger as `require`. Nothing here quotes a price.

From this folder:

```bash
python -m pip install -e ".[dev]"
python -m pytest
python -m mabel_verticals
```

Every rule file must list at least one fixture. A rule change without a fixture is a failed review.

`plumbing_burst_pipe.json` is a burst pipe: escalate now. `plumbing_slow_drain_2am.json` is a slow drain at 2am: no escalate, 7am recap.
