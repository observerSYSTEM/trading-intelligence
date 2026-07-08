const DEFAULT_DEV_API_BASE = "http://127.0.0.1:8000";
const rawApiBase = (
  process.env.NEXT_PUBLIC_API_BASE_URL ||
  process.env.NEXT_PUBLIC_API_BASE ||
  ""
).trim();
const API_BASE = rawApiBase
  ? rawApiBase
  : process.env.NODE_ENV === "production"
  ? ""
  : DEFAULT_DEV_API_BASE;

function getApiBase(): string {
  if (!API_BASE) {
    throw new Error(
      "NEXT_PUBLIC_API_BASE_URL is not configured. Set it to your backend URL."
    );
  }
  return API_BASE.replace(/\/+$/, "");
}

export function getConfiguredApiBase(): string {
  return getApiBase();
}

function authDebug(event: string, meta?: Record<string, unknown>) {
  void event;
  void meta;
}

function oracleDirectionDebug(event: string, meta?: Record<string, unknown>) {
  console.log(`[oracle-direction-api] ${event}`, meta || {});
}

function shouldDebugOracleDirection(path: string): boolean {
  return path.includes("/oracle/direction/");
}

function authHeaderPreview(token: string | null): string | null {
  if (!token) return null;
  const trimmed = token.trim();
  if (!trimmed) return null;
  return `Bearer ${trimmed.slice(0, 16)}...`;
}

export class ApiError extends Error {
  status: number | null;
  path: string;

  constructor(message: string, status: number | null, path: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.path = path;
  }
}

export function getApiErrorStatus(error: unknown): number | null {
  return error instanceof ApiError ? error.status : null;
}

export function isAuthError(error: unknown): boolean {
  const status = getApiErrorStatus(error);
  if (status === 401 || status === 403) return true;
  const message = error instanceof Error ? error.message : String(error || "");
  const normalized = message.trim().toLowerCase();
  return (
    normalized.includes("not authenticated") ||
    normalized.includes("invalid or expired token") ||
    normalized.includes("user not found or inactive") ||
    normalized.includes("invalid credentials") ||
    normalized.includes("invalid or expired refresh token") ||
    normalized.includes("request failed (401)") ||
    normalized.includes("request failed (403)")
  );
}

export function isNetworkError(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error || "");
  const normalized = message.trim().toLowerCase();
  return (
    normalized.includes("failed to fetch") ||
    normalized.includes("networkerror") ||
    normalized.includes("load failed") ||
    normalized.includes("fetch failed") ||
    normalized.includes("could not reach api")
  );
}

export function formatApiReachabilityMessage(context: string): string {
  return `Could not reach the backend for ${context}. Expected API base: ${getApiBase()}.`;
}

function truncateForDebug(value: string, maxLength = 800): string {
  const trimmed = value.trim();
  if (trimmed.length <= maxLength) return trimmed;
  return `${trimmed.slice(0, maxLength)}...`;
}

function formatOracleDirectionTransportMessage(
  path: string,
  url: string,
  error: unknown
): string {
  const cause = error instanceof Error ? error.message : String(error || "Unknown fetch error");
  return `Oracle Direction request failed before receiving a response. URL: ${url}. Path: ${path}. Fetch error: ${cause}. Expected API base: ${getApiBase()}.`;
}

function formatOracleDirectionHttpMessage(
  path: string,
  status: number,
  message: string,
  responseBody: string | null
): string {
  const parts = [
    `Oracle Direction request failed (${status}) for ${path}: ${message || `Request failed (${status})`}.`,
  ];
  const body = responseBody ? truncateForDebug(responseBody) : "";
  if (body && body !== message.trim()) {
    parts.push(`Response body: ${body}`);
  }
  return parts.join(" ");
}

export type Tier = "basic" | "pro" | "elite";

export type MeResponse = {
  id?: string;
  full_name?: string;
  email: string;
  role?: "admin" | "user";
  tier: Tier;
  status?: string;
  symbols_enabled?: string[];
  symbols_available?: string[];
};

export const ACCESS_TOKEN_KEY = "access_token";
export const REFRESH_TOKEN_KEY = "refresh_token";
export const SESSION_REFRESHED_AT_KEY = "session_refreshed_at";
export const SESSION_STATE_EVENT = "auth-session-changed";

function emitSessionStateChanged() {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new Event(SESSION_STATE_EVENT));
}

function readStoredToken(key: string): string | null {
  if (typeof window === "undefined") return null;
  const raw = localStorage.getItem(key);
  if (typeof raw !== "string") return null;
  const trimmed = raw.trim();
  return trimmed || null;
}

export function getToken(): string | null {
  return readStoredToken(ACCESS_TOKEN_KEY);
}

export function getRefreshToken(): string | null {
  return readStoredToken(REFRESH_TOKEN_KEY);
}

export function getSessionTokens(): {
  access_token: string | null;
  refresh_token: string | null;
} {
  if (typeof window === "undefined") {
    return {
      access_token: null,
      refresh_token: null,
    };
  }
  return {
    access_token: getToken(),
    refresh_token: getRefreshToken(),
  };
}

export function setToken(token: string) {
  const normalizedToken = token.trim();
  if (!normalizedToken) {
    throw new Error("Cannot persist an empty access token.");
  }
  localStorage.setItem(ACCESS_TOKEN_KEY, normalizedToken);
  if (getToken() !== normalizedToken) {
    localStorage.removeItem(ACCESS_TOKEN_KEY);
    throw new Error("Browser failed to persist the access token.");
  }
  emitSessionStateChanged();
}

export function setSessionTokens(accessToken: string, refreshToken?: string | null) {
  if (typeof window === "undefined") {
    throw new Error("Cannot persist session tokens outside the browser.");
  }
  const normalizedAccessToken = accessToken.trim();
  if (!normalizedAccessToken) {
    throw new Error("Login succeeded but no access token was returned.");
  }
  localStorage.setItem(ACCESS_TOKEN_KEY, normalizedAccessToken);
  const normalizedRefreshToken = refreshToken?.trim() || null;
  if (normalizedRefreshToken) {
    localStorage.setItem(REFRESH_TOKEN_KEY, normalizedRefreshToken);
  } else {
    localStorage.removeItem(REFRESH_TOKEN_KEY);
  }
  localStorage.setItem(SESSION_REFRESHED_AT_KEY, new Date().toISOString());

  const persisted = getSessionTokens();
  if (persisted.access_token !== normalizedAccessToken) {
    clearToken();
    throw new Error("Browser failed to persist the access token.");
  }
  if (normalizedRefreshToken && persisted.refresh_token !== normalizedRefreshToken) {
    clearToken();
    throw new Error("Browser failed to persist the refresh token.");
  }
  emitSessionStateChanged();
}

export function clearToken() {
  localStorage.removeItem(ACCESS_TOKEN_KEY);
  localStorage.removeItem(REFRESH_TOKEN_KEY);
  localStorage.removeItem(SESSION_REFRESHED_AT_KEY);
  emitSessionStateChanged();
}

export function subscribeToSessionState(onChange: () => void): () => void {
  if (typeof window === "undefined") {
    return () => {};
  }

  const handleStorage = (event: StorageEvent) => {
    if (
      event.key == null ||
      event.key === ACCESS_TOKEN_KEY ||
      event.key === REFRESH_TOKEN_KEY ||
      event.key === SESSION_REFRESHED_AT_KEY
    ) {
      onChange();
    }
  };

  const handleSessionChanged = () => {
    onChange();
  };

  window.addEventListener("storage", handleStorage);
  window.addEventListener(SESSION_STATE_EVENT, handleSessionChanged);
  return () => {
    window.removeEventListener("storage", handleStorage);
    window.removeEventListener(SESSION_STATE_EVENT, handleSessionChanged);
  };
}

export function getSessionState(): {
  has_access_token: boolean;
  has_refresh_token: boolean;
  last_refreshed_at: string | null;
} {
  if (typeof window === "undefined") {
    return {
      has_access_token: false,
      has_refresh_token: false,
      last_refreshed_at: null,
    };
  }
  const hasAccess = Boolean(getToken());
  const hasRefresh = Boolean(getRefreshToken());
  return {
    has_access_token: hasAccess,
    has_refresh_token: hasRefresh,
    last_refreshed_at: localStorage.getItem(SESSION_REFRESHED_AT_KEY),
  };
}

type ApiRequestOptions = RequestInit & {
  _retry?: boolean;
  _skipRefresh?: boolean;
};

export type ApiCallOptions = Omit<ApiRequestOptions, "_retry">;

function _headersFromInit(init?: HeadersInit): Record<string, string> {
  const headers: Record<string, string> = {};
  if (!init) return headers;
  if (init instanceof Headers) {
    init.forEach((value, key) => {
      headers[key] = value;
    });
    return headers;
  }
  if (Array.isArray(init)) {
    for (const [key, value] of init) {
      headers[key] = value;
    }
    return headers;
  }
  Object.assign(headers, init as Record<string, string>);
  return headers;
}

async function _extractErrorMessage(res: Response): Promise<string> {
  const contentType = res.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    try {
      const body = (await res.json()) as { detail?: unknown; message?: unknown };
      const detail = typeof body?.detail === "string" ? body.detail : "";
      const message = typeof body?.message === "string" ? body.message : "";
      const picked = detail || message;
      if (picked) return picked;
    } catch {
      // fall through to text body
    }
  }
  const text = await res.text();
  return text || `Request failed (${res.status})`;
}

async function _readResponse<T>(res: Response): Promise<T> {
  if (res.status === 204) return undefined as T;
  const contentType = res.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    return (await res.json()) as T;
  }
  return (await res.text()) as T;
}

export type AuthTokenResponse = {
  access_token: string;
  refresh_token?: string;
  token_type?: string;
  expires_in?: number;
};

type RefreshResult = {
  accessToken: string | null;
  reason: "ok" | "invalid" | "unavailable";
};

let refreshPromise: Promise<RefreshResult> | null = null;

async function refreshAccessToken(): Promise<RefreshResult> {
  const refreshToken = getRefreshToken();
  if (!refreshToken) {
    authDebug("refresh skipped: no refresh token");
    return { accessToken: null, reason: "invalid" };
  }
  if (refreshPromise) return refreshPromise;

  const pendingRefresh: Promise<RefreshResult> = (async (): Promise<RefreshResult> => {
    authDebug("refresh start", { apiBase: getApiBase() });
    try {
      const res = await fetch(`${getApiBase()}/auth/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: refreshToken }),
      });
      if (res.status === 401) {
        authDebug("refresh invalid", { status: res.status });
        clearToken();
        return { accessToken: null, reason: "invalid" };
      }
      if (!res.ok) {
        authDebug("refresh unavailable", { status: res.status });
        return { accessToken: null, reason: "unavailable" };
      }
      const data = (await res.json()) as AuthTokenResponse;
      if (!data?.access_token) {
        authDebug("refresh missing access token");
        return { accessToken: null, reason: "unavailable" };
      }
      setSessionTokens(data.access_token, data.refresh_token || refreshToken);
      authDebug("refresh success");
      return { accessToken: data.access_token, reason: "ok" };
    } catch (error) {
      authDebug("refresh transport failure", {
        message: error instanceof Error ? error.message : String(error || ""),
      });
      return { accessToken: null, reason: "unavailable" };
    }
  })();

  refreshPromise = pendingRefresh.finally(() => {
      refreshPromise = null;
    });

  return refreshPromise;
}

async function request<T>(
  path: string,
  options: ApiRequestOptions = {},
  auth = true
): Promise<T> {
  const { _retry = false, _skipRefresh = false, ...fetchOptions } = options;
  const headers = _headersFromInit(fetchOptions.headers);
  const method = (fetchOptions.method || "GET").toUpperCase();
  const accessToken = auth ? getToken() : null;
  const refreshToken = auth ? getRefreshToken() : null;

  if (auth) {
    if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
  }

  const hasContentType = Object.keys(headers).some(
    (key) => key.toLowerCase() === "content-type"
  );
  if (
    !hasContentType &&
    fetchOptions.body != null &&
    !(typeof FormData !== "undefined" && fetchOptions.body instanceof FormData)
  ) {
    headers["Content-Type"] = "application/json";
  }

  const url = `${getApiBase()}${path}`;
  const debugOracleDirection = shouldDebugOracleDirection(path);
  authDebug("request start", {
    path,
    url,
    method,
    auth,
    retry: _retry,
    skipRefresh: _skipRefresh,
    apiBase: getApiBase(),
    accessTokenKey: ACCESS_TOKEN_KEY,
    refreshTokenKey: REFRESH_TOKEN_KEY,
    hasAccessToken: Boolean(accessToken),
    hasRefreshToken: Boolean(refreshToken),
    authorizationHeaderPreview: authHeaderPreview(accessToken),
    isMeRequest: path === "/me",
  });
  if (debugOracleDirection) {
    oracleDirectionDebug("request", {
      url,
      path,
      method,
      auth,
      retry: _retry,
      skipRefresh: _skipRefresh,
      hasAccessToken: Boolean(accessToken),
      hasRefreshToken: Boolean(refreshToken),
      authorizationHeaderPreview: authHeaderPreview(accessToken),
    });
  }

  let res: Response;
  try {
    res = await fetch(url, { ...fetchOptions, headers });
  } catch (error) {
    authDebug("request transport failure", {
      path,
      url,
      apiBase: getApiBase(),
      message: error instanceof Error ? error.message : String(error || ""),
    });
    if (debugOracleDirection) {
      oracleDirectionDebug("transport_error", {
        url,
        path,
        method,
        error: error instanceof Error ? error.message : String(error || ""),
      });
    }
    throw new ApiError(
      debugOracleDirection
        ? formatOracleDirectionTransportMessage(path, url, error)
        : formatApiReachabilityMessage(path),
      null,
      path
    );
  }

  let debugResponseBody: string | null = null;
  if (debugOracleDirection) {
    try {
      debugResponseBody = await res.clone().text();
    } catch (error) {
      debugResponseBody = `Unable to read response body: ${error instanceof Error ? error.message : String(error || "")}`;
    }
    oracleDirectionDebug("response", {
      url,
      path,
      method,
      status: res.status,
      ok: res.ok,
      body: debugResponseBody,
    });
  }

  authDebug("request response", {
    path,
    url,
    method,
    status: res.status,
    ok: res.ok,
    isMeRequest: path === "/me",
  });

  if (res.status === 401 && auth && !_retry && !_skipRefresh) {
    const refreshed = await refreshAccessToken();
    if (refreshed.accessToken) {
      return request<T>(path, { ...options, _retry: true }, auth);
    }
    if (refreshed.reason === "unavailable") {
      throw new ApiError("Session refresh failed because the backend could not be reached.", 401, path);
    }
  }

  if (!res.ok) {
    const responseText = debugResponseBody ?? (await res.clone().text());
    authDebug("request non-200 response", {
      path,
      url,
      method,
      status: res.status,
      body: responseText || null,
      isMeRequest: path === "/me",
    });
    const message = await _extractErrorMessage(res);
    throw new ApiError(
      debugOracleDirection
        ? formatOracleDirectionHttpMessage(path, res.status, message, responseText)
        : message,
      res.status,
      path
    );
  }
  return _readResponse<T>(res);
}

export async function register(payload: {
  full_name: string;
  email: string;
  password: string;
  confirm_password: string;
}): Promise<AuthTokenResponse> {
  return request<AuthTokenResponse>(
    "/auth/register",
    {
      method: "POST",
      body: JSON.stringify(payload),
    },
    false
  );
}

export async function login(email: string, password: string): Promise<AuthTokenResponse> {
  authDebug("login request", { email: email.trim().toLowerCase(), apiBase: getApiBase() });
  return request<AuthTokenResponse>(
    "/auth/login",
    {
      method: "POST",
      body: JSON.stringify({ email, password }),
    },
    false
  );
}

export async function setPasswordFromActivation(payload: {
  token: string;
  password: string;
  confirm_password: string;
}): Promise<AuthTokenResponse> {
  return request<AuthTokenResponse>(
    "/auth/set-password",
    {
      method: "POST",
      body: JSON.stringify(payload),
    },
    false
  );
}

export async function me(): Promise<MeResponse> {
  return request("/me");
}

export async function logoutSession(): Promise<void> {
  const refreshToken = getRefreshToken();
  try {
    await request(
      "/auth/logout",
      {
        method: "POST",
        body: JSON.stringify({ refresh_token: refreshToken || undefined }),
        _skipRefresh: true,
      },
      false
    );
  } catch {
    // Local cleanup still happens even if backend revoke fails.
  } finally {
    clearToken();
  }
}

export async function createCheckout(
  plan: Tier,
  options?: {
    auth?: boolean;
    email?: string;
    full_name?: string;
  }
): Promise<{ url: string }> {
  const body: Record<string, string> = { plan };
  if (options?.email) body.email = options.email;
  if (options?.full_name) body.full_name = options.full_name;
  return request(
    "/billing/checkout-session",
    {
      method: "POST",
      body: JSON.stringify(body),
    },
    options?.auth ?? true
  );
}

export async function createPortalSession(): Promise<{ url: string }> {
  return request("/billing/portal", {
    method: "POST",
  });
}

export type CheckoutActivationResponse = {
  ready: boolean;
  requires_password_setup: boolean;
  message: string;
  email?: string | null;
  activation_token?: string | null;
  expires_in_seconds?: number | null;
};

export async function getCheckoutActivation(
  session_id: string
): Promise<CheckoutActivationResponse> {
  return request<CheckoutActivationResponse>(
    "/billing/checkout-activation",
    {
      method: "POST",
      body: JSON.stringify({ session_id }),
    },
    false
  );
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, {
      method: "POST",
      body: body ? JSON.stringify(body) : undefined,
    }),
  me,
  portal: createPortalSession,
  checkout: createCheckout,
};

export type UsageResponse = {
  status?: string;
  plan: "basic" | "pro" | "elite";
  used: number;
  limit: number | null;
  remaining: number | null;
  resets_at: string | null;
};

export async function getUsage(options: ApiCallOptions = {}): Promise<UsageResponse> {
  return request<UsageResponse>("/usage", options);
}

export type TelegramSettingsResponse = {
  telegram_enabled: boolean;
  telegram_chat_id: string;
  has_chat_id: boolean;
  pin_daily_bias?: boolean;
  symbols?: string[];
  allowed_symbols?: string[];
  locked_symbols?: string[];
};

export async function getTelegramSettings(): Promise<TelegramSettingsResponse> {
  return request<TelegramSettingsResponse>("/settings/telegram");
}

export async function saveTelegramSettings(payload: {
  telegram_enabled: boolean;
  telegram_chat_id?: string;
  pin_daily_bias?: boolean;
  symbols?: string[];
}): Promise<{
  ok: boolean;
  telegram_enabled: boolean;
  telegram_chat_id: string;
  has_chat_id: boolean;
  pin_daily_bias: boolean;
  symbols: string[];
  allowed_symbols: string[];
  locked_symbols: string[];
}> {
  return request("/settings/telegram", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function sendTelegramTest(): Promise<{ ok: boolean }> {
  return request<{ ok: boolean }>("/settings/telegram/test", {
    method: "POST",
  });
}

export type SymbolsAvailableResponse = {
  tier: Tier;
  available: string[];
  locked: string[];
};

export type SymbolPreferenceItem = {
  symbol: string;
  enabled: boolean;
  locked: boolean;
};

export type SymbolPreferencesResponse = {
  selected: string[];
  all: SymbolPreferenceItem[];
};

export async function getSymbolsAvailable(): Promise<SymbolsAvailableResponse> {
  return request<SymbolsAvailableResponse>("/symbols/available");
}

export async function getSymbolPreferences(): Promise<SymbolPreferencesResponse> {
  return request<SymbolPreferencesResponse>("/symbols/preferences");
}

export async function saveSymbolPreferences(payload: {
  selected: string[];
}): Promise<{
  selected: string[];
  available: string[];
  locked: string[];
}> {
  return request("/symbols/preferences", {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export type OracleRunParams = {
  symbol?: string;
  timeframe?: string;
};

export async function runBasicOracle(params: OracleRunParams = {}): Promise<{
  ok: boolean;
  symbol: string;
  timeframe: string;
  candle_time_utc: string;
  direction: "BUY_ONLY" | "SELL_ONLY" | "NO_TRADE";
  source: string;
  sent: number;
  consumed: number;
  failed: number;
  skipped: number;
}> {
  const qp = new URLSearchParams();
  if (params.symbol) qp.set("symbol", params.symbol);
  if (params.timeframe) qp.set("timeframe", params.timeframe);
  const query = qp.toString();

  return request(`/oracle/run-basic${query ? `?${query}` : ""}`, {
    method: "POST",
  });
}

export type OracleLatestResponse = {
  symbol: string;
  title: string;
  plan_view?: "basic" | "pro" | "elite";
  direction: "BUY_ONLY" | "SELL_ONLY" | "NO_TRADE";
  fast_bias?: "BUY_ONLY" | "SELL_ONLY" | "NO_TRADE";
  confirm_tf?: string;
  confirm_ok?: boolean;
  bias_m1?: "BUY_ONLY" | "SELL_ONLY" | "NO_TRADE";
  confirm_h1?: boolean;
  final_allowed: "BUY_ONLY" | "SELL_ONLY" | "NO_TRADE";
  daily_permission?: "BUY_ONLY" | "SELL_ONLY" | "NO_TRADE";
  daily_permission_as_of_utc?: string | null;
  permission_stage?: "PRELIM" | "OFFICIAL" | string;
  permission_source?: "ASIA" | "LONDON_0801" | string;
  permission_lock_time_london?: string | null;
  permission_for_date_uk?: string | null;
  conflict_with_prelim?: boolean;
  conflict_note?: string | null;
  opportunity_direction?: "BUY_ONLY" | "SELL_ONLY" | "NO_TRADE";
  reason?: string;
  message: string;
  targets_json?: Record<string, number>;
  liquidity_magnet?: number | null;
  zone_to_zone_target?: number | null;
  daily_bias?: "bullish" | "bearish" | "neutral";
  daily_alignment?: boolean;
  news_gate?: {
    pass: boolean;
    blocked_window?: {
      timestamp_utc: string;
      label: string;
      impact: string;
    } | null;
  };
  risk_stats?: {
    atr_h1: number | null;
    adr_d1: number | null;
    risk_gate_pass: boolean;
  };
  volume_state?: string;
  risk_banner?: {
    is_blueprint_day: boolean;
    volume_spike: boolean;
    suggested_risk_multiplier: number;
    reasons: string[];
    tier_copy?: {
      basic?: string;
      pro?: string;
      elite?: string;
    };
    volume_ratio?: number;
    last_m15_volume?: number | null;
    median_m15_volume_20?: number | null;
  };
  weekly_range?: {
    symbol: string;
    week_key: string;
    week_start_uk: string;
    high: number;
    low: number;
    mid: number;
    range_ready: boolean;
    status: "Building" | "Locked";
    as_of_utc: string;
    meta_json?: Record<string, unknown>;
  };
  ny_context_active?: boolean;
  ny_note?: string | null;
  ny_confidence_delta?: number | null;
  computed_at: string;
  as_of: string;
  timeframes: {
    signal: string;
    confirm: string;
    daily?: string;
  };
  timeframe: string;
  as_of_utc: string;
  allowed_direction: "BUY_ONLY" | "SELL_ONLY" | "NO_TRADE";
  regime: "bullish" | "bearish" | "range";
  confidence: number;
  headline: string;
  candle?: {
    open: number;
    high: number;
    low: number;
    close: number;
    volume: number | null;
  } | null;
  targets_as_of_utc?: string | null;
  targets_magnet_state?: Record<string, unknown>;
  timeframe_main?: string;
  timeframe_fast?: string;
  last_ingest_at?: string | null;
  last_ingest_at_utc?: string | null;
  last_compute_at?: string | null;
  last_compute_at_utc?: string | null;
  age_seconds?: number | null;
  compute_age_seconds?: number | null;
  ingest_age_seconds?: number | null;
  stale_after_minutes?: number;
  stale_compute_after_minutes?: number;
  stale_ingest_after_minutes?: number;
  stale_reasons?: string[];
  is_stale?: boolean;
  timezone?: string;
  last_08_01_candle_time_utc?: string | null;
  daily_permission_target_utc?: string | null;
  daily_permission_target_london?: string | null;
  broker_offset_seconds?: number;
  broker_offset_hours?: number;
  broker_server_time_utc?: string | null;
  expected_0801_broker_time?: string | null;
  actual_candle_found_time?: string | null;
  latest_candle_close_utc?: string | null;
  timeframe_seconds?: number;
  candle_age_seconds?: number | null;
  stale_threshold_seconds?: number;
  daily_permission_missing?: boolean;
  daily_permission_degraded?: boolean;
  daily_permission_degraded_reason?: string | null;
  daily_permission_backfill_attempted?: boolean;
  daily_permission_backfill_result?: Record<string, unknown> | null;
  api_mode?: boolean;
  data_provider?: string | null;
  candle_provider?: string | null;
  fallback_provider?: string | null;
  news_provider?: string | null;
  latest_candle_source?: string | null;
  last_candle_time?: string | null;
  anchor_candle_source?: string | null;
  anchor_candle_status?: string | null;
  api_candle_error?: string | null;
};

export async function getOracleLatest(
  symbol = "XAUUSD",
  options: ApiCallOptions = {}
): Promise<OracleLatestResponse> {
  const qp = new URLSearchParams({ symbol });
  return request<OracleLatestResponse>(`/oracle/latest?${qp.toString()}`, options);
}

export async function getOracleSnapshotLatest(
  symbol = "XAUUSD",
  staleAfterMinutes = 20
): Promise<OracleLatestResponse> {
  const qp = new URLSearchParams({
    symbol,
    stale_after_minutes: String(staleAfterMinutes),
  });
  return request<OracleLatestResponse>(`/oracle/snapshot/latest?${qp.toString()}`);
}

export type OracleStatusResponse = {
  symbol: string;
  last_ingest_at: string | null;
  last_ingest_at_utc?: string | null;
  last_snapshot_as_of: string | null;
  last_compute_at: string | null;
  last_compute_at_utc?: string | null;
  stale_after_minutes: number;
  stale_compute_after_minutes?: number;
  stale_ingest_after_minutes?: number;
  compute_age_seconds?: number | null;
  ingest_age_seconds?: number | null;
  age_seconds: number | null;
  is_stale: boolean;
  stale_reasons?: string[];
  timezone?: string;
  last_08_01_candle_time_utc?: string | null;
  daily_permission_target_utc?: string | null;
  daily_permission_target_london?: string | null;
  broker_offset_seconds?: number;
  broker_offset_hours?: number;
  broker_server_time_utc?: string | null;
  expected_0801_broker_time?: string | null;
  actual_candle_found_time?: string | null;
  latest_candle_close_utc?: string | null;
  timeframe_seconds?: number;
  candle_age_seconds?: number | null;
  stale_threshold_seconds?: number;
  daily_permission_missing?: boolean;
  daily_permission_degraded?: boolean;
  daily_permission_degraded_reason?: string | null;
  daily_permission_backfill_attempted?: boolean;
  daily_permission_backfill_result?: Record<string, unknown> | null;
  api_mode?: boolean;
  data_provider?: string | null;
  candle_provider?: string | null;
  fallback_provider?: string | null;
  news_provider?: string | null;
  latest_candle_source?: string | null;
  last_candle_time?: string | null;
  anchor_candle_source?: string | null;
  anchor_candle_status?: string | null;
  api_candle_error?: string | null;
  permission_stage?: "PRELIM" | "OFFICIAL" | string;
  permission_source?: "ASIA" | "LONDON_0801" | string;
  permission_lock_time_london?: string | null;
  permission_for_date_uk?: string | null;
};

export async function getOracleStatus(
  symbol = "XAUUSD",
  staleAfterMinutes = 20,
  options: ApiCallOptions = {}
): Promise<OracleStatusResponse> {
  const qp = new URLSearchParams({
    symbol,
    stale_after_minutes: String(staleAfterMinutes),
  });
  return request<OracleStatusResponse>(`/oracle/status?${qp.toString()}`, options);
}

export type OracleDirectionLabel = "STRONG BUY" | "BUY" | "NEUTRAL" | "SELL" | "STRONG SELL";

export type OracleDirectionResponse = {
  symbol: string;
  direction: OracleDirectionLabel;
  buy_percent: number;
  sell_percent: number;
  confidence_percent: number;
  score: number;
  next_buy_liquidity: number | null;
  next_sell_liquidity: number | null;
  as_of_utc: string;
  candle_time_utc: string | null;
  candle_provider?: string | null;
  fallback_provider?: string | null;
  latest_candle_source?: string | null;
  last_candle_time?: string | null;
  anchor_candle_source?: string | null;
  anchor_candle_status?: string | null;
  daily_permission?: string | null;
  permission_stage?: string | null;
  permission_source?: string | null;
  permission_degraded?: boolean;
  permission_reason?: string | null;
  targets_as_of_utc?: string | null;
  liquidity_magnet?: number | null;
  zone_to_zone_target?: number | null;
  news?: {
    provider?: string | null;
    available?: boolean;
    news_count?: number;
    economic_event_count?: number;
    high_impact_event_count?: number;
    risk_dampener?: number;
    error?: string | null;
  };
  components?: Array<{
    name: string;
    score: number;
    weight: number;
  }>;
};

type OracleDirectionWireResponse = Partial<OracleDirectionResponse> & Record<string, unknown>;

function _numberOrNull(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function _stringOrNull(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

function _pickNumber(payload: OracleDirectionWireResponse, keys: string[]): number | null {
  for (const key of keys) {
    const value = _numberOrNull(payload[key]);
    if (value !== null) return value;
  }
  return null;
}

function _pickString(payload: OracleDirectionWireResponse, keys: string[]): string | null {
  for (const key of keys) {
    const value = _stringOrNull(payload[key]);
    if (value !== null) return value;
  }
  return null;
}

function normalizeOracleDirection(payload: OracleDirectionWireResponse): OracleDirectionResponse {
  const direction = (_pickString(payload, ["direction", "label", "oracle_direction"]) || "NEUTRAL").toUpperCase();
  const buyPercent = _pickNumber(payload, ["buy_percent", "buyPercent", "BUY %", "buy_percentage"]) ?? 50;
  const sellPercent = _pickNumber(payload, ["sell_percent", "sellPercent", "SELL %", "sell_percentage"]) ?? 50;
  const confidencePercent =
    _pickNumber(payload, ["confidence_percent", "confidencePercent", "CONFIDENCE %", "confidence_percentage"]) ??
    Math.max(buyPercent, sellPercent);

  return {
    ...payload,
    symbol: _pickString(payload, ["symbol"]) || "XAUUSD",
    direction: (
      ["STRONG BUY", "BUY", "NEUTRAL", "SELL", "STRONG SELL"].includes(direction) ? direction : "NEUTRAL"
    ) as OracleDirectionLabel,
    buy_percent: buyPercent,
    sell_percent: sellPercent,
    confidence_percent: confidencePercent,
    score: _pickNumber(payload, ["score"]) ?? 0,
    next_buy_liquidity: _pickNumber(payload, ["next_buy_liquidity", "nextBuyLiquidity", "NEXT BUY LIQUIDITY"]),
    next_sell_liquidity: _pickNumber(payload, ["next_sell_liquidity", "nextSellLiquidity", "NEXT SELL LIQUIDITY"]),
    as_of_utc: _pickString(payload, ["as_of_utc", "asOfUtc"]) || new Date().toISOString(),
    candle_time_utc: _pickString(payload, ["candle_time_utc", "candleTimeUtc"]),
  };
}

export async function getOracleDirection(
  symbol = "XAUUSD",
  options: ApiCallOptions = {}
): Promise<OracleDirectionResponse> {
  const path = `/oracle/direction/${encodeURIComponent(symbol.trim().toUpperCase())}`;
  let payload: OracleDirectionWireResponse;
  try {
    payload = await request<OracleDirectionWireResponse>(path, options);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error || "");
    if (
      error instanceof ApiError &&
      error.status === 401 &&
      options._skipRefresh
    ) {
      oracleDirectionDebug("retry_with_refresh", {
        path,
        status: error.status,
        message,
      });
      payload = await request<OracleDirectionWireResponse>(path, {
        ...options,
        _skipRefresh: false,
      });
    } else {
      throw error;
    }
  }
  return normalizeOracleDirection(payload);
}

export type LceCheckpointResponse = {
  symbol: string;
  timeframe: string;
  status: "WAITING_FOR_SWEEP" | "NO_CHECKPOINT" | "ERROR" | string;
  checkpoint: number | null;
  checkpoint_type: "SELLSIDE_LIQUIDITY" | "BUYSIDE_LIQUIDITY" | null;
  meaning: string;
  after_sweep: {
    bullish_continuation: number[];
    bearish_rejection: number[];
  };
  confidence: number;
  reason: string[];
};

export async function getLceCheckpoint(
  symbol = "XAUUSD",
  timeframe = "H1",
  options: ApiCallOptions = {}
): Promise<LceCheckpointResponse> {
  const symbolValue = encodeURIComponent(symbol.trim().toUpperCase());
  const qp = new URLSearchParams({ timeframe: timeframe.trim().toUpperCase() || "H1" });
  return request<LceCheckpointResponse>(`/lce/checkpoint/${symbolValue}?${qp.toString()}`, options);
}

export type OracleSessionContextResponse = {
  symbol: string;
  session_state: "asia" | "london" | "new_york" | "off_session";
  asian_high: number | null;
  asian_low: number | null;
  asian_mid: number | null;
  asian_range_size_pips: number | null;
  asian_range_valid: boolean;
  london_now: string;
  source_timeframe_used: string;
  anchor_available: boolean;
  anchor_time_london: string | null;
  anchor_time_utc: string | null;
  anchor_classification: string | null;
  anchor_bias: "bullish" | "bearish" | "neutral" | null;
  anchor_quality: "strong" | "moderate" | "weak" | null;
  anchor_notes: string[];
  sweep_available: boolean;
  sweep_side: "buy_side" | "sell_side" | "both" | null;
  sweep_type: "rejection_sweep" | "breakout" | "double_sweep" | null;
  swept_level: number | null;
  sweep_buffer_pips: number | null;
  sweep_time_london: string | null;
  sweep_time_utc: string | null;
  returned_inside_range: boolean;
  sweep_quality: "strong" | "moderate" | "weak" | null;
  sweep_notes: string[];
  magnet_bias: "buyside" | "sellside" | "neutral" | null;
  active_magnet_level: number | null;
  active_magnet_type: string | null;
  next_buyside_liquidity: number | null;
  next_sellside_liquidity: number | null;
  distance_to_magnet_pips: number | null;
  magnet_notes: string[];
  zone_state: "premium" | "discount" | "equilibrium" | null;
  dealing_range_high: number | null;
  dealing_range_low: number | null;
  equilibrium: number | null;
  distance_from_equilibrium_pips: number | null;
  zone_notes: string[];
  structure_available: boolean;
  structure_state: "bullish_mss" | "bearish_mss" | "bullish_bos" | "bearish_bos" | "none" | null;
  structure_bias: "bullish" | "bearish" | "neutral" | null;
  mss_detected: boolean;
  bos_detected: boolean;
  break_level: number | null;
  break_time_london: string | null;
  break_time_utc: string | null;
  displacement_size_pips: number | null;
  displacement_quality: "strong" | "moderate" | "weak" | null;
  structure_notes: string[];
  fvg_available: boolean;
  fvg_direction: "bullish" | "bearish" | null;
  fvg_state: "fresh" | "partially_mitigated" | "fully_mitigated" | "expired" | "none" | null;
  fvg_high: number | null;
  fvg_low: number | null;
  fvg_mid: number | null;
  fvg_size_pips: number | null;
  fvg_created_time_london: string | null;
  fvg_created_time_utc: string | null;
  fvg_age_bars: number | null;
  fvg_mitigated: boolean;
  fvg_quality: "strong" | "moderate" | "weak" | null;
  fvg_notes: string[];
  setup_available: boolean;
  setup_direction: "bullish" | "bearish" | null;
  setup_state: "ready" | "developing" | "conflicted" | "invalid" | "none";
  setup_confidence: number;
  setup_score: number;
  setup_reason: string;
  blocking_factors: string[];
  confirming_factors: string[];
  entry_context_summary: string;
  anchor: {
    anchor_time_london: string | null;
    anchor_time_utc: string | null;
    open: number | null;
    high: number | null;
    low: number | null;
    close: number | null;
    total_range: number | null;
    body_size: number | null;
    upper_wick: number | null;
    lower_wick: number | null;
    body_ratio: number | null;
    wick_ratio: number | null;
    direction: "bullish" | "bearish" | "neutral" | null;
  };
};

export type SignalTargetsLatestResponse = {
  symbol: string;
  tier: "basic" | "pro" | "elite";
  timeframe_base: string;
  as_of_utc: string;
  price_bid: number | null;
  price_ask: number | null;
  magnet_price: number;
  zone_to_zone_target: number;
  sellside_liquidity: number;
  buyside_liquidity: number;
  magnet_state: Record<string, unknown>;
  latest_ingest_at_utc?: string | null;
  latest_candle_time_utc?: string | null;
  latest_candle_timeframe?: string | null;
  latest_market_feed_at_utc?: string | null;
  latest_market_feed_source?: string | null;
  market_feed_age_seconds?: number | null;
  market_feed_delayed?: boolean;
  market_feed_delay_reason?: string | null;
  market_feed_delay_threshold_seconds?: number;
};

export async function getSignalTargetsLatest(
  symbol = "XAUUSD",
  tier: "basic" | "pro" | "elite" = "pro",
  options: ApiCallOptions = {}
): Promise<SignalTargetsLatestResponse> {
  const qp = new URLSearchParams({ symbol, tier });
  return request<SignalTargetsLatestResponse>(`/signals/targets/latest?${qp.toString()}`, options);
}

export type SignalFeedItem = {
  id: string;
  symbol: string;
  timeframe: string;
  type: string;
  signal_type: string;
  direction: "BUY" | "SELL" | "BUY_ONLY" | "SELL_ONLY" | "NO_TRADE" | "NEUTRAL" | null;
  magnet: number | null;
  magnet_level: number | null;
  price: number | null;
  bias: "BUY_ONLY" | "SELL_ONLY" | "NO_TRADE" | "BULLISH" | "BEARISH" | "NEUTRAL" | null;
  reason: string | null;
  confidence: number | null;
  daily_permission: "BUY_ONLY" | "SELL_ONLY" | "NO_TRADE" | null;
  h1_confirmation: string | null;
  zone_target: number | null;
  sellside_liquidity: number | null;
  buyside_liquidity: number | null;
  source: string;
  detected_at: string;
  meta: Record<string, unknown>;
  dedup_key: string;
  created_at: string;
};

export type SignalFeedResponse = {
  items: SignalFeedItem[];
  total: number;
  limit: number;
  offset: number;
};

export async function getSignals(params?: {
  symbol?: string;
  timeframe?: string;
  signal_type?: string;
  limit?: number;
  offset?: number;
}): Promise<SignalFeedResponse> {
  const qp = new URLSearchParams();
  if (params?.symbol) qp.set("symbol", params.symbol);
  if (params?.timeframe) qp.set("timeframe", params.timeframe);
  if (params?.signal_type) qp.set("signal_type", params.signal_type);
  if (typeof params?.limit === "number") qp.set("limit", String(params.limit));
  if (typeof params?.offset === "number") qp.set("offset", String(params.offset));
  const query = qp.toString();
  return request<SignalFeedResponse>(`/signals${query ? `?${query}` : ""}`);
}

export type SignalIntelLatestResponse = {
  symbol: string;
  tier: "basic" | "pro" | "elite";
  as_of_utc: string | null;
  allowed_direction: "BUY_ONLY" | "SELL_ONLY" | "NO_TRADE";
  daily_permission?: "BUY_ONLY" | "SELL_ONLY" | "NO_TRADE" | null;
  opportunity_direction?: "BUY_ONLY" | "SELL_ONLY" | "NO_TRADE" | null;
  h1_confirm_ok?: boolean | null;
  confidence: number;
  message: string;
  targets?: {
    as_of_utc: string;
    tier: "basic" | "pro" | "elite";
    timeframe_base: string;
    price_bid: number | null;
    price_ask: number | null;
    magnet_price: number;
    zone_to_zone_target: number;
    sellside_liquidity: number;
    buyside_liquidity: number;
    magnet_state: Record<string, unknown>;
  } | null;
};

export async function getSignalIntelLatest(
  symbol = "XAUUSD"
): Promise<SignalIntelLatestResponse> {
  const qp = new URLSearchParams({ symbol });
  return request<SignalIntelLatestResponse>(`/signals/intel/latest?${qp.toString()}`);
}

export type AdminOracleRunResponse = {
  ok: boolean;
  runs: Array<{
    run_id?: string;
    symbol: string;
    bias?: "BUY_ONLY" | "SELL_ONLY" | "NO_TRADE";
    confidence?: number;
    status?: string;
    ingest?: Array<{
      ok: boolean;
      symbol: string;
      timeframe: string;
      time_open_utc?: string;
      created?: boolean;
      error?: string;
    }>;
    targets_refresh?: Array<{
      ok: boolean;
      symbol: string;
      tier: string;
      as_of_utc?: string;
      magnet_price?: number;
      error?: string;
    }>;
    targets_market_feed?: {
      latest_market_feed_at_utc?: string | null;
      latest_market_feed_source?: string | null;
      latest_ingest_at_utc?: string | null;
      latest_candle_time_utc?: string | null;
      latest_candle_timeframe?: string | null;
      market_feed_age_seconds?: number | null;
      market_feed_delayed?: boolean;
      market_feed_delay_reason?: string | null;
      market_feed_delay_threshold_seconds?: number;
    };
    confirm_scheduled_for?: string;
    error?: string;
  }>;
};

export async function runAdminOracle(payload: {
  symbol?: string;
  symbols?: string[];
} = {}): Promise<AdminOracleRunResponse> {
  return request("/admin/oracle/run", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export type ReadinessCheck = {
  ok: boolean;
  [key: string]: unknown;
};

export type ReadinessResponse = {
  ok: boolean;
  env: string;
  checks: Record<string, ReadinessCheck>;
};

export async function getAdminReadiness(options: ApiCallOptions = {}): Promise<ReadinessResponse> {
  return request<ReadinessResponse>("/admin/ops/readiness", options);
}

export type RunnerHealthItem = {
  runner_id: string;
  mt5_connected: boolean;
  last_tick_utc: string | null;
  last_ingest_utc: string | null;
  last_seen_utc: string;
  last_ok_at_utc: string | null;
  last_error: string | null;
  lag_seconds: number;
  symbols_ok: string[];
};

export type RunnerHealthResponse = {
  ok: boolean;
  mode?: string;
  api_mode?: boolean;
  provider_connected?: boolean;
  data_provider?: string | null;
  candle_provider?: string | null;
  fallback_provider?: string | null;
  news_provider?: string | null;
  latest_candle_source?: string | null;
  last_candle_time?: string | null;
  anchor_candle_source?: string | null;
  anchor_candle_status?: string | null;
  runner_ok?: boolean | null;
  mt5_connected: boolean;
  mt5_initialized?: boolean | null;
  mt5_logged_in?: boolean | null;
  last_tick_utc: string | null;
  last_ingest_utc: string | null;
  lag_seconds: number | null;
  symbols_ok: string[];
  symbols?: Record<
    string,
    {
      selected?: boolean;
      broker_symbol?: string;
      last_tick_utc?: string | null;
      last_success_utc?: string | null;
      last_error?: string | null;
    }
  >;
  account?: Record<string, unknown> | null;
  terminal?: Record<string, unknown> | null;
  server_time_utc?: string | null;
  last_error?: string | null;
  runner_control_configured?: boolean;
  runner_control_ok?: boolean;
  runner_control_error?: string | null;
  runner_control_warning?: string | null;
  runner_control_url?: string | null;
  items: RunnerHealthItem[];
  reason?: string;
};

export type RunnerStatusResponse = {
  ok: boolean;
  runner_id: string | null;
  runner_online: boolean;
  runner_status: "online" | "offline";
  mt5_connected: boolean;
  last_heartbeat_utc: string | null;
  last_signal_utc: string | null;
  last_telegram_sent_utc: string | null;
  last_tick_utc: string | null;
  last_ingest_utc: string | null;
  last_error: string | null;
  heartbeat_age_seconds: number | null;
  stale_after_seconds: number;
  symbols_ok: string[];
  session_independent: boolean;
};

export async function getRunnerHealth(options: ApiCallOptions = {}): Promise<RunnerHealthResponse> {
  return request<RunnerHealthResponse>("/health/runner", options);
}

export async function getRunnerStatus(options: ApiCallOptions = {}): Promise<RunnerStatusResponse> {
  return request<RunnerStatusResponse>("/runner/status", options);
}

export type AnchorDebugResponse = {
  ok: boolean;
  symbol: string;
  anchor: {
    direction: "BULL" | "BEAR" | "UNKNOWN";
    open: number | null;
    high: number | null;
    low: number | null;
    close: number | null;
    body_size: number | null;
    wick_size: number | null;
    candle_time_utc: string | null;
    candle_time_london: string | null;
  };
  official_permission: {
    daily_permission: string;
    permission_source: string;
    permission_lock_time: string | null;
    last_refreshed_at: string;
    permission_time_utc: string;
    permission_time_london: string | null;
    final_allowed: string;
  };
  time_mapping: {
    requested_symbol?: string | null;
    resolved_mt5_symbol?: string | null;
    resolved_candle_symbol?: string | null;
    london_time_used: string | null;
    utc_time_used: string | null;
    broker_server_time_utc: string | null;
    expected_0801_broker_time: string | null;
    actual_candle_found_time: string | null;
    lookup_start_utc?: string | null;
    lookup_end_utc?: string | null;
    lookup_start_broker_utc?: string | null;
    lookup_end_broker_utc?: string | null;
    m1_candles_returned_utc_window?: number | null;
    m1_candles_returned_broker_window?: number | null;
    m1_candles_returned_total?: number | null;
    nearest_available_candle_time?: string | null;
    nearest_available_candle_time_london?: string | null;
    nearest_available_candle_source?: string | null;
    nearest_available_candle_delta_seconds?: number | null;
    selection_source: string | null;
    selection_tolerance_seconds?: number | null;
    selected_time_delta_seconds?: number | null;
    ingest_origin: "direct_ingest" | "backfill" | "unknown";
    backfill_attempted: boolean;
    backfill_result: Record<string, unknown> | null;
    api_mode?: boolean;
    data_provider?: string | null;
    candle_provider?: string | null;
    fallback_provider?: string | null;
    news_provider?: string | null;
    latest_candle_source?: string | null;
    last_candle_time?: string | null;
    anchor_candle_source?: string | null;
    anchor_candle_status?: string | null;
  };
  explanations: string[];
};

export async function getOpsAnchorDebug(
  symbol = "XAUUSD",
  options: ApiCallOptions = {}
): Promise<AnchorDebugResponse> {
  const qp = new URLSearchParams({ symbol });
  return request<AnchorDebugResponse>(`/ops/anchor-debug?${qp.toString()}`, options);
}

export async function testTelegram(chat_id?: string, text?: string): Promise<{
  ok: boolean;
  chat_id: string;
  message_id: number;
}> {
  const payload: { chat_id?: string; text?: string } = {};
  if (chat_id) payload.chat_id = chat_id;
  if (text) payload.text = text;
  const hasPayload = Boolean(payload.chat_id || payload.text);
  return request("/ops/telegram/test", {
    method: "POST",
    body: hasPayload ? JSON.stringify(payload) : undefined,
  });
}

export async function reconnectRunner(reason = "dashboard_manual"): Promise<{
  ok: boolean;
  reason: string;
  runner?: Record<string, unknown>;
}> {
  return request("/admin/ops/runner/reconnect", {
    method: "POST",
    body: JSON.stringify({ reason }),
  });
}
