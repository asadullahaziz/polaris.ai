"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import {
  addMemory,
  approveCampaign,
  cancelCampaign,
  createConversation,
  deleteConversation,
  fetchMe,
  getPreferences,
  getValuation,
  listCampaigns,
  listConversations,
  listListings,
  listMemory,
  loadMessages,
  logout,
  type OutreachCampaign,
  renameConversation,
  type Message,
  type Valuation,
  WS_BASE,
} from "@/lib/api";
import { Markdown } from "./Markdown";

const fmt = (n: number | null | undefined) =>
  n == null ? "—" : `$${Math.round(n).toLocaleString()}`;

export default function CopilotPage() {
  const router = useRouter();
  const qc = useQueryClient();
  const { data: me, isLoading } = useQuery({ queryKey: ["me"], queryFn: fetchMe });
  const { data: conversations = [] } = useQuery({
    queryKey: ["conversations"],
    queryFn: listConversations,
    enabled: !!me,
  });

  const [activeId, setActiveId] = useState<number | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [streaming, setStreaming] = useState("");
  const [busy, setBusy] = useState(false);
  const [connected, setConnected] = useState(false);
  const [input, setInput] = useState("");
  const [rightTab, setRightTab] = useState<"listings" | "outreach" | "context">("listings");
  const [tick, setTick] = useState("");

  const wsRef = useRef<WebSocket | null>(null);
  const bufRef = useRef("");
  const scrollRef = useRef<HTMLDivElement | null>(null);
  // The WS handler closes over the first render — keep the active chat in a ref so
  // outreach ticks (which arrive async) route to the right conversation.
  const activeIdRef = useRef<number | null>(null);
  useEffect(() => {
    activeIdRef.current = activeId;
  }, [activeId]);

  // One socket per session (cookie auth rides along, like the spike socket).
  // Auto-reconnects on drop: in dev `uvicorn --reload` closes the socket on every backend
  // edit, and any network blip can drop it — without reconnect the chat looks broken (send
  // silently no-ops) until a manual page reload.
  useEffect(() => {
    if (!me) return;
    let stopped = false;
    let retry: ReturnType<typeof setTimeout> | undefined;

    function connect() {
      const ws = new WebSocket(`${WS_BASE}/ws/copilot/`);
      wsRef.current = ws;
      ws.onopen = () => setConnected(true);
      ws.onclose = () => {
        setConnected(false);
        setBusy(false); // a turn in flight is lost on drop — let the user retry
        if (!stopped) retry = setTimeout(connect, 1500); // reconnect with a short backoff
      };
      ws.onmessage = (ev) => {
        const msg = JSON.parse(ev.data);
        const d = msg.data || {};
        if (msg.type === "copilot.created") setActiveId(d.conversation_id);
        else if (msg.type === "copilot.token") {
          bufRef.current += d.token;
          setStreaming(bufRef.current);
        } else if (msg.type === "copilot.done") {
          const body = bufRef.current;
          bufRef.current = "";
          setStreaming("");
          setBusy(false);
          setMessages((m) => [
            ...m,
            { id: d.message_id, author_type: "agent", body, created_at: "" },
          ]);
          qc.invalidateQueries({ queryKey: ["conversations"] });
        } else if (msg.type === "copilot.error") {
          bufRef.current = "";
          setStreaming("");
          setBusy(false);
          setMessages((m) => [
            ...m,
            { id: -Date.now(), author_type: "system", body: `⚠️ ${d.detail}`, created_at: "" },
          ]);
        } else if (msg.type === "outreach.progress") {
          // Templated fan-out tick (no LLM) — transient status in the launching chat.
          if (d.conversation_id == null || d.conversation_id === activeIdRef.current) {
            setTick(d.text || "");
            if (d.done) setTimeout(() => setTick(""), 4000);
          }
        } else if (msg.type === "outreach.summary") {
          setTick("");
          qc.invalidateQueries({ queryKey: ["campaigns"] });
          qc.invalidateQueries({ queryKey: ["conversations"] });
          if (d.body && (d.conversation_id == null || d.conversation_id === activeIdRef.current)) {
            setMessages((m) => [
              ...m,
              { id: d.message_id ?? -Date.now(), author_type: "agent", body: d.body, created_at: "" },
            ]);
          }
        }
      };
    }

    connect();
    return () => {
      stopped = true;
      if (retry) clearTimeout(retry);
      wsRef.current?.close();
    };
  }, [me, qc]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages, streaming]);

  async function openChat(id: number) {
    setActiveId(id);
    setStreaming("");
    setMessages(await loadMessages(id));
  }

  function newChat() {
    setActiveId(null);
    setMessages([]);
    setStreaming("");
  }

  function send() {
    const body = input.trim();
    if (!body || busy || !wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
    setMessages((m) => [
      ...m,
      { id: -Date.now(), author_type: "human", body, created_at: "" },
    ]);
    setInput("");
    setBusy(true);
    bufRef.current = "";
    wsRef.current.send(
      JSON.stringify({ type: "copilot.send", data: { conversation_id: activeId, body } }),
    );
  }

  async function rename(id: number, current: string | null) {
    const title = window.prompt("Rename chat", current ?? "");
    if (title != null) {
      await renameConversation(id, title);
      qc.invalidateQueries({ queryKey: ["conversations"] });
    }
  }

  async function remove(id: number) {
    await deleteConversation(id);
    if (activeId === id) newChat();
    qc.invalidateQueries({ queryKey: ["conversations"] });
  }

  if (isLoading) return <main className="p-8">Loading…</main>;
  if (!me)
    return (
      <main className="mx-auto max-w-sm p-8">
        <p className="mb-4">Please log in to use Polaris.</p>
        <Link href="/login" className="rounded bg-black px-4 py-2 text-white dark:bg-white dark:text-black">
          Log in
        </Link>
      </main>
    );

  return (
    <div className="flex h-screen text-sm">
      {/* Sidebar */}
      <aside className="flex w-64 flex-col border-r border-gray-200 dark:border-gray-800">
        <div className="flex items-center justify-between p-3">
          <span className="font-semibold">Polaris</span>
          <button
            onClick={newChat}
            className="rounded bg-black px-2 py-1 text-xs text-white dark:bg-white dark:text-black"
          >
            + New
          </button>
        </div>
        <div className="flex-1 overflow-y-auto px-2">
          {conversations.map((c) => (
            <div
              key={c.id}
              className={`group flex items-center justify-between rounded px-2 py-2 ${
                c.id === activeId ? "bg-gray-200 dark:bg-gray-800" : "hover:bg-gray-100 dark:hover:bg-gray-900"
              }`}
            >
              <button className="flex-1 truncate text-left" onClick={() => openChat(c.id)}>
                {c.title || "Untitled chat"}
              </button>
              <span className="hidden gap-1 group-hover:flex">
                <button onClick={() => rename(c.id, c.title)} title="Rename">✏️</button>
                <button onClick={() => remove(c.id)} title="Delete">🗑️</button>
              </span>
            </div>
          ))}
          {conversations.length === 0 && (
            <p className="px-2 py-4 text-gray-500">No chats yet. Start one below.</p>
          )}
        </div>
        <div className="border-t border-gray-200 p-3 text-xs text-gray-500 dark:border-gray-800">
          <div className="mb-1">{me.full_name || me.username}</div>
          <Link href="/inbox" className="underline">
            Inbox
          </Link>
          <button
            onClick={() => logout().then(() => router.push("/login"))}
            className="ml-2 underline"
          >
            Log out
          </button>
          <span className={`ml-2 ${connected ? "text-green-600" : "text-gray-400"}`}>
            {connected ? "● online" : "○ offline"}
          </span>
        </div>
      </aside>

      {/* Chat */}
      <main className="flex flex-1 flex-col">
        <div ref={scrollRef} className="flex-1 overflow-y-auto">
          <div className="mx-auto max-w-3xl p-6">
            {messages.length === 0 && !streaming && (
              <div className="mt-16 text-center text-gray-500">
                <p className="text-lg">Ask Polaris to value a listing, intake a property, or set a mandate.</p>
                <p className="mt-2 text-xs">
                  Try: “Estimate the market value of listing #1 with a couple comps.”
                </p>
              </div>
            )}
            {messages.map((m) => (
              <Bubble key={m.id} role={m.author_type} body={m.body} />
            ))}
            {streaming && <Bubble role="agent" body={streaming} streaming />}
            {busy && !streaming && <p className="my-3 text-gray-400">Polaris is thinking…</p>}
            {tick && (
              <p className="my-3 text-center text-xs text-blue-600 dark:text-blue-400">
                📣 {tick}
              </p>
            )}
          </div>
        </div>
        <div className="border-t border-gray-200 p-4 dark:border-gray-800">
          <div className="mx-auto flex max-w-3xl gap-2">
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  send();
                }
              }}
              rows={2}
              placeholder="Message Polaris…  (Enter to send, Shift+Enter for newline)"
              className="flex-1 resize-none rounded border border-gray-300 px-3 py-2 dark:border-gray-700 dark:bg-gray-900"
            />
            <button
              onClick={send}
              disabled={busy}
              className="rounded bg-black px-4 text-white disabled:opacity-40 dark:bg-white dark:text-black"
            >
              Send
            </button>
          </div>
        </div>
      </main>

      {/* Right rail */}
      <RightRail tab={rightTab} setTab={setRightTab} enabled={!!me} />
    </div>
  );
}

function Bubble({
  role,
  body,
  streaming,
}: {
  role: Message["author_type"];
  body: string;
  streaming?: boolean;
}) {
  if (role === "human")
    return (
      <div className="my-3 flex justify-end">
        <div className="max-w-[80%] whitespace-pre-wrap rounded-2xl bg-black px-4 py-2 text-white dark:bg-white dark:text-black">
          {body}
        </div>
      </div>
    );
  if (role === "system")
    return <div className="my-3 text-center text-xs text-amber-600">{body}</div>;
  return (
    <div className="my-3">
      <div className="mb-1 text-xs font-medium text-gray-500">Polaris</div>
      <div className="max-w-none">
        <Markdown>{body || "…"}</Markdown>
        {streaming && <span className="ml-0.5 animate-pulse">▍</span>}
      </div>
    </div>
  );
}

function RightRail({
  tab,
  setTab,
  enabled,
}: {
  tab: "listings" | "outreach" | "context";
  setTab: (t: "listings" | "outreach" | "context") => void;
  enabled: boolean;
}) {
  return (
    <aside className="hidden w-80 flex-col border-l border-gray-200 lg:flex dark:border-gray-800">
      <div className="flex border-b border-gray-200 dark:border-gray-800">
        {(["listings", "outreach", "context"] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`flex-1 px-3 py-2 text-xs capitalize ${
              tab === t ? "border-b-2 border-black font-medium dark:border-white" : "text-gray-500"
            }`}
          >
            {t}
          </button>
        ))}
      </div>
      <div className="flex-1 overflow-y-auto p-3">
        {enabled && tab === "listings" && <ListingsPanel />}
        {enabled && tab === "outreach" && <OutreachPanel />}
        {enabled && tab === "context" && <ContextPanel />}
      </div>
    </aside>
  );
}

function OutreachPanel() {
  const qc = useQueryClient();
  const { data: campaigns = [] } = useQuery({
    queryKey: ["campaigns"],
    queryFn: listCampaigns,
    refetchInterval: 5000, // reflect status changes as the fan-out runs
  });
  const [busyId, setBusyId] = useState<number | null>(null);

  async function act(id: number, fn: (id: number) => Promise<unknown>) {
    setBusyId(id);
    try {
      await fn(id);
      await qc.invalidateQueries({ queryKey: ["campaigns"] });
      await qc.invalidateQueries({ queryKey: ["conversations"] });
    } finally {
      setBusyId(null);
    }
  }

  const badge: Record<string, string> = {
    awaiting_approval: "bg-amber-100 text-amber-800 dark:bg-amber-900 dark:text-amber-200",
    sending: "bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200",
    done: "bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200",
    cancelled: "bg-gray-200 text-gray-600 dark:bg-gray-800 dark:text-gray-400",
  };

  return (
    <div className="space-y-3">
      <p className="text-xs text-gray-500">
        Ask Polaris in chat to “reach out to the best buyers for listing #N”. Ranked batches land
        here for your approval — nothing sends until you approve.
      </p>
      {campaigns.length === 0 && <p className="text-gray-500">No outreach yet.</p>}
      {campaigns.map((c: OutreachCampaign) => {
        const pending = c.recipients.filter((r) => r.status === "pending").length;
        return (
          <div key={c.id} className="rounded border border-gray-200 p-2 dark:border-gray-800">
            <div className="flex items-center justify-between">
              <div className="truncate font-medium">
                {c.listing_address || `Listing #${c.listing}`}
              </div>
              <span className={`ml-2 shrink-0 rounded px-1.5 py-0.5 text-[10px] ${badge[c.status] || ""}`}>
                {c.status.replace(/_/g, " ")}
              </span>
            </div>
            <ul className="mt-2 space-y-1">
              {c.recipients.map((r) => (
                <li key={r.id} className="rounded bg-gray-100 px-2 py-1 text-xs dark:bg-gray-800">
                  <div className="flex items-center justify-between">
                    <span className="font-medium">
                      {r.name}{" "}
                      <span className="text-gray-400">
                        {r.kind === "prospect" ? "(prospect)" : ""}
                      </span>
                    </span>
                    <span className="text-gray-400">
                      {r.rank_score != null ? Number(r.rank_score).toFixed(2) : ""}
                      {r.status !== "pending" && r.status !== "sent" ? " · skipped" : ""}
                      {r.status === "sent" ? " · sent ✓" : ""}
                    </span>
                  </div>
                  {r.rank_reason && <div className="text-gray-500">{r.rank_reason}</div>}
                </li>
              ))}
            </ul>
            {c.status === "awaiting_approval" && (
              <div className="mt-2 flex gap-2">
                <button
                  disabled={busyId === c.id || pending === 0}
                  onClick={() => act(c.id, approveCampaign)}
                  className="flex-1 rounded bg-black px-2 py-1 text-xs text-white disabled:opacity-40 dark:bg-white dark:text-black"
                >
                  Approve &amp; send {pending}
                </button>
                <button
                  disabled={busyId === c.id}
                  onClick={() => act(c.id, cancelCampaign)}
                  className="rounded border border-gray-300 px-2 py-1 text-xs disabled:opacity-40 dark:border-gray-700"
                >
                  Cancel
                </button>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function ListingsPanel() {
  const { data: listings = [] } = useQuery({ queryKey: ["listings"], queryFn: listListings });
  const [valuations, setValuations] = useState<Record<number, Valuation | "loading">>({});

  async function value(id: number) {
    setValuations((v) => ({ ...v, [id]: "loading" }));
    const val = await getValuation(id);
    setValuations((v) => ({ ...v, [id]: val }));
  }

  return (
    <div className="space-y-3">
      {listings.length === 0 && <p className="text-gray-500">No listings yet.</p>}
      {listings.map((l) => {
        const val = valuations[l.id];
        return (
          <div key={l.id} className="rounded border border-gray-200 p-2 dark:border-gray-800">
            <div className="font-medium">{l.property?.address_raw || `Listing #${l.id}`}</div>
            <div className="text-xs text-gray-500">
              {l.property?.beds ?? "?"} bd · {l.property?.sqft ?? "?"} sqft · ask {fmt(l.asking_price)} · {l.status}
            </div>
            <button
              onClick={() => value(l.id)}
              className="mt-2 rounded border border-gray-300 px-2 py-1 text-xs dark:border-gray-700"
            >
              Value it
            </button>
            {val === "loading" && <p className="mt-2 text-xs text-gray-400">valuing…</p>}
            {val && val !== "loading" && (
              <div className="mt-2 text-xs">
                <div className="font-medium">
                  {fmt(val.low)} – <span className="text-green-700 dark:text-green-400">{fmt(val.point)}</span> – {fmt(val.high)}
                </div>
                <div className="mt-1 text-gray-500">
                  {String(val.basis?.n_comps ?? 0)} comps · {String(val.basis?.relaxed ?? "")}
                </div>
                <table className="mt-2 w-full border-collapse">
                  <thead>
                    <tr className="text-left text-gray-500">
                      <th className="py-0.5">Comp</th>
                      <th>Price</th>
                      <th>$/sf</th>
                      <th>mi</th>
                    </tr>
                  </thead>
                  <tbody>
                    {val.comps.slice(0, 5).map((c) => (
                      <tr key={c.id} className="border-t border-gray-200 dark:border-gray-800">
                        <td className="py-0.5">{c.beds}bd/{c.sqft}sf</td>
                        <td>{fmt(c.price)}</td>
                        <td>{c.ppsf ?? "—"}</td>
                        <td>{c.distance_mi ?? "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function ContextPanel() {
  const qc = useQueryClient();
  const { data: memory = [] } = useQuery({ queryKey: ["memory"], queryFn: listMemory });
  const { data: prefs = {} } = useQuery({ queryKey: ["preferences"], queryFn: getPreferences });
  const [note, setNote] = useState("");

  async function add() {
    if (!note.trim()) return;
    await addMemory(note.trim());
    setNote("");
    qc.invalidateQueries({ queryKey: ["memory"] });
  }

  return (
    <div className="space-y-4">
      <div>
        <div className="mb-1 font-medium">Agent memory</div>
        <p className="mb-2 text-xs text-gray-500">
          Shared with the agent — it reads/writes these same notes.
        </p>
        <div className="flex gap-1">
          <input
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Remember something…"
            className="flex-1 rounded border border-gray-300 px-2 py-1 text-xs dark:border-gray-700 dark:bg-gray-900"
          />
          <button onClick={add} className="rounded border border-gray-300 px-2 text-xs dark:border-gray-700">
            Add
          </button>
        </div>
        <ul className="mt-2 space-y-1">
          {memory.map((m) => (
            <li key={m.id} className="rounded bg-gray-100 px-2 py-1 text-xs dark:bg-gray-800">
              <span className="text-gray-400">[{m.namespace}]</span> {m.content}
            </li>
          ))}
          {memory.length === 0 && <li className="text-xs text-gray-500">No memories yet.</li>}
        </ul>
      </div>
      <div>
        <div className="mb-1 font-medium">Preferences</div>
        <pre className="overflow-x-auto rounded bg-gray-100 p-2 text-xs dark:bg-gray-800">
          {JSON.stringify(prefs, null, 2)}
        </pre>
      </div>
    </div>
  );
}
