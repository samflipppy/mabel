"use client";

import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { mabelFetch, readOfficeCreds, writeOfficeCreds } from "@/lib/office";

type ShopSettings = {
  name: string;
  timezone: string;
  owner_sms_e164: string;
  after_hours_start: string;
  after_hours_end: string;
  service_area_zips: string[];
  greeting_notes: string | null;
};

const empty: ShopSettings = {
  name: "",
  timezone: "America/New_York",
  owner_sms_e164: "",
  after_hours_start: "17:00",
  after_hours_end: "08:00",
  service_area_zips: [],
  greeting_notes: "",
};

export default function SettingsPage() {
  const [tenantId, setTenantId] = useState("");
  const [token, setToken] = useState("");
  const [form, setForm] = useState<ShopSettings>(empty);
  const [zipsText, setZipsText] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    const creds = readOfficeCreds();
    setTenantId(creds.tenantId);
    setToken(creds.token);
  }, []);

  async function load(event?: FormEvent) {
    event?.preventDefault();
    setError("");
    setMessage("");
    writeOfficeCreds(tenantId.trim(), token);
    const response = await mabelFetch(`/shops/${tenantId.trim()}`, token);
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      setError(typeof body.detail === "string" ? body.detail : "Mabel could not load this shop.");
      return;
    }
    setForm({
      name: body.name || "",
      timezone: body.timezone || "America/New_York",
      owner_sms_e164: body.owner_sms_e164 || "",
      after_hours_start: String(body.after_hours_start || "17:00").slice(0, 5),
      after_hours_end: String(body.after_hours_end || "08:00").slice(0, 5),
      service_area_zips: body.service_area_zips || [],
      greeting_notes: body.greeting_notes || "",
    });
    setZipsText((body.service_area_zips || []).join(", "));
    setMessage("Loaded.");
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    setError("");
    setMessage("");
    writeOfficeCreds(tenantId.trim(), token);
    const zips = zipsText
      .split(/[\s,]+/)
      .map((item) => item.trim())
      .filter(Boolean);
    const response = await mabelFetch(`/shops/${tenantId.trim()}`, token, {
      method: "PATCH",
      body: JSON.stringify({
        name: form.name,
        timezone: form.timezone,
        owner_sms_e164: form.owner_sms_e164,
        after_hours_start: form.after_hours_start,
        after_hours_end: form.after_hours_end,
        service_area_zips: zips,
        greeting_notes: form.greeting_notes || null,
      }),
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      setError(typeof body.detail === "string" ? body.detail : "Mabel could not save this shop.");
      return;
    }
    setMessage("Saved.");
    setForm((current) => ({ ...current, service_area_zips: body.service_area_zips || zips }));
  }

  return (
    <div className="flex flex-col gap-8">
      <div>
        <p className="text-sm uppercase tracking-wide text-[color:var(--taupe)]">Office</p>
        <h1 className="mt-1 font-serif text-4xl">Settings</h1>
        <p className="mt-3 max-w-xl text-[color:var(--taupe)]">
          Shop name, hours, timezone, owner SMS, service-area zips, greeting notes. Emergency
          rules are not editable here. Dollar-looking greeting notes are rejected.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Which shop</CardTitle>
          <CardDescription>Same admin token as POST /shops. Hire Mabel if this is not your shop.</CardDescription>
        </CardHeader>
        <CardContent>
          <form className="flex flex-col gap-3" onSubmit={load}>
            <Field label="Tenant id">
              <input
                className={inputClass}
                value={tenantId}
                onChange={(event) => setTenantId(event.target.value)}
                autoComplete="off"
              />
            </Field>
            <Field label="Admin token">
              <input
                className={inputClass}
                type="password"
                value={token}
                onChange={(event) => setToken(event.target.value)}
                autoComplete="off"
              />
            </Field>
            <Button type="submit" variant="outline" className="self-start">
              Load shop
            </Button>
          </form>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Shop packet</CardTitle>
          <CardDescription>Vertical emergency rules stay in the rule library. Not on this page.</CardDescription>
        </CardHeader>
        <CardContent>
          <form className="flex flex-col gap-3" onSubmit={save}>
            <Field label="Shop name">
              <input
                className={inputClass}
                value={form.name}
                onChange={(event) => setForm({ ...form, name: event.target.value })}
              />
            </Field>
            <Field label="Timezone">
              <input
                className={inputClass}
                value={form.timezone}
                onChange={(event) => setForm({ ...form, timezone: event.target.value })}
              />
            </Field>
            <Field label="Owner SMS">
              <input
                className={inputClass}
                value={form.owner_sms_e164}
                onChange={(event) => setForm({ ...form, owner_sms_e164: event.target.value })}
              />
            </Field>
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="After hours start">
                <input
                  className={inputClass}
                  type="time"
                  value={form.after_hours_start}
                  onChange={(event) => setForm({ ...form, after_hours_start: event.target.value })}
                />
              </Field>
              <Field label="Shop opens">
                <input
                  className={inputClass}
                  type="time"
                  value={form.after_hours_end}
                  onChange={(event) => setForm({ ...form, after_hours_end: event.target.value })}
                />
              </Field>
            </div>
            <Field label="Service-area zips">
              <input
                className={inputClass}
                value={zipsText}
                onChange={(event) => setZipsText(event.target.value)}
                placeholder="44107, 44102"
              />
            </Field>
            <Field label="Greeting notes">
              <textarea
                className={`${inputClass} min-h-24`}
                value={form.greeting_notes || ""}
                onChange={(event) => setForm({ ...form, greeting_notes: event.target.value })}
              />
            </Field>
            <Button type="submit" className="self-start">
              Save
            </Button>
            {error ? <p className="text-sm text-red-800">{error}</p> : null}
            {message ? <p className="text-sm text-[color:var(--taupe)]">{message}</p> : null}
          </form>
        </CardContent>
      </Card>
    </div>
  );
}

const inputClass =
  "mt-1 w-full rounded-md border border-[color:var(--line)] bg-white px-3 py-2 text-[color:var(--charcoal)]";

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="text-sm">
      {label}
      {children}
    </label>
  );
}
