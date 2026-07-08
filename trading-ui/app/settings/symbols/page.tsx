"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";

import AuthGate from "@/components/AuthGate";
import {
  getSymbolPreferences,
  getSymbolsAvailable,
  saveSymbolPreferences,
  type SymbolsAvailableResponse,
  type SymbolPreferenceItem,
} from "@/lib/api";

export default function SymbolSettingsPage() {
  const router = useRouter();

  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [info, setInfo] = useState<SymbolsAvailableResponse | null>(null);
  const [items, setItems] = useState<SymbolPreferenceItem[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;

    async function load() {
      try {
        const [available, prefs] = await Promise.all([getSymbolsAvailable(), getSymbolPreferences()]);
        if (!alive) return;
        setInfo(available);
        setItems(prefs.all);
        setSelected(prefs.selected);
      } catch (error: unknown) {
        if (!alive) return;
        setErr(error instanceof Error ? error.message : "Could not load symbol settings.");
      } finally {
        if (!alive) return;
        setLoading(false);
      }
    }

    void load();
    return () => {
      alive = false;
    };
  }, [router]);

  const selectedSet = useMemo(() => new Set(selected), [selected]);

  function toggleSymbol(symbol: string, locked: boolean) {
    if (locked) return;
    setMsg(null);
    setErr(null);
    setSelected((prev) => {
      const set = new Set(prev);
      if (set.has(symbol)) {
        set.delete(symbol);
      } else {
        set.add(symbol);
      }
      return Array.from(set);
    });
  }

  async function onSave() {
    setErr(null);
    setMsg(null);
    try {
      setSaving(true);
      const res = await saveSymbolPreferences({ selected });
      setSelected(res.selected);
      setMsg("Symbol preferences saved.");
      const refreshed = await getSymbolPreferences();
      setItems(refreshed.all);
    } catch (error: unknown) {
      setErr(error instanceof Error ? error.message : "Failed to save symbol preferences.");
    } finally {
      setSaving(false);
    }
  }

  if (loading) return <div className="p-6">Loading symbol settings...</div>;

  return (
    <AuthGate mode="auth">
      <div className="p-6 max-w-2xl space-y-6">
        <div className="flex items-center justify-between">
          <h1 className="text-2xl font-bold">Symbol Settings</h1>
          <button onClick={() => router.push("/dashboard")} className="text-sm underline">
            Back to Dashboard
          </button>
        </div>

        <div className="rounded-xl border p-4 space-y-4">
          <p className="text-sm text-gray-600">
            Select symbols for Telegram signal delivery. Locked symbols require plan upgrade.
          </p>

          <div className="space-y-2">
            {items.map((item) => (
              <label
                key={item.symbol}
                className={`flex items-center justify-between rounded-md border px-3 py-2 ${
                  item.locked ? "opacity-60" : ""
                }`}
              >
                <div className="flex items-center gap-3">
                  <input
                    type="checkbox"
                    checked={selectedSet.has(item.symbol)}
                    onChange={() => toggleSymbol(item.symbol, item.locked)}
                    disabled={item.locked}
                  />
                  <span className="font-medium">{item.symbol}</span>
                </div>
                {item.locked ? <span className="text-xs text-gray-500">Upgrade to unlock</span> : null}
              </label>
            ))}
          </div>

          {info ? (
            <div className="text-xs text-gray-500">
              Tier: <b className="uppercase">{info.tier}</b>
            </div>
          ) : null}

          {err ? <div className="text-sm text-red-600 border rounded-md p-2">{err}</div> : null}
          {msg ? <div className="text-sm text-green-700 border rounded-md p-2">{msg}</div> : null}

          <button
            onClick={onSave}
            disabled={saving}
            className="px-4 py-2 rounded-md bg-black text-white text-sm disabled:opacity-50"
          >
            {saving ? "Saving..." : "Save"}
          </button>
        </div>
      </div>
    </AuthGate>
  );
}
