"use client";

import type { ReactNode } from "react";

import type { OracleSessionContextResponse } from "@/lib/api";

const EMPTY_LABEL = "Not available yet";

function formatNumber(value: number | null | undefined): string {
  if (typeof value !== "number" || Number.isNaN(value)) return EMPTY_LABEL;
  if (Math.abs(value) >= 100) return value.toFixed(2);
  if (Math.abs(value) >= 1) return value.toFixed(4);
  return value.toFixed(5);
}

function formatPercent(value: number | null | undefined): string {
  if (typeof value !== "number" || Number.isNaN(value)) return EMPTY_LABEL;
  return `${value.toFixed(1)}%`;
}

function formatRatio(value: number | null | undefined): string {
  if (typeof value !== "number" || Number.isNaN(value)) return EMPTY_LABEL;
  return value.toFixed(2);
}

function formatDateTime(value: string | null | undefined): string {
  if (!value) return EMPTY_LABEL;
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString("en-GB", {
    timeZone: "Europe/London",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

function labelize(value: string | null | undefined): string {
  if (!value) return EMPTY_LABEL;
  return value
    .replace(/_/g, " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

function formatText(value: string | null | undefined): string {
  if (typeof value !== "string") return EMPTY_LABEL;
  const trimmed = value.trim();
  return trimmed || EMPTY_LABEL;
}

function formatBoolean(value: boolean | null | undefined): string {
  if (typeof value !== "boolean") return EMPTY_LABEL;
  return value ? "YES" : "NO";
}

function factorLabel(value: string): string {
  return value
    .replace(/^conflict:/, "Conflict: ")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

function stateTone(state: string | null | undefined): string {
  switch (state) {
    case "ready":
      return "bg-green-100 text-green-800";
    case "developing":
      return "bg-blue-100 text-blue-800";
    case "conflicted":
      return "bg-amber-100 text-amber-800";
    case "invalid":
      return "bg-rose-100 text-rose-800";
    default:
      return "bg-slate-100 text-slate-700";
  }
}

function hasMeaningfulValue(value: unknown): boolean {
  if (value == null) return false;
  if (typeof value === "string") return value.trim().length > 0;
  if (typeof value === "number") return Number.isFinite(value);
  if (typeof value === "boolean") return true;
  if (Array.isArray(value)) return value.length > 0;
  return true;
}

function DetailRow({
  label,
  value,
}: {
  label: string;
  value: ReactNode;
}) {
  return (
    <p>
      {label}: <b>{value}</b>
    </p>
  );
}

function Section({
  title,
  empty = false,
  children,
}: {
  title: string;
  empty?: boolean;
  children: ReactNode;
}) {
  return (
    <section className="rounded-lg border bg-white p-4">
      <h3 className="mb-2 text-sm font-semibold">{title}</h3>
      {empty ? (
        <p className="text-sm text-slate-500">{EMPTY_LABEL}</p>
      ) : (
        <div className="space-y-1 text-sm text-slate-700">{children}</div>
      )}
    </section>
  );
}

function FactorList({
  title,
  items,
  tone,
}: {
  title: string;
  items: string[];
  tone: "green" | "amber";
}) {
  const toneClass =
    tone === "green"
      ? "border-green-200 bg-green-50 text-green-800"
      : "border-amber-200 bg-amber-50 text-amber-800";

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold">{title}</h3>
        <span className="text-xs text-slate-500">{items.length}</span>
      </div>
      {items.length === 0 ? (
        <p className="text-sm text-slate-500">None.</p>
      ) : (
        <div className="flex flex-wrap gap-2">
          {items.map((item) => (
            <span key={`${title}-${item}`} className={`rounded-full border px-2 py-1 text-xs ${toneClass}`}>
              {factorLabel(item)}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

type Props = {
  symbol: string;
  context: OracleSessionContextResponse | null;
  loading: boolean;
  error: string | null;
};

export default function GbpjpySessionContextPanel({ symbol, context, loading, error }: Props) {
  const symbolLabel = formatText(context?.symbol || symbol || "Selected symbol");
  const topLevelMissingFields = context
    ? [
        !hasMeaningfulValue(context.setup_state) ? "setup_state" : null,
        !hasMeaningfulValue(context.setup_direction) ? "setup_direction" : null,
        !hasMeaningfulValue(context.setup_reason) ? "setup_reason" : null,
        !hasMeaningfulValue(context.session_state) ? "session_state" : null,
      ].filter((item): item is string => Boolean(item))
    : [];

  return (
    <section className="rounded-xl border p-4 md:col-span-2">
      <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
        <div>
          <div className="mb-2 inline-flex rounded-full border border-slate-300 bg-slate-50 px-3 py-1 text-xs font-semibold text-slate-700">
            Review Mode
          </div>
          <h2 className="font-semibold">{symbolLabel} Session Context</h2>
          <p className="mt-1 text-sm text-slate-500">
            Manual validation view for the current session-context model.
          </p>
        </div>
      </div>

      {error ? (
        <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800">
          <p className="font-semibold">{symbolLabel} session-context unavailable</p>
          <p className="mt-1">{error}</p>
        </div>
      ) : loading && !context ? (
        <div className="mt-4 rounded-lg border border-slate-200 bg-slate-50 p-4 text-sm text-slate-600">
          <p className="font-semibold">Loading {symbolLabel} session context...</p>
          <p className="mt-1">Review mode will populate as soon as the current payload arrives.</p>
        </div>
      ) : !context ? (
        <div className="mt-4 rounded-lg border border-slate-200 bg-slate-50 p-4 text-sm text-slate-600">
          <p className="font-semibold">No {symbolLabel} session context is available yet.</p>
          <p className="mt-1">This panel stays available even when the selected symbol only has partial or pending session-context data.</p>
        </div>
      ) : (
        <div className="mt-4 space-y-4">
          <div className="rounded-xl border border-slate-200 bg-slate-50 p-4">
            <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Top-Level Decision Context</p>
            <div className="mt-3 grid gap-4 lg:grid-cols-[1.5fr_1fr]">
              <div className="rounded-lg border bg-white p-4 shadow-sm">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <p className="text-sm text-slate-500">Setup Review</p>
                    <div className="mt-2 flex flex-wrap items-center gap-2">
                      <span className={`rounded-full px-3 py-1 text-xs font-semibold ${stateTone(context.setup_state)}`}>
                        {labelize(context.setup_state)}
                      </span>
                      <span className="rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-xs font-semibold text-slate-700">
                        {labelize(context.setup_direction)}
                      </span>
                    </div>
                  </div>
                  <div className="grid gap-2 text-sm sm:grid-cols-2 lg:grid-cols-1 lg:text-right">
                    <p>
                      Confidence: <b>{formatPercent(context.setup_confidence)}</b>
                    </p>
                    <p>
                      Score: <b>{hasMeaningfulValue(context.setup_score) ? context.setup_score : EMPTY_LABEL}</b>
                    </p>
                    <p>
                      Setup Available: <b>{formatBoolean(context.setup_available)}</b>
                    </p>
                  </div>
                </div>
                <p className="mt-4 text-sm text-slate-800">{formatText(context.setup_reason)}</p>
                <p className="mt-3 rounded-md bg-slate-50 p-3 text-sm text-slate-600">
                  <span className="font-semibold text-slate-700">Entry Context Summary:</span>{" "}
                  {formatText(context.entry_context_summary)}
                </p>
              </div>

              <div className="rounded-lg border bg-white p-4">
                <p className="text-sm font-semibold">Review Snapshot</p>
                <div className="mt-3 space-y-2 text-sm text-slate-700">
                  <DetailRow label="Session State" value={labelize(context.session_state)} />
                  <DetailRow label="London Now" value={formatDateTime(context.london_now)} />
                  <DetailRow label="Source Timeframe" value={formatText(context.source_timeframe_used)} />
                  <DetailRow label="Anchor Bias" value={labelize(context.anchor_bias)} />
                  <DetailRow label="Structure State" value={labelize(context.structure_state)} />
                  <DetailRow label="FVG State" value={labelize(context.fvg_state)} />
                </div>
              </div>
            </div>
          </div>

          {topLevelMissingFields.length > 0 ? (
            <div className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800">
              <p className="font-semibold">Partial session-context payload</p>
              <p className="mt-1">
                Review mode is active, but some top-level fields are still empty:{" "}
                <b>{topLevelMissingFields.map(factorLabel).join(", ")}</b>.
              </p>
            </div>
          ) : null}

          <div className="rounded-xl border border-slate-200 bg-white p-4">
            <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Factor Review</p>
            <div className="mt-3 grid gap-4 md:grid-cols-2">
              <FactorList title="Confirming Factors" items={context.confirming_factors || []} tone="green" />
              <FactorList title="Blocking Factors" items={context.blocking_factors || []} tone="amber" />
            </div>
          </div>

          <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50 p-4">
            <div className="mb-4">
              <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Raw Detail Review</p>
              <p className="mt-1 text-sm text-slate-500">
                These sections are for manual validation of the underlying {symbolLabel} session-context fields.
              </p>
            </div>

            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
              <Section title="Session State" empty={!hasMeaningfulValue(context.session_state)}>
                <DetailRow label="Session" value={labelize(context.session_state)} />
                <DetailRow label="London Now" value={formatDateTime(context.london_now)} />
                <DetailRow label="Source Timeframe" value={formatText(context.source_timeframe_used)} />
              </Section>

              <Section
                title="Asian Range"
                empty={
                  !hasMeaningfulValue(context.asian_high) &&
                  !hasMeaningfulValue(context.asian_low) &&
                  !hasMeaningfulValue(context.asian_mid)
                }
              >
                <DetailRow label="High" value={formatNumber(context.asian_high)} />
                <DetailRow label="Low" value={formatNumber(context.asian_low)} />
                <DetailRow label="Mid" value={formatNumber(context.asian_mid)} />
                <DetailRow label="Size" value={`${formatNumber(context.asian_range_size_pips)} pips`} />
                <DetailRow label="Valid" value={formatBoolean(context.asian_range_valid)} />
              </Section>

              <Section
                title="Anchor"
                empty={!context.anchor_available && !hasMeaningfulValue(context.anchor_classification)}
              >
                <DetailRow label="Available" value={formatBoolean(context.anchor_available)} />
                <DetailRow label="Classification" value={labelize(context.anchor_classification)} />
                <DetailRow label="Bias" value={labelize(context.anchor_bias)} />
                <DetailRow label="Quality" value={labelize(context.anchor_quality)} />
                <DetailRow
                  label="Time"
                  value={formatDateTime(context.anchor?.anchor_time_london || context.anchor_time_london)}
                />
                <DetailRow
                  label="O/H/L/C"
                  value={`${formatNumber(context.anchor?.open)} / ${formatNumber(context.anchor?.high)} / ${formatNumber(
                    context.anchor?.low
                  )} / ${formatNumber(context.anchor?.close)}`}
                />
                <DetailRow label="Body Ratio" value={formatRatio(context.anchor?.body_ratio)} />
                <DetailRow label="Wick Ratio" value={formatRatio(context.anchor?.wick_ratio)} />
              </Section>

              <Section title="Sweep" empty={!context.sweep_available && !hasMeaningfulValue(context.sweep_side)}>
                <DetailRow label="Available" value={formatBoolean(context.sweep_available)} />
                <DetailRow label="Side" value={labelize(context.sweep_side)} />
                <DetailRow label="Type" value={labelize(context.sweep_type)} />
                <DetailRow label="Swept Level" value={formatNumber(context.swept_level)} />
                <DetailRow label="Buffer" value={`${formatNumber(context.sweep_buffer_pips)} pips`} />
                <DetailRow label="Returned Inside Range" value={formatBoolean(context.returned_inside_range)} />
                <DetailRow label="Quality" value={labelize(context.sweep_quality)} />
                <DetailRow label="Time" value={formatDateTime(context.sweep_time_london)} />
              </Section>

              <Section
                title="Magnet"
                empty={!hasMeaningfulValue(context.active_magnet_level) && !hasMeaningfulValue(context.magnet_bias)}
              >
                <DetailRow label="Bias" value={labelize(context.magnet_bias)} />
                <DetailRow label="Active Magnet" value={formatNumber(context.active_magnet_level)} />
                <DetailRow label="Magnet Type" value={labelize(context.active_magnet_type)} />
                <DetailRow label="Next Buyside" value={formatNumber(context.next_buyside_liquidity)} />
                <DetailRow label="Next Sellside" value={formatNumber(context.next_sellside_liquidity)} />
                <DetailRow label="Distance To Magnet" value={`${formatNumber(context.distance_to_magnet_pips)} pips`} />
              </Section>

              <Section title="Zone" empty={!hasMeaningfulValue(context.zone_state) && !hasMeaningfulValue(context.equilibrium)}>
                <DetailRow label="Zone State" value={labelize(context.zone_state)} />
                <DetailRow label="Dealing Range High" value={formatNumber(context.dealing_range_high)} />
                <DetailRow label="Dealing Range Low" value={formatNumber(context.dealing_range_low)} />
                <DetailRow label="Equilibrium" value={formatNumber(context.equilibrium)} />
                <DetailRow
                  label="Distance From Equilibrium"
                  value={`${formatNumber(context.distance_from_equilibrium_pips)} pips`}
                />
              </Section>

              <Section
                title="Structure"
                empty={!context.structure_available && !hasMeaningfulValue(context.structure_state)}
              >
                <DetailRow label="Available" value={formatBoolean(context.structure_available)} />
                <DetailRow label="State" value={labelize(context.structure_state)} />
                <DetailRow label="Bias" value={labelize(context.structure_bias)} />
                <DetailRow label="MSS Detected" value={formatBoolean(context.mss_detected)} />
                <DetailRow label="BOS Detected" value={formatBoolean(context.bos_detected)} />
                <DetailRow label="Break Level" value={formatNumber(context.break_level)} />
                <DetailRow label="Displacement" value={`${formatNumber(context.displacement_size_pips)} pips`} />
                <DetailRow label="Break Time" value={formatDateTime(context.break_time_london)} />
              </Section>

              <Section title="FVG" empty={!context.fvg_available && !hasMeaningfulValue(context.fvg_state)}>
                <DetailRow label="Available" value={formatBoolean(context.fvg_available)} />
                <DetailRow label="Direction" value={labelize(context.fvg_direction)} />
                <DetailRow label="State" value={labelize(context.fvg_state)} />
                <DetailRow label="High" value={formatNumber(context.fvg_high)} />
                <DetailRow label="Low" value={formatNumber(context.fvg_low)} />
                <DetailRow label="Mid" value={formatNumber(context.fvg_mid)} />
                <DetailRow label="Size" value={`${formatNumber(context.fvg_size_pips)} pips`} />
                <DetailRow
                  label="Age"
                  value={hasMeaningfulValue(context.fvg_age_bars) ? `${context.fvg_age_bars} bars` : EMPTY_LABEL}
                />
                <DetailRow label="Mitigated" value={formatBoolean(context.fvg_mitigated)} />
              </Section>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
