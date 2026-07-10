"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ChevronRight,
  Loader2,
  Megaphone,
  Plus,
  Sparkles,
  Trash2,
  Wrench,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { ConfirmCard, type ConfirmPayload } from "@/components/confirm-card";
import { Markdown } from "@/components/markdown";
import { OutreachRail } from "@/components/outreach-rail";
import { Button } from "@/components/ui/button";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Textarea } from "@/components/ui/textarea";
import {
  deleteAiChat,
  getAiChat,
  listAiChats,
  WS_BASE,
} from "@/lib/api";
import { useMe } from "@/lib/hooks";
import { cn } from "@/lib/utils";

// One local message list per open session: REST transcript rows + live-turn artifacts
// (streamed segments, tool-activity chips, confirm cards). On `copilot.done` the list
// is replaced by the canonical transcript (block rows persisted by the backend).
type LocalMsg =
  | { type: "chat"; id: number | string; role: string; content: string }
  | {
      type: "tool";
      id: number | string;
      name: string;
      label: string;
      status: "running" | "done";
      result?: string;
    }
  | {
      type: "confirm";
      id: string;
      payload: ConfirmPayload;
      resolution?: "approved" | "declined" | "expired";
    };

const SUGGESTIONS = [
  "List my listings and how long each has been on the market",
  "Value listing #1 and suggest an asking price",
  "Create a listing for 123 Main St — 3bd, 2ba, 1800 sqft",
  "Reach out to the best buyers for one of my listings",
];

export default function PolarisAiPage() {
  const qc = useQueryClient();
  const { data: me } = useMe();
  const { data: chats = [] } = useQuery({ queryKey: ["ai-chats"], queryFn: listAiChats });

  const [activeId, setActiveId] = useState<number | null>(null);
  const [messages, setMessages] = useState<LocalMsg[]>([]);
  const [streaming, setStreaming] = useState("");
  const [busy, setBusy] = useState(false);
  const [connected, setConnected] = useState(false);
  const [pendingConfirm, setPendingConfirm] = useState(false);
  const [input, setInput] = useState("");
  const [tick, setTick] = useState("");

  const wsRef = useRef<WebSocket | null>(null);
  const bufRef = useRef("");
  const scrollRef = useRef<HTMLDivElement | null>(null);
  // WS handlers close over the first render — route async events via refs.
  const activeIdRef = useRef<number | null>(null);
  useEffect(() => {
    activeIdRef.current = activeId;
  }, [activeId]);

  // One socket per session; auto-reconnect (dev reloads + network blips drop it).
  useEffect(() => {
    if (!me) return;
    let stopped = false;
    let retry: ReturnType<typeof setTimeout> | undefined;

    function connect() {
      const ws = new WebSocket(`${WS_BASE}/ws/copilot/`);
      wsRef.current = ws;
      ws.onopen = () => setConnected(true);
      ws.onclose = (ev) => {
        setConnected(false);
        setBusy(false); // a turn in flight is lost on drop — let the user retry
        setPendingConfirm(false);
        // 4401 = unauthenticated — reconnecting won't help.
        if (!stopped && ev.code !== 4401) retry = setTimeout(connect, 1500);
      };
      ws.onmessage = (ev) => {
        const msg = JSON.parse(ev.data);
        const d = msg.data || {};
        if (msg.type === "copilot.created") {
          setActiveId(d.conversation_id);
          qc.invalidateQueries({ queryKey: ["ai-chats"] });
        } else if (msg.type === "copilot.token") {
          // Drop a leading segment separator — a fresh bubble never opens with blank lines.
          if (!bufRef.current && !String(d.token).trim()) return;
          bufRef.current += d.token;
          setStreaming(bufRef.current);
        } else if (msg.type === "copilot.tool") {
          if (d.status === "start") {
            // A tool is starting: flush the streamed segment into its own bubble, then
            // show the activity chip ("Ranking buyers…") beneath it.
            if (bufRef.current) {
              const body = bufRef.current;
              bufRef.current = "";
              setStreaming("");
              setMessages((m) => [
                ...m,
                { type: "chat", id: `seg-${Date.now()}-${m.length}`, role: "assistant", content: body },
              ]);
            }
            setMessages((m) => [
              ...m,
              {
                type: "tool",
                id: `tool-${Date.now()}-${m.length}`,
                name: String(d.name || ""),
                label: String(d.label || "Working…"),
                status: "running",
              },
            ]);
          } else {
            // Tool finished — settle the most recent matching running chip.
            setMessages((m) => {
              let target = -1;
              m.forEach((x, i) => {
                if (x.type === "tool" && x.status === "running" && (!d.name || x.name === d.name))
                  target = i;
              });
              if (target < 0) return m;
              return m.map((x, i) => (i === target ? { ...x, status: "done" as const } : x));
            });
          }
        } else if (msg.type === "copilot.confirm") {
          // Flush any streamed preamble, then park the turn on the confirm card.
          if (bufRef.current) {
            const body = bufRef.current;
            bufRef.current = "";
            setStreaming("");
            setMessages((m) => [
              ...m,
              { type: "chat", id: `pre-${Date.now()}`, role: "assistant", content: body },
            ]);
          }
          setPendingConfirm(true);
          setMessages((m) => [
            ...m,
            { type: "confirm", id: `confirm-${Date.now()}`, payload: d.value as ConfirmPayload },
          ]);
        } else if (msg.type === "copilot.done") {
          const body = bufRef.current;
          bufRef.current = "";
          setStreaming("");
          setBusy(false);
          setPendingConfirm(false);
          qc.invalidateQueries({ queryKey: ["ai-chats"] });
          qc.invalidateQueries({ queryKey: ["campaigns"] });
          // Replace the live-turn artifacts with the canonical block transcript
          // (segments + tool chips with results) — unless the user navigated away.
          if (d.conversation_id != null && d.conversation_id === activeIdRef.current) {
            void openChat(d.conversation_id);
          } else if (body) {
            setMessages((m) => [
              ...m,
              { type: "chat", id: d.message_id ?? `done-${Date.now()}`, role: "assistant", content: body },
            ]);
          }
        } else if (msg.type === "copilot.error") {
          bufRef.current = "";
          setStreaming("");
          setBusy(false);
          setPendingConfirm(false);
          setMessages((m) => [
            ...m,
            { type: "chat", id: `err-${Date.now()}`, role: "system", content: `⚠️ ${d.detail}` },
          ]);
        } else if (msg.type === "outreach.progress") {
          // Templated fan-out tick — transient status in the launching chat.
          if (d.ai_chat_id == null || d.ai_chat_id === activeIdRef.current) {
            setTick(d.text || "");
            if (d.done) setTimeout(() => setTick(""), 4000);
          }
          qc.invalidateQueries({ queryKey: ["campaigns"] });
        } else if (msg.type === "outreach.summary") {
          setTick("");
          qc.invalidateQueries({ queryKey: ["campaigns"] });
          if (d.body && (d.ai_chat_id == null || d.ai_chat_id === activeIdRef.current)) {
            setMessages((m) => [
              ...m,
              { type: "chat", id: d.message_id ?? `sum-${Date.now()}`, role: "assistant", content: d.body },
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
  }, [messages, streaming, tick]);

  async function openChat(id: number) {
    setActiveId(id);
    setStreaming("");
    setPendingConfirm(false);
    const chat = await getAiChat(id);
    const msgs: LocalMsg[] = chat.messages.flatMap((m): LocalMsg[] => {
      const tc = m.tool_calls;
      // A resolved/expired confirm is persisted as a role='tool' row carrying the card
      // payload + resolution — re-render it as a greyed, non-interactive card in place.
      if (m.role === "tool" && tc && tc.kind === "confirm_write") {
        const { resolution, ...payload } = tc;
        return [
          {
            type: "confirm",
            id: `outcome-${m.id}`,
            payload: payload as unknown as ConfirmPayload,
            resolution: resolution as "approved" | "declined" | "expired",
          },
        ];
      }
      // A persisted tool result → the same activity chip the live turn showed, with
      // the raw result available on demand.
      if (m.role === "tool" && tc && tc.kind === "tool_result") {
        return [
          {
            type: "tool",
            id: `chip-${m.id}`,
            name: String(tc.name || ""),
            label: String(tc.label || "Tool"),
            status: "done",
            result: m.content,
          },
        ];
      }
      // Call-only assistant rows carry no text (their chips follow as tool rows).
      if (m.role === "assistant" && !m.content) return [];
      return [{ type: "chat", id: m.id, role: m.role, content: m.content }];
    });
    // Restore a still-pending write-confirm (survives nav/reload) so it's actionable again.
    if (chat.pending_confirm) {
      msgs.push({
        type: "confirm",
        id: `confirm-${id}`,
        payload: chat.pending_confirm as unknown as ConfirmPayload,
      });
      setPendingConfirm(true);
    }
    setMessages(msgs);
  }

  function newChat() {
    setActiveId(null);
    setMessages([]);
    setStreaming("");
    setPendingConfirm(false);
  }

  async function removeChat(id: number) {
    await deleteAiChat(id);
    if (activeId === id) newChat();
    qc.invalidateQueries({ queryKey: ["ai-chats"] });
  }

  function send(text?: string) {
    const body = (text ?? input).trim();
    const ws = wsRef.current;
    if (!body || busy || !ws || ws.readyState !== WebSocket.OPEN) return;
    setMessages((m) => [...m, { type: "chat", id: `u-${Date.now()}`, role: "user", content: body }]);
    setInput("");
    setBusy(true);
    bufRef.current = "";
    ws.send(JSON.stringify({ type: "copilot.send", data: { conversation_id: activeId, body } }));
  }

  function respondConfirm(id: string, approved: boolean) {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    setMessages((m) =>
      m.map((x) =>
        x.type === "confirm" && x.id === id
          ? { ...x, resolution: approved ? "approved" : "declined" }
          : x,
      ),
    );
    setPendingConfirm(false);
    // Include conversation_id so the backend can rehydrate the parked turn on a fresh
    // socket (after a nav/reload the in-memory pending state is gone).
    ws.send(
      JSON.stringify({
        type: "copilot.confirm_response",
        data: { approved, conversation_id: activeId },
      }),
    );
  }

  return (
    <div className="flex h-full">
      {/* Session list */}
      <aside className="hidden w-60 flex-col border-r md:flex">
        <div className="flex h-11 shrink-0 items-center justify-between border-b px-3">
          <span className="text-sm font-medium">Conversations</span>
          <Button variant="ghost" size="icon" className="size-7" onClick={newChat} aria-label="New chat">
            <Plus className="size-4" />
          </Button>
        </div>
        <ScrollArea className="min-h-0 flex-1">
          <div className="p-2">
            {chats.map((c) => (
              <div
                key={c.id}
                className={cn(
                  "group flex items-center gap-1 rounded-md px-2 py-1.5 text-sm",
                  c.id === activeId ? "bg-accent" : "hover:bg-accent/50",
                )}
              >
                <button className="min-w-0 flex-1 truncate text-left" onClick={() => openChat(c.id)}>
                  {c.title || "Untitled chat"}
                </button>
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-6 opacity-0 group-hover:opacity-100"
                  onClick={() => removeChat(c.id)}
                  aria-label="Delete chat"
                >
                  <Trash2 className="size-3.5" />
                </Button>
              </div>
            ))}
            {chats.length === 0 && (
              <p className="px-2 py-4 text-sm text-muted-foreground">No chats yet.</p>
            )}
          </div>
        </ScrollArea>
        <div className="border-t p-2 text-xs text-muted-foreground">
          <span className={connected ? "text-green-600" : ""}>
            {connected ? "● connected" : "○ connecting…"}
          </span>
        </div>
      </aside>

      {/* Chat */}
      <main className="flex min-w-0 flex-1 flex-col">
        <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto">
          <div className="mx-auto max-w-3xl p-6">
            {messages.length === 0 && !streaming && (
              <div className="mt-16 text-center">
                <Sparkles className="mx-auto size-8 text-muted-foreground" />
                <p className="mt-3 text-lg font-medium">Your AI real-estate agent</p>
                <p className="mx-auto mt-1 max-w-md text-sm text-muted-foreground">
                  Polaris can manage listings, value property against real comps, find and
                  message buyers, and run outreach — it asks before it writes anything.
                </p>
                <div className="mx-auto mt-5 grid max-w-md gap-2">
                  {SUGGESTIONS.map((s) => (
                    <button
                      key={s}
                      onClick={() => send(s)}
                      className="flex items-center gap-2 rounded-lg border px-3 py-2 text-left text-sm hover:bg-accent"
                    >
                      <ChevronRight className="size-3.5 shrink-0 text-muted-foreground" />
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {messages.map((m) =>
              m.type === "confirm" ? (
                <ConfirmCard
                  key={m.id}
                  payload={m.payload}
                  resolution={m.resolution}
                  onRespond={(approved) => respondConfirm(m.id, approved)}
                />
              ) : m.type === "tool" ? (
                <ToolChip key={m.id} label={m.label} status={m.status} result={m.result} />
              ) : (
                <Bubble key={m.id} role={m.role} body={m.content} />
              ),
            )}
            {streaming && <Bubble role="assistant" body={streaming} streaming />}
            {busy &&
              !streaming &&
              !pendingConfirm &&
              !messages.some((m) => m.type === "tool" && m.status === "running") && (
                <p className="my-3 flex items-center gap-2 text-sm text-muted-foreground">
                  <Loader2 className="size-3.5 animate-spin" /> Polaris is thinking…
                </p>
              )}
            {tick && (
              <p className="my-3 flex items-center justify-center gap-1.5 text-center text-xs text-primary">
                <Megaphone className="size-3.5" /> {tick}
              </p>
            )}
          </div>
        </div>
        <div className="shrink-0 border-t p-4">
          <div className="mx-auto flex max-w-3xl gap-2">
            <Textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  send();
                }
              }}
              rows={2}
              disabled={pendingConfirm}
              placeholder={
                pendingConfirm
                  ? "Respond to the confirmation above to continue…"
                  : "Message Polaris…  (Enter to send, Shift+Enter for newline)"
              }
              className="flex-1 resize-none"
            />
            <Button onClick={() => send()} disabled={busy || pendingConfirm} className="h-auto">
              Send
            </Button>
          </div>
        </div>
      </main>

      {/* Outreach right rail */}
      <OutreachRail className="hidden xl:flex" />
    </div>
  );
}

function Bubble({ role, body, streaming }: { role: string; body: string; streaming?: boolean }) {
  if (role === "user")
    return (
      <div className="my-3 flex justify-end">
        <div className="max-w-[80%] whitespace-pre-wrap rounded-2xl bg-primary px-4 py-2 text-primary-foreground">
          {body}
        </div>
      </div>
    );
  if (role === "system")
    return (
      <div className="my-3 text-center text-xs text-amber-600 dark:text-amber-500">{body}</div>
    );
  if (role === "tool") return <ToolResult body={body} />;
  return (
    <div className="my-3">
      <div className="mb-1 flex items-center gap-1 text-xs font-medium text-muted-foreground">
        <Sparkles className="size-3" /> Polaris
      </div>
      <div className="max-w-none text-sm">
        <Markdown>{body || "…"}</Markdown>
        {streaming && <span className="ml-0.5 animate-pulse">▍</span>}
      </div>
    </div>
  );
}

// Tool-activity chip: "Ranking buyers…" with a spinner while running; once done, the
// raw result opens on demand. The label comes from the backend (live event + persisted
// row), so live and rehydrated turns render identically.
function ToolChip({
  label,
  status,
  result,
}: {
  label: string;
  status: "running" | "done";
  result?: string;
}) {
  const settled = label.replace(/…$/, "");
  if (status === "running" || !result) {
    return (
      <div className="my-2 flex items-center gap-1.5 text-xs text-muted-foreground">
        {status === "running" ? (
          <Loader2 className="size-3 animate-spin" />
        ) : (
          <Wrench className="size-3" />
        )}
        {status === "running" ? label : settled}
      </div>
    );
  }
  return (
    <Collapsible className="my-2">
      <CollapsibleTrigger className="flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs text-muted-foreground hover:bg-accent">
        <Wrench className="size-3" /> {settled}
        <ChevronRight className="size-3 transition-transform [[data-state=open]>&]:rotate-90" />
      </CollapsibleTrigger>
      <CollapsibleContent>
        <pre className="mt-1 max-h-48 overflow-auto rounded-md bg-muted p-2 text-xs">{result}</pre>
      </CollapsibleContent>
    </Collapsible>
  );
}

// Transcript tool rows (legacy shape) — collapsed, raw payload on demand.
function ToolResult({ body }: { body: string }) {
  return (
    <Collapsible className="my-2">
      <CollapsibleTrigger className="flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs text-muted-foreground hover:bg-accent">
        <Wrench className="size-3" /> Tool result
        <ChevronRight className="size-3 transition-transform [[data-state=open]>&]:rotate-90" />
      </CollapsibleTrigger>
      <CollapsibleContent>
        <pre className="mt-1 max-h-48 overflow-auto rounded-md bg-muted p-2 text-xs">{body}</pre>
      </CollapsibleContent>
    </Collapsible>
  );
}
