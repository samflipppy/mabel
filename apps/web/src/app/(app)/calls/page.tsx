"use client";

/**
 * The Calls screen. The transcript archive.
 *
 * The search box is the feature: "search for that guy who called about the
 * water heater" and it finds him. It is placed first and given the most room
 * for that reason — the filters below it are secondary and collapse on a
 * phone.
 */

import { useState } from "react";
import Link from "next/link";
import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { type CallSummary, calls } from "@/lib/api";

const OUTCOMES = [
  "lead",
  "emergency",
  "existing_customer",
  "spam",
  "wrong_number",
  "hangup",
  "transferred",
  "failed",
] as const;

export default function CallsPage() {
  const [query, setQuery] = useState("");
  const [outcome, setOutcome] = useState("");
  const [emergencyOnly, setEmergencyOnly] = useState(false);
  const [flaggedOnly, setFlaggedOnly] = useState(false);
  const [offset, setOffset] = useState(0);

  const { data, isPending, error } = useQuery({
    queryKey: ["calls", query, outcome, emergencyOnly, flaggedOnly, offset],
    queryFn: () =>
      calls.list({
        q: query || undefined,
        outcome: outcome || undefined,
        emergency_only: emergencyOnly,
        flagged_only: flaggedOnly,
        offset,
      }),
    // Keeps the previous page on screen while the next loads, so the list does
    // not flash empty on every keystroke.
    placeholderData: keepPreviousData,
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-serif text-3xl">Calls</h1>
        <p className="mt-1 text-base text-[var(--taupe)]">
          Every call Mabel has answered, with the transcript.
        </p>
      </div>

      <div className="space-y-3">
        <label className="block">
          <span className="sr-only">Search transcripts</span>
          <input
            type="search"
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              setOffset(0);
            }}
            placeholder="Search what was said — “water heater”, a street name, a part…"
            className="min-h-[48px] w-full rounded-lg border border-[var(--line)] bg-white px-4 text-base"
          />
        </label>

        <div className="flex flex-wrap items-center gap-3">
          <select
            value={outcome}
            onChange={(event) => {
              setOutcome(event.target.value);
              setOffset(0);
            }}
            className="min-h-[48px] rounded-lg border border-[var(--line)] bg-white px-3 text-base"
            aria-label="Outcome"
          >
            <option value="">All outcomes</option>
            {OUTCOMES.map((value) => (
              <option key={value} value={value}>
                {value.replace(/_/g, " ")}
              </option>
            ))}
          </select>

          <Toggle
            label="Emergencies only"
            checked={emergencyOnly}
            onChange={(next) => {
              setEmergencyOnly(next);
              setOffset(0);
            }}
          />
          <Toggle
            label="Flagged only"
            checked={flaggedOnly}
            onChange={(next) => {
              setFlaggedOnly(next);
              setOffset(0);
            }}
          />
        </div>
      </div>

      {error ? (
        <p className="text-base">Couldn&apos;t load calls.</p>
      ) : isPending ? (
        <div className="h-64 animate-pulse rounded-lg bg-[var(--cream-dark)]" />
      ) : data.calls.length === 0 ? (
        <p className="rounded-lg border border-[var(--line)] bg-white px-6 py-12 text-center text-base text-[var(--taupe)]">
          {query
            ? `Nothing matching “${query}”.`
            : "No calls yet. Mabel's listening."}
        </p>
      ) : (
        <>
          <p className="text-sm text-[var(--taupe)]">
            {data.total} call{data.total === 1 ? "" : "s"}
          </p>
          <ul className="divide-y divide-[var(--line)] rounded-lg border border-[var(--line)] bg-white">
            {data.calls.map((call) => (
              <CallRow key={call.id} call={call} />
            ))}
          </ul>
          {data.has_more && (
            <button
              type="button"
              onClick={() => setOffset(offset + 50)}
              className="min-h-[48px] w-full rounded-lg border border-[var(--line)] bg-white text-base"
            >
              Load more
            </button>
          )}
        </>
      )}
    </div>
  );
}

function CallRow({ call }: { call: CallSummary }) {
  return (
    <li>
      <Link
        href={`/calls/${call.id}`}
        className="flex min-h-[64px] flex-wrap items-center gap-x-4 gap-y-1 px-5 py-3"
      >
        <span className="w-32 shrink-0 text-base text-[var(--taupe)]">
          {new Date(call.started_at).toLocaleDateString("en-US", {
            month: "short",
            day: "numeric",
          })}
          {" · "}
          {new Date(call.started_at).toLocaleTimeString("en-US", {
            hour: "numeric",
            minute: "2-digit",
          })}
        </span>
        <span className="text-base font-medium">
          {call.caller ?? formatPhone(call.from_e164)}
        </span>
        {call.outcome && (
          <span className="rounded bg-[var(--cream-dark)] px-2 py-0.5 text-sm">
            {call.outcome.replace(/_/g, " ")}
          </span>
        )}
        {call.qa_flags.length > 0 && (
          <span className="rounded bg-amber-200 px-2 py-0.5 text-sm">flagged</span>
        )}
        {call.has_recording && (
          <span aria-label="has a recording" className="text-sm text-[var(--taupe)]">
            ▶
          </span>
        )}
        <span className="ml-auto text-base text-[var(--taupe)]">
          {formatDuration(call.duration_sec)}
        </span>
        {call.excerpt && (
          // The matching line, with the search terms marked by ts_headline.
          <p
            className="w-full text-base text-[var(--taupe)]"
            dangerouslySetInnerHTML={{ __html: sanitiseHeadline(call.excerpt) }}
          />
        )}
      </Link>
    </li>
  );
}

/**
 * `ts_headline` returns `<b>` around the matched terms and nothing else, but
 * the surrounding text is a caller's own words and has never been escaped.
 * Strip every tag, then put the `<b>` back — so a caller who said something
 * containing a `<script>` cannot have it rendered.
 */
function sanitiseHeadline(headline: string): string {
  return headline
    .replace(/<(?!\/?b>)[^>]*>/g, "")
    .replace(/&(?!(amp|lt|gt|quot|#\d+);)/g, "&amp;");
}

function Toggle({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (next: boolean) => void;
}) {
  return (
    <label className="flex min-h-[48px] cursor-pointer items-center gap-2 text-base">
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        className="h-5 w-5"
      />
      {label}
    </label>
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
