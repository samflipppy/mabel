/**
 * The API client.
 *
 * 00-STACK.md says types are generated from the FastAPI OpenAPI schema so the
 * contract cannot drift. They are not generated yet — that needs a running API
 * to generate from, and CI does not have one. The types below are written by
 * hand to match the Pydantic models, and `npm run generate:api` will replace
 * this file once there is a schema to read. Until then, the hand-written
 * shapes are marked so nobody mistakes them for generated output.
 *
 * Every request carries the Supabase access token. None carries a tenant:
 * there is no parameter for one, on purpose, and the API would ignore it.
 */

import { getAccessToken } from "@/lib/supabase";

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "https://api.hiremabel.com";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string,
  ) {
    super(detail);
  }

  /** Session expired or never existed. The shell redirects to sign-in. */
  get isUnauthenticated(): boolean {
    return this.status === 401;
  }

  /** Authenticated with Supabase but not a user here. */
  get isNotInvited(): boolean {
    return this.status === 403;
  }

  /** The API is up but a dependency is not configured. See docs/BLOCKED.md. */
  get isUnconfigured(): boolean {
    return this.status === 503;
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = await getAccessToken();
  if (!token) {
    throw new ApiError(401, "not signed in");
  }

  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      ...init.headers,
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
  });

  if (!response.ok) {
    // The API returns `{detail}`; a proxy or a gateway might not.
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = body.detail ?? detail;
    } catch {
      // Not JSON. The status is still the useful part.
    }
    throw new ApiError(response.status, detail);
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: JSON.stringify(body ?? {}) }),
  put: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "PUT", body: JSON.stringify(body ?? {}) }),
  del: <T>(path: string) => request<T>(path, { method: "DELETE" }),
};

// ---------------------------------------------------------------------------
// HAND-WRITTEN types mirroring the Pydantic models. Replace with generated
// output once `npm run generate:api` has a schema to read.
// ---------------------------------------------------------------------------

export interface Card {
  label: string;
  /** Cents when `is_money`. Never a formatted string. */
  value: number;
  is_money: boolean;
  previous: number;
}

export interface NeedsYouRow {
  kind: "untouched_lead" | "emergency" | "qa_flag";
  id: string;
  name: string | null;
  phone_e164: string | null;
  summary: string;
  since: string;
  hours_waiting: number;
  answered: boolean | null;
}

export interface RecentCall {
  id: string;
  caller: string | null;
  from_e164: string | null;
  started_at: string;
  duration_sec: number | null;
  outcome: string | null;
  qa_flags: string[];
  has_recording: boolean;
}

export interface DayBar {
  day: string;
  total: number;
  after_hours: number;
}

export interface Dashboard {
  cards: Card[];
  needs_you: NeedsYouRow[];
  recent_calls: RecentCall[];
  this_week: DayBar[];
  is_empty: boolean;
}

export interface CallSummary extends RecentCall {
  excerpt: string | null;
}

export interface CallPage {
  calls: CallSummary[];
  total: number;
  has_more: boolean;
}

export interface Turn {
  role: string;
  text: string;
  started_ms: number | null;
  ended_ms: number | null;
}

export interface ToolCall {
  tool: string;
  ok: boolean;
  mutating: boolean;
  duration_ms: number | null;
}

export interface Extraction {
  name: string | null;
  address: string | null;
  phone: string | null;
  job_type: string | null;
  urgency: string | null;
  source: string | null;
}

export interface CallDetail {
  id: string;
  started_at: string;
  ended_at: string | null;
  duration_sec: number | null;
  caller: string | null;
  from_e164: string | null;
  to_e164: string | null;
  outcome: string | null;
  qa_flags: string[];
  qa_summary: string | null;
  turns: Turn[];
  summary: string | null;
  tool_trace: ToolCall[];
  extraction: Extraction | null;
  lead_id: string | null;
  contact_id: string | null;
  has_recording: boolean;
}

export interface Lead {
  id: string;
  caller_name: string | null;
  service_address: string | null;
  callback_e164: string | null;
  job_type: string | null;
  description: string | null;
  urgency: "routine" | "soon" | "emergency";
  source: string | null;
  status: LeadStatus;
  /** Cents, or null when the owner has not priced it. */
  value_cents: number | null;
  currency: string;
  lost_reason: string | null;
  created_at: string;
  first_touched_at: string | null;
  won_at: string | null;
  days_in_stage: number;
  is_stale: boolean;
  contact_id: string | null;
  call_id: string | null;
}

export type LeadStatus =
  | "new"
  | "contacted"
  | "estimate_scheduled"
  | "estimate_sent"
  | "won"
  | "lost"
  | "spam";

export interface Board {
  stages: Record<LeadStatus, Lead[]>;
  counts: Record<LeadStatus, number>;
  won_value_cents: number;
}

export const dashboard = {
  get: () => api.get<Dashboard>("/api/dashboard"),
};

export const calls = {
  list: (params: Record<string, string | number | boolean | undefined>) => {
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== "" && value !== false) {
        query.set(key, String(value));
      }
    }
    return api.get<CallPage>(`/api/calls?${query.toString()}`);
  },
  get: (id: string) => api.get<CallDetail>(`/api/calls/${id}`),
  setOutcome: (id: string, outcome: string) =>
    api.post<CallDetail>(`/api/calls/${id}/outcome`, { outcome }),
  markReviewed: (id: string) => api.post(`/api/calls/${id}/reviewed`),
};

export const leads = {
  board: () => api.get<Board>("/api/leads/board"),
  list: (status?: LeadStatus) =>
    api.get<Lead[]>(`/api/leads${status ? `?status=${status}` : ""}`),
  get: (id: string) => api.get<Lead>(`/api/leads/${id}`),
  /**
   * The amount goes as the raw string he typed. The server parses it, so the
   * portal and the SMS grammar share one set of rules.
   */
  setValue: (id: string, amount: string) =>
    api.put<Lead>(`/api/leads/${id}/value`, { amount }),
  setStatus: (id: string, status: LeadStatus, lostReason?: string) =>
    api.put<Lead>(`/api/leads/${id}/status`, {
      status,
      lost_reason: lostReason ?? null,
    }),
};
