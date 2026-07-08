"use client";

import { useEffect, useState, useSyncExternalStore } from "react";
import { usePathname, useRouter } from "next/navigation";

import {
  ACCESS_TOKEN_KEY,
  clearToken,
  formatApiReachabilityMessage,
  getConfiguredApiBase,
  getSessionState,
  subscribeToSessionState,
} from "@/lib/api";

type ValidationState = "idle" | "checking" | "valid" | "invalid" | "error";
const AUTHGATE_TIMEOUT_MS = 8_000;
const PUBLIC_PATH_PREFIXES = [
  "/",
  "/login",
  "/pricing",
  "/signup",
  "/register",
  "/activate-account",
  "/support",
  "/legal",
  "/billing/success",
  "/billing/cancelled",
];
const PROTECTED_PATH_PREFIXES = ["/dashboard", "/signals", "/settings", "/oracle"];

function authGateDebug(message: string, meta?: unknown) {
  if (meta === undefined) {
    console.log(`[authgate] ${message}`);
    return;
  }
  console.log(`[authgate] ${message}`, meta);
}

function isProtectedPath(pathname: string | null): boolean {
  if (!pathname) return false;
  return PROTECTED_PATH_PREFIXES.some((prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`));
}

function isPublicPath(pathname: string | null): boolean {
  if (!pathname) return true;
  return PUBLIC_PATH_PREFIXES.some((prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`));
}

async function readResponseMessage(response: Response): Promise<string | null> {
  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    try {
      const payload = (await response.json()) as { detail?: unknown; message?: unknown };
      if (typeof payload.detail === "string" && payload.detail.trim()) return payload.detail.trim();
      if (typeof payload.message === "string" && payload.message.trim()) return payload.message.trim();
    } catch {
      return null;
    }
    return null;
  }
  const text = await response.text();
  return text.trim() || null;
}

function withAuthGateTimeout(
  promise: Promise<Response>,
  controller: AbortController
): Promise<Response> {
  return new Promise<Response>((resolve, reject) => {
    const timeoutId = window.setTimeout(() => {
      controller.abort();
      reject(new Error("Session validation timed out after 8 seconds."));
    }, AUTHGATE_TIMEOUT_MS);

    promise.then(
      (response) => {
        window.clearTimeout(timeoutId);
        resolve(response);
      },
      (error: unknown) => {
        window.clearTimeout(timeoutId);
        reject(error);
      }
    );
  });
}

function getClientHasAccessToken() {
  return getSessionState().has_access_token;
}

function getServerHasAccessToken() {
  return false;
}

export default function AuthGate({
  children,
  mode = "auth", // "auth" | "guest"
}: {
  children: React.ReactNode;
  mode?: "auth" | "guest";
}) {
  const router = useRouter();
  const pathname = usePathname();
  const [validationState, setValidationState] = useState<ValidationState>("idle");
  const [validationError, setValidationError] = useState<string | null>(null);
  const hasAccessToken = useSyncExternalStore(
    subscribeToSessionState,
    getClientHasAccessToken,
    getServerHasAccessToken
  );

  useEffect(() => {
    let cancelled = false;
    let activeController: AbortController | null = null;
    let activeTimeoutId: number | null = null;

    async function validateSession() {
      if (isPublicPath(pathname) || !isProtectedPath(pathname)) {
        setValidationError(null);
        setValidationState("idle");
        return;
      }

      setValidationError(null);

      if (!hasAccessToken) {
        setValidationState("invalid");
        if (mode === "auth") {
          authGateDebug("token missing");
          router.replace("/login");
        }
        return;
      }

      const token = typeof window !== "undefined" ? window.localStorage.getItem(ACCESS_TOKEN_KEY)?.trim() || "" : "";
      if (!token) {
        setValidationState("invalid");
        if (mode === "auth") {
          authGateDebug("token missing");
          router.replace("/login");
        }
        return;
      }

      authGateDebug("validate /me", { pathname });
      setValidationState("checking");

      let apiBase = "";
      try {
        apiBase = getConfiguredApiBase();
      } catch (error: unknown) {
        if (cancelled) return;
        setValidationState("error");
        setValidationError(error instanceof Error ? error.message : "Missing API base.");
        return;
      }

      let response: Response;
      const controller = new AbortController();
      activeController = controller;
      try {
        response = await withAuthGateTimeout(
          fetch(`${apiBase}/me`, {
            method: "GET",
            headers: {
              Authorization: `Bearer ${token}`,
            },
            signal: controller.signal,
          }),
          controller
        );
      } catch (error: unknown) {
        activeTimeoutId = null;
        if (cancelled) return;
        setValidationState("error");
        authGateDebug("api error", {
          request: "/me",
          status: null,
          message: error instanceof Error ? error.message : String(error || ""),
        });
        if (
          error instanceof Error &&
          (error.name === "AbortError" || error.message.includes("timed out"))
        ) {
          setValidationError("Session validation timed out after 8 seconds.");
        } else {
          setValidationError(
            error instanceof Error ? error.message : formatApiReachabilityMessage("/me")
          );
        }
        return;
      }
      activeTimeoutId = null;

      authGateDebug("/me status", response.status);

      if (response.ok) {
        if (cancelled) return;
        setValidationState("valid");
        authGateDebug("session valid");
        if (mode === "guest") {
          router.replace("/dashboard");
        }
        return;
      }

      const responseMessage = await readResponseMessage(response);
      if (cancelled) return;

      if (response.status === 401 || response.status === 403) {
        clearToken();
        setValidationState("invalid");
        if (mode === "guest") {
          setValidationError("Stored session is no longer valid. Please sign in again.");
          return;
        }
        router.replace("/login");
        return;
      }

      setValidationState("error");
      setValidationError(responseMessage || formatApiReachabilityMessage("/me"));
      authGateDebug("api error", {
        request: "/me",
        status: response.status,
        message: responseMessage || formatApiReachabilityMessage("/me"),
      });
    }

    void validateSession();

    return () => {
      cancelled = true;
      if (activeTimeoutId != null) {
        window.clearTimeout(activeTimeoutId);
      }
      activeController?.abort();
    };
  }, [router, mode, hasAccessToken, pathname]);

  if (isPublicPath(pathname) || !isProtectedPath(pathname)) {
    return <>{children}</>;
  }

  if (mode === "guest") {
    if (hasAccessToken && (validationState === "idle" || validationState === "checking" || validationState === "valid")) {
      return <div className="p-6">Validating existing session...</div>;
    }
    return (
      <>
        {validationError ? (
          <div className="p-6">
            <div className="max-w-xl rounded-xl border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900">
              <p className="font-semibold">Session validation failed</p>
              <p className="mt-2">{validationError}</p>
            </div>
          </div>
        ) : null}
        {children}
      </>
    );
  }

  if (validationState === "idle" || validationState === "checking") {
    return <div className="p-6">Checking your session...</div>;
  }

  if (validationState === "error" && validationError) {
    return (
      <div className="p-6">
        <div className="max-w-xl rounded-xl border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900">
          <p className="font-semibold">Session validation failed</p>
          <p className="mt-2">{validationError}</p>
        </div>
      </div>
    );
  }

  if (validationState === "valid") return <>{children}</>;

  return <div className="p-6">Redirecting to login...</div>;
}
