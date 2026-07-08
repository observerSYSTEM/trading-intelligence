"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import { getCheckoutActivation, type CheckoutActivationResponse } from "@/lib/api";

const ACTIVATION_TOKEN_KEY = "activation_token";
const ACTIVATION_EMAIL_KEY = "activation_email";

function BillingSuccessContent() {
  const params = useSearchParams();
  const router = useRouter();
  const sessionId = (params.get("session_id") || "").trim();

  const [status, setStatus] = useState<CheckoutActivationResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const missingSession = !sessionId;

  useEffect(() => {
    if (!sessionId) return;

    let alive = true;
    let timer: ReturnType<typeof setTimeout> | null = null;

    async function poll() {
      try {
        const data = await getCheckoutActivation(sessionId);
        if (!alive) return;
        setStatus(data);
        setError(null);

        if (data.ready && data.requires_password_setup && data.activation_token) {
          sessionStorage.setItem(ACTIVATION_TOKEN_KEY, data.activation_token);
          sessionStorage.setItem(ACTIVATION_EMAIL_KEY, (data.email || "").trim());
          return;
        }
        if (data.ready) return;
      } catch (err: unknown) {
        if (!alive) return;
        setError(err instanceof Error ? err.message : "Could not complete account setup.");
      }

      if (alive) timer = setTimeout(poll, 2000);
    }

    void poll();

    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
    };
  }, [sessionId]);

  return (
    <main className="min-h-screen bg-slate-50 px-6 py-16 text-slate-900 dark:bg-slate-950 dark:text-slate-100">
      <section className="mx-auto max-w-2xl rounded-2xl border border-slate-200 bg-white p-8 shadow-sm dark:border-slate-800 dark:bg-slate-900">
        <p className="text-xs font-semibold uppercase tracking-[0.14em] text-emerald-700 dark:text-emerald-400">
          Payment Success
        </p>
        <h1 className="mt-3 text-2xl font-semibold tracking-tight">Completing account setup</h1>
        <p className="mt-2 text-sm text-slate-600 dark:text-slate-300">
          We verify payment via webhook, create or update your account, then unlock password setup.
        </p>

        <div className="mt-6 rounded-lg border border-slate-200 bg-slate-50 p-4 text-sm dark:border-slate-800 dark:bg-slate-950">
          <p className="font-medium">Stripe session</p>
          <p className="mt-1 break-all text-slate-600 dark:text-slate-300">{sessionId || "-"}</p>
        </div>

        {missingSession ? (
          <div className="mt-4 rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-800 dark:bg-red-950/30 dark:text-red-300">
            Missing Stripe session. Return to pricing and try checkout again.
          </div>
        ) : null}
        {error ? (
          <div className="mt-4 rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-800 dark:bg-red-950/30 dark:text-red-300">
            {error}
          </div>
        ) : null}

        {status ? (
          <div className="mt-4 rounded-md border border-slate-200 bg-slate-50 px-3 py-3 text-sm dark:border-slate-800 dark:bg-slate-950">
            <p className="font-medium">{status.message}</p>
            {status.email ? <p className="mt-1 text-slate-600 dark:text-slate-300">Email: {status.email}</p> : null}
          </div>
        ) : (
          <p className="mt-4 text-sm text-slate-600 dark:text-slate-300">Checking activation status...</p>
        )}

        <div className="mt-6 flex flex-wrap gap-3">
          {status?.ready && status.requires_password_setup ? (
            <button
              onClick={() => router.push("/activate-account")}
              className="rounded-lg bg-slate-900 px-4 py-2.5 text-sm font-medium text-white transition hover:bg-slate-800 dark:bg-slate-100 dark:text-slate-900 dark:hover:bg-slate-300"
            >
              Set Password
            </button>
          ) : null}
          {status?.ready && !status.requires_password_setup ? (
            <button
              onClick={() => router.push("/dashboard")}
              className="rounded-lg bg-slate-900 px-4 py-2.5 text-sm font-medium text-white transition hover:bg-slate-800 dark:bg-slate-100 dark:text-slate-900 dark:hover:bg-slate-300"
            >
              Open Dashboard
            </button>
          ) : null}
          <button
            onClick={() => router.push("/pricing")}
            className="rounded-lg border border-slate-300 bg-white px-4 py-2.5 text-sm font-medium text-slate-800 transition hover:bg-slate-100 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100 dark:hover:bg-slate-800"
          >
            Back to Pricing
          </button>
        </div>
      </section>
    </main>
  );
}

export default function BillingSuccessPage() {
  return (
    <Suspense
      fallback={
        <main className="min-h-screen bg-slate-50 px-6 py-16 text-slate-900 dark:bg-slate-950 dark:text-slate-100">
          <section className="mx-auto max-w-2xl rounded-2xl border border-slate-200 bg-white p-8 shadow-sm dark:border-slate-800 dark:bg-slate-900">
            <p className="text-sm">Loading payment status...</p>
          </section>
        </main>
      }
    >
      <BillingSuccessContent />
    </Suspense>
  );
}
