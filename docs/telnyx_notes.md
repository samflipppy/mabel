# Telnyx notes

The same ledger `xai_notes.md` keeps, for the same reason. What is written down
here as VERIFIED has been confirmed against Telnyx's published documentation or
against a payload we have actually received. Everything else is an ASSUMPTION,
is marked as one in the code, and is listed below so it can be checked in ten
minutes once the account exists (BLOCKED #4).

Nothing here was guessed at silently. If a field name below turns out to be
wrong, the code fails closed — a missed-call text that does not send is a
feature that is not working, and a missed-call text sent to the wrong number is
a stranger receiving a message from a business they never called.

---

## VERIFIED

**Webhook signature.** Ed25519 over `{timestamp}|{raw_body}`, headers
`telnyx-signature-ed25519` and `telnyx-timestamp`. Implemented in
`packages/telnyx/webhooks.py` and tested there.

**Webhook envelope.** `{"data": {"id", "event_type", "occurred_at",
"payload": {...}}}`. The `id` is what `webhook_receipts` deduplicates on.

**Inbound message payload.** `payload.from.phone_number`, `payload.to` as a
**list** of `{phone_number, status}`, `payload.text`. The list is why
`_to_number` takes the first entry — an MMS can have several recipients and
ours never do.

**Delivery receipts.** `payload.to[0].status`, one of `queued`, `sending`,
`sent`, `delivered`, `delivery_failed`, `delivery_unconfirmed`. Handled in
`/webhooks/telnyx/status`.

**10DLC.** Messages sent under an unregistered campaign are accepted by the API
and dropped by the carrier. There is no error on the send path; the delivery
receipt is the only signal. This is why `tenants.customer_sms_enabled` defaults
to false.

---

## ASSUMPTIONS

Each is marked `ASSUMPTION (T-n)` at the point in the code that relies on it.

**T-1 — Call Control event names.** `call.initiated`, `call.answered`,
`call.hangup`. Used by `webhooks/telnyx_calls.py` to decide whether a call was
ever picked up.

*Why it matters:* the missed-call text is sent on a hangup with no preceding
answer. If `call.answered` is named something else, every answered call also
looks missed, and every caller who spoke to Mabel for four minutes gets a text
saying sorry we missed you. The handler therefore requires a *positive*
identification of a missed call rather than treating "not answered" as the
default — see `_was_missed`.

**T-2 — `payload.direction`.** Expected `"incoming"` for a call to our DID.
Used to make sure we never text someone because of an outbound call.

*Why it matters:* without a direction check, a callback the owner placed
through us would text the customer "sorry we missed your call".

**T-3 — `payload.hangup_cause`.** Expected to include `originator_cancel` when
the caller hung up before anything answered. Used only to log why; no branch
depends on the exact value.

**T-4 — `payload.call_leg_id` / `call_session_id` stability.** Assumed stable
across the events of one call, so `call.answered` for a leg can be correlated
with `call.hangup` for the same leg.

*Why it matters:* correlation is what makes T-1's positive check possible. If
these do not correlate, the feature must be switched off rather than guessed
at, which is what an empty correlation produces — no text.

---

## How to check these

Once the account exists, point a Telnyx Call Control application at
`/webhooks/telnyx/call` and place two calls: one answered, one cancelled while
ringing. The handler logs every event type and payload key it receives at INFO
under `mabel_api.webhooks.telnyx_calls`. Compare against T-1 to T-4, move what
holds up into VERIFIED, and fix what does not.
