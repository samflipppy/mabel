"use client";

/**
 * Sign in. Magic link and password, per 00-STACK.md.
 *
 * Magic link is offered first because the office manager checks this from a
 * phone and a password on a phone keyboard is friction for no security gain.
 * Password stays available because some people simply prefer it.
 */

import { useState } from "react";
import { useRouter } from "next/navigation";

import {
  isAuthConfigured,
  signInWithMagicLink,
  signInWithPassword,
} from "@/lib/supabase";

export default function SignInPage() {
  const router = useRouter();
  const [mode, setMode] = useState<"link" | "password">("link");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [state, setState] = useState<"idle" | "sending" | "sent" | "error">("idle");
  const [problem, setProblem] = useState<string | null>(null);

  if (!isAuthConfigured) {
    return (
      <main className="mx-auto max-w-md px-4 py-20">
        <h1 className="font-serif text-3xl">Sign-in isn&apos;t set up yet</h1>
        <p className="mt-4 text-base text-[var(--taupe)]">
          The Supabase project hasn&apos;t been connected. Nothing here is
          broken — there is just nowhere to authenticate against.
        </p>
      </main>
    );
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setProblem(null);
    setState("sending");
    try {
      if (mode === "link") {
        await signInWithMagicLink(email);
        setState("sent");
      } else {
        await signInWithPassword(email, password);
        router.replace("/dashboard");
      }
    } catch {
      setState("error");
      // Deliberately vague. Telling an unauthenticated visitor whether an
      // email exists here is telling them something.
      setProblem(
        mode === "link"
          ? "Couldn't send that link. Check the address and try again."
          : "That didn't work. Check the email and password.",
      );
    }
  }

  if (state === "sent") {
    return (
      <main className="mx-auto max-w-md px-4 py-20">
        <h1 className="font-serif text-3xl">Check your email</h1>
        <p className="mt-4 text-base text-[var(--taupe)]">
          There&apos;s a link on its way to {email}. It signs you straight in.
        </p>
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-md px-4 py-20">
      <h1 className="font-serif text-3xl">Mabel</h1>
      <p className="mt-2 text-base text-[var(--taupe)]">
        Sign in to see your calls.
      </p>

      <form onSubmit={submit} className="mt-8 space-y-4">
        <label className="block text-base">
          Email
          <input
            type="email"
            required
            autoComplete="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            className="mt-1 min-h-[48px] w-full rounded-lg border border-[var(--line)] bg-white px-4 text-base"
          />
        </label>

        {mode === "password" && (
          <label className="block text-base">
            Password
            <input
              type="password"
              required
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              className="mt-1 min-h-[48px] w-full rounded-lg border border-[var(--line)] bg-white px-4 text-base"
            />
          </label>
        )}

        {problem && <p className="text-base text-red-700">{problem}</p>}

        <button
          type="submit"
          disabled={state === "sending"}
          className="min-h-[48px] w-full rounded-lg bg-[var(--charcoal)] text-base text-white"
        >
          {state === "sending"
            ? "One moment…"
            : mode === "link"
              ? "Email me a link"
              : "Sign in"}
        </button>

        <button
          type="button"
          onClick={() => setMode(mode === "link" ? "password" : "link")}
          className="min-h-[48px] w-full text-base underline underline-offset-4"
        >
          {mode === "link" ? "Use a password instead" : "Email me a link instead"}
        </button>
      </form>
    </main>
  );
}
