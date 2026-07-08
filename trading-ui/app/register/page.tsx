"use client";

import Link from "next/link";

import AuthGate from "@/components/AuthGate";

export default function RegisterPage() {
  return (
    <AuthGate mode="guest">
      <main className="min-h-screen bg-slate-50 px-6 py-16 text-slate-900 dark:bg-slate-950 dark:text-slate-100">
        <section className="mx-auto max-w-lg rounded-2xl border border-slate-200 bg-white p-8 shadow-sm dark:border-slate-800 dark:bg-slate-900">
          <p className="text-xs font-semibold uppercase tracking-[0.14em] text-emerald-700 dark:text-emerald-400">
            Account Access
          </p>
          <h1 className="mt-3 text-2xl font-semibold tracking-tight">Registration starts at checkout</h1>
          <p className="mt-3 text-sm leading-6 text-slate-600 dark:text-slate-300">
            Public sign-up is disabled. Select a paid plan first, complete Stripe checkout, then set your password to
            activate dashboard access.
          </p>

          <div className="mt-6 flex flex-wrap gap-3">
            <Link
              href="/pricing"
              className="rounded-lg bg-slate-900 px-4 py-2.5 text-sm font-medium text-white transition hover:bg-slate-800 dark:bg-slate-100 dark:text-slate-900 dark:hover:bg-slate-300"
            >
              View Pricing
            </Link>
            <Link
              href="/login"
              className="rounded-lg border border-slate-300 bg-white px-4 py-2.5 text-sm font-medium text-slate-800 transition hover:bg-slate-100 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100 dark:hover:bg-slate-800"
            >
              Existing User Login
            </Link>
          </div>
        </section>
      </main>
    </AuthGate>
  );
}
