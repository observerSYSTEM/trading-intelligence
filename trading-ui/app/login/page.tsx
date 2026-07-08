"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

import { setSessionTokens } from "@/lib/api";

const LOGIN_TIMEOUT_MS = 8_000;
const LOGIN_API_URL = "http://127.0.0.1:8000/auth/login";

type LoginTokenPayload = {
  access_token?: unknown;
  refresh_token?: unknown;
  token?: unknown;
};

function withLoginTimeout<T>(
  promise: Promise<T>,
  controller: AbortController
): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timeoutId = window.setTimeout(() => {
      controller.abort();
      reject(new Error("Login timed out after 8 seconds. Please try again."));
    }, LOGIN_TIMEOUT_MS);

    promise.then(
      (value) => {
        window.clearTimeout(timeoutId);
        resolve(value);
      },
      (error: unknown) => {
        window.clearTimeout(timeoutId);
        reject(error);
      }
    );
  });
}

async function readLoginError(response: Response): Promise<string> {
  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    try {
      const payload = (await response.json()) as { detail?: unknown; message?: unknown };
      if (typeof payload.detail === "string" && payload.detail.trim()) return payload.detail.trim();
      if (typeof payload.message === "string" && payload.message.trim()) return payload.message.trim();
    } catch {
      return `Login failed with status ${response.status}.`;
    }
  }

  const text = await response.text().catch(() => "");
  return text.trim() || `Login failed with status ${response.status}.`;
}

export default function LoginPage() {
  const router = useRouter();

  const [email, setEmail] = useState("admin@yourdomain.com");
  const [password, setPassword] = useState("StrongPassword123!");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function submitLogin() {
    if (loading) return;

    const trimmedEmail = email.trim();
    if (!trimmedEmail) {
      setError("Email is required.");
      return;
    }
    if (!password) {
      setError("Password is required.");
      return;
    }

    setError("");
    setLoading(true);

    console.log("[login] submit", { email: trimmedEmail });

    const controller = new AbortController();
    try {
      const response = await withLoginTimeout(
        fetch(LOGIN_API_URL, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email: trimmedEmail, password }),
          signal: controller.signal,
        }),
        controller
      );

      console.log("[login] status", response.status);

      if (!response.ok) {
        const message =
          response.status === 401
            ? "Invalid email or password."
            : await readLoginError(response);
        setError(message);
        return;
      }

      const responseText = await response.text().catch(() => "");
      let data: LoginTokenPayload | null = null;
      if (responseText) {
        try {
          data = JSON.parse(responseText) as LoginTokenPayload;
        } catch {
          setError("Login succeeded but the response was not valid JSON.");
          return;
        }
      }
      const accessToken =
        typeof data?.access_token === "string"
          ? data.access_token
          : typeof data?.token === "string"
            ? data.token
            : "";
      const refreshToken = typeof data?.refresh_token === "string" ? data.refresh_token : null;

      if (!accessToken.trim()) {
        setError("Login succeeded but no access token was returned.");
        return;
      }

      setSessionTokens(accessToken, refreshToken);
      console.log("[login] token saved");
      router.replace("/dashboard");
    } catch (err: unknown) {
      if (
        err instanceof Error &&
        (err.name === "AbortError" || err.message.includes("timed out"))
      ) {
        setError("Login timed out after 8 seconds. Please try again.");
      } else {
        setError(
          err instanceof Error
            ? err.message
            : `Could not reach the backend for /auth/login. Expected API URL: ${LOGIN_API_URL}.`
        );
      }
      console.log("[login] status", "failed");
    } finally {
      setLoading(false);
    }
  }

  function handleSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    void submitLogin();
  }

  function handleSignInClick(e: React.MouseEvent<HTMLButtonElement>) {
    e.preventDefault();
    void submitLogin();
  }

  return (
    <main style={{ minHeight: "100vh", display: "grid", placeItems: "center", background: "#f8fafc" }}>
      <form
        onSubmit={handleSubmit}
        style={{
          width: "min(420px, calc(100vw - 32px))",
          background: "white",
          border: "1px solid #e5e7eb",
          borderRadius: 16,
          padding: 32,
          boxShadow: "0 10px 25px rgba(15,23,42,0.08)",
        }}
      >
        <p style={{ color: "#047857", fontWeight: 700, letterSpacing: 1 }}>LOGIN</p>

        <h1 style={{ fontSize: 26, margin: "10px 0" }}>Welcome back</h1>

        <p style={{ color: "#475569", marginBottom: 24 }}>
          Sign in to access your dashboard, subscription settings, and trading intelligence modules.
        </p>

        {error ? (
          <div
            aria-live="polite"
            style={{
              background: "#fee2e2",
              color: "#991b1b",
              padding: 12,
              borderRadius: 8,
              marginBottom: 16,
              border: "1px solid #fecaca",
            }}
          >
            {error}
          </div>
        ) : null}

        <label style={{ display: "block", fontWeight: 700, marginBottom: 6 }}>Email</label>
        <input
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          style={{ width: "100%", padding: 12, border: "1px solid #cbd5e1", borderRadius: 8, marginBottom: 16 }}
          placeholder="you@example.com"
          type="email"
          autoComplete="email"
          required
        />

        <label style={{ display: "block", fontWeight: 700, marginBottom: 6 }}>Password</label>
        <input
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          style={{ width: "100%", padding: 12, border: "1px solid #cbd5e1", borderRadius: 8, marginBottom: 16 }}
          placeholder="Enter your password"
          type="password"
          autoComplete="current-password"
          required
        />

        <button
          type="button"
          onClick={handleSignInClick}
          disabled={loading}
          style={{
            width: "100%",
            padding: 14,
            borderRadius: 8,
            border: "none",
            background: "#0f172a",
            color: "white",
            fontWeight: 700,
            cursor: loading ? "not-allowed" : "pointer",
          }}
        >
          {loading ? "Signing in..." : "Sign In"}
        </button>
      </form>
    </main>
  );
}
