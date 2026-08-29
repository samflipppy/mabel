"use client";

/**
 * Billing.
 *
 * The next invoice sits at the top, because 02-PORTAL.md wants usage
 * transparent *before* the bill arrives: "surprise overage bills are how
 * answering services lose customers."
 *
 * Everything that touches a card is a redirect to Stripe's own pages. There is
 * no cancel button here — cancellation lives in Stripe's customer portal,
 * which is both their flow to get right and the correct place for something
 * irreversible.
 */

import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";

import { ApiError, api } from "@/lib/api";
import { formatMoney, formatMoneyWhole } from "@/lib/money";

interface PlanOption {
  key: string;
  name: string;
  price_cents: number;
  included_minutes: number;
  overage_cents_per_min: number;
  blurb: string;
  is_current: boolean;
}

interface BillingState {
  configured: boolean;
  plan_key: string | null;
  subscription_status: string | null;
  current_period_end: string | null;
  plan_cents: number | null;
  estimated_overage_cents: number;
  estimated_total_cents: number | null;
  minutes_used: number;
  minutes_included: number | null;
  plans: PlanOption[];
  message: string | null;
}

export default function BillingPage() {
  const [problem, setProblem] = useState<string | null>(null);

  const { data, isPending } = useQuery({
    queryKey: ["billing"],
    queryFn: () => api.get<BillingState>("/api/billing"),
  });

  const checkout = useMutation({
    mutationFn: (planKey: string) =>
      api.post<{ url: string }>("/api/billing/checkout", { plan: planKey }),
    onSuccess: ({ url }) => {
      window.location.href = url;
    },
    onError: (error) =>
      setProblem(error instanceof ApiError ? error.detail : "Couldn't start checkout."),
  });

  const portal = useMutation({
    mutationFn: () => api.post<{ url: string }>("/api/billing/portal"),
    onSuccess: ({ url }) => {
      window.location.href = url;
    },
    onError: (error) =>
      setProblem(error instanceof ApiError ? error.detail : "Couldn't open billing."),
  });

  if (isPending) {
    return <div className="h-96 animate-pulse rounded-lg bg-[var(--cream-dark)]" />;
  }
  if (!data) return <p className="text-base">Couldn&apos;t load billing.</p>;

  return (
    <div className="space-y-10">
      <h1 className="font-serif text-3xl">Billing</h1>

      {!data.configured && (
        <p className="rounded-lg border border-[var(--line)] bg-white p-5 text-base">
          Billing isn&apos;t connected yet. Nothing to pay and nothing to do
          here — Mabel keeps answering either way.
        </p>
      )}

      {data.plan_cents !== null && (
        <section className="rounded-lg border-2 border-[var(--line)] bg-white p-6">
          <p className="text-base text-[var(--taupe)]">Your next invoice</p>
          <p className="mt-1 font-serif text-5xl">
            {formatMoney(data.estimated_total_cents ?? data.plan_cents)}
          </p>

          <dl className="mt-5 space-y-2 border-t border-[var(--line)] pt-4">
            <Row label="Plan" value={formatMoney(data.plan_cents)} />
            {data.estimated_overage_cents > 0 && (
              <Row
                label={`Extra minutes (${Math.round(
                  data.minutes_used - (data.minutes_included ?? 0),
                )} over)`}
                value={formatMoney(data.estimated_overage_cents)}
              />
            )}
            <Row
              label="Minutes used"
              value={`${data.minutes_used}${
                data.minutes_included !== null ? ` of ${data.minutes_included}` : ""
              }`}
            />
            {data.current_period_end && (
              <Row
                label="Billed on"
                value={new Date(data.current_period_end).toLocaleDateString()}
              />
            )}
          </dl>

          {data.message && (
            <p className="mt-4 rounded bg-amber-50 p-3 text-base text-amber-900">
              {data.message}
            </p>
          )}

          <button
            type="button"
            onClick={() => portal.mutate()}
            className="mt-5 min-h-[48px] rounded-lg border border-[var(--charcoal)] px-5 text-base"
          >
            Payment method and invoices
          </button>
          <p className="mt-2 text-sm text-[var(--taupe)]">
            {/* Cancellation lives in Stripe's portal, not here. */}
            Card, receipts, and cancelling are all in there.
          </p>
        </section>
      )}

      {problem && (
        <p className="rounded-lg border border-red-300 bg-red-50 p-3 text-base text-red-800">
          {problem}
        </p>
      )}

      <section>
        <h2 className="font-serif text-2xl">Plans</h2>
        <div className="mt-4 grid gap-4 lg:grid-cols-3">
          {data.plans.map((option) => (
            <article
              key={option.key}
              className={[
                "rounded-lg border-2 bg-white p-5",
                option.is_current
                  ? "border-[var(--charcoal)]"
                  : "border-[var(--line)]",
              ].join(" ")}
            >
              <h3 className="text-base font-medium">{option.name}</h3>
              <p className="mt-1 font-serif text-3xl">
                {formatMoneyWhole(option.price_cents)}
                <span className="text-base text-[var(--taupe)]">/mo</span>
              </p>
              <p className="mt-2 text-base text-[var(--taupe)]">{option.blurb}</p>
              <p className="mt-2 text-sm text-[var(--taupe)]">
                {option.included_minutes} minutes included, then{" "}
                {formatMoney(option.overage_cents_per_min)} a minute.
              </p>
              {option.is_current ? (
                <p className="mt-4 min-h-[48px] text-base font-medium">
                  Your plan
                </p>
              ) : (
                <button
                  type="button"
                  onClick={() => checkout.mutate(option.key)}
                  disabled={checkout.isPending || !data.configured}
                  className="mt-4 min-h-[48px] w-full rounded-lg bg-[var(--charcoal)] text-base text-white disabled:opacity-40"
                >
                  {data.plan_key ? "Switch to this" : "Choose this"}
                </button>
              )}
            </article>
          ))}
        </div>
      </section>
    </div>
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
