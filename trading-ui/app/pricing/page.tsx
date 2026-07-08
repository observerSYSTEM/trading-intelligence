"use client";

import { useState } from "react";

import { createCheckout, getToken, type Tier } from "@/lib/api";

type Plan = {
  tier: Tier;
  title: string;
  price: string;
  description: string;
};

const PLANS: Plan[] = [
  {
    tier: "basic",
    title: "Basic",
    price: "$19/mo",
    description: "Daily market direction and core dashboard access.",
  },
  {
    tier: "pro",
    title: "Pro",
    price: "$49/mo",
    description: "Adds richer context and larger monthly signal allowance.",
  },
  {
    tier: "elite",
    title: "Elite",
    price: "$99/mo",
    description: "Full intelligence tier with maximum monthly allowance.",
  },
];

export default function PricingPage() {
  const [loadingTier, setLoadingTier] = useState<Tier | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function onUpgrade(tier: Tier) {
    setError(null);
    const isAuthenticated = Boolean(getToken());

    try {
      setLoadingTier(tier);
      const { url } = await createCheckout(tier, { auth: isAuthenticated });
      window.location.assign(url);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to start checkout.");
    } finally {
      setLoadingTier(null);
    }
  }

  return (
    <main className="mx-auto max-w-5xl px-6 py-20">
      <h1 className="text-4xl font-semibold">Pricing</h1>
      <p className="mt-3 text-gray-600">
        Choose a plan to start onboarding. After successful payment, you will set your account password.
      </p>

      {error ? <div className="mt-4 rounded-md border p-3 text-sm text-red-600">{error}</div> : null}

      <div className="mt-10 grid gap-5 md:grid-cols-3">
        {PLANS.map((plan) => (
          <div key={plan.tier} className="rounded-xl border p-5">
            <h2 className="text-xl font-semibold">{plan.title}</h2>
            <p className="mt-2 text-3xl font-bold">{plan.price}</p>
            <p className="mt-2 text-sm text-gray-600">{plan.description}</p>
            <button
              onClick={() => onUpgrade(plan.tier)}
              disabled={loadingTier === plan.tier}
              className="mt-5 w-full rounded-md bg-black px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
            >
              {loadingTier === plan.tier ? "Redirecting..." : `Upgrade to ${plan.title}`}
            </button>
          </div>
        ))}
      </div>
    </main>
  );
}
