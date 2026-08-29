/**
 * Supabase Auth. Magic link and password, per 00-STACK.md.
 *
 * Fails closed. With no project configured (docs/BLOCKED.md #1) the client is
 * null and the shell shows a plain "sign-in is not configured" rather than a
 * broken login form — and, importantly, rather than a development bypass. A
 * bypass added to unblock local work is the thing that survives into
 * production.
 */

import {
  type Session,
  type SupabaseClient,
  createClient,
} from "@supabase/supabase-js";

const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

/** Null when the project is not configured. Callers must handle it. */
export const supabase: SupabaseClient | null =
  url && anonKey
    ? createClient(url, anonKey, {
        auth: {
          persistSession: true,
          autoRefreshToken: true,
          // The office manager lives in this thing all day. Being signed out
          // every hour is the sort of thing that gets a product abandoned.
          detectSessionInUrl: true,
        },
      })
    : null;

export const isAuthConfigured = supabase !== null;

/**
 * The access token for the current session, or null.
 *
 * Reads the session rather than caching a token: supabase-js refreshes in the
 * background, and a cached token means the first request after a refresh is
 * made with the expired one.
 */
export async function getAccessToken(): Promise<string | null> {
  if (!supabase) return null;
  const { data } = await supabase.auth.getSession();
  return data.session?.access_token ?? null;
}

export async function getSession(): Promise<Session | null> {
  if (!supabase) return null;
  const { data } = await supabase.auth.getSession();
  return data.session;
}

export async function signInWithMagicLink(email: string): Promise<void> {
  if (!supabase) throw new Error("sign-in is not configured");
  const { error } = await supabase.auth.signInWithOtp({
    email,
    options: {
      emailRedirectTo:
        typeof window !== "undefined"
          ? `${window.location.origin}/dashboard`
          : undefined,
    },
  });
  if (error) throw error;
}

export async function signInWithPassword(
  email: string,
  password: string,
): Promise<void> {
  if (!supabase) throw new Error("sign-in is not configured");
  const { error } = await supabase.auth.signInWithPassword({ email, password });
  if (error) throw error;
}

export async function signOut(): Promise<void> {
  await supabase?.auth.signOut();
}

/** Fires on sign-in, sign-out, and token refresh. */
export function onAuthChange(
  handler: (session: Session | null) => void,
): () => void {
  if (!supabase) return () => {};
  const { data } = supabase.auth.onAuthStateChange((_event, session) => {
    handler(session);
  });
  return () => data.subscription.unsubscribe();
}
