import type { SignalFeedItem } from "@/lib/api";

export const PRO_FEED_TYPES = new Set([
  "opportunity_m15_confirmed",
  "magnet_snapshot",
]);

function signalSortTimeMs(item: SignalFeedItem): number {
  const detected = Date.parse(item.detected_at || "");
  if (!Number.isNaN(detected)) return detected;
  const created = Date.parse(item.created_at || "");
  if (!Number.isNaN(created)) return created;
  return 0;
}

type SelectProFeedOptions = {
  symbol?: string | null;
};

export function selectMeaningfulProSignals(
  items: SignalFeedItem[],
  options: SelectProFeedOptions = {}
): SignalFeedItem[] {
  const wantedSymbol = (options.symbol || "").trim().toUpperCase();
  const bySymbol = new Map<string, SignalFeedItem[]>();

  for (const item of items) {
    const type = String(item.signal_type || "").trim().toLowerCase();
    if (!PRO_FEED_TYPES.has(type)) continue;

    const symbol = String(item.symbol || "").trim().toUpperCase();
    if (wantedSymbol && symbol !== wantedSymbol) continue;
    if (!symbol) continue;

    const bucket = bySymbol.get(symbol) || [];
    bucket.push(item);
    bySymbol.set(symbol, bucket);
  }

  const selected: SignalFeedItem[] = [];

  for (const group of bySymbol.values()) {
    const sorted = [...group].sort((a, b) => signalSortTimeMs(b) - signalSortTimeMs(a));
    const latestAligned =
      sorted.find((item) => String(item.signal_type || "").trim().toLowerCase() === "opportunity_m15_confirmed") ||
      sorted[0];
    if (latestAligned) {
      selected.push(latestAligned);
    }
  }

  return selected.sort((a, b) => signalSortTimeMs(b) - signalSortTimeMs(a));
}
