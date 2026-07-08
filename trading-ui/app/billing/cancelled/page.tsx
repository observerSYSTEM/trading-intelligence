"use client";

import { useRouter } from "next/navigation";

export default function BillingCancelledPage() {
  const router = useRouter();
  return (
    <div style={{ maxWidth: 800, margin: "60px auto", padding: 16 }}>
      <h1 style={{ fontSize: 28, fontWeight: 800 }}>Payment cancelled</h1>
      <p style={{ opacity: 0.85 }}>No worries — you can upgrade anytime.</p>

      <div style={{ marginTop: 16 }}>
        <button onClick={() => router.push("/pricing")}>Back to pricing</button>
      </div>
    </div>
  );
}
