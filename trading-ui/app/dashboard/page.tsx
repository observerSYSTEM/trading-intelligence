"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import UpgradeButton from "@/components/UpgradeButton";
import {
  createPortalSession,
  getAdminReadiness,
  getApiErrorStatus,
  getConfiguredApiBase,
  getLceCheckpoint,
  getOpsAnchorDebug,
  getOracleDirection,
  getOracleLatest,
  getOracleStatus,
  getRunnerHealth,
  getRunnerStatus,
  getSessionState,
  getSignalTargetsLatest,
  getUsage,
  logoutSession,
  me,
  reconnectRunner,
  runAdminOracle,
  testTelegram,
  type AnchorDebugResponse,
  type LceCheckpointResponse,
  type MeResponse,
  type OracleDirectionResponse,
  type OracleLatestResponse,
  type OracleStatusResponse,
  type ReadinessResponse,
  type RunnerHealthResponse,
  type RunnerStatusResponse,
  type SignalTargetsLatestResponse,
  type UsageResponse,
} from "@/lib/api";

const ORACLE_POLL_MS = 60_000;
const LONDON_TZ = "Europe/London";
const DASHBOARD_BOOTSTRAP_TIMEOUT_MS = 8_000;
const DASHBOARD_OPTIONAL_API_OPTIONS = { _skipRefresh: true } as const;
const PRO_TARGETS_DELAY_FALLBACK_SECONDS = 10 * 60;
const LOCAL_DEV_SESSION_CHECK_FAILED = "Local dev mode: session check failed";
const LOCAL_DEV_FALLBACK_SYMBOLS = ["XAUUSD", "GBPJPY"];
const LOCAL_DEV_FALLBACK_USER: MeResponse = {
  email: "local-dev@example.local",
  role: "admin",
  tier: "elite",
  status: "local-dev",
  symbols_enabled: LOCAL_DEV_FALLBACK_SYMBOLS,
  symbols_available: LOCAL_DEV_FALLBACK_SYMBOLS,
};

function dashboardDebug(event: string, meta?: Record<string, unknown>) {
  if (meta === undefined) {
    console.log(`[dashboard] ${event}`);
    return;
  }
  console.log(`[dashboard] ${event}`, meta);
}

function safeDashboardApiBase(): string {
  try {
    return getConfiguredApiBase();
  } catch (error) {
    return error instanceof Error ? error.message : "API base unavailable";
  }
}

function withTimeout<T>(promise: Promise<T>, ms: number, message: string): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timeoutId = window.setTimeout(() => {
      reject(new Error(message));
    }, ms);

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

function dashboardErrorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

function dashboardApiError(
  request: string,
  error: unknown,
  meta: Record<string, unknown> = {}
) {
  dashboardDebug("optional api failed", {
    request,
    status: getApiErrorStatus(error),
    message: dashboardErrorMessage(error, "Unknown API error."),
    ...meta,
  });
}

function formatLondonUtc(value?: string | null): string | null {
  if (!value) return null;
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return null;
  const london = d.toLocaleString("en-GB", {
    timeZone: LONDON_TZ,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
  return `${london} London (UTC ${d.toISOString()})`;
}

function minutesAgo(value?: string | null): number | null {
  if (!value) return null;
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return null;
  return Math.max(Math.floor((Date.now() - d.getTime()) / 60_000), 0);
}

function latestIsoTimestamp(...values: Array<string | null | undefined>): string | null {
  let latestValue: string | null = null;
  let latestTime = Number.NEGATIVE_INFINITY;
  for (const value of values) {
    if (!value) continue;
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) continue;
    if (parsed.getTime() > latestTime) {
      latestTime = parsed.getTime();
      latestValue = value;
    }
  }
  return latestValue;
}

function formatNumber(value: number | null | undefined): string {
  if (typeof value !== "number" || Number.isNaN(value)) return "-";
  if (Math.abs(value) >= 100) return value.toFixed(2);
  if (Math.abs(value) >= 1) return value.toFixed(4);
  return value.toFixed(5);
}

function formatPercent(value: number | null | undefined): string {
  if (typeof value !== "number" || Number.isNaN(value)) return "-";
  return `${value.toFixed(1)}%`;
}

function formatConfidence(value: number | null | undefined): string {
  if (typeof value !== "number" || Number.isNaN(value)) return "-";
  return formatPercent(value <= 1 ? value * 100 : value);
}

function formatLceType(value?: string | null): string {
  if (!value) return "-";
  return value.replace(/_/g, " ");
}

function formatLevelPath(values?: number[] | null): string {
  if (!values || values.length === 0) return "-";
  return values.map((value) => formatNumber(value)).join(" -> ");
}

function oracleDirectionTone(direction?: string | null): string {
  if (direction === "STRONG BUY" || direction === "BUY") return "text-emerald-700";
  if (direction === "STRONG SELL" || direction === "SELL") return "text-red-700";
  return "text-gray-900";
}

export default function DashboardPage() {
  const router = useRouter();
  const renderDashboardLoggedRef = useRef(false);

  const [user, setUser] = useState<MeResponse>(LOCAL_DEV_FALLBACK_USER);
  const [usage, setUsage] = useState<UsageResponse | null>(null);
  const [oracle, setOracle] = useState<OracleLatestResponse | null>(null);
  const [oracleStatus, setOracleStatus] = useState<OracleStatusResponse | null>(null);
  const [oracleDirection, setOracleDirection] = useState<OracleDirectionResponse | null>(null);
  const [lceCheckpoint, setLceCheckpoint] = useState<LceCheckpointResponse | null>(null);
  const [proTargets, setProTargets] = useState<SignalTargetsLatestResponse | null>(null);
  const [anchorDebug, setAnchorDebug] = useState<AnchorDebugResponse | null>(null);
  const [readiness, setReadiness] = useState<ReadinessResponse | null>(null);
  const [runnerHealth, setRunnerHealth] = useState<RunnerHealthResponse | null>(null);
  const [runnerStatus, setRunnerStatus] = useState<RunnerStatusResponse | null>(null);

  const [billingLoading, setBillingLoading] = useState(false);
  const [oracleLoading, setOracleLoading] = useState(false);
  const [runningOracle, setRunningOracle] = useState(false);
  const [selectedSymbol, setSelectedSymbol] = useState(LOCAL_DEV_FALLBACK_SYMBOLS[0]);

  const [usageError, setUsageError] = useState<string | null>(null);
  const [billingError, setBillingError] = useState<string | null>(null);
  const [oracleError, setOracleError] = useState<string | null>(null);
  const [oracleStatusError, setOracleStatusError] = useState<string | null>(null);
  const [oracleDirectionError, setOracleDirectionError] = useState<string | null>(null);
  const [lceCheckpointError, setLceCheckpointError] = useState<string | null>(null);
  const [proTargetsError, setProTargetsError] = useState<string | null>(null);
  const [anchorDebugError, setAnchorDebugError] = useState<string | null>(null);
  const [oracleRunMsg, setOracleRunMsg] = useState<string | null>(null);
  const [readinessError, setReadinessError] = useState<string | null>(null);
  const [runnerHealthError, setRunnerHealthError] = useState<string | null>(null);
  const [runnerStatusError, setRunnerStatusError] = useState<string | null>(null);
  const [runnerReconnectMsg, setRunnerReconnectMsg] = useState<string | null>(null);
  const [reconnectingRunner, setReconnectingRunner] = useState(false);
  const [telegramTestMsg, setTelegramTestMsg] = useState<string | null>(null);
  const [telegramTestErr, setTelegramTestErr] = useState<string | null>(null);
  const [telegramTestLoading, setTelegramTestLoading] = useState(false);
  const [sessionState, setSessionState] = useState(getSessionState());
  const [authBootstrapError, setAuthBootstrapError] = useState<string | null>(null);

  const oracleSymbols = useMemo(() => {
    const enabled = (user.symbols_enabled || []).map((s) => s.trim().toUpperCase()).filter(Boolean);
    if (enabled.length > 0) return Array.from(new Set(enabled));
    const available = (user.symbols_available || []).map((s) => s.trim().toUpperCase()).filter(Boolean);
    if (available.length > 0) return Array.from(new Set(available));
    return [];
  }, [user.symbols_available, user.symbols_enabled]);

  useEffect(() => {
    dashboardDebug("mounted");
  }, []);

  useEffect(() => {
    if (user && !renderDashboardLoggedRef.current) {
      renderDashboardLoggedRef.current = true;
      dashboardDebug("render dashboard");
    }
  }, [user]);

  useEffect(() => {
    let cancelled = false;

    async function bootstrapDashboard() {
      let currentSession = getSessionState();

      try {
        setAuthBootstrapError(null);
        setSessionState(currentSession);

        dashboardDebug("dashboard bootstrap start", {
          apiBase: safeDashboardApiBase(),
          hasAccessToken: currentSession.has_access_token,
          hasRefreshToken: currentSession.has_refresh_token,
        });

        if (!currentSession.has_access_token) {
          dashboardDebug("token missing", {
            accessTokenKey: "access_token",
          });
          dashboardDebug("local dev fallback", {
            reason: "missing access token",
          });
          if (!cancelled) {
            setUser(LOCAL_DEV_FALLBACK_USER);
            setSelectedSymbol((current) => current || LOCAL_DEV_FALLBACK_SYMBOLS[0]);
            setAuthBootstrapError(LOCAL_DEV_SESSION_CHECK_FAILED);
          }
          return;
        }
        dashboardDebug("token exists", {
          accessTokenKey: "access_token",
        });
        dashboardDebug("token found", {
          accessTokenKey: "access_token",
        });
        dashboardDebug("/me start", {
          url: `${safeDashboardApiBase()}/me`,
        });
        dashboardDebug("request start", {
          request: "/me",
          url: `${safeDashboardApiBase()}/me`,
        });
        const profile = await withTimeout(
          me(),
          DASHBOARD_BOOTSTRAP_TIMEOUT_MS,
          "Dashboard bootstrap timed out after 8 seconds while loading /me."
        );
        if (cancelled) return;

        currentSession = getSessionState();
        setSessionState(currentSession);

        dashboardDebug("request success", {
          request: "/me",
          status: 200,
          email: profile.email,
          role: profile.role,
        });
        dashboardDebug("/me response", {
          status: 200,
          ok: true,
          email: profile.email,
          role: profile.role,
        });
        dashboardDebug("/me success", {
          email: profile.email,
          role: profile.role,
        });
        setUser(profile);
        setAuthBootstrapError(null);
        const initialSymbols = (profile.symbols_enabled?.length
          ? profile.symbols_enabled
          : profile.symbols_available?.length
            ? profile.symbols_available
            : []
        )
          .map((s) => s.trim().toUpperCase())
          .filter(Boolean);
        setSelectedSymbol(initialSymbols[0] || LOCAL_DEV_FALLBACK_SYMBOLS[0]);
      } catch (err: unknown) {
        if (cancelled) return;
        dashboardDebug("session api failed", {
          request: "/me",
          status: getApiErrorStatus(err),
          message: dashboardErrorMessage(err, "Could not load your session."),
          apiBase: safeDashboardApiBase(),
          hasAccessToken: currentSession.has_access_token,
          hasRefreshToken: currentSession.has_refresh_token,
        });
        dashboardDebug("/me response", {
          status: getApiErrorStatus(err),
          ok: false,
          message: dashboardErrorMessage(err, "Could not load your session."),
        });
        dashboardDebug("bootstrap failure", {
          apiBase: safeDashboardApiBase(),
          hasAccessToken: currentSession.has_access_token,
          hasRefreshToken: currentSession.has_refresh_token,
          message: err instanceof Error ? err.message : String(err || ""),
        });
        setUser((current) => current ?? LOCAL_DEV_FALLBACK_USER);
        setSelectedSymbol((current) => current || LOCAL_DEV_FALLBACK_SYMBOLS[0]);
        setAuthBootstrapError(LOCAL_DEV_SESSION_CHECK_FAILED);
      }
    }

    void bootstrapDashboard();

    return () => {
      cancelled = true;
    };
  }, [router]);

  useEffect(() => {
    let alive = true;

    async function loadUsage() {
      try {
        dashboardDebug("/usage start", {
          url: `${safeDashboardApiBase()}/usage`,
        });
        dashboardDebug("request start", {
          request: "/usage",
          url: `${safeDashboardApiBase()}/usage`,
        });
        const usageData = await withTimeout(
          getUsage(DASHBOARD_OPTIONAL_API_OPTIONS),
          DASHBOARD_BOOTSTRAP_TIMEOUT_MS,
          "Dashboard bootstrap timed out after 8 seconds while loading /usage."
        );
        if (!alive) return;
        setUsage(usageData);
        setUsageError(null);
        dashboardDebug("request success", {
          request: "/usage",
          status: 200,
          plan: usageData.plan,
          usageStatus: usageData.status || null,
        });
        dashboardDebug("/usage response", {
          status: 200,
          ok: true,
          plan: usageData.plan,
          usageStatus: usageData.status || null,
        });
        dashboardDebug("/usage success", {
          plan: usageData.plan,
          status: usageData.status || null,
        });
      } catch (err: unknown) {
        if (!alive) return;
        setUsage(null);
        setUsageError(dashboardErrorMessage(err, "Could not load usage."));
        dashboardApiError("/usage", err);
        dashboardDebug("/usage response", {
          status: getApiErrorStatus(err),
          ok: false,
          message: dashboardErrorMessage(err, "Could not load usage."),
        });
        dashboardDebug("request failure", {
          request: "/usage",
          message: err instanceof Error ? err.message : String(err || ""),
        });
      }
    }

    void loadUsage();

    return () => {
      alive = false;
    };
  }, [user]);

  useEffect(() => {
    let alive = true;
    if (!selectedSymbol) {
      return () => {
        alive = false;
      };
    }

    async function loadOracle() {
      try {
        dashboardDebug("request start", {
          request: "/oracle/status",
          symbol: selectedSymbol,
          url: `${getConfiguredApiBase()}/oracle/status?symbol=${selectedSymbol}&stale_after_minutes=20`,
        });
        const status = await withTimeout(
          getOracleStatus(selectedSymbol, 20, DASHBOARD_OPTIONAL_API_OPTIONS),
          DASHBOARD_BOOTSTRAP_TIMEOUT_MS,
          "Dashboard optional API timed out after 8 seconds while loading /oracle/status."
        );
        if (!alive) return;
        setOracleStatus(status);
        setOracleStatusError(null);
        dashboardDebug("request success", {
          request: "/oracle/status",
          symbol: selectedSymbol,
          stale: status.is_stale,
        });
      } catch (err: unknown) {
        if (!alive) return;
        setOracleStatus(null);
        setOracleStatusError(dashboardErrorMessage(err, "Could not load oracle status."));
        dashboardApiError("/oracle/status", err, { symbol: selectedSymbol });
        dashboardDebug("request failure", {
          request: "/oracle/status",
          symbol: selectedSymbol,
          message: err instanceof Error ? err.message : String(err || ""),
        });
      }

      try {
        dashboardDebug("request start", {
          request: "/oracle/latest",
          symbol: selectedSymbol,
          url: `${getConfiguredApiBase()}/oracle/latest?symbol=${selectedSymbol}`,
        });
        const snapshot = await withTimeout(
          getOracleLatest(selectedSymbol, DASHBOARD_OPTIONAL_API_OPTIONS),
          DASHBOARD_BOOTSTRAP_TIMEOUT_MS,
          "Dashboard optional API timed out after 8 seconds while loading /oracle/latest."
        );
        if (!alive) return;
        setOracle(snapshot);
        setOracleError(null);
        dashboardDebug("request success", {
          request: "/oracle/latest",
          symbol: selectedSymbol,
          direction: snapshot.direction,
        });
      } catch (err: unknown) {
        if (!alive) return;
        setOracle(null);
        const raw = dashboardErrorMessage(err, "Could not load live oracle snapshot.");
        dashboardApiError("/oracle/latest", err, { symbol: selectedSymbol });
        dashboardDebug("request failure", {
          request: "/oracle/latest",
          symbol: selectedSymbol,
          message: raw,
        });
        if (raw.includes("No oracle snapshot available yet")) {
          setOracleError(
            `No market data available for ${selectedSymbol} yet. Start MT5 ingest for this symbol and refresh.`
          );
        } else {
          setOracleError(raw);
        }
      } finally {
        if (!alive) return;
        setOracleLoading(false);
      }
    }

    void loadOracle();
    const timer = setInterval(() => {
      void loadOracle();
    }, ORACLE_POLL_MS);

    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [selectedSymbol, user]);

  useEffect(() => {
    let alive = true;
    if (!selectedSymbol) {
      return () => {
        alive = false;
      };
    }
    setOracleDirection((current) => (current?.symbol === selectedSymbol ? current : null));
    setOracleDirectionError(null);

    async function loadOracleDirection() {
      try {
        dashboardDebug("request start", {
          request: "/oracle/direction/{symbol}",
          symbol: selectedSymbol,
          url: `${getConfiguredApiBase()}/oracle/direction/${selectedSymbol}`,
        });
        const data = await withTimeout(
          getOracleDirection(selectedSymbol, DASHBOARD_OPTIONAL_API_OPTIONS),
          DASHBOARD_BOOTSTRAP_TIMEOUT_MS,
          "Dashboard optional API timed out after 8 seconds while loading /oracle/direction."
        );
        if (!alive) return;
        setOracleDirection(data);
        setOracleDirectionError(null);
        dashboardDebug("request success", {
          request: "/oracle/direction/{symbol}",
          symbol: selectedSymbol,
          direction: data.direction,
        });
      } catch (err: unknown) {
        if (!alive) return;
        setOracleDirection((current) => (current?.symbol === selectedSymbol ? current : null));
        setOracleDirectionError(dashboardErrorMessage(err, "Could not load oracle direction."));
        dashboardApiError("/oracle/direction/{symbol}", err, { symbol: selectedSymbol });
        dashboardDebug("request failure", {
          request: "/oracle/direction/{symbol}",
          symbol: selectedSymbol,
          message: err instanceof Error ? err.message : String(err || ""),
        });
      }
    }

    void loadOracleDirection();
    const timer = setInterval(() => {
      void loadOracleDirection();
    }, ORACLE_POLL_MS);

    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [selectedSymbol, user]);

  useEffect(() => {
    let alive = true;
    if (!selectedSymbol) {
      return () => {
        alive = false;
      };
    }
    setLceCheckpoint((current) => (current?.symbol === selectedSymbol ? current : null));
    setLceCheckpointError(null);

    async function loadLceCheckpoint() {
      try {
        dashboardDebug("request start", {
          request: "/lce/checkpoint/{symbol}",
          symbol: selectedSymbol,
          timeframe: "H1",
          url: `${getConfiguredApiBase()}/lce/checkpoint/${selectedSymbol}?timeframe=H1`,
        });
        const data = await withTimeout(
          getLceCheckpoint(selectedSymbol, "H1", DASHBOARD_OPTIONAL_API_OPTIONS),
          DASHBOARD_BOOTSTRAP_TIMEOUT_MS,
          "Dashboard optional API timed out after 8 seconds while loading /lce/checkpoint."
        );
        if (!alive) return;
        setLceCheckpoint(data);
        setLceCheckpointError(null);
        dashboardDebug("request success", {
          request: "/lce/checkpoint/{symbol}",
          symbol: selectedSymbol,
          status: data.status,
          checkpoint: data.checkpoint,
        });
      } catch (err: unknown) {
        if (!alive) return;
        setLceCheckpoint((current) => (current?.symbol === selectedSymbol ? current : null));
        setLceCheckpointError(dashboardErrorMessage(err, "Could not load liquidity checkpoint."));
        dashboardApiError("/lce/checkpoint/{symbol}", err, { symbol: selectedSymbol });
        dashboardDebug("request failure", {
          request: "/lce/checkpoint/{symbol}",
          symbol: selectedSymbol,
          message: err instanceof Error ? err.message : String(err || ""),
        });
      }
    }

    void loadLceCheckpoint();
    const timer = setInterval(() => {
      void loadLceCheckpoint();
    }, ORACLE_POLL_MS);

    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [selectedSymbol, user]);

  useEffect(() => {
    let alive = true;
    if (!selectedSymbol) return () => undefined;
    const currentUser = user;
    const canViewPro =
      currentUser.role === "admin" || currentUser.tier === "pro" || currentUser.tier === "elite";
    if (!canViewPro) {
      return () => {
        alive = false;
      };
    }

    async function loadTargets() {
      try {
        const tier = currentUser.role === "admin" || currentUser.tier === "elite" ? "elite" : "pro";
        dashboardDebug("request start", {
          request: "/signals/targets/latest",
          symbol: selectedSymbol,
          tier,
          url: `${getConfiguredApiBase()}/signals/targets/latest?symbol=${selectedSymbol}&tier=${tier}`,
        });
        const data = await withTimeout(
          getSignalTargetsLatest(selectedSymbol, tier, DASHBOARD_OPTIONAL_API_OPTIONS),
          DASHBOARD_BOOTSTRAP_TIMEOUT_MS,
          "Dashboard optional API timed out after 8 seconds while loading /signals/targets/latest."
        );
        if (!alive) return;
        setProTargets(data);
        setProTargetsError(null);
        dashboardDebug("request success", {
          request: "/signals/targets/latest",
          symbol: selectedSymbol,
          tier,
          targetsAsOf: data.as_of_utc,
          latestMarketFeedAt: data.latest_market_feed_at_utc,
          latestMarketFeedSource: data.latest_market_feed_source,
          marketFeedDelayed: data.market_feed_delayed,
        });
      } catch (err: unknown) {
        if (!alive) return;
        setProTargets(null);
        setProTargetsError(dashboardErrorMessage(err, "Could not load target snapshot."));
        dashboardApiError("/signals/targets/latest", err, { symbol: selectedSymbol });
        dashboardDebug("request failure", {
          request: "/signals/targets/latest",
          symbol: selectedSymbol,
          message: err instanceof Error ? err.message : String(err || ""),
        });
      }
    }

    void loadTargets();
    const timer = setInterval(() => {
      void loadTargets();
    }, ORACLE_POLL_MS);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [selectedSymbol, user]);

  useEffect(() => {
    let alive = true;
    if (!selectedSymbol || user.role !== "admin") {
      return () => {
        alive = false;
      };
    }

    async function loadAnchorDebug() {
      try {
        dashboardDebug("request start", {
          request: "/ops/anchor-debug",
          symbol: selectedSymbol,
          url: `${getConfiguredApiBase()}/ops/anchor-debug?symbol=${selectedSymbol}`,
        });
        const data = await withTimeout(
          getOpsAnchorDebug(selectedSymbol, DASHBOARD_OPTIONAL_API_OPTIONS),
          DASHBOARD_BOOTSTRAP_TIMEOUT_MS,
          "Dashboard optional API timed out after 8 seconds while loading /ops/anchor-debug."
        );
        if (!alive) return;
        setAnchorDebug(data);
        setAnchorDebugError(null);
        dashboardDebug("request success", {
          request: "/ops/anchor-debug",
          symbol: selectedSymbol,
        });
      } catch (err: unknown) {
        if (!alive) return;
        setAnchorDebug(null);
        setAnchorDebugError(dashboardErrorMessage(err, "Failed to load 08:01 anchor debug."));
        dashboardApiError("/ops/anchor-debug", err, { symbol: selectedSymbol });
        dashboardDebug("request failure", {
          request: "/ops/anchor-debug",
          symbol: selectedSymbol,
          message: err instanceof Error ? err.message : String(err || ""),
        });
      }
    }

    void loadAnchorDebug();
    const timer = setInterval(() => {
      void loadAnchorDebug();
    }, ORACLE_POLL_MS);

    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [selectedSymbol, user]);

  useEffect(() => {
    let alive = true;

    async function loadRunnerStatus() {
      try {
        dashboardDebug("request start", {
          request: "/runner/status",
          url: `${getConfiguredApiBase()}/runner/status`,
        });
        const data = await withTimeout(
          getRunnerStatus(DASHBOARD_OPTIONAL_API_OPTIONS),
          DASHBOARD_BOOTSTRAP_TIMEOUT_MS,
          "Dashboard optional API timed out after 8 seconds while loading /runner/status."
        );
        if (!alive) return;
        setRunnerStatus(data);
        setRunnerStatusError(null);
        dashboardDebug("request success", {
          request: "/runner/status",
          runnerOnline: data.runner_online,
          mt5Connected: data.mt5_connected,
        });
      } catch (err: unknown) {
        if (!alive) return;
        setRunnerStatus(null);
        setRunnerStatusError(dashboardErrorMessage(err, "Failed to load runner status."));
        dashboardApiError("/runner/status", err);
        dashboardDebug("request failure", {
          request: "/runner/status",
          message: err instanceof Error ? err.message : String(err || ""),
        });
      } finally {
        if (alive) {
          setSessionState(getSessionState());
        }
      }
    }

    void loadRunnerStatus();
    const timer = setInterval(() => {
      void loadRunnerStatus();
    }, ORACLE_POLL_MS);

    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [user]);

  useEffect(() => {
    let alive = true;
    if (user.role !== "admin") {
      return () => {
        alive = false;
      };
    }
    async function loadReadiness() {
      try {
        dashboardDebug("request start", {
          request: "/admin/ops/readiness",
          url: `${getConfiguredApiBase()}/admin/ops/readiness`,
        });
        const data = await withTimeout(
          getAdminReadiness(DASHBOARD_OPTIONAL_API_OPTIONS),
          DASHBOARD_BOOTSTRAP_TIMEOUT_MS,
          "Dashboard optional API timed out after 8 seconds while loading /admin/ops/readiness."
        );
        if (!alive) return;
        setReadiness(data);
        setReadinessError(null);
        dashboardDebug("request success", {
          request: "/admin/ops/readiness",
          ok: data.ok,
        });
      } catch (err: unknown) {
        if (!alive) return;
        setReadiness(null);
        setReadinessError(dashboardErrorMessage(err, "Failed to load readiness."));
        dashboardApiError("/admin/ops/readiness", err);
        dashboardDebug("request failure", {
          request: "/admin/ops/readiness",
          message: err instanceof Error ? err.message : String(err || ""),
        });
      }
    }
    void loadReadiness();
    async function loadRunnerHealth() {
      try {
        dashboardDebug("request start", {
          request: "/health/runner",
          url: `${getConfiguredApiBase()}/health/runner`,
        });
        const data = await withTimeout(
          getRunnerHealth(DASHBOARD_OPTIONAL_API_OPTIONS),
          DASHBOARD_BOOTSTRAP_TIMEOUT_MS,
          "Dashboard optional API timed out after 8 seconds while loading /health/runner."
        );
        if (!alive) return;
        setRunnerHealth(data);
        setRunnerHealthError(null);
        dashboardDebug("request success", {
          request: "/health/runner",
          ok: data.ok,
          mt5Connected: data.mt5_connected,
        });
      } catch (err: unknown) {
        if (!alive) return;
        setRunnerHealth(null);
        setRunnerHealthError(dashboardErrorMessage(err, "Failed to load runner health."));
        dashboardApiError("/health/runner", err);
        dashboardDebug("request failure", {
          request: "/health/runner",
          message: err instanceof Error ? err.message : String(err || ""),
        });
      }
    }
    void loadRunnerHealth();
    const timer = setInterval(() => {
      void loadRunnerHealth();
    }, ORACLE_POLL_MS);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [user]);

  async function openBillingPortal() {
    setBillingError(null);
    try {
      setBillingLoading(true);
      const { url } = await withTimeout(
        createPortalSession(),
        DASHBOARD_BOOTSTRAP_TIMEOUT_MS,
        "Billing portal timed out after 8 seconds."
      );
      window.location.assign(url);
    } catch (error: unknown) {
      setBillingError(dashboardErrorMessage(error, "Failed to open billing portal."));
      dashboardApiError("/billing/portal", error);
    } finally {
      setBillingLoading(false);
    }
  }

  async function handleLogout() {
    await logoutSession();
    router.replace("/login");
  }

  async function runOracleNow() {
    setOracleRunMsg(null);
    setOracleError(null);
    try {
      setRunningOracle(true);
      const runResult = await withTimeout(
        runAdminOracle({ symbols: [selectedSymbol] }),
        DASHBOARD_BOOTSTRAP_TIMEOUT_MS,
        "Run oracle timed out after 8 seconds."
      );
      try {
        const [latest, status] = await Promise.all([
          withTimeout(
            getOracleLatest(selectedSymbol, DASHBOARD_OPTIONAL_API_OPTIONS),
            DASHBOARD_BOOTSTRAP_TIMEOUT_MS,
            "Dashboard optional API timed out after 8 seconds while loading /oracle/latest."
          ),
          withTimeout(
            getOracleStatus(selectedSymbol, 20, DASHBOARD_OPTIONAL_API_OPTIONS),
            DASHBOARD_BOOTSTRAP_TIMEOUT_MS,
            "Dashboard optional API timed out after 8 seconds while loading /oracle/status."
          ),
        ]);
        setOracle(latest);
        setOracleStatus(status);
        setOracleStatusError(null);
      } catch (refreshError: unknown) {
        setOracleError(dashboardErrorMessage(refreshError, "Oracle was run, but the refreshed snapshot could not be loaded."));
        dashboardApiError("/oracle/latest", refreshError, { symbol: selectedSymbol, source: "manual oracle refresh" });
      }
      try {
        const direction = await withTimeout(
          getOracleDirection(selectedSymbol, DASHBOARD_OPTIONAL_API_OPTIONS),
          DASHBOARD_BOOTSTRAP_TIMEOUT_MS,
          "Dashboard optional API timed out after 8 seconds while loading /oracle/direction."
        );
        setOracleDirection(direction);
        setOracleDirectionError(null);
      } catch (directionError: unknown) {
        setOracleDirectionError(dashboardErrorMessage(directionError, "Could not refresh oracle direction."));
        dashboardApiError("/oracle/direction/{symbol}", directionError, {
          symbol: selectedSymbol,
          source: "manual oracle refresh",
        });
      }
      if (user && (user.role === "admin" || user.tier === "pro" || user.tier === "elite")) {
        try {
          const tier = user.role === "admin" || user.tier === "elite" ? "elite" : "pro";
          const targets = await withTimeout(
            getSignalTargetsLatest(selectedSymbol, tier, DASHBOARD_OPTIONAL_API_OPTIONS),
            DASHBOARD_BOOTSTRAP_TIMEOUT_MS,
            "Dashboard optional API timed out after 8 seconds while loading /signals/targets/latest."
          );
          setProTargets(targets);
          setProTargetsError(null);
          dashboardDebug("manual oracle targets refresh", {
            symbol: selectedSymbol,
            tier,
            targetsAsOf: targets.as_of_utc,
            latestMarketFeedAt: targets.latest_market_feed_at_utc,
            latestMarketFeedSource: targets.latest_market_feed_source,
            marketFeedDelayed: targets.market_feed_delayed,
            targetRefreshRows: runResult.runs?.[0]?.targets_refresh?.length ?? 0,
          });
        } catch (targetsError: unknown) {
          setProTargetsError(dashboardErrorMessage(targetsError, "Could not refresh target snapshot."));
          dashboardApiError("/signals/targets/latest", targetsError, { symbol: selectedSymbol, source: "manual oracle refresh" });
          dashboardDebug("manual oracle targets refresh failed", {
            symbol: selectedSymbol,
            message: targetsError instanceof Error ? targetsError.message : String(targetsError || ""),
          });
        }
      }
      setOracleRunMsg("Oracle snapshot updated.");
    } catch (error: unknown) {
      setOracleError(dashboardErrorMessage(error, "Failed to run oracle."));
      dashboardApiError("/admin/oracle/run", error, { symbol: selectedSymbol });
    } finally {
      setRunningOracle(false);
    }
  }

  async function reconnectRunnerNow() {
    setRunnerReconnectMsg(null);
    setRunnerHealthError(null);
    try {
      setReconnectingRunner(true);
      await withTimeout(
        reconnectRunner(`dashboard_manual_${selectedSymbol}`),
        DASHBOARD_BOOTSTRAP_TIMEOUT_MS,
        "Runner reconnect timed out after 8 seconds."
      );
      const data = await withTimeout(
        getRunnerHealth(DASHBOARD_OPTIONAL_API_OPTIONS),
        DASHBOARD_BOOTSTRAP_TIMEOUT_MS,
        "Dashboard optional API timed out after 8 seconds while loading /health/runner."
      );
      setRunnerHealth(data);
      setRunnerReconnectMsg("Runner reconnect requested.");
    } catch (error: unknown) {
      setRunnerHealthError(dashboardErrorMessage(error, "Failed to reconnect runner."));
      dashboardApiError("/admin/ops/runner/reconnect", error, { symbol: selectedSymbol });
    } finally {
      setReconnectingRunner(false);
    }
  }

  async function sendAdminTelegramTest() {
    setTelegramTestMsg(null);
    setTelegramTestErr(null);
    try {
      setTelegramTestLoading(true);
      const res = await withTimeout(
        testTelegram(),
        DASHBOARD_BOOTSTRAP_TIMEOUT_MS,
        "Telegram test timed out after 8 seconds."
      );
      const msg = `Sent message_id=${res.message_id}`;
      setTelegramTestMsg(msg);
      alert(msg);
    } catch (error: unknown) {
      setTelegramTestErr(dashboardErrorMessage(error, "Failed to send telegram test."));
      dashboardApiError("/ops/telegram/test", error);
    } finally {
      setTelegramTestLoading(false);
    }
  }

  const usagePct = useMemo(() => {
    if (!usage) return 0;
    if (usage.limit == null) return 0;
    const denom = Math.max(1, usage.limit);
    return Math.min(100, Math.round((usage.used / denom) * 100));
  }, [usage]);

  const resetLabel = useMemo(() => {
    if (!usage?.resets_at) return null;
    const d = new Date(usage.resets_at);
    if (Number.isNaN(d.getTime())) return null;
    return d.toLocaleString();
  }, [usage]);

  const computedAtLabel = useMemo(() => {
    return formatLondonUtc(oracle?.computed_at);
  }, [oracle?.computed_at]);

  const targetsAsOf = useMemo(() => {
    if (proTargets?.as_of_utc) return proTargets.as_of_utc;
    if (oracle?.targets_as_of_utc) return oracle.targets_as_of_utc;
    return null;
  }, [proTargets, oracle]);

  const targetsAsOfLabel = useMemo(() => {
    return formatLondonUtc(targetsAsOf);
  }, [targetsAsOf]);

  const targetsAgeMinutes = useMemo(() => {
    return minutesAgo(targetsAsOf);
  }, [targetsAsOf]);

  const targetsFeedAsOf = useMemo(() => {
    if (proTargets?.latest_market_feed_at_utc) return proTargets.latest_market_feed_at_utc;
    return latestIsoTimestamp(oracleStatus?.last_ingest_at_utc, oracleStatus?.latest_candle_close_utc);
  }, [oracleStatus, proTargets]);

  const targetsFeedAgeMinutes = useMemo(() => {
    if (typeof proTargets?.market_feed_age_seconds === "number") {
      return Math.max(Math.floor(proTargets.market_feed_age_seconds / 60), 0);
    }
    return minutesAgo(targetsFeedAsOf);
  }, [proTargets, targetsFeedAsOf]);

  const targetsFeedDelayed = useMemo(() => {
    if (typeof proTargets?.market_feed_delayed === "boolean") {
      return proTargets.market_feed_delayed;
    }
    if (targetsFeedAgeMinutes != null) {
      const thresholdSeconds = proTargets?.market_feed_delay_threshold_seconds ?? PRO_TARGETS_DELAY_FALLBACK_SECONDS;
      return targetsFeedAgeMinutes * 60 > thresholdSeconds;
    }
    if (typeof oracleStatus?.is_stale === "boolean") {
      return oracleStatus.is_stale;
    }
    return false;
  }, [oracleStatus, proTargets, targetsFeedAgeMinutes]);

  const asOfLabel = useMemo(() => {
    return formatLondonUtc(oracle?.as_of);
  }, [oracle?.as_of]);

  const lastComputeLabel = useMemo(() => {
    return formatLondonUtc(oracleStatus?.last_compute_at_utc || oracleStatus?.last_compute_at);
  }, [oracleStatus?.last_compute_at, oracleStatus?.last_compute_at_utc]);

  const lastIngestLabel = useMemo(() => {
    return formatLondonUtc(oracleStatus?.last_ingest_at_utc || oracleStatus?.last_ingest_at);
  }, [oracleStatus?.last_ingest_at, oracleStatus?.last_ingest_at_utc]);

  const permissionAsOfLabel = useMemo(() => {
    return formatLondonUtc(oracle?.daily_permission_as_of_utc);
  }, [oracle?.daily_permission_as_of_utc]);
  const permissionLockLabel = useMemo(() => {
    return formatLondonUtc(oracle?.permission_lock_time_london ?? oracleStatus?.permission_lock_time_london);
  }, [oracle?.permission_lock_time_london, oracleStatus?.permission_lock_time_london]);

  const last0801Label = useMemo(() => {
    return formatLondonUtc(oracleStatus?.last_08_01_candle_time_utc);
  }, [oracleStatus?.last_08_01_candle_time_utc]);

  const lastComputeAgeMinutes = useMemo(() => {
    return minutesAgo(oracleStatus?.last_compute_at_utc || oracleStatus?.last_compute_at);
  }, [oracleStatus?.last_compute_at, oracleStatus?.last_compute_at_utc]);

  const runnerLastTickLabel = useMemo(() => formatLondonUtc(runnerHealth?.last_tick_utc), [runnerHealth?.last_tick_utc]);
  const runnerLagMinutes = useMemo(() => {
    if (runnerHealth?.lag_seconds == null) return null;
    return Math.max(Math.floor(runnerHealth.lag_seconds / 60), 0);
  }, [runnerHealth]);
  const apiProviderMode = Boolean(runnerHealth?.api_mode || oracleStatus?.api_mode);

  const runnerHeartbeatLabel = useMemo(
    () => formatLondonUtc(runnerStatus?.last_heartbeat_utc),
    [runnerStatus?.last_heartbeat_utc]
  );
  const runnerLastSignalLabel = useMemo(
    () => formatLondonUtc(runnerStatus?.last_signal_utc),
    [runnerStatus?.last_signal_utc]
  );
  const runnerLastTelegramLabel = useMemo(
    () => formatLondonUtc(runnerStatus?.last_telegram_sent_utc),
    [runnerStatus?.last_telegram_sent_utc]
  );
  const sessionStateLabel = useMemo(() => {
    if (!sessionState.has_access_token) return "Signed out";
    if (sessionState.has_refresh_token) return "Auto-refresh enabled";
    return "Access token only";
  }, [sessionState.has_access_token, sessionState.has_refresh_token]);

  const isAdmin = user.role === "admin";
  const canViewPro = isAdmin || user.tier === "pro" || user.tier === "elite";
  const canViewElite = isAdmin || user.tier === "elite";
  const effectiveTier = isAdmin ? "elite" : user.tier;
  const riskBanner = oracle?.risk_banner;
  const weeklyRange = oracle?.weekly_range;
  const hasRiskFlags = Boolean(riskBanner?.is_blueprint_day || riskBanner?.volume_spike);
  const tierRiskCopy =
    (effectiveTier === "basic"
      ? riskBanner?.tier_copy?.basic
      : effectiveTier === "pro"
        ? riskBanner?.tier_copy?.pro
        : riskBanner?.tier_copy?.elite) || "Risk filter active.";
  const optionalApiErrors = [
    usageError ? { label: "/usage", message: usageError } : null,
    oracleStatusError ? { label: "/oracle/status", message: oracleStatusError } : null,
    oracleError ? { label: "/oracle/latest", message: oracleError } : null,
    oracleDirectionError ? { label: "/oracle/direction/{symbol}", message: oracleDirectionError } : null,
    proTargetsError ? { label: "/signals/targets/latest", message: proTargetsError } : null,
    runnerStatusError ? { label: "/runner/status", message: runnerStatusError } : null,
    readinessError ? { label: "/admin/ops/readiness", message: readinessError } : null,
    runnerHealthError ? { label: "/health/runner", message: runnerHealthError } : null,
    anchorDebugError ? { label: "/ops/anchor-debug", message: anchorDebugError } : null,
  ].filter((item): item is { label: string; message: string } => Boolean(item));

  return (
    <div className="p-6 space-y-8">
        {authBootstrapError ? (
          <div className="max-w-xl rounded-xl border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900">
            <p className="font-semibold">{authBootstrapError}</p>
          </div>
        ) : null}
        <div className="flex items-center justify-between">
          <h1 className="text-2xl font-bold">Trading Intelligence Dashboard</h1>
          <div className="flex items-center gap-3">
            <select
              value={selectedSymbol}
              onChange={(e) => setSelectedSymbol(e.target.value)}
              className="text-sm px-2 py-2 rounded-md border bg-white"
            >
              {oracleSymbols.map((symbol) => (
                <option key={symbol} value={symbol}>
                  {symbol}
                </option>
              ))}
            </select>
            {isAdmin ? (
              <button
                onClick={runOracleNow}
                disabled={runningOracle}
                className="text-sm px-3 py-2 rounded-md border hover:bg-gray-50 disabled:opacity-50"
              >
                {runningOracle ? "Running..." : "Run Oracle"}
              </button>
            ) : null}
            <button
              onClick={openBillingPortal}
              disabled={billingLoading}
              className="text-sm px-3 py-2 rounded-md border hover:bg-gray-50 disabled:opacity-50"
            >
              {billingLoading ? "Opening..." : "Manage Billing"}
            </button>
            <button
              onClick={handleLogout}
              className="text-sm text-red-500 hover:underline"
            >
              Logout
            </button>
          </div>
        </div>

        {billingError ? (
          <div className="rounded-xl border p-3 text-sm">
            <span className="font-medium">Billing:</span> {billingError}
          </div>
        ) : null}

        {optionalApiErrors.length > 0 ? (
          <div className="rounded-xl border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900">
            <p className="font-semibold">Some dashboard data could not be loaded</p>
            <div className="mt-2 space-y-1">
              {optionalApiErrors.map((error) => (
                <p key={`${error.label}:${error.message}`}>
                  <span className="font-medium">{error.label}:</span> {error.message}
                </p>
              ))}
            </div>
          </div>
        ) : null}

        {oracleRunMsg ? <div className="rounded-xl border p-3 text-sm">{oracleRunMsg}</div> : null}

        <div className="rounded-xl border p-4">
          <h2 className="font-semibold mb-2">Automation Status</h2>
          <div className="text-sm space-y-1">
            <p>
              Runner:{" "}
              <b className={runnerStatus?.runner_online ? "text-green-700" : "text-amber-700"}>
                {runnerStatus?.runner_online ? "ONLINE" : "OFFLINE"}
              </b>
            </p>
            <p>
              Last heartbeat: <b>{runnerHeartbeatLabel || "-"}</b>
            </p>
            <p>
              Last signal: <b>{runnerLastSignalLabel || "-"}</b>
            </p>
            <p>
              Last Telegram sent: <b>{runnerLastTelegramLabel || "-"}</b>
            </p>
            <p>
              Session: <b>{sessionStateLabel}</b>
            </p>
            {sessionState.last_refreshed_at ? (
              <p>
                Session refreshed: <b>{formatLondonUtc(sessionState.last_refreshed_at) || sessionState.last_refreshed_at}</b>
              </p>
            ) : null}
            {runnerStatusError ? <p className="text-amber-700">Runner status: {runnerStatusError}</p> : null}
          </div>
        </div>

        {isAdmin ? (
          <div className="rounded-xl border p-4">
            <h2 className="font-semibold mb-2">Production Readiness</h2>
            {readinessError ? (
              <p className="text-sm">{readinessError}</p>
            ) : readiness ? (
              <div className="text-sm space-y-1">
                <p>
                  Overall: <b>{readiness.ok ? "READY" : "NOT READY"}</b> ({readiness.env})
                </p>
                {Object.entries(readiness.checks).map(([name, check]) => (
                  <p key={name}>
                    {name}: <b>{check.ok ? "OK" : "FAIL"}</b>
                  </p>
                ))}
              </div>
            ) : (
              <p className="text-sm text-gray-500">Loading readiness checks...</p>
            )}
          </div>
        ) : null}

        {isAdmin ? (
          <div className="rounded-xl border p-4">
            <h2 className="font-semibold mb-2">Ops (Admin)</h2>
            <button
              onClick={sendAdminTelegramTest}
              disabled={telegramTestLoading}
              className="text-sm px-3 py-2 rounded-md border hover:bg-gray-50 disabled:opacity-50"
            >
              {telegramTestLoading ? "Sending..." : "Send Telegram Test"}
            </button>
            {telegramTestMsg ? <p className="mt-2 text-sm">{telegramTestMsg}</p> : null}
            {telegramTestErr ? <p className="mt-2 text-sm text-amber-700">{telegramTestErr}</p> : null}
          </div>
        ) : null}

        {isAdmin ? (
          <div className="rounded-xl border p-4">
            <div className="mb-2 flex items-center justify-between">
              <h2 className="font-semibold">Oracle Debug</h2>
              {!apiProviderMode ? (
                <button
                  onClick={reconnectRunnerNow}
                  disabled={reconnectingRunner}
                  className="text-sm px-3 py-1 rounded-md border hover:bg-gray-50 disabled:opacity-50"
                >
                  {reconnectingRunner ? "Reconnecting..." : "Reconnect Runner"}
                </button>
              ) : null}
            </div>
            {oracleStatus ? (
              <div className="text-sm space-y-1">
                <p>
                  Timezone: <b>{oracleStatus.timezone || "Europe/London"}</b>
                </p>
                <p>
                  Last ingest (UTC): <b>{oracleStatus.last_ingest_at_utc || "-"}</b>
                </p>
                <p>
                  Last compute (UTC): <b>{oracleStatus.last_compute_at_utc || "-"}</b>
                </p>
                <p>
                  {apiProviderMode ? "Last candle time (UTC)" : "MT5 last tick (UTC)"}:{" "}
                  <b>{(apiProviderMode ? runnerHealth?.last_candle_time || oracleStatus.last_candle_time : runnerHealth?.last_tick_utc) || "-"}</b>
                </p>
                <p>
                  {apiProviderMode ? "Candle lag (minutes)" : "MT5 lag (minutes)"}: <b>{runnerLagMinutes ?? "-"}</b>
                </p>
                <p>
                  {apiProviderMode ? "Last candle time (London)" : "MT5 last tick (London)"}:{" "}
                  <b>{apiProviderMode ? formatLondonUtc(runnerHealth?.last_candle_time || oracleStatus.last_candle_time) || "-" : runnerLastTickLabel || "-"}</b>
                </p>
                {apiProviderMode ? (
                  <>
                    <p>
                      Candle provider: <b>{oracleStatus.candle_provider || runnerHealth?.candle_provider || "-"}</b>
                    </p>
                    <p>
                      Fallback provider: <b>{oracleStatus.fallback_provider || runnerHealth?.fallback_provider || "-"}</b>
                    </p>
                    <p>
                      Latest candle source: <b>{oracleStatus.latest_candle_source || runnerHealth?.latest_candle_source || "-"}</b>
                    </p>
                    <p>
                      08:01 anchor candle source: <b>{oracleStatus.anchor_candle_source || "-"}</b>
                    </p>
                    <p>
                      08:01 anchor candle status: <b>{oracleStatus.anchor_candle_status || "-"}</b>
                    </p>
                  </>
                ) : null}
                <p>
                  Last 08:01 candle (UTC): <b>{oracleStatus.last_08_01_candle_time_utc || "-"}</b>
                </p>
                <p>
                  08:01 target (UTC): <b>{oracleStatus.daily_permission_target_utc || "-"}</b>
                </p>
                {!apiProviderMode ? (
                  <>
                    <p>
                      Broker offset (hours): <b>{oracleStatus.broker_offset_hours ?? "-"}</b>
                    </p>
                    <p>
                      Broker server time (UTC): <b>{oracleStatus.broker_server_time_utc || "-"}</b>
                    </p>
                    <p>
                      Expected 08:01 broker time (UTC): <b>{oracleStatus.expected_0801_broker_time || "-"}</b>
                    </p>
                  </>
                ) : null}
                <p>
                  Actual candle found time (UTC): <b>{oracleStatus.actual_candle_found_time || "-"}</b>
                </p>
                {!apiProviderMode ? (
                  <>
                    <p>
                      Runner control configured: <b>{runnerHealth?.runner_control_configured ? "YES" : "NO"}</b>
                    </p>
                    <p>
                      Runner control ok: <b>{runnerHealth?.runner_control_ok ? "YES" : "NO"}</b>
                    </p>
                    <p>
                      Runner MT5 initialized: <b>{runnerHealth?.mt5_initialized ? "YES" : "NO"}</b>
                    </p>
                    <p>
                      Runner MT5 logged in: <b>{runnerHealth?.mt5_logged_in ? "YES" : "NO"}</b>
                    </p>
                    <p>
                      Runner MT5 connected: <b>{runnerHealth?.mt5_connected ? "YES" : "NO"}</b>
                    </p>
                  </>
                ) : (
                  <p>
                    API candle provider connected: <b>{runnerHealth?.provider_connected ? "YES" : "NO"}</b>
                  </p>
                )}
                <p>
                  {apiProviderMode ? "Provider last error" : "Runner last error"}:{" "}
                  <b>{runnerHealth?.last_error || "-"}</b>
                </p>
                <p>
                  {apiProviderMode ? "Provider check time (UTC)" : "Runner server time (UTC)"}:{" "}
                  <b>{runnerHealth?.server_time_utc || "-"}</b>
                </p>
                {!apiProviderMode ? (
                  <>
                    <p>
                      Runner account login/server:{" "}
                      <b>
                        {runnerHealth?.account?.login ? String(runnerHealth.account.login) : "-"}
                        {" / "}
                        {runnerHealth?.account?.server ? String(runnerHealth.account.server) : "-"}
                      </b>
                    </p>
                    <p>
                      Runner algo trading allowed:{" "}
                      <b>
                        {runnerHealth?.terminal?.trade_allowed == null
                          ? "-"
                          : String(Boolean(runnerHealth.terminal.trade_allowed)).toUpperCase()}
                      </b>
                    </p>
                    <p>
                      Runner symbols OK: <b>{runnerHealth?.symbols_ok?.join(", ") || "-"}</b>
                    </p>
                  </>
                ) : (
                  <p>
                    Provider symbol checked: <b>{runnerHealth?.symbols_ok?.join(", ") || "-"}</b>
                  </p>
                )}
                {!apiProviderMode && runnerHealth?.runner_control_error ? (
                  <p className="text-amber-700">Runner control error: {runnerHealth.runner_control_error}</p>
                ) : null}
                {!apiProviderMode && !runnerHealth?.runner_control_error && runnerHealth?.runner_control_warning ? (
                  <p className="text-gray-600">Runner control: {runnerHealth.runner_control_warning}</p>
                ) : null}
                {runnerHealthError ? (
                  <p className="text-amber-700">
                    {apiProviderMode ? "Provider health" : "Runner health"}: {runnerHealthError}
                  </p>
                ) : null}
                {!apiProviderMode && runnerReconnectMsg ? <p className="text-gray-700">{runnerReconnectMsg}</p> : null}
                <p>
                  Stale: <b>{oracleStatus.is_stale ? "YES" : "NO"}</b>
                </p>
                <p>
                  Stale reasons: <b>{oracleStatus.stale_reasons?.join(", ") || "-"}</b>
                </p>
                {oracleStatus.daily_permission_degraded ? (
                  <p className="text-amber-700">
                    Degraded: <b>{oracleStatus.daily_permission_degraded_reason || "08:01 data missing"}</b>
                  </p>
                ) : null}
                {oracleStatus.daily_permission_backfill_attempted ? (
                  <p className="text-amber-700">
                    Backfill: <b>attempted</b>
                  </p>
                ) : null}
                {last0801Label ? (
                  <p>
                    Last 08:01 candle (London): <b>{last0801Label}</b>
                  </p>
                ) : null}
              </div>
            ) : (
              <p className="text-sm text-gray-500">No oracle status loaded.</p>
            )}
          </div>
        ) : null}

        {isAdmin ? (
          <div className="rounded-xl border p-4">
            <h2 className="font-semibold mb-2">08:01 London Permission Debug</h2>
            {anchorDebugError ? (
              <p className="text-sm text-amber-700">{anchorDebugError}</p>
            ) : anchorDebug ? (
              <div className="grid gap-4 text-sm md:grid-cols-3">
                <div className="space-y-1">
                  <p className="font-medium">08:01 Anchor Candle</p>
                  <p>
                    Direction: <b>{anchorDebug.anchor.direction}</b>
                  </p>
                  <p>
                    Open: <b>{formatNumber(anchorDebug.anchor.open)}</b>
                  </p>
                  <p>
                    High: <b>{formatNumber(anchorDebug.anchor.high)}</b>
                  </p>
                  <p>
                    Low: <b>{formatNumber(anchorDebug.anchor.low)}</b>
                  </p>
                  <p>
                    Close: <b>{formatNumber(anchorDebug.anchor.close)}</b>
                  </p>
                  <p>
                    Body Size: <b>{formatNumber(anchorDebug.anchor.body_size)}</b>
                  </p>
                  <p>
                    Wick Size: <b>{formatNumber(anchorDebug.anchor.wick_size)}</b>
                  </p>
                  <p>
                    Anchor Time (London): <b>{anchorDebug.anchor.candle_time_london || "-"}</b>
                  </p>
                  <p>
                    Anchor Time (UTC): <b>{anchorDebug.anchor.candle_time_utc || "-"}</b>
                  </p>
                </div>

                <div className="space-y-1">
                  <p className="font-medium">Official Permission</p>
                  <p>
                    Daily Permission: <b>{anchorDebug.official_permission.daily_permission}</b>
                  </p>
                  <p>
                    Permission Source: <b>{anchorDebug.official_permission.permission_source}</b>
                  </p>
                  <p>
                    Permission Lock Time: <b>{anchorDebug.official_permission.permission_lock_time || "-"}</b>
                  </p>
                  <p>
                    Last Refreshed At:{" "}
                    <b>{formatLondonUtc(anchorDebug.official_permission.last_refreshed_at) || "-"}</b>
                  </p>
                  <p>
                    Permission Time:{" "}
                    <b>{formatLondonUtc(anchorDebug.official_permission.permission_time_utc) || "-"}</b>
                  </p>
                  <p>
                    Final Allowed: <b>{anchorDebug.official_permission.final_allowed}</b>
                  </p>
                </div>

                <div className="space-y-1">
                  <p className="font-medium">Time Mapping</p>
                  <p>
                    Requested symbol: <b>{anchorDebug.time_mapping.requested_symbol || "-"}</b>
                  </p>
                  <p>
                    {anchorDebug.time_mapping.api_mode ? "Resolved candle symbol" : "Resolved MT5 symbol"}:{" "}
                    <b>{anchorDebug.time_mapping.resolved_candle_symbol || anchorDebug.time_mapping.resolved_mt5_symbol || "-"}</b>
                  </p>
                  {anchorDebug.time_mapping.api_mode ? (
                    <>
                      <p>
                        Candle provider: <b>{anchorDebug.time_mapping.candle_provider || "-"}</b>
                      </p>
                      <p>
                        Fallback provider: <b>{anchorDebug.time_mapping.fallback_provider || "-"}</b>
                      </p>
                      <p>
                        08:01 anchor candle source: <b>{anchorDebug.time_mapping.anchor_candle_source || "-"}</b>
                      </p>
                      <p>
                        08:01 anchor candle status: <b>{anchorDebug.time_mapping.anchor_candle_status || "-"}</b>
                      </p>
                    </>
                  ) : null}
                  <p>
                    London time used: <b>{anchorDebug.time_mapping.london_time_used || "-"}</b>
                  </p>
                  <p>
                    UTC time used: <b>{anchorDebug.time_mapping.utc_time_used || "-"}</b>
                  </p>
                  {!anchorDebug.time_mapping.api_mode ? (
                    <>
                      <p>
                        Broker/server time: <b>{anchorDebug.time_mapping.broker_server_time_utc || "-"}</b>
                      </p>
                      <p>
                        Expected 08:01 broker time: <b>{anchorDebug.time_mapping.expected_0801_broker_time || "-"}</b>
                      </p>
                    </>
                  ) : null}
                  <p>
                    Actual candle found: <b>{anchorDebug.time_mapping.actual_candle_found_time || "-"}</b>
                  </p>
                  <p>
                    Lookup UTC window:{" "}
                    <b>
                      {anchorDebug.time_mapping.lookup_start_utc || "-"} to {anchorDebug.time_mapping.lookup_end_utc || "-"}
                    </b>
                  </p>
                  {!anchorDebug.time_mapping.api_mode ? (
                    <p>
                      Lookup broker window:{" "}
                      <b>
                        {anchorDebug.time_mapping.lookup_start_broker_utc || "-"} to{" "}
                        {anchorDebug.time_mapping.lookup_end_broker_utc || "-"}
                      </b>
                    </p>
                  ) : null}
                  <p>
                    M1 candles returned:{" "}
                    <b>
                      UTC {anchorDebug.time_mapping.m1_candles_returned_utc_window ?? "-"}
                      {!anchorDebug.time_mapping.api_mode
                        ? ` / Broker ${anchorDebug.time_mapping.m1_candles_returned_broker_window ?? "-"}`
                        : ""}{" "}
                      / Total{" "}
                      {anchorDebug.time_mapping.m1_candles_returned_total ?? "-"}
                    </b>
                  </p>
                  <p>
                    Nearest available candle:{" "}
                    <b>
                      {anchorDebug.time_mapping.nearest_available_candle_time || "-"}
                      {anchorDebug.time_mapping.nearest_available_candle_source
                        ? ` (${anchorDebug.time_mapping.nearest_available_candle_source})`
                        : ""}
                    </b>
                  </p>
                  {anchorDebug.time_mapping.nearest_available_candle_delta_seconds != null ? (
                    <p>
                      Nearest delta (seconds): <b>{anchorDebug.time_mapping.nearest_available_candle_delta_seconds}</b>
                    </p>
                  ) : null}
                  <p>
                    Source path:{" "}
                    <b>
                      {anchorDebug.time_mapping.ingest_origin}
                      {anchorDebug.time_mapping.selection_source
                        ? ` (${anchorDebug.time_mapping.selection_source})`
                        : ""}
                    </b>
                  </p>
                  {anchorDebug.time_mapping.selection_tolerance_seconds != null ? (
                    <p>
                      Selection tolerance (seconds): <b>{anchorDebug.time_mapping.selection_tolerance_seconds}</b>
                    </p>
                  ) : null}
                  {anchorDebug.time_mapping.selected_time_delta_seconds != null ? (
                    <p>
                      Selected delta (seconds): <b>{anchorDebug.time_mapping.selected_time_delta_seconds}</b>
                    </p>
                  ) : null}
                  <p>
                    Backfill attempted: <b>{anchorDebug.time_mapping.backfill_attempted ? "YES" : "NO"}</b>
                  </p>
                </div>

                <div className="space-y-1 md:col-span-3">
                  <p className="font-medium">Explanation</p>
                  {anchorDebug.explanations?.map((line) => (
                    <p key={line} className="text-gray-700">
                      - {line}
                    </p>
                  ))}
                </div>
              </div>
            ) : (
              <p className="text-sm text-gray-500">Loading 08:01 anchor debug...</p>
            )}
          </div>
        ) : null}

        {hasRiskFlags ? (
          <div className="rounded-xl border border-amber-300 bg-amber-50 p-3 text-sm">
            <p className="font-semibold text-amber-900">Risk Banner Active</p>
            <p className="text-amber-900">{tierRiskCopy}</p>
            <p className="text-amber-900">
              {riskBanner?.is_blueprint_day ? "Blueprint Day" : null}
              {riskBanner?.is_blueprint_day && riskBanner?.volume_spike ? " + " : null}
              {riskBanner?.volume_spike ? "M15 Volume Spike" : null}
              {typeof riskBanner?.suggested_risk_multiplier === "number"
                ? ` | Suggested Risk: ${riskBanner.suggested_risk_multiplier.toFixed(2)}x`
                : ""}
            </p>
            <p className="text-amber-900">
              Weekly Range: <b>{weeklyRange?.status || "Building"}</b>
            </p>
          </div>
        ) : null}

        <div className="flex items-center justify-between rounded-xl border p-4">
          <div>
            <p className="text-sm text-gray-500">Current Plan</p>
            <p className="text-lg font-semibold uppercase">{user.tier}</p>
            {user.status ? <p className="text-xs text-gray-500 mt-1">Status: {user.status}</p> : null}
          </div>
          <div className="flex gap-2">
            {user.tier === "basic" && <UpgradeButton targetTier="pro" />}
            {user.tier === "pro" && <UpgradeButton targetTier="elite" />}
            {user.tier === "elite" && (
              <span className="text-sm text-green-600 font-medium">You&apos;re on Elite</span>
            )}
          </div>
        </div>

        <div className="grid gap-6 md:grid-cols-2">
          <div className="rounded-xl border p-4">
            <div className="flex items-center justify-between">
              <h2 className="font-semibold">Signals Remaining</h2>
              {usage ? (
                <span className="text-sm text-gray-500">
                  {usage.remaining == null ? "Unlimited" : `${usage.remaining} left`}
                </span>
              ) : (
                <span className="text-sm text-gray-500">-</span>
              )}
            </div>

            {usageError ? (
              <p className="mt-2 text-sm">{usageError}</p>
            ) : usage ? (
              <>
                <p className="mt-2 text-sm text-gray-500">
                  Used {usage.used} / {usage.limit == null ? "Unlimited" : usage.limit}
                </p>
                {usage.limit == null ? null : (
                  <div className="mt-3 h-2 w-full rounded bg-gray-200 overflow-hidden">
                    <div className="h-2 bg-black" style={{ width: `${usagePct}%` }} />
                  </div>
                )}
                <div className="mt-3 flex items-center justify-between text-xs text-gray-500">
                  <span>{usage.limit == null ? "Fair-use tracking" : `${usagePct}% used`}</span>
                  {resetLabel ? <span>Resets: {resetLabel}</span> : <span />}
                </div>
              </>
            ) : (
              <p className="mt-2 text-sm text-gray-500">Fetching usage...</p>
            )}
          </div>

          <div className="rounded-xl border p-4">
            <div className="mb-2 flex items-center gap-2">
              <h2 className="font-semibold">
                {oracle ? `${oracle.symbol} Daily Bias Snapshot` : `${selectedSymbol} Live Oracle Snapshot`}
              </h2>
              {oracleStatus?.is_stale ? (
                <span className="rounded bg-amber-100 px-2 py-0.5 text-xs font-semibold text-amber-800">
                  STALE
                </span>
              ) : null}
            </div>
            {oracleError ? (
              <p className="text-sm">{oracleError}</p>
            ) : oracle ? (
              <div className="space-y-1 text-sm">
                <p className="text-lg font-semibold">
                  Final Allowed: <span>{oracle.final_allowed}</span>
                </p>
                {oracle.daily_permission ? (
                  <p>
                    Daily Permission: <b>{oracle.daily_permission}</b>
                    {oracle.permission_stage ? (
                      <span>
                        {" "}
                        ({oracle.permission_stage === "PRELIM" ? "PRELIM (Asia)" : "OFFICIAL (London 08:01)"})
                      </span>
                    ) : null}
                  </p>
                ) : null}
                {oracle.permission_source ? (
                  <p className="text-gray-500">
                    Source: <b>{oracle.permission_source}</b>
                  </p>
                ) : null}
                {oracle.permission_for_date_uk ? (
                  <p className="text-gray-500">
                    For Date (London): <b>{oracle.permission_for_date_uk}</b>
                  </p>
                ) : null}
                {permissionAsOfLabel ? (
                  <p className="text-gray-500">
                    Permission Time: <b>{permissionAsOfLabel}</b>
                  </p>
                ) : null}
                {permissionLockLabel ? (
                  <p className="text-gray-500">
                    Permission Lock Time: <b>{permissionLockLabel}</b>
                  </p>
                ) : null}
                {oracle.conflict_with_prelim && oracle.conflict_note ? (
                  <p className="text-amber-700">
                    <b>{oracle.conflict_note}</b>
                  </p>
                ) : null}
                {!permissionAsOfLabel && oracleStatus?.daily_permission_missing ? (
                  <p className="text-amber-700 font-medium">
                    08:01 candle not available yet.
                    {oracleStatus.daily_permission_backfill_attempted ? " Backfill attempted." : ""}
                  </p>
                ) : null}
                {oracle.opportunity_direction ? (
                  <p>
                    Opportunity (M15): <b>{oracle.opportunity_direction}</b>
                  </p>
                ) : null}
                {typeof oracle.confirm_ok === "boolean" ? (
                  <p>
                    H1 Confirmation: <b>{oracle.confirm_ok ? "CONFIRMED" : "NOT CONFIRMED"}</b>
                  </p>
                ) : null}
                <p className="text-gray-500">{oracle.reason || oracle.message}</p>
                <p className="text-gray-500">Confidence: {(oracle.confidence * 100).toFixed(1)}%</p>
                {oracle.ny_context_active && oracle.ny_note ? (
                  <p className="text-gray-500">
                    NY Context: <b>{oracle.ny_note}</b>
                  </p>
                ) : null}
                <p className="text-gray-500">
                  Timeframes: {oracle.timeframes.signal} signal / {oracle.timeframes.confirm} confirm
                </p>
                {oracle.weekly_range ? (
                  <p className="text-gray-500">
                    Weekly Range: <b>{oracle.weekly_range.status}</b> | High: {oracle.weekly_range.high} Low:{" "}
                    {oracle.weekly_range.low} Mid: {oracle.weekly_range.mid}
                  </p>
                ) : null}
                {oracle.risk_banner?.volume_spike ? (
                  <p className="text-gray-500">
                    Volume Spike: <b>YES</b>
                    {typeof oracle.risk_banner.volume_ratio === "number"
                      ? ` (${oracle.risk_banner.volume_ratio.toFixed(2)}x median)`
                      : ""}
                  </p>
                ) : null}
                {oracle.candle ? (
                  <p className="text-gray-500">
                    O: {oracle.candle.open} H: {oracle.candle.high} L: {oracle.candle.low} C: {oracle.candle.close}
                    {oracle.candle.volume != null ? ` | Vol: ${oracle.candle.volume}` : ""}
                  </p>
                ) : null}
                {oracleStatus?.is_stale ? (
                  <p className="text-amber-700 font-medium">
                    Market feed delayed: {oracleStatus.stale_reasons?.join(", ") || "freshness threshold exceeded"}.
                  </p>
                ) : null}
                {asOfLabel ? <p className="text-gray-500">As of: {asOfLabel}</p> : null}
                {computedAtLabel ? <p className="text-gray-500">Computed: {computedAtLabel}</p> : null}
                {lastComputeLabel ? <p className="text-gray-500">Last compute: {lastComputeLabel}</p> : null}
                {lastIngestLabel ? <p className="text-gray-500">Last ingest: {lastIngestLabel}</p> : null}
                {lastComputeAgeMinutes != null ? (
                  <p className="text-gray-500">Updated {lastComputeAgeMinutes} min ago</p>
                ) : null}
              </div>
            ) : (
              <p className="text-sm text-gray-500">
                {oracleLoading ? "Loading live oracle snapshot..." : "No snapshot available yet."}
              </p>
            )}
          </div>

          <div className="rounded-xl border p-4">
            <div className="mb-2 flex items-center justify-between gap-3">
              <h2 className="font-semibold">Oracle Direction</h2>
              {oracleDirection?.latest_candle_source ? (
                <span className="text-xs uppercase text-gray-500">{oracleDirection.latest_candle_source}</span>
              ) : null}
            </div>
            {oracleDirection ? (
              <div className="space-y-3 text-sm">
                <p className={`text-2xl font-semibold ${oracleDirectionTone(oracleDirection.direction)}`}>
                  {oracleDirection.direction}
                </p>
                <div className="grid grid-cols-3 gap-2">
                  <div>
                    <p className="text-xs text-gray-500">BUY %</p>
                    <p className="font-semibold">{formatPercent(oracleDirection.buy_percent)}</p>
                  </div>
                  <div>
                    <p className="text-xs text-gray-500">SELL %</p>
                    <p className="font-semibold">{formatPercent(oracleDirection.sell_percent)}</p>
                  </div>
                  <div>
                    <p className="text-xs text-gray-500">CONFIDENCE</p>
                    <p className="font-semibold">{formatPercent(oracleDirection.confidence_percent)}</p>
                  </div>
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <p className="text-xs text-gray-500">NEXT BUY LIQUIDITY</p>
                    <p className="font-semibold">{formatNumber(oracleDirection.next_buy_liquidity)}</p>
                  </div>
                  <div>
                    <p className="text-xs text-gray-500">NEXT SELL LIQUIDITY</p>
                    <p className="font-semibold">{formatNumber(oracleDirection.next_sell_liquidity)}</p>
                  </div>
                </div>
                <p className="text-gray-500">
                  08:01 Permission: <b>{oracleDirection.daily_permission || "-"}</b>
                  {oracleDirection.permission_stage ? ` (${oracleDirection.permission_stage})` : ""}
                </p>
                {oracleDirection.news ? (
                  <p className="text-gray-500">
                    Finnhub:{" "}
                    <b>
                      {oracleDirection.news.available
                        ? `${oracleDirection.news.news_count ?? 0} news / ${
                            oracleDirection.news.high_impact_event_count ?? 0
                          } high-impact events`
                        : "unavailable"}
                    </b>
                  </p>
                ) : null}
                <p className="text-gray-500">
                  Candle: <b>{formatLondonUtc(oracleDirection.candle_time_utc) || "-"}</b>
                </p>
                {oracleDirectionError ? <p className="text-amber-700">{oracleDirectionError}</p> : null}
              </div>
            ) : oracleDirectionError ? (
              <p className="text-sm text-amber-700">{oracleDirectionError}</p>
            ) : (
              <p className="text-sm text-gray-500">Loading oracle direction...</p>
            )}
          </div>

          <div className="rounded-xl border p-4">
            <div className="mb-2 flex items-center justify-between gap-3">
              <h2 className="font-semibold">Liquidity Checkpoint Engine</h2>
              {lceCheckpoint?.timeframe ? (
                <span className="text-xs uppercase text-gray-500">{lceCheckpoint.timeframe}</span>
              ) : null}
            </div>
            {lceCheckpoint ? (
              <div className="space-y-3 text-sm">
                <div className="grid grid-cols-3 gap-2">
                  <div>
                    <p className="text-xs text-gray-500">CHECKPOINT</p>
                    <p className="font-semibold">{formatNumber(lceCheckpoint.checkpoint)}</p>
                  </div>
                  <div>
                    <p className="text-xs text-gray-500">TYPE</p>
                    <p className="font-semibold">{formatLceType(lceCheckpoint.checkpoint_type)}</p>
                  </div>
                  <div>
                    <p className="text-xs text-gray-500">CONFIDENCE</p>
                    <p className="font-semibold">{formatConfidence(lceCheckpoint.confidence)}</p>
                  </div>
                </div>
                <p className="text-gray-500">
                  Status: <b>{formatLceType(lceCheckpoint.status)}</b>
                </p>
                <p className="text-gray-500">{lceCheckpoint.meaning}</p>
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <p className="text-xs text-gray-500">BULLISH CONTINUATION</p>
                    <p className="font-semibold">
                      {formatLevelPath(lceCheckpoint.after_sweep?.bullish_continuation)}
                    </p>
                  </div>
                  <div>
                    <p className="text-xs text-gray-500">BEARISH REJECTION</p>
                    <p className="font-semibold">{formatLevelPath(lceCheckpoint.after_sweep?.bearish_rejection)}</p>
                  </div>
                </div>
                {lceCheckpointError ? <p className="text-amber-700">{lceCheckpointError}</p> : null}
              </div>
            ) : lceCheckpointError ? (
              <p className="text-sm text-amber-700">{lceCheckpointError}</p>
            ) : (
              <p className="text-sm text-gray-500">Loading liquidity checkpoint...</p>
            )}
          </div>

          <div className="rounded-xl border p-4">
            <h2 className="font-semibold mb-2">Pro Targets</h2>
            {!canViewPro ? (
              <div className="space-y-2">
                <p className="text-sm text-gray-500">Upgrade to Pro to view liquidity magnet and zone target.</p>
                <UpgradeButton targetTier="pro" />
              </div>
            ) : proTargets ? (
              <div className="text-sm space-y-1">
                <p>
                  Liquidity Magnet: <b>{proTargets.magnet_price ?? "-"}</b>
                </p>
                <p>
                  Zone-to-Zone Target: <b>{proTargets.zone_to_zone_target ?? "-"}</b>
                </p>
                <p>
                  Sellside Liquidity: <b>{proTargets.sellside_liquidity ?? "-"}</b>
                </p>
                <p>
                  Buyside Liquidity: <b>{proTargets.buyside_liquidity ?? "-"}</b>
                </p>
                {targetsAsOfLabel ? <p className="text-gray-500">As of: {targetsAsOfLabel}</p> : null}
                {targetsAgeMinutes != null ? <p className="text-gray-500">Updated {targetsAgeMinutes} min ago</p> : null}
                {targetsFeedDelayed ? (
                  <p className="text-amber-700 font-medium">Market feed delayed</p>
                ) : null}
              </div>
            ) : oracle ? (
              <div className="text-sm space-y-1">
                <p>
                  Liquidity Magnet: <b>{oracle.liquidity_magnet ?? "-"}</b>
                </p>
                <p>
                  Zone-to-Zone Target: <b>{oracle.zone_to_zone_target ?? "-"}</b>
                </p>
                <p>
                  Sellside Liquidity: <b>{oracle.targets_json?.sellside_liquidity ?? "-"}</b>
                </p>
                <p>
                  Buyside Liquidity: <b>{oracle.targets_json?.buyside_liquidity ?? "-"}</b>
                </p>
                {targetsAsOfLabel ? <p className="text-gray-500">As of: {targetsAsOfLabel}</p> : null}
                {targetsAgeMinutes != null ? <p className="text-gray-500">Updated {targetsAgeMinutes} min ago</p> : null}
                {targetsFeedDelayed ? (
                  <p className="text-amber-700 font-medium">Market feed delayed</p>
                ) : null}
              </div>
            ) : (
              <p className="text-sm text-gray-500">
                {proTargetsError ? proTargetsError : "Targets will appear when snapshot is available."}
              </p>
            )}
          </div>

          <div className="rounded-xl border p-4">
            <h2 className="font-semibold mb-2">Elite Risk & News</h2>
            {!canViewElite ? (
              <div className="space-y-2">
                <p className="text-sm text-gray-500">Upgrade to Elite to view daily alignment, news gate, and ATR/ADR.</p>
                <UpgradeButton targetTier="elite" />
              </div>
            ) : oracle ? (
              <div className="grid gap-1 text-sm">
                <p>
                  Daily Bias: <b>{oracle.daily_bias ?? "-"}</b>
                </p>
                <p>
                  Daily Alignment: <b>{oracle.daily_alignment ? "ALIGNED" : "CONFLICT"}</b>
                </p>
                <p>
                  News Gate: <b>{oracle.news_gate?.pass ? "PASS" : "BLOCKED"}</b>
                </p>
                <p>
                  ATR (H1): <b>{oracle.risk_stats?.atr_h1 ?? "-"}</b>
                </p>
                <p>
                  ADR (D1): <b>{oracle.risk_stats?.adr_d1 ?? "-"}</b>
                </p>
                <p>
                  Risk Gate: <b>{oracle.risk_stats?.risk_gate_pass ? "PASS" : "BLOCKED"}</b>
                </p>
              </div>
            ) : (
              <p className="text-sm text-gray-500">Elite diagnostics will appear when snapshot is available.</p>
            )}
          </div>
        </div>

        <div className="pt-6 border-t text-sm text-gray-500 flex items-center gap-6">
          <a href="/pricing" className="hover:underline">
            View Pricing -&gt;
          </a>
          <a href="/settings/telegram" className="hover:underline">
            Telegram Settings -&gt;
          </a>
          <a href="/settings/symbols" className="hover:underline">
            Symbol Settings -&gt;
          </a>
        </div>
      </div>
  );
}
