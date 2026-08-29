"use client";

/**
 * One customer's thread.
 *
 * Open items pinned at the top, then everything in order. 02-PORTAL.md calls
 * the pinned section "the dropped-ball surfacing, and it's the feature owners
 * will talk about" — so it is a banner, not a filter someone has to find.
 *
 * The merge banner is deliberately two equal buttons. "Merge" and "Not the
 * same" carry different costs — a wrong merge splices two histories together —
 * so neither is styled as the obvious one to click.
 */

import { use } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";

interface ThreadEntry {
  id: string;
  kind: string;
  direction: string | null;
  occurred_at: string;
  body: string | null;
  lead_id: string | null;
  has_recording: boolean;
}

interface MergeCandidate {
  id: string;
  display_name: string | null;
  primary_phone: string | null;
  score: number;
}

interface ContactDetail {
  id: string;
  display_name: string | null;
  primary_phone: string | null;
  phones: string[];
  first_seen_at: string;
  last_seen_at: string;
  open_items: ThreadEntry[];
  thread: ThreadEntry[];
  merge_candidates: MergeCandidate[];
}

const KIND_LABELS: Record<string, string> = {
  call: "Call",
  sms_in: "Text from them",
  sms_out: "Text to them",
  email_in: "Email from them",
  email_out: "Email to them",
  note: "Note",
  estimate: "Estimate",
  photo: "Photo",
  status_change: "Status change",
  identity_merged: "Records merged",
  system: "System",
};

export default function CustomerPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const queryClient = useQueryClient();

  const { data, isPending } = useQuery({
    queryKey: ["contact", id],
    queryFn: () => api.get<ContactDetail>(`/api/contacts/${id}`),
  });

  const merge = useMutation({
    mutationFn: (otherId: string) =>
      api.post(`/api/contacts/${id}/merge`, { other_id: otherId }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["contact", id] }),
  });

  const dismiss = useMutation({
    mutationFn: (otherId: string) =>
      api.post(`/api/contacts/${id}/not-a-duplicate/${otherId}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["contact", id] }),
  });

  if (isPending) {
    return <div className="h-96 animate-pulse rounded-lg bg-[var(--cream-dark)]" />;
  }
  if (!data) return <p className="text-base">Couldn&apos;t load that customer.</p>;

  return (
    <div className="space-y-8">
      <div>
        <Link href="/customers" className="text-base underline underline-offset-4">
          ← All customers
        </Link>
        <h1 className="mt-2 font-serif text-3xl">
          {data.display_name ?? formatPhone(data.primary_phone)}
        </h1>
        <p className="mt-1 text-base text-[var(--taupe)]">
          {data.phones.map(formatPhone).join(" · ")}
          {" · first called "}
          {new Date(data.first_seen_at).toLocaleDateString()}
        </p>
        {data.primary_phone && (
          <div className="mt-3 flex gap-4">
            <a
              href={`tel:${data.primary_phone}`}
              className="min-h-[48px] text-base underline underline-offset-4"
            >
              Call
            </a>
            <a
              href={`sms:${data.primary_phone}`}
              className="min-h-[48px] text-base underline underline-offset-4"
            >
              Text
            </a>
          </div>
        )}
      </div>

      {data.merge_candidates.map((candidate) => (
        <div
          key={candidate.id}
          className="rounded-lg border border-[var(--line)] bg-[var(--cream-dark)] p-5"
        >
          <p className="text-base">
            This might be the same person as{" "}
            <strong>{candidate.display_name ?? "another record"}</strong>
            {candidate.primary_phone
              ? ` (${formatPhone(candidate.primary_phone)})`
              : ""}
            .
          </p>
          <div className="mt-3 flex gap-3">
            {/* Equal weight. A wrong merge splices two histories together, so
                neither button is the obvious one to press. */}
            <button
              type="button"
              onClick={() => merge.mutate(candidate.id)}
              className="min-h-[48px] rounded-lg border border-[var(--charcoal)] px-4 text-base"
            >
              Merge them
            </button>
            <button
              type="button"
              onClick={() => dismiss.mutate(candidate.id)}
              className="min-h-[48px] rounded-lg border border-[var(--charcoal)] px-4 text-base"
            >
              Not the same
            </button>
          </div>
          <p className="mt-2 text-sm text-[var(--taupe)]">
            Merging can be undone — nothing is deleted.
          </p>
        </div>
      ))}

      {data.open_items.length > 0 && (
        <section className="rounded-lg border-2 border-amber-400 bg-amber-50 p-5">
          <h2 className="font-serif text-2xl">Waiting on you</h2>
          <ul className="mt-3 space-y-2">
            {data.open_items.map((entry) => (
              <li key={entry.id} className="text-base">
                {entry.body ?? KIND_LABELS[entry.kind] ?? entry.kind}
                <span className="text-[var(--taupe)]">
                  {" — "}
                  {new Date(entry.occurred_at).toLocaleDateString("en-US", {
                    month: "short",
                    day: "numeric",
                  })}
                  , no reply
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section>
        <h2 className="font-serif text-2xl">Everything</h2>
        <ol className="mt-4 space-y-3">
          {data.thread.map((entry) => (
            <li
              key={entry.id}
              className="rounded-lg border border-[var(--line)] bg-white p-4"
            >
              <div className="flex flex-wrap items-baseline gap-x-3 text-sm text-[var(--taupe)]">
                <span>{KIND_LABELS[entry.kind] ?? entry.kind}</span>
                <span>
                  {new Date(entry.occurred_at).toLocaleString("en-US", {
                    month: "short",
                    day: "numeric",
                    hour: "numeric",
                    minute: "2-digit",
                  })}
                </span>
                {entry.lead_id && (
                  <Link
                    href={`/leads`}
                    className="underline underline-offset-4"
                  >
                    lead
                  </Link>
                )}
              </div>
              {entry.body && <p className="mt-1 text-base">{entry.body}</p>}
            </li>
          ))}
        </ol>
        {data.thread.length === 0 && (
          <p className="mt-3 text-base text-[var(--taupe)]">
            Nothing recorded yet.
          </p>
        )}
      </section>
    </div>
  );
}

function formatPhone(e164: string | null): string {
  if (!e164) return "Unknown";
  const digits = e164.replace(/\D/g, "").replace(/^1/, "");
  if (digits.length !== 10) return e164;
  return `(${digits.slice(0, 3)}) ${digits.slice(3, 6)}-${digits.slice(6)}`;
}
