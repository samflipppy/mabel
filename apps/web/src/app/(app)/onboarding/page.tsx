"use client";

/**
 * The six-step wizard. Target under fifteen minutes.
 *
 * 02-PORTAL.md: "That last step is the whole onboarding. Everything else is
 * prefilled." So steps one to five are confirmations that can be moved through
 * quickly, and step six gets the whole screen and does not complete on a
 * button press — it completes when a call actually arrives.
 *
 * Progress is read from the server, which derives it from the data rather than
 * from a stored counter. A wizard that tracks its own position gets out of
 * step the moment somebody edits something on the Mabel screen instead.
 */

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError, api } from "@/lib/api";

interface StepState {
  key: string;
  label: string;
  complete: boolean;
  detail: string | null;
}

interface OnboardingState {
  steps: StepState[];
  current: string;
  complete: boolean;
  did_display: string | null;
}

interface VerificationResult {
  verified: boolean;
  message: string;
  call_at: string | null;
}

export default function OnboardingPage() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [step, setStep] = useState<string | null>(null);

  const { data, isPending } = useQuery({
    queryKey: ["onboarding"],
    queryFn: () => api.get<OnboardingState>("/api/onboarding/state"),
  });

  if (isPending) {
    return <div className="h-96 animate-pulse rounded-lg bg-[var(--cream-dark)]" />;
  }
  if (!data) return <p className="text-base">Couldn&apos;t load your setup.</p>;

  const active = step ?? data.current;
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["onboarding"] });

  return (
    <div className="mx-auto max-w-2xl space-y-8">
      <div>
        <h1 className="font-serif text-3xl">Setting up Mabel</h1>
        <p className="mt-1 text-base text-[var(--taupe)]">
          About fifteen minutes. Most of it is already filled in.
        </p>
      </div>

      <ol className="space-y-1">
        {data.steps.map((entry, index) => (
          <li key={entry.key}>
            <button
              type="button"
              onClick={() => setStep(entry.key)}
              className={[
                "flex min-h-[48px] w-full items-center gap-3 rounded px-3 text-left text-base",
                active === entry.key ? "bg-[var(--cream-dark)]" : "",
              ].join(" ")}
            >
              <span
                aria-hidden
                className={[
                  "flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-sm",
                  entry.complete
                    ? "bg-[var(--charcoal)] text-white"
                    : "border border-[var(--line)]",
                ].join(" ")}
              >
                {entry.complete ? "✓" : index + 1}
              </span>
              {entry.label}
              {entry.detail && (
                <span className="ml-auto text-sm text-[var(--taupe)]">
                  {entry.detail}
                </span>
              )}
            </button>
          </li>
        ))}
      </ol>

      <div className="rounded-lg border border-[var(--line)] bg-white p-6">
        {active === "business" && <BusinessStep onSaved={refresh} />}
        {active === "notify" && <NotifyStep onSaved={refresh} />}
        {active === "forward" && (
          <ForwardStep did={data.did_display} onVerified={refresh} />
        )}
        {["hours", "services", "emergencies"].includes(active) && (
          <PointAtMabelScreen step={active} />
        )}
      </div>

      {data.complete && (
        <button
          type="button"
          onClick={() => router.replace("/dashboard")}
          className="min-h-[56px] w-full rounded-lg bg-[var(--charcoal)] text-base text-white"
        >
          All set — take me to the dashboard
        </button>
      )}
    </div>
  );
}

function BusinessStep({ onSaved }: { onSaved: () => void }) {
  const [name, setName] = useState("");
  const [trade, setTrade] = useState("plumbing");
  const [timezone, setTimezone] = useState(
    Intl.DateTimeFormat().resolvedOptions().timeZone,
  );
  const [problem, setProblem] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: () =>
      api.put("/api/onboarding/business", {
        business_name: name,
        trade,
        timezone,
      }),
    onSuccess: onSaved,
    onError: (error) =>
      setProblem(error instanceof ApiError ? error.detail : "Couldn't save."),
  });

  return (
    <section className="space-y-4">
      <h2 className="font-serif text-2xl">Your business</h2>
      <label className="block text-base">
        Business name
        <input
          value={name}
          onChange={(event) => setName(event.target.value)}
          className="mt-1 min-h-[48px] w-full rounded border border-[var(--line)] px-3 text-base"
        />
      </label>
      <label className="block text-base">
        Trade
        <select
          value={trade}
          onChange={(event) => setTrade(event.target.value)}
          className="mt-1 min-h-[48px] w-full rounded border border-[var(--line)] px-3 text-base"
        >
          {[
            "plumbing",
            "hvac",
            "electrical",
            "restoration",
            "roofing",
            "locksmith",
            "towing",
          ].map((value) => (
            <option key={value} value={value}>
              {value}
            </option>
          ))}
        </select>
        <span className="text-sm text-[var(--taupe)]">
          This picks the emergency rules Mabel starts with.
        </span>
      </label>
      <label className="block text-base">
        Timezone
        <input
          value={timezone}
          onChange={(event) => setTimezone(event.target.value)}
          className="mt-1 min-h-[48px] w-full rounded border border-[var(--line)] px-3 text-base"
        />
      </label>
      {problem && <p className="text-base text-red-700">{problem}</p>}
      <button
        type="button"
        onClick={() => save.mutate()}
        disabled={save.isPending}
        className="min-h-[48px] rounded-lg bg-[var(--charcoal)] px-6 text-base text-white"
      >
        Save and continue
      </button>
    </section>
  );
}

function NotifyStep({ onSaved }: { onSaved: () => void }) {
  const [phone, setPhone] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [sent, setSent] = useState(false);

  const save = useMutation({
    mutationFn: () => api.put("/api/onboarding/notify", { phone_e164: phone }),
    onSuccess: () => {
      setProblem(null);
      setSent(true);
      onSaved();
    },
    onError: (error) =>
      setProblem(
        error instanceof ApiError ? error.detail : "Couldn't save that number.",
      ),
  });

  return (
    <section className="space-y-4">
      <h2 className="font-serif text-2xl">Who to text</h2>
      <p className="text-base text-[var(--taupe)]">
        Where the 3am text goes when something&apos;s urgent, and the 7am recap
        every morning.
      </p>
      <label className="block text-base">
        Mobile number
        <input
          type="tel"
          inputMode="tel"
          value={phone}
          onChange={(event) => setPhone(event.target.value)}
          placeholder="(216) 555-0148"
          className="mt-1 min-h-[48px] w-full rounded border border-[var(--line)] px-3 text-base"
        />
      </label>
      {problem && <p className="text-base text-red-700">{problem}</p>}
      {sent ? (
        <p className="text-base">
          A confirmation text is on its way. If it doesn&apos;t arrive, the
          number may be wrong — try again.
        </p>
      ) : (
        <button
          type="button"
          onClick={() => save.mutate()}
          disabled={save.isPending}
          className="min-h-[48px] rounded-lg bg-[var(--charcoal)] px-6 text-base text-white"
        >
          Save and send me a test
        </button>
      )}
    </section>
  );
}

function ForwardStep({
  did,
  onVerified,
}: {
  did: string | null;
  onVerified: () => void;
}) {
  const { data } = useQuery({
    queryKey: ["onboarding", "verify"],
    queryFn: () => api.get<VerificationResult>("/api/onboarding/verify-forwarding"),
    // Polled while they make the test call. Stops once it has landed.
    refetchInterval: (query) =>
      query.state.data?.verified ? false : 5_000,
  });

  if (data?.verified) onVerified();

  return (
    <section className="space-y-4">
      <h2 className="font-serif text-2xl">Forward your phone</h2>
      <p className="text-base">
        This is the step that matters. Everything else is settings; this is what
        makes Mabel answer.
      </p>

      {did && (
        <div className="rounded-lg bg-[var(--cream-dark)] p-5 text-center">
          <p className="text-base text-[var(--taupe)]">Forward your line to</p>
          <p className="mt-1 font-serif text-3xl">{did}</p>
        </div>
      )}

      <p className="text-base">
        The exact codes for your carrier are on the Settings screen. Once
        you&apos;ve dialled them, ring your business line from another phone and
        let it go through.
      </p>

      <div
        className={[
          "rounded-lg border-2 p-5",
          data?.verified
            ? "border-green-600 bg-green-50"
            : "border-[var(--line)] bg-white",
        ].join(" ")}
        aria-live="polite"
      >
        <p className="text-base">
          {data?.verified ? "✓ " : ""}
          {data?.message ?? "Waiting for a call…"}
        </p>
      </div>

      <FinishAnyway />
    </section>
  );
}

function FinishAnyway() {
  const router = useRouter();
  const [acknowledged, setAcknowledged] = useState(false);

  const finish = useMutation({
    mutationFn: () =>
      api.post("/api/onboarding/complete", { acknowledged_untested: true }),
    onSuccess: () => router.replace("/dashboard"),
  });

  return (
    <details className="text-base">
      <summary className="min-h-[48px] cursor-pointer text-[var(--taupe)]">
        Can&apos;t test it right now?
      </summary>
      <div className="mt-3 space-y-3">
        <p>
          {/* Some contractors set forwarding up at the carrier's office and
              cannot test it there and then. Refusing to let them finish would
              be worse than letting them — and the Settings indicator stays red
              until a call actually arrives. */}
          You can finish now and test later. Your Settings screen will show red
          until a call actually reaches Mabel, so you won&apos;t forget.
        </p>
        <label className="flex min-h-[48px] items-center gap-2">
          <input
            type="checkbox"
            checked={acknowledged}
            onChange={(event) => setAcknowledged(event.target.checked)}
            className="h-5 w-5"
          />
          I understand Mabel won&apos;t answer until forwarding is on
        </label>
        <button
          type="button"
          disabled={!acknowledged || finish.isPending}
          onClick={() => finish.mutate()}
          className="min-h-[48px] rounded-lg border border-[var(--charcoal)] px-4 text-base disabled:opacity-50"
        >
          Finish anyway
        </button>
      </div>
    </details>
  );
}

function PointAtMabelScreen({ step }: { step: string }) {
  const labels: Record<string, string> = {
    hours: "When you're closed",
    services: "What you do, and where",
    emergencies: "What's worth waking you",
  };
  const tabs: Record<string, string> = {
    hours: "Hours",
    services: "Services",
    emergencies: "Emergencies",
  };

  return (
    <section className="space-y-4">
      <h2 className="font-serif text-2xl">{labels[step]}</h2>
      <p className="text-base">
        {/* One editor, not two. A second copy of the hours grid inside the
            wizard is a second thing to keep in step with the schema. */}
        This lives on the Mabel screen, under {tabs[step]} — the same editor
        you&apos;ll use later when things change.
      </p>
      <a
        href="/mabel"
        className="inline-block min-h-[48px] rounded-lg bg-[var(--charcoal)] px-6 py-3 text-base text-white"
      >
        Open {tabs[step]}
      </a>
    </section>
  );
}
