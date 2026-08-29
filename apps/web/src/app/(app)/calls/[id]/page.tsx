"use client";

/**
 * Call detail: the transcript, the extraction, and the tool trace.
 *
 * 02-PORTAL.md on the tool trace: "what Mabel actually did during the call
 * (created lead, texted owner). This builds trust; they can see the machine
 * working." It is given real space here for that reason rather than being
 * tucked into a debug panel.
 *
 * The waveform player is not built. Recordings need Supabase Storage and
 * signed URLs, and there is no bucket (docs/BLOCKED.md #2) — so the screen says
 * so plainly instead of showing a player that cannot play anything.
 */

import { use } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { type CallDetail, calls } from "@/lib/api";

export default function CallDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const queryClient = useQueryClient();

  const { data, isPending, error } = useQuery({
    queryKey: ["call", id],
    queryFn: () => calls.get(id),
  });

  const markReviewed = useMutation({
    mutationFn: () => calls.markReviewed(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["call", id] });
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
    },
  });

  if (isPending) {
    return <div className="h-96 animate-pulse rounded-lg bg-[var(--cream-dark)]" />;
  }
  if (error) return <p className="text-base">Couldn&apos;t load that call.</p>;

  return (
    <div className="space-y-8">
      <div>
        <Link href="/calls" className="text-base underline underline-offset-4">
          ← All calls
        </Link>
        <h1 className="mt-2 font-serif text-3xl">
          {data.caller ?? formatPhone(data.from_e164)}
        </h1>
        <p className="mt-1 text-base text-[var(--taupe)]">
          {new Date(data.started_at).toLocaleString("en-US", {
            weekday: "long",
            month: "long",
            day: "numeric",
            hour: "numeric",
            minute: "2-digit",
          })}
          {" · "}
          {formatDuration(data.duration_sec)}
          {data.outcome ? ` · ${data.outcome.replace(/_/g, " ")}` : ""}
        </p>
      </div>

      {data.qa_flags.length > 0 && <QaBanner call={data} onReview={markReviewed.mutate} />}

      <div className="grid gap-8 lg:grid-cols-[2fr_1fr]">
        <Transcript call={data} />
        <div className="space-y-8">
          <ExtractionPanel call={data} />
          <ToolTrace call={data} />
          <Recording call={data} />
        </div>
      </div>
    </div>
  );
}

function QaBanner({
  call,
  onReview,
}: {
  call: CallDetail;
  onReview: () => void;
}) {
  return (
    <div className="rounded-lg border border-amber-400 bg-amber-50 p-5">
      <p className="text-base font-medium">Flagged for review</p>
      <p className="mt-1 text-base">{call.qa_summary}</p>
      <button
        type="button"
        onClick={onReview}
        className="mt-3 min-h-[48px] rounded-lg border border-[var(--charcoal)] px-4 text-base"
      >
        I&apos;ve looked at this
      </button>
      <p className="mt-2 text-sm text-[var(--taupe)]">
        {/* The flags stay on the record. What changes is that somebody has
            looked — a call that quoted a price still quoted a price. */}
        This clears it off the dashboard. The flag stays on the record.
      </p>
    </div>
  );
}

function Transcript({ call }: { call: CallDetail }) {
  if (call.turns.length === 0) {
    return (
      <section>
        <h2 className="font-serif text-2xl">Transcript</h2>
        <p className="mt-3 text-base text-[var(--taupe)]">
          No transcript for this call.
        </p>
      </section>
    );
  }

  return (
    <section>
      <h2 className="font-serif text-2xl">Transcript</h2>
      <ol className="mt-4 space-y-3">
        {call.turns.map((turn, index) => {
          const isMabel = turn.role === "assistant" || turn.role === "mabel";
          return (
            <li
              key={index}
              className={[
                "rounded-lg px-4 py-3 text-base",
                isMabel
                  ? "bg-[var(--cream-dark)]"
                  : "border border-[var(--line)] bg-white",
              ].join(" ")}
            >
              <span className="mr-2 text-sm font-medium text-[var(--taupe)]">
                {isMabel ? "Mabel" : "Caller"}
              </span>
              {turn.text}
            </li>
          );
        })}
      </ol>
    </section>
  );
}

function ExtractionPanel({ call }: { call: CallDetail }) {
  if (!call.extraction) return null;
  const fields: [string, string | null][] = [
    ["Name", call.extraction.name],
    ["Address", call.extraction.address],
    ["Phone", call.extraction.phone ? formatPhone(call.extraction.phone) : null],
    ["Job", call.extraction.job_type],
    ["Urgency", call.extraction.urgency],
    ["Heard about you via", call.extraction.source],
  ];

  return (
    <section>
      <h2 className="font-serif text-2xl">What she got</h2>
      <dl className="mt-4 space-y-2 rounded-lg border border-[var(--line)] bg-white p-5">
        {fields.map(([label, value]) => (
          <div key={label} className="flex justify-between gap-4">
            <dt className="text-base text-[var(--taupe)]">{label}</dt>
            <dd
              className={[
                "text-right text-base",
                value ? "" : "text-[var(--taupe)] italic",
              ].join(" ")}
            >
              {/* A gap is information: it is what she did not manage to ask. */}
              {value ?? "not captured"}
            </dd>
          </div>
        ))}
      </dl>
      {call.lead_id && (
        <Link
          href={`/leads/${call.lead_id}`}
          className="mt-3 inline-block text-base underline underline-offset-4"
        >
          Open the lead
        </Link>
      )}
    </section>
  );
}

function ToolTrace({ call }: { call: CallDetail }) {
  if (call.tool_trace.length === 0) return null;
  return (
    <section>
      <h2 className="font-serif text-2xl">What she did</h2>
      <ul className="mt-4 space-y-2 rounded-lg border border-[var(--line)] bg-white p-5">
        {call.tool_trace.map((entry, index) => (
          <li key={index} className="flex items-center gap-2 text-base">
            <span aria-hidden>{entry.ok ? "✓" : "✕"}</span>
            <span>{describeTool(entry.tool)}</span>
            {entry.mutating && (
              <span className="rounded bg-[var(--cream-dark)] px-2 py-0.5 text-sm">
                changed something
              </span>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

/** Plain English. The owner should not have to learn our tool names. */
function describeTool(tool: string): string {
  return (
    {
      lookup_customer: "Looked them up",
      get_service_area: "Checked the service area",
      check_availability: "Checked the calendar",
      create_lead: "Wrote the lead",
      escalate_emergency: "Texted whoever was on call",
      book_estimate: "Booked an estimate",
      get_job_history: "Looked at their past jobs",
      answer_question: "Answered a question from your Q&A",
      log_note: "Made a note",
    }[tool] ?? tool
  );
}

function Recording({ call }: { call: CallDetail }) {
  return (
    <section>
      <h2 className="font-serif text-2xl">Recording</h2>
      <p className="mt-3 text-base text-[var(--taupe)]">
        {call.has_recording
          ? "Playback isn't wired up yet — recordings need the storage bucket connecting first."
          : "No recording for this call."}
      </p>
    </section>
  );
}

function formatPhone(e164: string | null): string {
  if (!e164) return "unknown";
  const digits = e164.replace(/\D/g, "").replace(/^1/, "");
  if (digits.length !== 10) return e164;
  return `(${digits.slice(0, 3)}) ${digits.slice(3, 6)}-${digits.slice(6)}`;
}

function formatDuration(seconds: number | null): string {
  if (seconds === null) return "—";
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${String(seconds % 60).padStart(2, "0")}`;
}
