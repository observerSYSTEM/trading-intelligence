"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { api, isAuthError, type MeResponse } from "@/lib/api";

export default function BillingSettingsPage() {
  const router = useRouter();
  const [me, setMe] = useState<MeResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        setMe(await api.me());
      } catch (error: unknown) {
        if (isAuthError(error)) {
          router.push("/login");
          return;
        }
        setErr(error instanceof Error ? error.message : "Could not load billing settings.");
      }
    })();
  }, [router]);

  async function openPortal() {
    setErr(null);
    setLoading(true);
    try {
      const res = await api.portal();
      window.location.href = res.url;
    } catch (error: unknown) {
      setErr(error instanceof Error ? error.message : "Failed to open portal");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div style={{ maxWidth: 800, margin: "60px auto", padding: 16 }}>
      <h1 style={{ fontSize: 28, fontWeight: 800 }}>Billing</h1>

      <div style={{ marginTop: 14, padding: 12, border: "1px solid #ddd", borderRadius: 10 }}>
        <p>
          Plan: <b>{me?.tier || "basic"}</b>
        </p>
        <p>
          Status: <b>{me?.status || "inactive"}</b>
        </p>

        <button onClick={openPortal} disabled={loading} style={{ marginTop: 10 }}>
          {loading ? "Opening..." : "Open Stripe Billing Portal"}
        </button>
      </div>

      <div style={{ marginTop: 16 }}>
        <button onClick={() => router.push("/pricing")}>Back to pricing</button>
      </div>

      {err && <pre style={{ marginTop: 12, color: "crimson", whiteSpace: "pre-wrap" }}>{err}</pre>}
    </div>
  );
}
