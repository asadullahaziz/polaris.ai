"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  approveThreadDraft,
  fetchMe,
  getThreadMandate,
  getThreadMessages,
  listNotifications,
  listThreads,
  readAllNotifications,
  setThreadMandate,
  type ThreadListItem,
  type ThreadMandate,
  type ThreadMessage,
  WS_BASE,
} from "@/lib/api";

const ACTION_CHIP: Record<string, { label: string; cls: string }> = {
  qualify: { label: "Qualified interest", cls: "bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200" },
  hold: { label: "Holding for you", cls: "bg-amber-100 text-amber-800 dark:bg-amber-900 dark:text-amber-200" },
  decline: { label: "Passed", cls: "bg-gray-200 text-gray-700 dark:bg-gray-800 dark:text-gray-300" },
  ask: { label: "Asked for info", cls: "bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200" },
  inform: { label: "Answered", cls: "bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200" },
  escalate: { label: "Escalated", cls: "bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-200" },
};

function uuid(): string {
  if (typeof crypto !== "undefined" && crypto.randomUUID) return crypto.randomUUID();
  return `${Date.now()}-${Math.random()}`;
}

export default function InboxPage() {
  const router = useRouter();
  const qc = useQueryClient();
  const { data: me, isLoading } = useQuery({ queryKey: ["me"], queryFn: fetchMe });
  const { data: threads = [] } = useQuery({
    queryKey: ["threads"],
    queryFn: listThreads,
    enabled: !!me,
    refetchInterval: 5000, // reflect newly-opened threads + auto-replies landing
  });

  const [activeId, setActiveId] = useState<number | null>(null);
  const [messages, setMessages] = useState<ThreadMessage[]>([]);
  const [mySide, setMySide] = useState<"buyer" | "seller" | null>(null);
  const [counterpartyPresent, setCounterpartyPresent] = useState(false);
  const [connected, setConnected] = useState(false);
  const [input, setInput] = useState("");
  const wsRef = useRef<WebSocket | null>(null);
  const lastTypingRef = useRef(0);

  const active = threads.find((t) => t.id === activeId) || null;

  const openThread = useCallback(async (id: number) => {
    setActiveId(id);
    setMessages(await getThreadMessages(id));
  }, []);

  // One socket per open thread: connect = present; the counterparty's presence + any
  // agent/human message arrive live (architecture §4.2).
  useEffect(() => {
    if (!activeId || !me) return;
    let stopped = false;
    let retry: ReturnType<typeof setTimeout> | undefined;

    function connect() {
      const ws = new WebSocket(`${WS_BASE}/ws/thread/${activeId}/`);
      wsRef.current = ws;
      ws.onopen = () => setConnected(true);
      ws.onclose = () => {
        setConnected(false);
        if (!stopped) retry = setTimeout(connect, 1500);
      };
      ws.onmessage = (ev) => {
        const msg = JSON.parse(ev.data);
        const d = msg.data || {};
        if (msg.type === "thread.ready") setMySide(d.side);
        else if (msg.type === "presence") setCounterpartyPresent(!!d.present);
        else if (msg.type === "message.new") {
          setMessages((m) =>
            m.some((x) => x.id === d.id)
              ? m
              : [
                  ...m,
                  {
                    id: d.id,
                    author_type: d.author_type,
                    author_side: d.author_side ?? null,
                    action: d.action ?? null,
                    body: d.body,
                    status: "sent",
                    created_at: "",
                  },
                ],
          );
          qc.invalidateQueries({ queryKey: ["threads"] });
        }
      };
    }
    connect();

    // Tab focus/blur → presence (the agent stays silent while you're looking).
    const onVis = () => {
      const ws = wsRef.current;
      if (!ws || ws.readyState !== WebSocket.OPEN) return;
      ws.send(JSON.stringify({ type: document.hidden ? "thread.blur" : "thread.focus", data: {} }));
    };
    document.addEventListener("visibilitychange", onVis);

    return () => {
      stopped = true;
      if (retry) clearTimeout(retry);
      document.removeEventListener("visibilitychange", onVis);
      wsRef.current?.close();
      setConnected(false);
      setCounterpartyPresent(false);
    };
  }, [activeId, me, qc]);

  function send() {
    const body = input.trim();
    const ws = wsRef.current;
    if (!body || !ws || ws.readyState !== WebSocket.OPEN) return;
    ws.send(JSON.stringify({ type: "message.send", data: { body, client_dedup_uuid: uuid() } }));
    setInput("");
  }

  function onType(v: string) {
    setInput(v);
    const now = Date.now();
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN && now - lastTypingRef.current > 3000) {
      lastTypingRef.current = now;
      ws.send(JSON.stringify({ type: "typing", data: {} }));
    }
  }

  async function approve(messageId: number) {
    if (!activeId) return;
    await approveThreadDraft(activeId, messageId);
    setMessages(await getThreadMessages(activeId));
  }

  if (isLoading) return <main className="p-8">Loading…</main>;
  if (!me)
    return (
      <main className="mx-auto max-w-sm p-8">
        <p className="mb-4">Please log in to view your threads.</p>
        <Link href="/login" className="rounded bg-black px-4 py-2 text-white dark:bg-white dark:text-black">
          Log in
        </Link>
      </main>
    );

  return (
    <div className="flex h-screen text-sm">
      {/* Thread list */}
      <aside className="flex w-72 flex-col border-r border-gray-200 dark:border-gray-800">
        <div className="flex items-center justify-between p-3">
          <span className="font-semibold">Inbox</span>
          <div className="flex items-center gap-2">
            <NotificationsBell enabled={!!me} onOpen={(cid) => cid && openThread(cid)} />
            <Link href="/copilot" className="text-xs underline text-gray-500">
              Copilot →
            </Link>
          </div>
        </div>
        <div className="flex-1 overflow-y-auto px-2">
          {threads.length === 0 && (
            <p className="px-2 py-4 text-gray-500">
              No threads yet. Launch outreach from the copilot to open some.
            </p>
          )}
          {threads.map((t) => (
            <button
              key={t.id}
              onClick={() => openThread(t.id)}
              className={`mb-1 block w-full rounded px-2 py-2 text-left ${
                t.id === activeId ? "bg-gray-200 dark:bg-gray-800" : "hover:bg-gray-100 dark:hover:bg-gray-900"
              }`}
            >
              <div className="flex items-center justify-between">
                <span className="truncate font-medium">{t.counterparty_name}</span>
                <SideBadge side={t.my_side} />
              </div>
              <div className="truncate text-xs text-gray-500">
                {t.listing_address || `Listing #${t.listing_id}`}
              </div>
              {t.last_message && (
                <div className="mt-0.5 truncate text-xs text-gray-400">
                  {t.last_message.author_type === "agent" ? "🤖 " : ""}
                  {t.last_message.body}
                </div>
              )}
              {t.terminal && (
                <span className="text-[10px] uppercase tracking-wide text-gray-400">{t.terminal}</span>
              )}
            </button>
          ))}
        </div>
        <div className="border-t border-gray-200 p-3 text-xs text-gray-500 dark:border-gray-800">
          <div className="mb-1">{me.full_name || me.username}</div>
          <Link href="/" className="underline">
            Home
          </Link>
        </div>
      </aside>

      {/* Thread view */}
      <main className="flex flex-1 flex-col">
        {!active ? (
          <div className="m-auto text-gray-500">Select a thread.</div>
        ) : (
          <>
            <ThreadHeader
              thread={active}
              connected={connected}
              counterpartyPresent={counterpartyPresent}
            />
            <div className="flex-1 overflow-y-auto">
              <div className="mx-auto max-w-3xl space-y-3 p-6">
                {messages.map((m) => (
                  <MessageRow
                    key={m.id}
                    m={m}
                    mySide={mySide || active.my_side}
                    counterpartyName={active.counterparty_name}
                    onApprove={approve}
                  />
                ))}
                {messages.length === 0 && (
                  <p className="text-center text-gray-500">No messages yet.</p>
                )}
              </div>
            </div>
            <div className="border-t border-gray-200 p-4 dark:border-gray-800">
              <div className="mx-auto flex max-w-3xl gap-2">
                <textarea
                  value={input}
                  onChange={(e) => onType(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      send();
                    }
                  }}
                  rows={2}
                  placeholder="Reply… (opening this thread pauses your Polaris — you've taken over)"
                  className="flex-1 resize-none rounded border border-gray-300 px-3 py-2 dark:border-gray-700 dark:bg-gray-900"
                />
                <button
                  onClick={send}
                  className="rounded bg-black px-4 text-white dark:bg-white dark:text-black"
                >
                  Send
                </button>
              </div>
            </div>
          </>
        )}
      </main>
    </div>
  );
}

function SideBadge({ side }: { side: "buyer" | "seller" }) {
  return (
    <span
      className={`ml-2 shrink-0 rounded px-1.5 py-0.5 text-[10px] ${
        side === "seller"
          ? "bg-purple-100 text-purple-800 dark:bg-purple-900 dark:text-purple-200"
          : "bg-teal-100 text-teal-800 dark:bg-teal-900 dark:text-teal-200"
      }`}
    >
      {side}
    </span>
  );
}

function ThreadHeader({
  thread,
  connected,
  counterpartyPresent,
}: {
  thread: ThreadListItem;
  connected: boolean;
  counterpartyPresent: boolean;
}) {
  const qc = useQueryClient();
  const { data: mandate } = useQuery<ThreadMandate>({
    queryKey: ["thread-mandate", thread.id],
    queryFn: () => getThreadMandate(thread.id),
  });

  async function patch(body: Partial<ThreadMandate>) {
    await setThreadMandate(thread.id, body);
    qc.invalidateQueries({ queryKey: ["thread-mandate", thread.id] });
  }

  return (
    <div className="border-b border-gray-200 px-6 py-3 dark:border-gray-800">
      <div className="flex items-center justify-between">
        <div>
          <div className="font-medium">
            {thread.counterparty_name} <SideBadge side={thread.my_side} />
          </div>
          <div className="text-xs text-gray-500">
            {thread.listing_address || `Listing #${thread.listing_id}`} · {thread.status}
            {thread.terminal ? ` · ${thread.terminal}` : ""}
          </div>
        </div>
        <div className="text-right text-xs">
          <div className={counterpartyPresent ? "text-green-600" : "text-gray-400"}>
            {counterpartyPresent ? `● ${thread.counterparty_name} is here` : `○ ${thread.counterparty_name} away`}
          </div>
          <div className={connected ? "text-green-600" : "text-gray-400"}>
            {connected ? "● connected" : "○ connecting…"}
          </div>
        </div>
      </div>
      {mandate?.has_mandate && (
        <div className="mt-2 flex flex-wrap items-center gap-3 text-xs">
          <label className="flex items-center gap-1">
            <input
              type="checkbox"
              checked={!!mandate.auto_reply}
              onChange={(e) => patch({ auto_reply: e.target.checked })}
            />
            Auto-reply when I&apos;m away
          </label>
          <label className="flex items-center gap-1">
            Autonomy:
            <select
              value={mandate.autonomy || "confirm_batch"}
              onChange={(e) => patch({ autonomy: e.target.value })}
              className="rounded border border-gray-300 bg-transparent px-1 py-0.5 dark:border-gray-700"
            >
              <option value="auto_with_policy">auto (send within policy)</option>
              <option value="confirm_batch">confirm (draft for me)</option>
              <option value="assist">assist (draft for me)</option>
            </select>
          </label>
          <span className="text-gray-400">
            {mandate.autonomy === "auto_with_policy"
              ? "Polaris sends one reply, then pauses."
              : "Polaris drafts a reply for your approval."}
          </span>
        </div>
      )}
    </div>
  );
}

function MessageRow({
  m,
  mySide,
  counterpartyName,
  onApprove,
}: {
  m: ThreadMessage;
  mySide: "buyer" | "seller";
  counterpartyName: string;
  onApprove: (id: number) => void;
}) {
  const mine = m.author_side === mySide;
  const isAgent = m.author_type === "agent";
  const label =
    m.author_type === "system"
      ? "System"
      : isAgent
        ? mine
          ? "Polaris · on your behalf"
          : `Polaris · ${counterpartyName}'s agent`
        : mine
          ? "You"
          : counterpartyName;
  const chip = m.action ? ACTION_CHIP[m.action] : undefined;

  return (
    <div className={`flex ${mine ? "justify-end" : "justify-start"}`}>
      <div className={`max-w-[80%] ${mine ? "text-right" : "text-left"}`}>
        <div className="mb-0.5 flex items-center gap-1 text-[11px] text-gray-500">
          {isAgent && <span>🤖</span>}
          <span>{label}</span>
          {chip && <span className={`rounded px-1.5 py-0.5 ${chip.cls}`}>{chip.label}</span>}
          {m.status === "draft" && (
            <span className="rounded bg-amber-100 px-1.5 py-0.5 text-amber-800 dark:bg-amber-900 dark:text-amber-200">
              DRAFT
            </span>
          )}
        </div>
        <div
          className={`inline-block whitespace-pre-wrap rounded-2xl px-4 py-2 ${
            mine
              ? "bg-black text-white dark:bg-white dark:text-black"
              : "bg-gray-100 dark:bg-gray-800"
          } ${isAgent ? "ring-1 ring-blue-300 dark:ring-blue-700" : ""}`}
        >
          {m.body}
        </div>
        {m.status === "draft" && (
          <div className="mt-1">
            <button
              onClick={() => onApprove(m.id)}
              className="rounded bg-blue-600 px-2 py-1 text-xs text-white"
            >
              Approve &amp; send
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

function NotificationsBell({
  enabled,
  onOpen,
}: {
  enabled: boolean;
  onOpen: (conversationId: number | null) => void;
}) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const { data: notes = [] } = useQuery({
    queryKey: ["notifications"],
    queryFn: listNotifications,
    enabled,
    refetchInterval: 8000,
  });
  const unread = notes.filter((n) => !n.read_at).length;

  async function markAll() {
    await readAllNotifications();
    qc.invalidateQueries({ queryKey: ["notifications"] });
  }

  return (
    <div className="relative">
      <button onClick={() => setOpen((o) => !o)} className="text-base" title="Notifications">
        🔔
        {unread > 0 && (
          <span className="absolute -right-1 -top-1 rounded-full bg-red-600 px-1 text-[9px] text-white">
            {unread}
          </span>
        )}
      </button>
      {open && (
        <div className="absolute right-0 z-10 mt-1 max-h-80 w-72 overflow-y-auto rounded border border-gray-200 bg-white p-2 shadow-lg dark:border-gray-800 dark:bg-gray-950">
          <div className="mb-1 flex items-center justify-between">
            <span className="font-medium">Notifications</span>
            <button onClick={markAll} className="text-xs underline text-gray-500">
              Mark all read
            </button>
          </div>
          {notes.length === 0 && <p className="text-xs text-gray-500">Nothing yet.</p>}
          {notes.map((n) => (
            <button
              key={n.id}
              onClick={() => {
                onOpen(n.conversation);
                setOpen(false);
              }}
              className={`block w-full rounded px-2 py-1 text-left text-xs ${
                n.read_at ? "text-gray-400" : "bg-gray-100 dark:bg-gray-800"
              }`}
            >
              <span className="font-medium">{n.type.replace(/_/g, " ")}</span>
              {typeof n.payload?.note === "string" && <span> — {n.payload.note as string}</span>}
              {typeof n.payload?.reason === "string" && <span> — {n.payload.reason as string}</span>}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
