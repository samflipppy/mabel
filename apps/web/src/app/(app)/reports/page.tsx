"use client";

/**
 * Reports. The retention artifact.
 *
 * The monthly narrative is rendered by the server so the portal and the PDF
 * read identically — a report that says one thing on screen and another in the
 * attachment is a report nobody trusts.
 *
 * The usage panel exists because "surprise overage bills are how answering
 * services lose customers". It shows minutes against the included allowance
 * before it shows anything else.
 */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { formatMoneyWhole } from "@/lib/money";

interface MonthlyReport {
  period_start: string;
  period_end: string;
  calls_answered: number;
  leads_created: number;
  emergencies: number;
  jobs_won: number;
  won_value_cents: number;
  source_breakdown: Record<string, number>;
  untouched_leads: { name?: string; job_type?: string; since?: string }[];
  pdf_path: string | null;
  sent_at: string | null;
  narrative: string[];
}

interface UsageDay {
  day: string;
  calls_answered: number;
  voice_minutes: number;
  sms_sent: number;
  cost_cents: number;
}

interface Usage {
  days: UsageDay[];
  minutes_used: number;
  minutes_included: number | null;
  cost_cents: number;
}

interface SourceMonth {
  month: string;
  sources: Record<string, number>;
}

type Tab = "Monthly" | "Usage" | "Where they came from";

export default function ReportsPage() {
  const [tab, setTab] = useState<Tab>("Monthly");

  return (
    <div className="space-y-6">
      <h1 className="font-serif text-3xl">Reports</h1>

      <div role="tablist" className="flex gap-1 border-b border-[var(--line)]">
        {(["Monthly", "Usage", "Where they came from"] as Tab[]).map((name) => (
          <button
            key={name}
            role="tab"
            aria-selected={tab === name}
            onClick={() => setTab(name)}
            className={[
              "min-h-[48px] shrink-0 border-b-2 px-4 text-base",
              tab === name
                ? "border-[var(--charcoal)] font-medium"
                : "border-transparent text-[var(--taupe)]",
            ].join(" ")}
          >
            {name}
          </button>
        ))}
      </div>

      {tab === "Monthly" && <Monthly />}
      {tab === "Usage" && <UsagePanel />}
      {tab === "Where they came from" && <Sources />}
    </div>
  );
}

function Monthly() {
  const { data, isPending } = useQuery({
    queryKey: ["reports", "monthly"],
    queryFn: () => api.get<MonthlyReport[]>("/api/reports/monthly"),
  });

  if (isPending) {
    return <div className="h-64 animate-pulse rounded-lg bg-[var(--cream-dark)]" />;
  }
  if (!data || data.length === 0) {
    return (
      <p className="rounded-lg border border-[var(--line)] bg-white px-6 py-12 text-center text-base text-[var(--taupe)]">
        The first report lands on the 1st of next month.
      </p>
    );
  }

  return (
    <div className="space-y-6">
      {data.map((report) => (
        <article
          key={report.period_start}
          className="rounded-lg border border-[var(--line)] bg-white p-6"
        >
          {report.narrative.map((line, index) => (
            <p
              key={index}
              className={
                index === 0
                  ? "font-serif text-2xl"
                  : "mt-3 text-base leading-relaxed"
              }
            >
              {line}
            </p>
          ))}

          {report.jobs_won > 0 && (
            <p className="mt-6 font-serif text-4xl">
              {formatMoneyWhole(report.won_value_cents)}
            </p>
          )}

          {report.pdf_path ? (
            <a
              href={report.pdf_path}
              className="mt-4 inline-block min-h-[48px] text-base underline underline-offset-4"
            >
              Download the PDF
            </a>
          ) : (
            <p className="mt-4 text-sm text-[var(--taupe)]">
              PDF generation isn&apos;t switched on yet.
            </p>
          )}
        </article>
      ))}
    </div>
  );
}

function UsagePanel() {
  const { data, isPending } = useQuery({
    queryKey: ["reports", "usage"],
    queryFn: () => api.get<Usage>("/api/reports/usage"),
  });

  if (isPending) {
    return <div className="h-64 animate-pulse rounded-lg bg-[var(--cream-dark)]" />;
  }
  if (!data) return null;

  const over =
    data.minutes_included !== null && data.minutes_used > data.minutes_included;
  const busiest = Math.max(1, ...data.days.map((day) => day.voice_minutes));

  return (
    <div className="space-y-6">
      <div className="rounded-lg border border-[var(--line)] bg-white p-6">
        <p className="text-base text-[var(--taupe)]">Minutes this period</p>
        <p className="mt-1 font-serif text-4xl">
          {data.minutes_used}
          {data.minutes_included !== null && (
            <span className="text-2xl text-[var(--taupe)]">
              {" / "}
              {data.minutes_included}
            </span>
          )}
        </p>
        {over && (
          <p className="mt-2 text-base text-amber-800">
            You&apos;re over the included minutes. The overage is on your next
            invoice — no surprises at the end of the month.
          </p>
        )}
      </div>

      <div className="rounded-lg border border-[var(--line)] bg-white p-6">
        <h2 className="font-serif text-2xl">Minutes per day</h2>
        <div className="mt-4 flex items-end gap-1" style={{ height: 120 }}>
          {data.days.map((day) => (
            <div
              key={day.day}
              className="flex-1 rounded-t bg-[var(--charcoal)]"
              style={{ height: `${(day.voice_minutes / busiest) * 100}%` }}
              title={`${day.day}: ${day.voice_minutes} minutes, ${day.calls_answered} calls`}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

function Sources() {
  const { data, isPending } = useQuery({
    queryKey: ["reports", "sources"],
    queryFn: () => api.get<SourceMonth[]>("/api/reports/sources"),
  });

  if (isPending) {
    return <div className="h-64 animate-pulse rounded-lg bg-[var(--cream-dark)]" />;
  }
  if (!data || data.length === 0) {
    return (
      <p className="rounded-lg border border-[var(--line)] bg-white px-6 py-12 text-center text-base text-[var(--taupe)]">
        Nothing yet. This fills in as Mabel asks callers how they heard about
        you.
      </p>
    );
  }

  const names = [...new Set(data.flatMap((m) => Object.keys(m.sources)))];

  return (
    <div className="overflow-x-auto rounded-lg border border-[var(--line)] bg-white">
      <table className="w-full text-base">
        <caption className="px-5 py-4 text-left text-base text-[var(--taupe)]">
          Where your work has come from. Most contractors have never had this.
        </caption>
        <thead>
          <tr className="border-b border-[var(--line)]">
            <th scope="col" className="px-5 py-3 text-left">
              Month
            </th>
            {names.map((name) => (
              <th key={name} scope="col" className="px-5 py-3 text-right capitalize">
                {name}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.map((month) => (
            <tr key={month.month} className="border-b border-[var(--line)]">
              <th scope="row" className="px-5 py-3 text-left font-normal">
                {new Date(month.month).toLocaleDateString("en-US", {
                  month: "short",
                  year: "numeric",
                })}
              </th>
              {names.map((name) => (
                <td key={name} className="px-5 py-3 text-right">
                  {month.sources[name] ?? "—"}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
