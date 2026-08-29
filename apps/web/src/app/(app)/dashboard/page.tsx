"use client";

/**
 * The Dashboard. "What happened and what needs me."
 *
 * 02-PORTAL.md puts the needs-you list above everything else, and it is above
 * everything else here — before the recent calls, before the chart. The four
 * cards sit at the top because that is what the owner opens the portal for;
 * the list underneath is what the office manager acts on.
 */

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";

import { type Dashboard, type NeedsYouRow, dashboard } from "@/lib/api";
import { formatMoneyWhole, percentDelta } from "@/lib/money";

export default function DashboardPage() {
  const { data, isPending, error } = useQuery({
    queryKey: ["dashboard"],
    queryFn: dashboard.get,
  });

  if (isPending) return <Skeleton />;
  if (error) return <LoadFailed />;
  if (data.is_empty) return <EmptyState />;

  return (
    <div className="space-y-10">
      <Cards data={data} />
      <NeedsYou rows={data.needs_you} />
      <RecentCalls data={data} />
      <ThisWeek data={data} />
    </div>
  );
}

function Cards({ data }: { data: Dashboard }) {
  return (
    <section aria-label="This month">
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        {data.cards.map((card) => {
          const delta = percentDelta(card.value, card.previous);
          return (
            <div
              key={card.label}
              className="rounded-lg border border-[var(--line)] bg-white p-5"
            >
              <div className="text-base text-[var(--taupe)]">{card.label}</div>
              {/* "Every dollar figure is large and unmissable." */}
              <div className="mt-2 font-serif text-4xl text-[var(--charcoal)]">
                {card.is_money ? formatMoneyWhole(card.value) : card.value}
              </div>
              <div className="mt-1 text-sm text-[var(--taupe)]">
                {delta === null
                  ? "no comparison yet"
                  : `${delta >= 0 ? "+" : ""}${delta}% vs last month`}
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function NeedsYou({ rows }: { rows: NeedsYouRow[] }) {
  if (rows.length === 0) {
    return (
      <section aria-labelledby="needs-you">
        <h2 id="needs-you" className="font-serif text-2xl">
          Needs you
        </h2>
        <p className="mt-3 text-base text-[var(--taupe)]">
          Nothing waiting. Every lead has been touched.
        </p>
      </section>
    );
  }

  return (
    <section aria-labelledby="needs-you">
      <h2 id="needs-you" className="font-serif text-2xl">
        Needs you
      </h2>
      <ul className="mt-4 divide-y divide-[var(--line)] rounded-lg border border-[var(--line)] bg-white">
        {rows.map((row) => (
          <li
            key={`${row.kind}-${row.id}`}
            className="flex min-h-[64px] flex-wrap items-center gap-x-4 gap-y-1 px-5 py-3"
          >
            <Badge kind={row.kind} answered={row.answered} />
            <span className="text-base font-medium">
              {row.name ?? "Unknown caller"}
            </span>
            <span className="text-base text-[var(--taupe)]">{row.summary}</span>
            {row.phone_e164 && (
              // Tap to call. On a phone this is the whole point of the row.
              <a
                href={`tel:${row.phone_e164}`}
                className="min-h-[48px] shrink-0 self-center text-base underline underline-offset-4"
              >
                {formatPhone(row.phone_e164)}
              </a>
            )}
            <span className="ml-auto text-base text-[var(--taupe)]">
              {formatWaiting(row.hours_waiting)}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function Badge({
  kind,
  answered,
}: {
  kind: NeedsYouRow["kind"];
  answered: boolean | null;
}) {
  // An emergency nobody has answered is the one row on this screen that must
  // be impossible to skim past.
  if (kind === "emergency") {
    return (
      <span
        className={[
          "rounded px-2 py-1 text-sm font-medium",
          answered
            ? "bg-[var(--cream-dark)] text-[var(--charcoal)]"
            : "bg-red-700 text-white",
        ].join(" ")}
      >
        {answered ? "Emergency · answered" : "Emergency · unanswered"}
      </span>
    );
  }
  if (kind === "qa_flag") {
    return (
      <span className="rounded bg-amber-200 px-2 py-1 text-sm font-medium text-[var(--charcoal)]">
        Flagged
      </span>
    );
  }
  return (
    <span className="rounded bg-[var(--cream-dark)] px-2 py-1 text-sm font-medium">
      Waiting
    </span>
  );
}

function RecentCalls({ data }: { data: Dashboard }) {
  return (
    <section aria-labelledby="recent">
      <div className="flex items-baseline justify-between">
        <h2 id="recent" className="font-serif text-2xl">
          Recent calls
        </h2>
        <Link href="/calls" className="text-base underline underline-offset-4">
          All calls
        </Link>
      </div>
      <ul className="mt-4 divide-y divide-[var(--line)] rounded-lg border border-[var(--line)] bg-white">
        {data.recent_calls.map((call) => (
          <li key={call.id} className="flex min-h-[56px] items-center gap-4 px-5 py-3">
            <Link href={`/calls/${call.id}`} className="text-base font-medium">
              {call.caller ?? formatPhone(call.from_e164)}
            </Link>
            {call.outcome && (
              <span className="text-base text-[var(--taupe)]">{call.outcome}</span>
            )}
            {call.qa_flags.length > 0 && (
              <span className="rounded bg-amber-200 px-2 py-0.5 text-sm">flagged</span>
            )}
            <span className="ml-auto text-base text-[var(--taupe)]">
              {formatDuration(call.duration_sec)}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function ThisWeek({ data }: { data: Dashboard }) {
  const busiest = Math.max(1, ...data.this_week.map((day) => day.total));
  return (
    <section aria-labelledby="this-week">
      <h2 id="this-week" className="font-serif text-2xl">
        This week
      </h2>
      <div className="mt-4 flex items-end gap-3 rounded-lg border border-[var(--line)] bg-white p-5">
        {data.this_week.map((day) => (
          <div key={day.day} className="flex flex-1 flex-col items-center gap-2">
            <div
              className="flex w-full flex-col justify-end"
              style={{ height: 120 }}
              // Two segments: after-hours on top, so the split 02-PORTAL.md
              // asks for is readable without a legend.
              title={`${day.total} calls, ${day.after_hours} after hours`}
            >
              <div
                className="w-full rounded-t bg-[var(--charcoal)]"
                style={{ height: `${(day.after_hours / busiest) * 100}%` }}
              />
              <div
                className="w-full bg-[var(--cream-dark)]"
                style={{
                  height: `${((day.total - day.after_hours) / busiest) * 100}%`,
                }}
              />
            </div>
            <span className="text-sm text-[var(--taupe)]">
              {new Date(day.day).toLocaleDateString("en-US", { weekday: "short" })}
            </span>
          </div>
        ))}
      </div>
      <p className="mt-2 text-sm text-[var(--taupe)]">
        Dark is after hours — the calls that would have gone to voicemail.
      </p>
    </section>
  );
}

function EmptyState() {
  return (
    <div className="rounded-lg border border-[var(--line)] bg-white px-6 py-16 text-center">
      <p className="font-serif text-2xl">Mabel&apos;s live and listening.</p>
      <p className="mt-2 text-base text-[var(--taupe)]">
        Calls will show up here.
      </p>
    </div>
  );
}

function LoadFailed() {
  return (
    <div className="rounded-lg border border-[var(--line)] bg-white px-6 py-16 text-center">
      <p className="text-base">
        Couldn&apos;t load the dashboard. Mabel is still answering the phone —
        this screen is the part that&apos;s broken.
      </p>
    </div>
  );
}

function Skeleton() {
  return (
    <div className="space-y-4" aria-busy="true">
      <div className="h-28 animate-pulse rounded-lg bg-[var(--cream-dark)]" />
      <div className="h-64 animate-pulse rounded-lg bg-[var(--cream-dark)]" />
    </div>
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

function formatWaiting(hours: number): string {
  if (hours < 1) return "just now";
  if (hours < 24) return `${hours}h`;
  return `${Math.floor(hours / 24)}d`;
}
