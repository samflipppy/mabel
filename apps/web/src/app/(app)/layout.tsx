"use client";

/**
 * The portal shell. Everything behind sign-in lives under this layout.
 *
 * Auth is checked here rather than per page, so a new screen cannot be added
 * unauthenticated by forgetting a guard. The API would refuse it anyway — that
 * is the real boundary — but showing a signed-out user a page frame full of
 * failed requests is a bad way to tell them to sign in.
 */

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { AppNav } from "@/components/app-nav";
import { getSession, isAuthConfigured, onAuthChange, signOut } from "@/lib/supabase";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // The office manager leaves this open all day. Refetching on focus is
      // what makes a lead marked won on her phone show up on the desktop.
      refetchOnWindowFocus: true,
      staleTime: 30_000,
      retry: (failureCount, error) => {
        // Never retry an auth failure into a loop.
        const status = (error as { status?: number }).status;
        if (status === 401 || status === 403 || status === 503) return false;
        return failureCount < 2;
      },
    },
  },
});

type AuthState = "checking" | "signed-in" | "signed-out" | "unconfigured";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [state, setState] = useState<AuthState>("checking");

  useEffect(() => {
    if (!isAuthConfigured) {
      setState("unconfigured");
      return;
    }
    // The cleanup has to do both things: stop the in-flight getSession from
    // setting state after unmount, and unsubscribe. Returning only the
    // unsubscribe left `cancelled` permanently false, so the guard did
    // nothing — the linter caught it.
    let cancelled = false;

    getSession().then((session) => {
      if (!cancelled) setState(session ? "signed-in" : "signed-out");
    });

    const unsubscribe = onAuthChange((session) => {
      if (!cancelled) setState(session ? "signed-in" : "signed-out");
    });

    return () => {
      cancelled = true;
      unsubscribe();
    };
  }, []);

  useEffect(() => {
    if (state === "signed-out") router.replace("/sign-in");
  }, [state, router]);

  if (state === "unconfigured") {
    // Fails closed, and says what is missing rather than showing a login form
    // that cannot work. See docs/BLOCKED.md #1.
    return (
      <main className="mx-auto max-w-2xl px-4 py-20 text-base">
        <h1 className="font-serif text-3xl">Sign-in isn&apos;t set up yet</h1>
        <p className="mt-4 text-[var(--taupe)]">
          The Supabase project hasn&apos;t been connected. Nothing is broken —
          this portal just has nowhere to authenticate against yet.
        </p>
      </main>
    );
  }

  if (state !== "signed-in") {
    return (
      <main className="px-4 py-20 text-center text-base text-[var(--taupe)]">
        Checking your session…
      </main>
    );
  }

  return (
    <QueryClientProvider client={queryClient}>
      <div className="min-h-screen">
        <AppNav />
        <main className="mx-auto max-w-6xl px-4 py-8">{children}</main>
        <footer className="mx-auto max-w-6xl px-4 pb-10 text-sm text-[var(--taupe)]">
          <button
            type="button"
            onClick={() => signOut()}
            className="min-h-[48px] underline underline-offset-4"
          >
            Sign out
          </button>
        </footer>
      </div>
    </QueryClientProvider>
  );
}
