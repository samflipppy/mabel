"use client";

/**
 * Customers, and the unified thread.
 *
 * 02-PORTAL.md: "the thing that turns this from an answering service into a
 * system of record."
 *
 * The list leads with the open-item count rather than the last-seen date,
 * because the question this screen answers is "who am I ignoring", not "who
 * called recently".
 */

import { useState } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";

interface ContactSummary {
  id: string;
  display_name: string | null;
  primary_phone: string | null;
  last_seen_at: string;
  open_items: number;
}

export default function CustomersPage() {
  const [query, setQuery] = useState("");
  const { data, isPending } = useQuery({
    queryKey: ["contacts", query],
    queryFn: () =>
      api.get<ContactSummary[]>(
        `/api/contacts${query ? `?q=${encodeURIComponent(query)}` : ""}`,
      ),
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-serif text-3xl">Customers</h1>
        <p className="mt-1 text-base text-[var(--taupe)]">
          Everyone who has ever called, and everything said since.
        </p>
      </div>

      <input
        type="search"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        placeholder="A name or a number"
        className="min-h-[48px] w-full rounded-lg border border-[var(--line)] bg-white px-4 text-base"
      />

      {isPending ? (
        <div className="h-64 animate-pulse rounded-lg bg-[var(--cream-dark)]" />
      ) : (data ?? []).length === 0 ? (
        <p className="rounded-lg border border-[var(--line)] bg-white px-6 py-12 text-center text-base text-[var(--taupe)]">
          {query ? "Nobody matching that." : "No customers yet."}
        </p>
      ) : (
        <ul className="divide-y divide-[var(--line)] rounded-lg border border-[var(--line)] bg-white">
          {(data ?? []).map((contact) => (
            <li key={contact.id}>
              <Link
                href={`/customers/${contact.id}`}
                className="flex min-h-[64px] items-center gap-4 px-5 py-3"
              >
                <span className="text-base font-medium">
                  {contact.display_name ?? formatPhone(contact.primary_phone)}
                </span>
                {contact.open_items > 0 && (
                  // The dropped-ball count. First thing on the row for a
                  // reason: it is what this screen is for.
                  <span className="rounded bg-amber-200 px-2 py-0.5 text-sm">
                    {contact.open_items} waiting on you
                  </span>
                )}
                <span className="ml-auto text-base text-[var(--taupe)]">
                  {new Date(contact.last_seen_at).toLocaleDateString()}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function formatPhone(e164: string | null): string {
  if (!e164) return "Unknown";
  const digits = e164.replace(/\D/g, "").replace(/^1/, "");
  if (digits.length !== 10) return e164;
  return `(${digits.slice(0, 3)}) ${digits.slice(3, 6)}-${digits.slice(6)}`;
}
