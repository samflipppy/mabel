"use client";

/**
 * The Mabel configuration screen. 02-PORTAL.md's differentiator: "Everyone
 * else makes you email support to change your hours."
 *
 * Seven tabs, and the important thing about them is that editing never touches
 * what she is running right now. Every save is a new version; Publish flips
 * which one is live. That is what makes Revert honest — it publishes an older
 * version rather than trying to remember what the old values were.
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError, api } from "@/lib/api";

const TABS = [
  "Voice",
  "Hours",
  "Services",
  "Emergencies",
  "Knowledge",
  "Team",
  "Test",
] as const;

type Tab = (typeof TABS)[number];

const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"] as const;

interface ConfigVersion {
  id: string;
  version: number;
  is_live: boolean;
  greeting: string;
  voice: string;
  speaking_rate: number;
  services: string[];
  service_area_zips: string[];
  service_area_note: string | null;
  business_hours: Record<string, { open: string; close: string } | null>;
  after_hours_only: boolean;
  never_say: string[];
  custom_rules: string | null;
  keyterms: string[];
  emergency_overrides: Record<string, { severity?: string; enabled?: boolean }>;
  created_at: string;
  published_at: string | null;
  created_by_email: string | null;
}

interface TriggerToggle {
  code: string;
  label: string;
  severity: string;
  default_severity: string;
  enabled: boolean;
  has_safety_script: boolean;
}

export default function MabelPage() {
  const [tab, setTab] = useState<Tab>("Voice");

  const { data: config, isPending } = useQuery({
    queryKey: ["config", "current"],
    queryFn: () => api.get<ConfigVersion>("/api/config/current"),
    retry: false,
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-serif text-3xl">Mabel</h1>
        <p className="mt-1 text-base text-[var(--taupe)]">
          What she says, when she answers, and who she wakes up.
        </p>
      </div>

      <div
        role="tablist"
        className="flex gap-1 overflow-x-auto border-b border-[var(--line)]"
      >
        {TABS.map((name) => (
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

      {isPending ? (
        <div className="h-64 animate-pulse rounded-lg bg-[var(--cream-dark)]" />
      ) : !config ? (
        <NotConfiguredYet />
      ) : (
        <>
          {tab === "Voice" && <VoiceTab config={config} />}
          {tab === "Hours" && <HoursTab config={config} />}
          {tab === "Services" && <ServicesTab config={config} />}
          {tab === "Emergencies" && <EmergenciesTab config={config} />}
          {tab === "Knowledge" && <KnowledgeTab />}
          {tab === "Team" && <TeamTab />}
          {tab === "Test" && <TestTab />}
        </>
      )}

      {config && <ChangeLog current={config} />}
    </div>
  );
}

function NotConfiguredYet() {
  return (
    <p className="rounded-lg border border-[var(--line)] bg-white px-6 py-12 text-center text-base">
      Nothing published yet. Finish onboarding and Mabel will have something to
      say.
    </p>
  );
}

/** Saves a draft and publishes it. One button, because a contractor does not
 * want a two-step workflow to change his hours. */
function useSaveAndPublish() {
  const queryClient = useQueryClient();
  const [problem, setProblem] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: async (draft: Record<string, unknown>) => {
      const saved = await api.post<ConfigVersion>("/api/config/draft", draft);
      return api.post<ConfigVersion>(`/api/config/${saved.id}/publish`);
    },
    onSuccess: () => {
      setProblem(null);
      queryClient.invalidateQueries({ queryKey: ["config"] });
    },
    onError: (error) => {
      // A 422 here is the money check. Its message names the field, so it is
      // worth showing verbatim rather than replacing with something generic.
      setProblem(
        error instanceof ApiError ? error.detail : "Couldn't save that.",
      );
    },
  });

  return { ...mutation, problem };
}

function draftFrom(config: ConfigVersion, overrides: Record<string, unknown>) {
  return {
    greeting: config.greeting,
    voice: config.voice,
    speaking_rate: config.speaking_rate,
    services: config.services,
    service_area_zips: config.service_area_zips,
    service_area_note: config.service_area_note,
    business_hours: config.business_hours,
    after_hours_only: config.after_hours_only,
    keyterms: config.keyterms,
    custom_rules: config.custom_rules,
    emergency_overrides: config.emergency_overrides,
    ...overrides,
  };
}

function VoiceTab({ config }: { config: ConfigVersion }) {
  const [greeting, setGreeting] = useState(config.greeting);
  const [rate, setRate] = useState(config.speaking_rate);
  const [keyterms, setKeyterms] = useState(config.keyterms.join(", "));
  const save = useSaveAndPublish();

  return (
    <Panel
      title="Voice"
      onSave={() =>
        save.mutate(
          draftFrom(config, {
            greeting,
            speaking_rate: rate,
            keyterms: keyterms
              .split(",")
              .map((term) => term.trim())
              .filter(Boolean),
          }),
        )
      }
      saving={save.isPending}
      problem={save.problem}
    >
      <label className="block text-base">
        Greeting
        <textarea
          value={greeting}
          onChange={(event) => setGreeting(event.target.value)}
          rows={3}
          maxLength={500}
          className="mt-1 w-full rounded-lg border border-[var(--line)] bg-white p-3 text-base"
        />
        <span className="text-sm text-[var(--taupe)]">
          {greeting.length}/500
        </span>
      </label>

      <label className="block text-base">
        Speaking rate: {rate.toFixed(2)}×
        <input
          type="range"
          min={0.5}
          max={2}
          step={0.05}
          value={rate}
          onChange={(event) => setRate(Number(event.target.value))}
          className="mt-2 w-full"
        />
      </label>

      <label className="block text-base">
        Words she should recognise
        <input
          value={keyterms}
          onChange={(event) => setKeyterms(event.target.value)}
          placeholder="Detroit Ave, Rinnai, Lakewood"
          className="mt-1 min-h-[48px] w-full rounded-lg border border-[var(--line)] bg-white px-3 text-base"
        />
        <span className="text-sm text-[var(--taupe)]">
          Street names, neighbourhoods, brands. A phone line mangles these more
          than anything else.
        </span>
      </label>
    </Panel>
  );
}

function HoursTab({ config }: { config: ConfigVersion }) {
  const [hours, setHours] = useState(config.business_hours);
  const [afterHoursOnly, setAfterHoursOnly] = useState(config.after_hours_only);
  const save = useSaveAndPublish();

  return (
    <Panel
      title="Hours"
      onSave={() =>
        save.mutate(
          draftFrom(config, {
            business_hours: hours,
            after_hours_only: afterHoursOnly,
          }),
        )
      }
      saving={save.isPending}
      problem={save.problem}
    >
      <p className="text-base text-[var(--taupe)]">
        When you&apos;re open. Mabel answers outside these.
      </p>
      <div className="space-y-2">
        {DAYS.map((day) => {
          const value = hours[day];
          return (
            <div key={day} className="flex items-center gap-3">
              <label className="flex min-h-[48px] w-32 items-center gap-2 text-base capitalize">
                <input
                  type="checkbox"
                  checked={value !== null && value !== undefined}
                  onChange={(event) =>
                    setHours({
                      ...hours,
                      [day]: event.target.checked
                        ? { open: "08:00", close: "17:00" }
                        : null,
                    })
                  }
                  className="h-5 w-5"
                />
                {day}
              </label>
              {value && (
                <>
                  <input
                    type="time"
                    value={value.open}
                    onChange={(event) =>
                      setHours({
                        ...hours,
                        [day]: { ...value, open: event.target.value },
                      })
                    }
                    className="min-h-[48px] rounded border border-[var(--line)] px-2 text-base"
                  />
                  <span className="text-base text-[var(--taupe)]">to</span>
                  <input
                    type="time"
                    value={value.close}
                    onChange={(event) =>
                      setHours({
                        ...hours,
                        [day]: { ...value, close: event.target.value },
                      })
                    }
                    className="min-h-[48px] rounded border border-[var(--line)] px-2 text-base"
                  />
                </>
              )}
            </div>
          );
        })}
      </div>

      <label className="flex min-h-[48px] items-center gap-2 text-base">
        <input
          type="checkbox"
          checked={afterHoursOnly}
          onChange={(event) => setAfterHoursOnly(event.target.checked)}
          className="h-5 w-5"
        />
        Only answer outside these hours
      </label>
      <p className="text-sm text-[var(--taupe)]">
        Untick it and she also picks up when your line is busy during the day.
      </p>
    </Panel>
  );
}

function ServicesTab({ config }: { config: ConfigVersion }) {
  const [services, setServices] = useState(config.services.join(", "));
  const [zips, setZips] = useState(config.service_area_zips.join(", "));
  const [note, setNote] = useState(config.service_area_note ?? "");
  const save = useSaveAndPublish();

  return (
    <Panel
      title="Services and area"
      onSave={() =>
        save.mutate(
          draftFrom(config, {
            services: splitList(services),
            service_area_zips: splitList(zips),
            service_area_note: note || null,
          }),
        )
      }
      saving={save.isPending}
      problem={save.problem}
    >
      <label className="block text-base">
        What you do
        <input
          value={services}
          onChange={(event) => setServices(event.target.value)}
          placeholder="drain cleaning, water heaters, repiping"
          className="mt-1 min-h-[48px] w-full rounded-lg border border-[var(--line)] bg-white px-3 text-base"
        />
      </label>

      <label className="block text-base">
        ZIP codes you cover
        <input
          value={zips}
          onChange={(event) => setZips(event.target.value)}
          placeholder="44107, 44116, 44126"
          className="mt-1 min-h-[48px] w-full rounded-lg border border-[var(--line)] bg-white px-3 text-base"
        />
      </label>

      <label className="block text-base">
        What she says to someone out of area
        <textarea
          value={note}
          onChange={(event) => setNote(event.target.value)}
          rows={2}
          placeholder="We don't get out that far, but leave a number and Ray will call you back."
          className="mt-1 w-full rounded-lg border border-[var(--line)] bg-white p-3 text-base"
        />
        <span className="text-sm text-[var(--taupe)]">
          She takes a message either way — you still want to know they called.
        </span>
      </label>
    </Panel>
  );
}

function EmergenciesTab({ config }: { config: ConfigVersion }) {
  const { data: toggles, isPending } = useQuery({
    queryKey: ["config", "emergencies"],
    queryFn: () => api.get<TriggerToggle[]>("/api/config/emergencies"),
  });
  const [overrides, setOverrides] = useState(config.emergency_overrides);
  const [customRules, setCustomRules] = useState(config.custom_rules ?? "");
  const save = useSaveAndPublish();

  if (isPending) {
    return <div className="h-64 animate-pulse rounded-lg bg-[var(--cream-dark)]" />;
  }

  return (
    <Panel
      title="Emergencies"
      onSave={() =>
        save.mutate(
          draftFrom(config, {
            emergency_overrides: overrides,
            custom_rules: customRules || null,
          }),
        )
      }
      saving={save.isPending}
      problem={save.problem}
    >
      <p className="text-base text-[var(--taupe)]">
        What&apos;s worth waking you up for.
      </p>
      <ul className="space-y-2">
        {(toggles ?? []).map((trigger) => {
          const override = overrides[trigger.code] ?? {};
          const severity = override.severity ?? trigger.default_severity;
          const enabled = override.enabled !== false;
          return (
            <li
              key={trigger.code}
              className="flex flex-wrap items-center gap-3 rounded-lg border border-[var(--line)] bg-white p-4"
            >
              <span className="flex-1 text-base">{trigger.label}</span>
              {trigger.has_safety_script && (
                <span className="rounded bg-[var(--cream-dark)] px-2 py-0.5 text-sm">
                  she gives safety advice
                </span>
              )}
              <select
                value={enabled ? severity : "off"}
                onChange={(event) => {
                  const next = event.target.value;
                  setOverrides({
                    ...overrides,
                    [trigger.code]:
                      next === "off"
                        ? { enabled: false }
                        : { severity: next, enabled: true },
                  });
                }}
                className="min-h-[48px] rounded border border-[var(--line)] px-3 text-base"
                aria-label={trigger.label}
              >
                <option value="wake_now">Wake me</option>
                <option value="morning">Morning is fine</option>
                <option value="routine">Whenever</option>
                <option value="off">Don&apos;t flag it</option>
              </select>
            </li>
          );
        })}
      </ul>

      <label className="block text-base">
        Anything else she should know
        <textarea
          value={customRules}
          onChange={(event) => setCustomRules(event.target.value)}
          rows={4}
          className="mt-1 w-full rounded-lg border border-[var(--line)] bg-white p-3 text-base"
        />
        <span className="text-sm text-[var(--taupe)]">
          Notes about your business. Don&apos;t put prices here — she isn&apos;t
          allowed to quote, and this will be rejected if it has an amount in it.
        </span>
      </label>
    </Panel>
  );
}

function KnowledgeTab() {
  const queryClient = useQueryClient();
  interface Item {
    id?: string | null;
    question: string;
    answer: string;
    sort_order: number;
    is_active: boolean;
  }
  const { data, isPending } = useQuery({
    queryKey: ["config", "knowledge"],
    queryFn: () => api.get<Item[]>("/api/config/knowledge"),
  });
  const [items, setItems] = useState<Item[] | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: (next: Item[]) => api.put<Item[]>("/api/config/knowledge", next),
    onSuccess: () => {
      setProblem(null);
      queryClient.invalidateQueries({ queryKey: ["config", "knowledge"] });
    },
    onError: (error) =>
      setProblem(error instanceof ApiError ? error.detail : "Couldn't save."),
  });

  if (isPending) {
    return <div className="h-64 animate-pulse rounded-lg bg-[var(--cream-dark)]" />;
  }

  const current = items ?? data ?? [];

  return (
    <Panel
      title="Questions she can answer"
      onSave={() => save.mutate(current)}
      saving={save.isPending}
      problem={problem}
    >
      <p className="text-base text-[var(--taupe)]">
        She reads these back word for word. If a caller asks something not on
        this list, she says someone will follow up — she never guesses.
      </p>
      {current.map((item, index) => (
        <div
          key={index}
          className="space-y-2 rounded-lg border border-[var(--line)] bg-white p-4"
        >
          <input
            value={item.question}
            onChange={(event) => {
              const next = [...current];
              next[index] = { ...item, question: event.target.value };
              setItems(next);
            }}
            placeholder="Do you do drywall repair?"
            className="min-h-[48px] w-full rounded border border-[var(--line)] px-3 text-base"
          />
          <textarea
            value={item.answer}
            onChange={(event) => {
              const next = [...current];
              next[index] = { ...item, answer: event.target.value };
              setItems(next);
            }}
            rows={2}
            placeholder="Yes, as part of a painting job."
            className="w-full rounded border border-[var(--line)] p-3 text-base"
          />
          <button
            type="button"
            onClick={() => setItems(current.filter((_, i) => i !== index))}
            className="min-h-[48px] text-base underline underline-offset-4"
          >
            Remove
          </button>
        </div>
      ))}
      <button
        type="button"
        onClick={() =>
          setItems([
            ...current,
            { question: "", answer: "", sort_order: current.length, is_active: true },
          ])
        }
        className="min-h-[48px] rounded-lg border border-[var(--line)] px-4 text-base"
      >
        Add a question
      </button>
    </Panel>
  );
}

function TeamTab() {
  interface Member {
    id: string;
    email: string;
    full_name: string | null;
    phone_e164: string | null;
    role: string;
    notify_emergencies: boolean;
    notify_recap: boolean;
  }
  const queryClient = useQueryClient();
  const { data, isPending } = useQuery({
    queryKey: ["settings", "team"],
    queryFn: () => api.get<Member[]>("/api/settings/team"),
  });

  const update = useMutation({
    mutationFn: ({ id, ...prefs }: { id: string } & Record<string, unknown>) =>
      api.put(`/api/settings/team/${id}/notifications`, prefs),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["settings", "team"] }),
  });

  if (isPending) {
    return <div className="h-64 animate-pulse rounded-lg bg-[var(--cream-dark)]" />;
  }

  return (
    <section className="space-y-4">
      <h2 className="font-serif text-2xl">Who gets told what</h2>
      <ul className="divide-y divide-[var(--line)] rounded-lg border border-[var(--line)] bg-white">
        {(data ?? []).map((member) => (
          <li key={member.id} className="flex flex-wrap items-center gap-4 p-4">
            <div className="flex-1">
              <p className="text-base font-medium">
                {member.full_name ?? member.email}
              </p>
              <p className="text-sm text-[var(--taupe)]">
                {member.role}
                {member.phone_e164 ? ` · ${member.phone_e164}` : " · no number"}
              </p>
            </div>
            <label className="flex min-h-[48px] items-center gap-2 text-base">
              <input
                type="checkbox"
                checked={member.notify_emergencies}
                onChange={(event) =>
                  update.mutate({
                    id: member.id,
                    notify_emergencies: event.target.checked,
                    notify_recap: member.notify_recap,
                  })
                }
                className="h-5 w-5"
              />
              Emergencies
            </label>
            <label className="flex min-h-[48px] items-center gap-2 text-base">
              <input
                type="checkbox"
                checked={member.notify_recap}
                onChange={(event) =>
                  update.mutate({
                    id: member.id,
                    notify_emergencies: member.notify_emergencies,
                    notify_recap: event.target.checked,
                  })
                }
                className="h-5 w-5"
              />
              7am recap
            </label>
          </li>
        ))}
      </ul>
    </section>
  );
}

function TestTab() {
  interface Result {
    placed: boolean;
    calling: string | null;
    message: string;
  }
  const [result, setResult] = useState<Result | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  const call = useMutation({
    mutationFn: () => api.post<Result>("/api/config/test-call"),
    onSuccess: (data) => {
      setProblem(null);
      setResult(data);
    },
    onError: (error) =>
      setProblem(
        error instanceof ApiError ? error.detail : "Couldn't start a test call.",
      ),
  });

  return (
    <section className="space-y-4">
      <h2 className="font-serif text-2xl">Hear her</h2>
      <p className="text-base text-[var(--taupe)]">
        Mabel answers inbound. She will not ring you. Call your business line
        and we&apos;ll tell you when it reaches her.
      </p>
      <button
        type="button"
        onClick={() => call.mutate()}
        disabled={call.isPending}
        className="min-h-[64px] w-full rounded-lg bg-[var(--charcoal)] px-6 font-serif text-2xl text-white"
      >
        {call.isPending ? "Checking…" : "Call Mabel now"}
      </button>
      {problem && <p className="text-base text-red-700">{problem}</p>}
      {result && <p className="text-base">{result.message}</p>}
    </section>
  );
}

function ChangeLog({ current }: { current: ConfigVersion }) {
  const queryClient = useQueryClient();
  const { data } = useQuery({
    queryKey: ["config", "versions"],
    queryFn: () => api.get<ConfigVersion[]>("/api/config/versions"),
  });

  const revert = useMutation({
    mutationFn: (id: string) => api.post(`/api/config/${id}/publish`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["config"] }),
  });

  if (!data || data.length <= 1) return null;

  return (
    <section className="pt-6">
      <h2 className="font-serif text-2xl">Change log</h2>
      <ul className="mt-3 divide-y divide-[var(--line)] rounded-lg border border-[var(--line)] bg-white">
        {data.map((version) => (
          <li key={version.id} className="flex items-center gap-4 p-4">
            <span className="text-base">v{version.version}</span>
            <span className="text-base text-[var(--taupe)]">
              {new Date(version.created_at).toLocaleDateString()}
              {version.created_by_email ? ` · ${version.created_by_email}` : ""}
            </span>
            {version.is_live && (
              <span className="rounded bg-[var(--cream-dark)] px-2 py-0.5 text-sm">
                live
              </span>
            )}
            {!version.is_live && (
              <button
                type="button"
                onClick={() => revert.mutate(version.id)}
                className="ml-auto min-h-[48px] text-base underline underline-offset-4"
              >
                Go back to this
              </button>
            )}
          </li>
        ))}
      </ul>
      <p className="mt-2 text-sm text-[var(--taupe)]">
        Currently live: v{current.version}. Going back publishes an older
        version — nothing is lost either way.
      </p>
    </section>
  );
}

function Panel({
  title,
  children,
  onSave,
  saving,
  problem,
}: {
  title: string;
  children: React.ReactNode;
  onSave: () => void;
  saving: boolean;
  problem: string | null;
}) {
  return (
    <section className="space-y-4">
      <h2 className="font-serif text-2xl">{title}</h2>
      {children}
      {problem && (
        <p className="rounded-lg border border-red-300 bg-red-50 p-3 text-base text-red-800">
          {problem}
        </p>
      )}
      <button
        type="button"
        onClick={onSave}
        disabled={saving}
        className="min-h-[48px] rounded-lg bg-[var(--charcoal)] px-6 text-base text-white"
      >
        {saving ? "Saving…" : "Save and go live"}
      </button>
    </section>
  );
}

function splitList(value: string): string[] {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}
