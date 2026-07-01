"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { fetchMe, logout, WS_BASE } from "@/lib/api";

type Signals = {
  authed: boolean; // spike.ready  -> session-cookie WS authenticated
  count: number | null; // spike.echo.count -> LangGraph state persisted (increments)
  geo: number | null; // spike.echo.geo_within_50km -> GeoDjango ST_DWithin
  inngest: string | null; // inngest.tick -> Inngest round-trip over channel layer
};

const EMPTY: Signals = { authed: false, count: null, geo: null, inngest: null };

function Signal({ ok, label }: { ok: boolean; label: string }) {
  return (
    <li className="flex items-center gap-2">
      <span className={ok ? "text-green-600" : "text-gray-400"}>
        {ok ? "✓" : "○"}
      </span>
      <span>{label}</span>
    </li>
  );
}

export default function SpikePage() {
  const { data: me, isLoading, refetch } = useQuery({
    queryKey: ["me"],
    queryFn: fetchMe,
  });

  const wsRef = useRef<WebSocket | null>(null);
  const [connected, setConnected] = useState(false);
  const [log, setLog] = useState<string[]>([]);
  const [signals, setSignals] = useState<Signals>(EMPTY);

  const append = (line: string) => setLog((l) => [...l.slice(-40), line]);

  function connect() {
    if (wsRef.current) return;
    const ws = new WebSocket(`${WS_BASE}/ws/spike/`);
    wsRef.current = ws;
    ws.onopen = () => {
      setConnected(true);
      append("socket open");
    };
    ws.onclose = (e) => {
      setConnected(false);
      wsRef.current = null;
      append(`socket closed (code ${e.code})`);
    };
    ws.onerror = () => append("socket error");
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      append(`recv ${msg.type}: ${JSON.stringify(msg.data)}`);
      if (msg.type === "spike.ready") setSignals((s) => ({ ...s, authed: true }));
      if (msg.type === "spike.echo")
        setSignals((s) => ({
          ...s,
          count: msg.data.count,
          geo: msg.data.geo_within_50km,
        }));
      if (msg.type === "inngest.tick")
        setSignals((s) => ({ ...s, inngest: msg.data.message }));
    };
  }

  function ping() {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      append("not connected");
      return;
    }
    ws.send(JSON.stringify({ type: "ping", data: { ping: `hi-${Date.now()}` } }));
    append("sent ping");
  }

  async function onLogout() {
    wsRef.current?.close();
    await logout();
    setSignals(EMPTY);
    refetch();
  }

  useEffect(() => () => wsRef.current?.close(), []);

  if (isLoading) return <main className="p-8">Loading…</main>;

  if (!me) {
    return (
      <main className="mx-auto max-w-2xl p-8">
        <p>
          Not signed in.{" "}
          <Link href="/login" className="underline">
            Log in
          </Link>{" "}
          to run the spike.
        </p>
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-2xl p-8">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">P0 spike</h1>
        <div className="text-sm text-gray-600">
          {me.username}{" "}
          <button onClick={onLogout} className="ml-2 underline">
            log out
          </button>
        </div>
      </div>

      <div className="mt-4 flex gap-3">
        <button
          onClick={connect}
          disabled={connected}
          className="rounded bg-black px-4 py-2 text-white disabled:opacity-50 dark:bg-white dark:text-black"
        >
          {connected ? "Connected" : "Open socket"}
        </button>
        <button
          onClick={ping}
          disabled={!connected}
          className="rounded border border-gray-400 px-4 py-2 disabled:opacity-50"
        >
          Send ping
        </button>
      </div>

      <ul className="mt-6 space-y-1">
        <Signal ok={signals.authed} label="Session-cookie WebSocket authenticated (spike.ready)" />
        <Signal
          ok={signals.count !== null}
          label={`LangGraph state persisted — count=${signals.count ?? "?"} (increments per ping)`}
        />
        <Signal
          ok={signals.geo !== null}
          label={`GeoDjango ST_DWithin — ${signals.geo ?? "?"} points within 50km`}
        />
        <Signal
          ok={signals.inngest !== null}
          label={`Inngest round-trip — ${signals.inngest ?? "(waiting for tick)"}`}
        />
      </ul>

      <pre className="mt-6 max-h-72 overflow-auto rounded bg-gray-100 p-3 text-xs dark:bg-gray-900">
        {log.join("\n")}
      </pre>
    </main>
  );
}
