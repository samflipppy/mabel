"""Job handlers, one per `job_queue.kind`.

Every one has the same signature — `run(job, engine)` — and every one is
registered in `runner.build_registry()`. A kind with no handler fails
immediately rather than retrying, because retrying a missing handler burns the
attempts and hides the real problem.

None of these send anything. They compose and queue; `send_notification`
delivers. That split keeps a composition bug and a delivery outage
distinguishable in the notifications table, which is what somebody needs at 7am
when the text did not arrive.
"""
