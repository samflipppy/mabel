"use client";

import { useEffect, useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { mabelFetch, readOfficeCreds, writeOfficeCreds } from "@/lib/office";

type OvernightLead = {
  time: string;
  name: string;
  problem: string;
  emergency: boolean;
  sms_sent: boolean;
  sms_reason: string | null;
};

export default function OvernightPage() {
  const [tenantId, setTenantId] = useState("");
  const [token, setToken] = useState("");
  const [shopName, setShopName] = useState("");
  const [leads, setLeads] = useState<OvernightLead[] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    const creds = readOfficeCreds();
    setTenantId(creds.tenantId);
    setToken(creds.token);
  }, []);

  async function load(event: FormEvent) {
    event.preventDefault();
    setError("");
    writeOfficeCreds(tenantId.trim(), token);
    const response = await mabelFetch(`/shops/${tenantId.trim()}/overnight`, token);
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      setLeads(null);
      setError(typeof body.detail === "string" ? body.detail : "Mabel could not load last night.");
      return;
    }
    setShopName(body.shop_name || "");
    setLeads(Array.isArray(body.leads) ? body.leads : []);
  }

  return (
    <div className="flex flex-col gap-8">
      <div>
        <p className="text-sm uppercase tracking-wide text-[color:var(--taupe)]">Office</p>
        <h1 className="mt-1 font-serif text-4xl">Overnight recap</h1>
        <p className="mt-3 max-w-xl text-[color:var(--taupe)]">
          The owner gets a text at 7am. This page is for whoever handles the office. If nobody
          logs in, Mabel still did the job.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Which shop</CardTitle>
          <CardDescription>
            Tenant id from onboard. Admin token is the same one as POST /shops. Not stored in git.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form className="flex flex-col gap-3" onSubmit={load}>
            <label className="text-sm">
              Tenant id
              <input
                className="mt-1 w-full rounded-md border border-[color:var(--line)] bg-white px-3 py-2"
                value={tenantId}
                onChange={(event) => setTenantId(event.target.value)}
                autoComplete="off"
              />
            </label>
            <label className="text-sm">
              Admin token
              <input
                className="mt-1 w-full rounded-md border border-[color:var(--line)] bg-white px-3 py-2"
                type="password"
                value={token}
                onChange={(event) => setToken(event.target.value)}
                autoComplete="off"
              />
            </label>
            <Button type="submit" className="self-start">
              Load last night
            </Button>
            {error ? <p className="text-sm text-red-800">{error}</p> : null}
          </form>
        </CardContent>
      </Card>

      {leads === null || leads.length === 0 ? (
        <Card>
          <CardHeader>
            <CardTitle>No calls yet</CardTitle>
            <CardDescription>When Mabel takes one, it lands here. Nothing made up.</CardDescription>
          </CardHeader>
          <CardContent>
            <p className="text-sm text-[color:var(--taupe)]">
              Emergencies already went out as a text. Everything else waits for morning.
            </p>
          </CardContent>
        </Card>
      ) : (
        <div className="flex flex-col gap-4">
          {shopName ? (
            <p className="text-sm text-[color:var(--taupe)]">{shopName}</p>
          ) : null}
          {leads.map((lead) => (
            <Card key={`${lead.time}-${lead.name}-${lead.problem}`}>
              <CardHeader>
                <CardTitle>{lead.name || "Name not captured"}</CardTitle>
                <CardDescription>{formatTime(lead.time)}</CardDescription>
              </CardHeader>
              <CardContent className="flex flex-col gap-1 text-sm">
                <p>{lead.problem}</p>
                <p>Emergency: {lead.emergency ? "yes" : "no"}</p>
                <p>Owner text: {lead.sms_sent ? "sent" : "unsent"}</p>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}

function formatTime(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return parsed.toLocaleString();
}
