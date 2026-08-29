export const API_URL =
  process.env.NEXT_PUBLIC_MABEL_API_URL?.replace(/\/$/, "") || "http://127.0.0.1:8000";

const TENANT_KEY = "mabel-office-tenant";
const TOKEN_KEY = "mabel-office-token";

export function readOfficeCreds(): { tenantId: string; token: string } {
  if (typeof window === "undefined") {
    return { tenantId: "", token: "" };
  }
  return {
    tenantId: window.localStorage.getItem(TENANT_KEY) || "",
    token: window.localStorage.getItem(TOKEN_KEY) || "",
  };
}

export function writeOfficeCreds(tenantId: string, token: string): void {
  window.localStorage.setItem(TENANT_KEY, tenantId);
  window.localStorage.setItem(TOKEN_KEY, token);
}

export async function mabelFetch(path: string, token: string, init: RequestInit = {}) {
  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${token}`);
  if (init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  return fetch(`${API_URL}${path}`, { ...init, headers });
}
