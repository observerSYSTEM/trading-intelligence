// app/oracle/liquidity/page.tsx
"use client";

import AuthGate from "@/components/AuthGate";

export default function OracleLiquidity() {
  return (
    <AuthGate mode="auth">
      <h1>Oracle Liquidity (Pro)</h1>
    </AuthGate>
  );
}
