"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import AuthGate from "@/components/AuthGate";
import {
  getTelegramSettings,
  saveTelegramSettings,
  sendTelegramTest,
} from "@/lib/api";

export default function TelegramSettingsPage() {
  const router = useRouter();

  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);

  const [telegramEnabled, setTelegramEnabled] = useState(false);
  const [chatIdInput, setChatIdInput] = useState("");
  const [savedMaskedChatId, setSavedMaskedChatId] = useState("");
  const [hasSavedChatId, setHasSavedChatId] = useState(false);

  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [allowedSymbols, setAllowedSymbols] = useState<string[]>([]);
  const [lockedSymbols, setLockedSymbols] = useState<string[]>([]);
  const [selectedSymbols, setSelectedSymbols] = useState<string[]>([]);

  useEffect(() => {
    let alive = true;

    async function loadSettings() {
      try {
        const data = await getTelegramSettings();
        if (!alive) return;
        setTelegramEnabled(Boolean(data.telegram_enabled));
        setSavedMaskedChatId(data.telegram_chat_id || "");
        setHasSavedChatId(Boolean(data.has_chat_id));
        setAllowedSymbols(data.allowed_symbols || []);
        setLockedSymbols(data.locked_symbols || []);
        setSelectedSymbols(data.symbols || []);
      } catch (error: unknown) {
        if (!alive) return;
        setErr(error instanceof Error ? error.message : "Could not load Telegram settings.");
      } finally {
        if (!alive) return;
        setLoading(false);
      }
    }

    void loadSettings();
    return () => {
      alive = false;
    };
  }, [router]);

  async function onSave() {
    setErr(null);
    setMsg(null);
    const trimmed = chatIdInput.trim();
    const hasNew = Boolean(trimmed);
    const chatIdPattern = /^-?\d+$/;

    if (telegramEnabled && !chatIdInput.trim() && !hasSavedChatId) {
      setErr("Please enter your Telegram chat_id before enabling alerts.");
      return;
    }
    if (hasNew && !chatIdPattern.test(trimmed)) {
      setErr("Telegram chat_id must contain digits only.");
      return;
    }

    try {
      setSaving(true);
      const res = await saveTelegramSettings({
        telegram_enabled: telegramEnabled,
        telegram_chat_id: trimmed || undefined,
        symbols: selectedSymbols,
      });
      if (res.ok) {
        const refreshed = await getTelegramSettings();
        setSavedMaskedChatId(refreshed.telegram_chat_id || "");
        setHasSavedChatId(Boolean(refreshed.has_chat_id));
        setAllowedSymbols(refreshed.allowed_symbols || []);
        setLockedSymbols(refreshed.locked_symbols || []);
        setSelectedSymbols(refreshed.symbols || []);
        setChatIdInput("");
      }
      setMsg("Saved successfully.");
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "Failed to save settings.");
    } finally {
      setSaving(false);
    }
  }

  async function onTest() {
    setErr(null);
    setMsg(null);

    try {
      setTesting(true);
      await sendTelegramTest();
      setMsg("Test message sent. Check Telegram.");
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "Failed to send test message.");
    } finally {
      setTesting(false);
    }
  }

  function toggleSymbol(symbol: string, locked: boolean) {
    if (locked) return;
    setSelectedSymbols((prev) => {
      const set = new Set(prev);
      if (set.has(symbol)) {
        set.delete(symbol);
      } else {
        set.add(symbol);
      }
      return Array.from(set);
    });
  }

  if (loading) return <div className="p-6">Loading settings...</div>;

  return (
    <AuthGate mode="auth">
      <div className="p-6 max-w-2xl space-y-6">
        <div className="flex items-center justify-between">
          <h1 className="text-2xl font-bold">Telegram Settings</h1>
          <button onClick={() => router.push("/dashboard")} className="text-sm underline">
            Back to Dashboard
          </button>
        </div>

        <div className="rounded-xl border p-4 space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <p className="font-semibold">Enable Telegram alerts</p>
              <p className="text-sm text-gray-500">
                Receive your signal outcomes directly in Telegram.
              </p>
              <p className="text-xs text-gray-500 mt-1">
                Signals are sent only for symbols enabled in Symbol Settings.
              </p>
            </div>

            <button
              onClick={() => setTelegramEnabled((v) => !v)}
              className={`px-3 py-2 rounded-md border text-sm ${
                telegramEnabled ? "bg-black text-white" : "bg-white"
              }`}
            >
              {telegramEnabled ? "Enabled" : "Disabled"}
            </button>
          </div>

          <div>
            <label className="text-sm font-medium">Telegram chat_id</label>
            <input
              value={chatIdInput}
              onChange={(e) => setChatIdInput(e.target.value)}
              placeholder="e.g. 123456789"
              className="mt-2 w-full rounded-md border p-2 text-sm"
              disabled={!telegramEnabled}
            />
            {savedMaskedChatId ? (
              <p className="mt-2 text-xs text-gray-500">
                Saved chat_id: <b>{savedMaskedChatId}</b>
              </p>
            ) : (
              <p className="mt-2 text-xs text-gray-500">No chat_id saved yet.</p>
            )}
            <p className="mt-2 text-xs text-gray-500">
              Open Telegram, start your bot with <b>/start</b>, then send a message and fetch your chat_id from bot updates.
            </p>
          </div>

          <div>
            <p className="text-sm font-medium">Symbols for Telegram signals</p>
            <div className="mt-2 space-y-2">
              {[...allowedSymbols, ...lockedSymbols.filter((s) => !allowedSymbols.includes(s))].map((symbol) => {
                const locked = lockedSymbols.includes(symbol);
                const checked = selectedSymbols.includes(symbol);
                return (
                  <label
                    key={symbol}
                    className={`flex items-center justify-between rounded-md border px-3 py-2 text-sm ${
                      locked ? "opacity-60" : ""
                    }`}
                  >
                    <div className="flex items-center gap-3">
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => toggleSymbol(symbol, locked)}
                        disabled={locked}
                      />
                      <span>{symbol}</span>
                    </div>
                    {locked ? <span className="text-xs text-gray-500">Upgrade to unlock</span> : null}
                  </label>
                );
              })}
            </div>
          </div>

          {err ? <div className="text-sm text-red-600 border rounded-md p-2">{err}</div> : null}
          {msg ? <div className="text-sm text-green-700 border rounded-md p-2">{msg}</div> : null}

          <div className="flex gap-3">
            <button
              onClick={onSave}
              disabled={saving}
              className="px-4 py-2 rounded-md bg-black text-white text-sm disabled:opacity-50"
            >
              {saving ? "Saving..." : "Save"}
            </button>

            <button
              onClick={onTest}
              disabled={testing || !telegramEnabled || !hasSavedChatId}
              className="px-4 py-2 rounded-md border text-sm disabled:opacity-50"
            >
              {testing ? "Sending..." : "Send test message"}
            </button>
          </div>
        </div>
      </div>
    </AuthGate>
  );
}
