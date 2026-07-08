"use client";

import type { SignalFeedItem } from "@/lib/api";

function toNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string") {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function toText(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const clean = value.trim();
  return clean ? clean : null;
}

function formatPrice(value: number | null): string {
  if (value == null) return "-";
  if (Math.abs(value) >= 100) return value.toFixed(2);
  if (Math.abs(value) >= 1) return value.toFixed(4);
  return value.toFixed(5);
}

function formatConfidence(value: number | null): string {
  if (value == null) return "-";
  const percentage = value <= 1 ? value * 100 : value;
  return `${percentage.toFixed(1)}%`;
}

type SignalMarketCardProps = {
  signal: SignalFeedItem;
  showFooter?: boolean;
};

export default function SignalMarketCard({ signal, showFooter = true }: SignalMarketCardProps) {
  const meta = signal.meta || {};
  const symbolDisplay = toText(meta.symbol_display) || signal.symbol;
  const timeframeDisplay = toText(meta.timeframe_display) || signal.timeframe;
  const bias = signal.bias || signal.direction || signal.daily_permission || "NO_TRADE";
  const magnet =
    signal.magnet ??
    signal.zone_target ??
    signal.magnet_level ??
    toNumber(meta.magnet) ??
    toNumber(meta.magnet_price) ??
    toNumber(meta.magnet_level) ??
    toNumber(meta.zone_target) ??
    toNumber(meta.zone_to_zone_target);
  const zoneTarget = signal.zone_target ?? toNumber(meta.zone_target) ?? toNumber(meta.zone_to_zone_target);
  const sellside = signal.sellside_liquidity ?? toNumber(meta.sellside_liquidity);
  const buyside = signal.buyside_liquidity ?? toNumber(meta.buyside_liquidity);
  const confidence = signal.confidence ?? toNumber(meta.confidence);
  const reason =
    signal.reason || toText(meta.reason_short) || toText(meta.reason) || "Opportunity aligned with daily permission.";
  const detectedLabel = new Date(signal.detected_at).toLocaleString();

  return (
    <article className="space-y-2 rounded-xl border bg-white p-4 shadow-sm">
      <h2 className="text-base font-semibold">{`${symbolDisplay} \u00b7 ${timeframeDisplay} \u00b7 ${bias}`}</h2>
      <div className="space-y-1 text-sm">
        <p>Magnet: {formatPrice(magnet)}</p>
        <p>Zone Target: {formatPrice(zoneTarget)}</p>
        <p>Sellside: {formatPrice(sellside)}</p>
        <p>Buyside: {formatPrice(buyside)}</p>
        {confidence != null ? <p>Confidence: {formatConfidence(confidence)}</p> : null}
        <p className="text-gray-700">Reason: {reason}</p>
        <p className="text-gray-500">Detected: {detectedLabel}</p>
        <p className="text-gray-500">Source: {signal.source}</p>
      </div>
      {showFooter ? (
        <p className="pt-2 text-xs text-gray-500">
          Type: {signal.type || signal.signal_type}
        </p>
      ) : null}
    </article>
  );
}
