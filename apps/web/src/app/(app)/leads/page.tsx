"use client";

/**
 * The Leads board.
 *
 * Tap-to-advance rather than drag-and-drop. 02-PORTAL.md asks for drag on
 * desktop and tap on mobile; tap works on both, works with a keyboard, and
 * works one-handed on a roof — so it ships first and drag is an addition
 * rather than the thing everything else depends on.
 *
 * The value field is the one that matters. Every report is built from it, so
 * it is a real input on the card rather than something behind a detail view.
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError, type Board, type Lead, type LeadStatus, leads } from "@/lib/api";
import { formatMoneyOrDash, formatMoneyWhole } from "@/lib/money";

const STAGES: { key: LeadStatus; label: string }[] = [
  { key: "new", label: "New" },
  { key: "contacted", label: "Contacted" },
  { key: "estimate_scheduled", label: "Estimate scheduled" },
  { key: "estimate_sent", label: "Estimate sent" },
  { key: "won", label: "Won" },
  { key: "lost", label: "Lost" },
];

const NEXT_STAGE: Partial<Record<LeadStatus, LeadStatus>> = {
  new: "contacted",
  contacted: "estimate_scheduled",
  estimate_scheduled: "estimate_sent",
  estimate_sent: "won",
};

export default function LeadsPage() {
  const { data, isPending, error } = useQuery({
    queryKey: ["leads", "board"],
    queryFn: leads.board,
  });

  if (isPending) {
    return <div className="h-96 animate-pulse rounded-lg bg-[var(--cream-dark)]" />;
  }
  if (error) return <p className="text-base">Couldn&apos;t load the board.</p>;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-baseline justify-between gap-4">
        <h1 className="font-serif text-3xl">Leads</h1>
        <p className="text-base text-[var(--taupe)]">
          Won this board:{" "}
          <span className="font-serif text-2xl text-[var(--charcoal)]">
            {formatMoneyWhole(data.won_value_cents)}
          </span>
        </p>
      </div>

      <div className="grid gap-4 lg:grid-cols-3 xl:grid-cols-6">
        {STAGES.map((stage) => (
          <Column key={stage.key} stage={stage} board={data} />
        ))}
      </div>
    </div>
  );
}

function Column({
  stage,
  board,
}: {
  stage: { key: LeadStatus; label: string };
  board: Board;
}) {
  const items = board.stages[stage.key] ?? [];
  return (
    <section aria-labelledby={`stage-${stage.key}`} className="space-y-3">
      <h2
        id={`stage-${stage.key}`}
        className="flex items-baseline justify-between text-base font-medium"
      >
        {stage.label}
        <span className="text-[var(--taupe)]">{items.length}</span>
      </h2>
      {items.map((lead) => (
        <LeadCard key={lead.id} lead={lead} />
      ))}
      {items.length === 0 && (
        <p className="rounded-lg border border-dashed border-[var(--line)] px-3 py-6 text-center text-sm text-[var(--taupe)]">
          nothing here
        </p>
      )}
    </section>
  );
}

function LeadCard({ lead }: { lead: Lead }) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["leads"] });
    queryClient.invalidateQueries({ queryKey: ["dashboard"] });
  };

  const advance = useMutation({
    mutationFn: (status: LeadStatus) => leads.setStatus(lead.id, status),
    onSuccess: invalidate,
  });

  const next = NEXT_STAGE[lead.status];

  return (
    <article className="rounded-lg border border-[var(--line)] bg-white p-4">
      <div className="flex items-start justify-between gap-2">
        <h3 className="text-base font-medium">
          {lead.caller_name ?? "Unknown caller"}
        </h3>
        {/* The red dot: untouched for over 24 hours. */}
        {lead.is_stale && (
          <span
            aria-label="waiting more than a day"
            className="mt-1 h-3 w-3 shrink-0 rounded-full bg-red-700"
          />
        )}
      </div>

      <p className="mt-1 text-base text-[var(--taupe)]">
        {lead.job_type ?? "no detail"}
      </p>

      {lead.urgency === "emergency" && (
        <span className="mt-2 inline-block rounded bg-red-700 px-2 py-0.5 text-sm text-white">
          emergency
        </span>
      )}

      {editing ? (
        <ValueInput lead={lead} onDone={() => { setEditing(false); invalidate(); }} />
      ) : (
        <button
          type="button"
          onClick={() => setEditing(true)}
          className="mt-3 flex min-h-[48px] w-full items-baseline justify-between rounded border border-[var(--line)] px-3 text-left"
        >
          <span className="text-sm text-[var(--taupe)]">
            What&apos;s this job worth?
          </span>
          <span className="font-serif text-xl">
            {formatMoneyOrDash(lead.value_cents)}
          </span>
        </button>
      )}

      <div className="mt-3 flex items-center justify-between gap-2">
        <span className="text-sm text-[var(--taupe)]">
          {lead.days_in_stage}d
        </span>
        {next && (
          <button
            type="button"
            onClick={() => advance.mutate(next)}
            disabled={advance.isPending}
            className="min-h-[48px] rounded border border-[var(--charcoal)] px-3 text-base"
          >
            → {STAGES.find((s) => s.key === next)?.label}
          </button>
        )}
      </div>

      {lead.callback_e164 && (
        <div className="mt-2 flex gap-3">
          <a
            href={`tel:${lead.callback_e164}`}
            className="min-h-[48px] text-base underline underline-offset-4"
          >
            Call
          </a>
          <a
            href={`sms:${lead.callback_e164}`}
            className="min-h-[48px] text-base underline underline-offset-4"
          >
            Text
          </a>
        </div>
      )}
    </article>
  );
}

function ValueInput({ lead, onDone }: { lead: Lead; onDone: () => void }) {
  const [raw, setRaw] = useState(
    lead.value_cents === null ? "" : String(lead.value_cents / 100),
  );
  const [problem, setProblem] = useState<string | null>(null);

  const save = useMutation({
    // The raw string goes to the server. The browser does not parse it — one
    // parser, shared with the SMS grammar, is what keeps "3,800" and "38OO"
    // behaving the same in both places.
    mutationFn: () => leads.setValue(lead.id, raw),
    onSuccess: onDone,
    onError: (error) => {
      setProblem(
        error instanceof ApiError && error.status === 422
          ? error.detail
          : "Couldn't save that.",
      );
    },
  });

  return (
    <form
      className="mt-3"
      onSubmit={(event) => {
        event.preventDefault();
        setProblem(null);
        save.mutate();
      }}
    >
      <label className="block text-sm text-[var(--taupe)]">
        What&apos;s this job worth?
        <input
          autoFocus
          inputMode="decimal"
          value={raw}
          onChange={(event) => setRaw(event.target.value)}
          placeholder="3800"
          className="mt-1 min-h-[48px] w-full rounded border border-[var(--line)] px-3 font-serif text-xl"
        />
      </label>
      {problem && <p className="mt-1 text-sm text-red-700">{problem}</p>}
      <div className="mt-2 flex gap-2">
        <button
          type="submit"
          disabled={save.isPending}
          className="min-h-[48px] flex-1 rounded bg-[var(--charcoal)] px-3 text-base text-white"
        >
          Save
        </button>
        <button
          type="button"
          onClick={onDone}
          className="min-h-[48px] rounded border border-[var(--line)] px-3 text-base"
        >
          Cancel
        </button>
      </div>
    </form>
  );
}
