"use client";

/**
 * Settings, built around the forwarding health indicator.
 *
 * That indicator is the most valuable thing on this screen. A contractor whose
 * call forwarding got switched off stops getting calls, decides Mabel does not
 * work, and cancels — without ever opening a ticket. So it goes at the top,
 * with the codes to fix it directly underneath, rather than being a small
 * status dot next to a phone number.
 */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";

interface ForwardingHealth {
  state: "green" | "amber" | "red" | "never";
  last_call_at: string | null;
  days_quiet: number | null;
  did_e164: string | null;
  did_display: string | null;
  message: string;
}

interface ForwardingCodes {
  carrier: string;
  enable_no_answer: string;
  enable_busy: string;
  enable_unreachable: string;
  disable_all: string;
  note: string;
}

interface Account {
  business_name: string;
  legal_name: string | null;
  trade: string;
  timezone: string;
  status: string;
  did_e164: string | null;
}

interface DataExport {
  calls: number;
  leads: number;
  contacts: number;
  events: number;
}

const CARRIER_NAMES: Record<string, string> = {
  verizon: "Verizon",
  att: "AT&T",
  tmobile: "T-Mobile",
  other: "Anything else",
};

export default function SettingsPage() {
  return (
    <div className="space-y-10">
      <h1 className="font-serif text-3xl">Settings</h1>
      <Forwarding />
      <AccountSection />
      <DataSection />
    </div>
  );
}

function Forwarding() {
  const { data, isPending } = useQuery({
    queryKey: ["settings", "forwarding"],
    queryFn: () => api.get<ForwardingHealth>("/api/settings/forwarding"),
    // The one thing on this screen worth checking often.
    refetchInterval: 60_000,
  });

  if (isPending) {
    return <div className="h-40 animate-pulse rounded-lg bg-[var(--cream-dark)]" />;
  }

  const tone = {
    green: "border-green-600 bg-green-50",
    amber: "border-amber-500 bg-amber-50",
    red: "border-red-600 bg-red-50",
    never: "border-[var(--line)] bg-white",
  }[data!.state];

  const dot = {
    green: "bg-green-600",
    amber: "bg-amber-500",
    red: "bg-red-600",
    never: "bg-[var(--taupe)]",
  }[data!.state];

  return (
    <section className={`rounded-lg border-2 p-6 ${tone}`}>
      <div className="flex items-center gap-3">
        <span className={`h-4 w-4 rounded-full ${dot}`} aria-hidden />
        <h2 className="font-serif text-2xl">Your phone</h2>
      </div>

      {data!.did_display && (
        <p className="mt-3 font-serif text-3xl">{data!.did_display}</p>
      )}
      <p className="mt-1 text-base text-[var(--taupe)]">
        This is Mabel&apos;s number. Forward your business line to it.
      </p>

      <p className="mt-4 text-base">{data!.message}</p>
      {data!.last_call_at && (
        <p className="mt-1 text-sm text-[var(--taupe)]">
          Last call {new Date(data!.last_call_at).toLocaleDateString()}.
        </p>
      )}

      {data!.state !== "green" && <ForwardingCodesPanel />}
    </section>
  );
}

function ForwardingCodesPanel() {
  const [carrier, setCarrier] = useState("verizon");
  const { data } = useQuery({
    queryKey: ["settings", "forwarding", "codes"],
    queryFn: () => api.get<ForwardingCodes[]>("/api/settings/forwarding/codes"),
  });

  const codes = data?.find((entry) => entry.carrier === carrier);

  return (
    <div className="mt-6 rounded-lg border border-[var(--line)] bg-white p-5">
      <h3 className="text-base font-medium">Set up forwarding</h3>
      <label className="mt-3 block text-base">
        Your carrier
        <select
          value={carrier}
          onChange={(event) => setCarrier(event.target.value)}
          className="mt-1 min-h-[48px] w-full rounded border border-[var(--line)] px-3 text-base"
        >
          {Object.entries(CARRIER_NAMES).map(([key, label]) => (
            <option key={key} value={key}>
              {label}
            </option>
          ))}
        </select>
      </label>

      {codes && (
        <>
          <p className="mt-4 text-base text-[var(--taupe)]">
            Dial these from your business phone. {codes.note}
          </p>
          <dl className="mt-3 space-y-2">
            <Code label="When you don't answer" value={codes.enable_no_answer} />
            <Code label="When you're on another call" value={codes.enable_busy} />
            <Code label="When your phone's off" value={codes.enable_unreachable} />
            <Code label="To turn it all off again" value={codes.disable_all} />
          </dl>
        </>
      )}
    </div>
  );
}

function Code({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-2">
      <dt className="text-base text-[var(--taupe)]">{label}</dt>
      <dd className="font-mono text-lg">{value}</dd>
    </div>
  );
}

function AccountSection() {
  const { data } = useQuery({
    queryKey: ["settings", "account"],
    queryFn: () => api.get<Account>("/api/settings/account"),
  });
  if (!data) return null;

  return (
    <section>
      <h2 className="font-serif text-2xl">Account</h2>
      <dl className="mt-3 space-y-2 rounded-lg border border-[var(--line)] bg-white p-5">
        <Row label="Business" value={data.business_name} />
        <Row label="Trade" value={data.trade} />
        <Row label="Timezone" value={data.timezone} />
        <Row label="Status" value={data.status} />
      </dl>
    </section>
  );
}

function DataSection() {
  const { data } = useQuery({
    queryKey: ["settings", "data"],
    queryFn: () => api.get<DataExport>("/api/settings/data"),
  });

  return (
    <section>
      <h2 className="font-serif text-2xl">Your data</h2>
      <p className="mt-2 text-base text-[var(--taupe)]">
        Everything Mabel has recorded for you. It&apos;s yours.
      </p>
      {data && (
        <dl className="mt-3 space-y-2 rounded-lg border border-[var(--line)] bg-white p-5">
          <Row label="Calls" value={String(data.calls)} />
          <Row label="Leads" value={String(data.leads)} />
          <Row label="Customers" value={String(data.contacts)} />
          <Row label="Thread entries" value={String(data.events)} />
        </dl>
      )}
      <p className="mt-3 text-sm text-[var(--taupe)]">
        {/* Deleting an account removes a contractor's entire call history.
            Nothing irreversible happens without a human, so it is not a button
            on a settings page. */}
        To export everything or close the account, email us — we&apos;d rather a
        person handled it than a button did.
      </p>
    </section>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-4">
      <dt className="text-base text-[var(--taupe)]">{label}</dt>
      <dd className="text-base">{value}</dd>
    </div>
  );
}
