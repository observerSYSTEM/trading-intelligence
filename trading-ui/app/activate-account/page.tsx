"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import AuthGate from "@/components/AuthGate";
import { setPasswordFromActivation, setSessionTokens } from "@/lib/api";

const ACTIVATION_TOKEN_KEY = "activation_token";
const ACTIVATION_EMAIL_KEY = "activation_email";

function passwordChecklist(password: string) {
  return {
    length: password.length >= 8,
    upper: /[A-Z]/.test(password),
    lower: /[a-z]/.test(password),
    number: /\d/.test(password),
  };
}

export default function ActivateAccountPage() {
  const router = useRouter();
  const [activationToken] = useState(() =>
    typeof window === "undefined" ? "" : sessionStorage.getItem(ACTIVATION_TOKEN_KEY) || ""
  );
  const [emailHint] = useState(() =>
    typeof window === "undefined" ? "" : sessionStorage.getItem(ACTIVATION_EMAIL_KEY) || ""
  );
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const passwordRules = useMemo(() => passwordChecklist(password), [password]);
  const passwordRulesOk = Object.values(passwordRules).every(Boolean);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSuccess(null);

    if (!activationToken) {
      setError("Activation token is missing. Return to billing success and retry.");
      return;
    }
    if (!passwordRulesOk) {
      setError("Password does not meet the minimum security requirements.");
      return;
    }
    if (password !== confirmPassword) {
      setError("Passwords do not match.");
      return;
    }

    try {
      setLoading(true);
      const res = await setPasswordFromActivation({
        token: activationToken,
        password,
        confirm_password: confirmPassword,
      });
      setSessionTokens(res.access_token, res.refresh_token);
      sessionStorage.removeItem(ACTIVATION_TOKEN_KEY);
      sessionStorage.removeItem(ACTIVATION_EMAIL_KEY);
      setSuccess("Password set successfully. Redirecting to dashboard...");
      setTimeout(() => router.push("/dashboard"), 150);
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Could not set password.";
      if (message.includes("Invalid or expired activation token")) {
        setError("Your setup link expired. Return to billing success and request a new setup token.");
      } else {
        setError(message);
      }
    } finally {
      setLoading(false);
    }
  }

  return (
    <AuthGate mode="guest">
      <main className="min-h-screen bg-slate-50 px-6 py-16 text-slate-900 dark:bg-slate-950 dark:text-slate-100">
        <section className="mx-auto max-w-lg rounded-2xl border border-slate-200 bg-white p-8 shadow-sm dark:border-slate-800 dark:bg-slate-900">
          <p className="text-xs font-semibold uppercase tracking-[0.14em] text-emerald-700 dark:text-emerald-400">
            Activate Account
          </p>
          <h1 className="mt-3 text-2xl font-semibold tracking-tight">Set your password</h1>
          <p className="mt-2 text-sm text-slate-600 dark:text-slate-300">
            Final step to activate dashboard access.
            {emailHint ? ` Account: ${emailHint}` : ""}
          </p>

          <form onSubmit={onSubmit} className="mt-6 space-y-4">
            <div>
              <label htmlFor="password" className="text-sm font-medium">
                Password
              </label>
              <input
                id="password"
                type="password"
                autoComplete="new-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Create a strong password"
                className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm outline-none ring-emerald-500 focus:ring-2 dark:border-slate-700 dark:bg-slate-950"
              />
              <ul className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-xs text-slate-500 dark:text-slate-400">
                <li className={passwordRules.length ? "text-emerald-700 dark:text-emerald-400" : ""}>8+ characters</li>
                <li className={passwordRules.upper ? "text-emerald-700 dark:text-emerald-400" : ""}>Uppercase letter</li>
                <li className={passwordRules.lower ? "text-emerald-700 dark:text-emerald-400" : ""}>Lowercase letter</li>
                <li className={passwordRules.number ? "text-emerald-700 dark:text-emerald-400" : ""}>Number</li>
              </ul>
            </div>

            <div>
              <label htmlFor="confirm-password" className="text-sm font-medium">
                Confirm Password
              </label>
              <input
                id="confirm-password"
                type="password"
                autoComplete="new-password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                placeholder="Re-enter your password"
                className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm outline-none ring-emerald-500 focus:ring-2 dark:border-slate-700 dark:bg-slate-950"
              />
            </div>

            {error ? (
              <div className="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-800 dark:bg-red-950/30 dark:text-red-300">
                {error}
              </div>
            ) : null}
            {success ? (
              <div className="rounded-md border border-emerald-300 bg-emerald-50 px-3 py-2 text-sm text-emerald-700 dark:border-emerald-800 dark:bg-emerald-950/30 dark:text-emerald-300">
                {success}
              </div>
            ) : null}

            <button
              type="submit"
              disabled={loading}
              className="w-full rounded-lg bg-slate-900 px-4 py-2.5 text-sm font-medium text-white transition hover:bg-slate-800 disabled:opacity-50 dark:bg-slate-100 dark:text-slate-900 dark:hover:bg-slate-300"
            >
              {loading ? "Saving password..." : "Set Password"}
            </button>
          </form>

          <p className="mt-5 text-sm text-slate-600 dark:text-slate-300">
            Need a fresh token?{" "}
            <Link href="/billing/success" className="font-medium text-slate-900 underline dark:text-slate-100">
              Return to billing success
            </Link>
          </p>
        </section>
      </main>
    </AuthGate>
  );
}
