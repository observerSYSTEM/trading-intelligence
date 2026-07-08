"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";

import AuthGate from "@/components/AuthGate";

export default function BillingSettingsPage() {
  const router = useRouter();

  return (
    <AuthGate mode="auth">
      <main className="min-h-screen bg-slate-50 px-6 py-16 text-slate-900 dark:bg-slate-950 dark:text-slate-100">
        <section className="mx-auto max-w-xl rounded-2xl border border-slate-200 bg-white p-8 shadow-sm dark:border-slate-800 dark:bg-slate-900">
          <div className="flex items-center justify-between gap-4">
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.14em] text-emerald-700 dark:text-emerald-400">
                Billing Settings
              </p>
              <h1 className="mt-3 text-2xl font-semibold tracking-tight">Manage your subscription</h1>
            </div>
            <button onClick={() => router.push("/dashboard")} className="text-sm underline">
              Back
            </button>
          </div>

          <p className="mt-3 text-sm leading-6 text-slate-600 dark:text-slate-300">
            Subscription and plan management are available from your dashboard billing controls.
          </p>
          <div className="mt-6 flex flex-wrap gap-3">
            <Link
              href="/dashboard"
              className="rounded-lg bg-slate-900 px-4 py-2.5 text-sm font-medium text-white transition hover:bg-slate-800 dark:bg-slate-100 dark:text-slate-900 dark:hover:bg-slate-300"
            >
              Open Dashboard
            </Link>
            <Link
              href="/pricing"
              className="rounded-lg border border-slate-300 bg-white px-4 py-2.5 text-sm font-medium text-slate-800 transition hover:bg-slate-100 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100 dark:hover:bg-slate-800"
            >
              Compare Plans
            </Link>
          </div>
        </section>
      </main>
    </AuthGate>
  );
}
