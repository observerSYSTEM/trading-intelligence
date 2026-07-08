"use client";

import { useEffect, useMemo, useState } from "react";

import AuthGate from "@/components/AuthGate";
import SignalMarketCard from "@/components/SignalMarketCard";
import {
  getOpsAnchorDebug,
  getRunnerStatus,
  getSignalIntelLatest,
  getSignalTargetsLatest,
  getSignals,
  me,
  type AnchorDebugResponse,
  type MeResponse,
  type RunnerStatusResponse,
  type SignalFeedItem,
  type SignalIntelLatestResponse,
  type SignalTargetsLatestResponse,
} from "@/lib/api";
import { selectMeaningfulProSignals } from "@/lib/signal-feed";

const POLL_MS = 60_000;
const LONDON_TZ = "Europe/London";

function formatLondon(value?: string | null): string {
  if (!value) return "-";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return "-";
  return d.toLocaleString("en-GB", {
    timeZone: LONDON_TZ,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

function formatPrice(value: number | null | undefined): string {
  if (typeof value !== "number" || Number.isNaN(value)) return "-";
  if (Math.abs(value) >= 100) return value.toFixed(2);
  if (Math.abs(value) >= 1) return value.toFixed(4);
  return value.toFixed(5);
}

export default function SignalsPage() {
  const [user, setUser] = useState<MeResponse | null>(null);

  const [signals, setSignals] = useState<SignalFeedItem[]>([]);
  const [signalsLoading, setSignalsLoading] = useState(true);
  const [signalsError, setSignalsError] = useState<string | null>(null);

  const [selectedSymbol, setSelectedSymbol] = useState("XAUUSD");
  const [targets, setTargets] = useState<SignalTargetsLatestResponse | null>(null);
  const [targetsError, setTargetsError] = useState<string | null>(null);

  const [intel, setIntel] = useState<SignalIntelLatestResponse | null>(null);
  const [intelError, setIntelError] = useState<string | null>(null);

  const [anchorDebug, setAnchorDebug] = useState<AnchorDebugResponse | null>(null);
  const [anchorDebugError, setAnchorDebugError] = useState<string | null>(null);

  const [runnerStatus, setRunnerStatus] = useState<RunnerStatusResponse | null>(null);
  const [runnerStatusError, setRunnerStatusError] = useState<string | null>(null);

  const isAdmin = user?.role === "admin";

  useEffect(() => {
    let alive = true;

    async function loadUser() {
      try {
        const profile = await me();
        if (!alive) return;
        setUser(profile);
      } catch {
        if (!alive) return;
        setUser(null);
      }
    }

    void loadUser();
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    let alive = true;

    async function loadSignals() {
      try {
        const feed = await getSignals({ limit: 200 });
        if (!alive) return;
        const cleaned = selectMeaningfulProSignals(feed.items || []);
        setSignals(cleaned);
        setSignalsError(null);

        const symbols = Array.from(new Set(cleaned.map((s) => (s.symbol || "").toUpperCase()).filter(Boolean)));
        if (symbols.length > 0) {
          setSelectedSymbol((current) => (symbols.includes(current) ? current : symbols[0]));
        }
      } catch (err: unknown) {
        if (!alive) return;
        setSignals([]);
        setSignalsError(err instanceof Error ? err.message : "Failed to load signals.");
      } finally {
        if (alive) setSignalsLoading(false);
      }
    }

    void loadSignals();
    const timer = setInterval(() => {
      void loadSignals();
    }, POLL_MS);

    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    let alive = true;
    if (!selectedSymbol) return () => undefined;

    async function loadTargets() {
      try {
        const data = await getSignalTargetsLatest(selectedSymbol, "pro");
        if (!alive) return;
        setTargets(data);
        setTargetsError(null);
      } catch (err: unknown) {
        if (!alive) return;
        setTargets(null);
        setTargetsError(err instanceof Error ? err.message : "Could not load Pro targets.");
      }
    }

    void loadTargets();
    const timer = setInterval(() => {
      void loadTargets();
    }, POLL_MS);

    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [selectedSymbol]);

  useEffect(() => {
    let alive = true;
    if (!selectedSymbol) return () => undefined;

    async function loadIntel() {
      try {
        const data = await getSignalIntelLatest(selectedSymbol);
        if (!alive) return;
        setIntel(data);
        setIntelError(null);
      } catch (err: unknown) {
        if (!alive) return;
        setIntel(null);
        setIntelError(err instanceof Error ? err.message : "Intel unavailable.");
      }
    }

    void loadIntel();
    const timer = setInterval(() => {
      void loadIntel();
    }, POLL_MS);

    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [selectedSymbol]);

  useEffect(() => {
    let alive = true;

    async function loadRunnerStatus() {
      try {
        const data = await getRunnerStatus();
        if (!alive) return;
        setRunnerStatus(data);
        setRunnerStatusError(null);
      } catch (err: unknown) {
        if (!alive) return;
        setRunnerStatus(null);
        setRunnerStatusError(err instanceof Error ? err.message : "Could not load runner status.");
      }
    }

    void loadRunnerStatus();
    const timer = setInterval(() => {
      void loadRunnerStatus();
    }, POLL_MS);

    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    let alive = true;
    if (!selectedSymbol || !isAdmin) {
      return () => {
        alive = false;
      };
    }

    async function loadAnchorDebug() {
      try {
        const data = await getOpsAnchorDebug(selectedSymbol);
        if (!alive) return;
        setAnchorDebug(data);
        setAnchorDebugError(null);
      } catch (err: unknown) {
        if (!alive) return;
        setAnchorDebug(null);
        setAnchorDebugError(err instanceof Error ? err.message : "Oracle debug unavailable.");
      }
    }

    void loadAnchorDebug();
    const timer = setInterval(() => {
      void loadAnchorDebug();
    }, POLL_MS);

    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [selectedSymbol, isAdmin]);

  const symbols = useMemo(() => {
    const fromSignals = Array.from(new Set(signals.map((s) => (s.symbol || "").toUpperCase()).filter(Boolean)));
    if (fromSignals.length > 0) return fromSignals;
    return [selectedSymbol || "XAUUSD"];
  }, [signals, selectedSymbol]);

  const visibleSignals = useMemo(
    () => selectMeaningfulProSignals(signals, { symbol: selectedSymbol }),
    [signals, selectedSymbol]
  );

  return (
    <AuthGate mode="auth">
      <div className="space-y-6 p-6">
        <div className="flex items-center justify-between">
          <h1 className="text-2xl font-bold">Pi Pro Engine Dashboard</h1>
          <div className="flex items-center gap-2">
            <span className="text-sm text-gray-500">Symbol</span>
            <select
              value={selectedSymbol}
              onChange={(e) => setSelectedSymbol(e.target.value)}
              className="rounded-md border px-2 py-1 text-sm"
            >
              {symbols.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </div>
        </div>

        <section className="rounded-xl border p-4">
          <h2 className="mb-2 font-semibold">Pro Targets</h2>
          {targetsError ? (
            <p className="text-sm text-amber-700">{targetsError}</p>
          ) : targets ? (
            <div className="grid gap-1 text-sm">
              <p>
                Liquidity Magnet: <b>{formatPrice(targets.magnet_price)}</b>
              </p>
              <p>
                Zone-to-Zone Target: <b>{formatPrice(targets.zone_to_zone_target)}</b>
              </p>
              <p>
                Sellside Liquidity: <b>{formatPrice(targets.sellside_liquidity)}</b>
              </p>
              <p>
                Buyside Liquidity: <b>{formatPrice(targets.buyside_liquidity)}</b>
              </p>
              <p>
                Updated: <b>{formatLondon(targets.as_of_utc)}</b>
              </p>
            </div>
          ) : (
            <p className="text-sm text-gray-500">No Pro targets available for {selectedSymbol} yet.</p>
          )}
        </section>

        <section className="rounded-xl border p-4">
          <h2 className="mb-2 font-semibold">Latest Signals</h2>
          {signalsLoading ? <p className="text-sm">Loading signals...</p> : null}
          {signalsError ? <p className="text-sm text-amber-700">{signalsError}</p> : null}
          {!signalsLoading && !signalsError && visibleSignals.length === 0 ? (
            <p className="text-sm text-gray-500">No meaningful Pro signals available for {selectedSymbol}.</p>
          ) : null}

          <div className="grid gap-4 md:grid-cols-2">
            {visibleSignals.map((signal) => (
              <SignalMarketCard key={signal.id} signal={signal} showFooter={false} />
            ))}
          </div>
        </section>

        {isAdmin ? (
          <section className="rounded-xl border p-4">
            <h2 className="mb-2 font-semibold">Oracle Debug (Admin/Local)</h2>
            {anchorDebugError ? (
              <p className="text-sm text-amber-700">{anchorDebugError}</p>
            ) : anchorDebug ? (
              <div className="grid gap-1 text-sm md:grid-cols-2">
                <p>
                  08:01 Anchor Direction: <b>{anchorDebug.anchor.direction}</b>
                </p>
                <p>
                  Daily Permission: <b>{anchorDebug.official_permission.daily_permission || intel?.daily_permission || "-"}</b>
                </p>
                <p>
                  Final Allowed: <b>{anchorDebug.official_permission.final_allowed || intel?.allowed_direction || "-"}</b>
                </p>
                <p>
                  Permission Lock Time: <b>{anchorDebug.official_permission.permission_lock_time || "-"}</b>
                </p>
                <p className="md:col-span-2">
                  Last Refreshed At: <b>{formatLondon(anchorDebug.official_permission.last_refreshed_at || intel?.as_of_utc)}</b>
                </p>
              </div>
            ) : (
              <p className="text-sm text-gray-500">Oracle debug not available.</p>
            )}
            {intelError ? <p className="mt-2 text-xs text-amber-700">{intelError}</p> : null}
          </section>
        ) : null}

        <section className="rounded-xl border p-4">
          <h2 className="mb-2 font-semibold">Runner Status</h2>
          {runnerStatusError ? (
            <p className="text-sm text-amber-700">{runnerStatusError}</p>
          ) : runnerStatus ? (
            <div className="grid gap-1 text-sm md:grid-cols-2">
              <p>
                Status: <b className={runnerStatus.runner_online ? "text-green-700" : "text-amber-700"}>{runnerStatus.runner_online ? "ONLINE" : "OFFLINE"}</b>
              </p>
              <p>
                Last heartbeat: <b>{formatLondon(runnerStatus.last_heartbeat_utc)}</b>
              </p>
              <p>
                Last signal sent: <b>{formatLondon(runnerStatus.last_signal_utc)}</b>
              </p>
              <p>
                Last Telegram sent: <b>{formatLondon(runnerStatus.last_telegram_sent_utc)}</b>
              </p>
            </div>
          ) : (
            <p className="text-sm text-gray-500">Runner status unavailable.</p>
          )}
        </section>
      </div>
    </AuthGate>
  );
}
