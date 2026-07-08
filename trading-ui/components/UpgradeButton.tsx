"use client";

import { useState } from "react";

import { createCheckout } from "@/lib/api";

type Props = {
  targetTier: "pro" | "elite";
};

export default function UpgradeButton({ targetTier }: Props) {
  const [loading, setLoading] = useState(false);

  const handleUpgrade = async () => {
    setLoading(true);
    try {
      const { url } = await createCheckout(targetTier);
      window.location.href = url;
    } catch (error: unknown) {
      const message = error instanceof Error ? error.message : "Upgrade failed";
      alert(message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <button
      onClick={handleUpgrade}
      disabled={loading}
      className="px-4 py-2 rounded-lg bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-50"
    >
      {loading ? "Redirecting..." : `Upgrade to ${targetTier.toUpperCase()}`}
    </button>
  );
}
