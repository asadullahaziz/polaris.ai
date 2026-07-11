"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Bot,
  Building2,
  Check,
  MessageSquare,
  Paperclip,
  Pencil,
  Trash2,
  X,
} from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { ListingAttachmentCard } from "@/components/listing-attachment-card";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Textarea } from "@/components/ui/textarea";
import {
  approveChatDraft,
  discardChatDraft,
  getChatMessages,
  listChats,
  listListings,
  markChatRead,
  type ChatMessage,
  type ChatRow,
  WS_BASE,
} from "@/lib/api";
import { fmtDateTime, initials, useMe, uuid } from "@/lib/hooks";
import { cn } from "@/lib/utils";

const ACTION_CHIP: Record<string, { label: string; cls: string }> = {
  qualify: { label: "Qualified interest", cls: "bg-green-100 text-green-800 dark:bg-green-950 dark:text-green-300" },
  hold: { label: "Holding for you", cls: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300" },
  decline: { label: "Passed", cls: "bg-muted text-muted-foreground" },
  ask: { label: "Asked for info", cls: "bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300" },
  inform: { label: "Answered", cls: "bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300" },
  escalate: { label: "Escalated", cls: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300" },
  propose: { label: "Made an offer", cls: "bg-purple-100 text-purple-800 dark:bg-purple-950 dark:text-purple-300" },
  counter: { label: "Countered", cls: "bg-purple-100 text-purple-800 dark:bg-purple-950 dark:text-purple-300" },
  accept: { label: "Accepted", cls: "bg-green-100 text-green-800 dark:bg-green-950 dark:text-green-300" },
};

function ChatPageInner() {
  const router = useRouter();
  const qc = useQueryClient();
  const { data: me } = useMe();
  const initialChat = useSearchParams().get("chat");

  const { data: chats = [] } = useQuery({
    queryKey: ["chats"],
    queryFn: listChats,
    refetchInterval: 5000, // newly-opened chats + away-agent replies land live-ish
  });

  const [activeId, setActiveId] = useState<number | null>(
    initialChat ? Number(initialChat) : null,
  );
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [connected, setConnected] = useState(false);
  const [counterpartyPresent, setCounterpartyPresent] = useState(false);
  const [input, setInput] = useState("");
  const [attachIds, setAttachIds] = useState<number[]>([]);

  const wsRef = useRef<WebSocket | null>(null);
  const lastTypingRef = useRef(0);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  const active = chats.find((c) => c.id === activeId) || null;

  const openChat = useCallback(
    async (id: number) => {
      setActiveId(id);
      setInput("");
      setAttachIds([]);
      router.replace(`/chat?chat=${id}`, { scroll: false });
      setMessages(await getChatMessages(id));
      await markChatRead(id);
      qc.invalidateQueries({ queryKey: ["chats"] });
    },
    [qc, router],
  );

  // Follow ?chat= (e.g. from a notification click or /buyers) into the pane.
  useEffect(() => {
    const id = initialChat ? Number(initialChat) : null;
    if (id && id !== activeId) void openChat(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialChat]);

  // One socket per open chat: connect = present (your away-agent stands down while
  // you're looking); the counterparty's presence + any human/agent message arrive live.
  useEffect(() => {
    if (!activeId || !me) return;
    let stopped = false;
    let retry: ReturnType<typeof setTimeout> | undefined;

    function connect() {
      const ws = new WebSocket(`${WS_BASE}/ws/chat/${activeId}/`);
      wsRef.current = ws;
      ws.onopen = () => setConnected(true);
      ws.onclose = (ev) => {
        setConnected(false);
        // 4400/4401/4403 = bad chat / unauthenticated / not a member — don't retry.
        if (!stopped && ev.code < 4400) retry = setTimeout(connect, 1500);
      };
      ws.onmessage = (ev) => {
        const msg = JSON.parse(ev.data);
        const d = msg.data || {};
        if (msg.type === "presence") setCounterpartyPresent(!!d.present);
        else if (msg.type === "message.new") {
          setMessages((m) => (m.some((x) => x.id === d.id) ? m : [...m, d as ChatMessage]));
          void markChatRead(activeId!);
          qc.invalidateQueries({ queryKey: ["chats"] });
        }
      };
    }
    connect();

    // Tab focus/blur → presence (focus also cancels the away-agent's grace window).
    const onVis = () => {
      const ws = wsRef.current;
      if (!ws || ws.readyState !== WebSocket.OPEN) return;
      ws.send(JSON.stringify({ type: document.hidden ? "chat.blur" : "chat.focus", data: {} }));
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

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages]);

  function send() {
    const body = input.trim();
    const ws = wsRef.current;
    if ((!body && attachIds.length === 0) || !ws || ws.readyState !== WebSocket.OPEN) return;
    ws.send(
      JSON.stringify({
        type: "message.send",
        data: { body, attachment_listing_ids: attachIds, client_dedup_uuid: uuid() },
      }),
    );
    setInput("");
    setAttachIds([]);
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
    try {
      await approveChatDraft(activeId, messageId);
      setMessages(await getChatMessages(activeId));
      qc.invalidateQueries({ queryKey: ["deals"] }); // approved accept/decline moves the deal
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Approve failed");
    }
  }

  async function discard(messageId: number) {
    if (!activeId) return;
    await discardChatDraft(activeId, messageId);
    setMessages(await getChatMessages(activeId));
  }

  async function editAndSend(m: ChatMessage) {
    // Take over: the draft body moves into the composer as YOUR message; the draft dies.
    if (!activeId) return;
    setInput(m.body);
    await discardChatDraft(activeId, m.id);
    setMessages(await getChatMessages(activeId));
  }

  if (!me) return null;

  return (
    <div className="flex h-full">
      {/* Chat list */}
      <aside
        className={cn(
          "w-full shrink-0 flex-col border-r md:flex md:w-72",
          activeId ? "hidden" : "flex",
        )}
      >
        <div className="flex h-11 shrink-0 items-center border-b px-3 text-sm font-medium">
          Chats
        </div>
        <ScrollArea className="min-h-0 flex-1">
          <div className="p-2">
            {chats.length === 0 && (
              <p className="px-2 py-6 text-center text-sm text-muted-foreground">
                No conversations yet. Find buyers on the Buyers page, message a seller
                from any listing, or let outreach open them for you.
              </p>
            )}
            {chats.map((c) => (
              <ChatListRow key={c.id} c={c} active={c.id === activeId} onOpen={openChat} />
            ))}
          </div>
        </ScrollArea>
      </aside>

      {/* Active pane */}
      <main className={cn("min-w-0 flex-1 flex-col md:flex", activeId ? "flex" : "hidden")}>
        {!active ? (
          <div className="m-auto flex flex-col items-center gap-2 text-muted-foreground">
            <MessageSquare className="size-8" />
            <p className="text-sm">Select a conversation.</p>
          </div>
        ) : (
          <>
            <ChatHeader
              chat={active}
              connected={connected}
              counterpartyPresent={counterpartyPresent}
              autoReplyOn={me.profile.auto_reply_when_away}
              onBack={() => {
                setActiveId(null);
                router.replace("/chat", { scroll: false });
              }}
            />
            <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto">
              <div className="mx-auto max-w-3xl space-y-1 p-4 md:p-6">
                {messages.map((m) => (
                  <MessageRow
                    key={m.id}
                    m={m}
                    myId={me.id}
                    counterpartyName={active.counterparty?.name || "Counterparty"}
                    onApprove={approve}
                    onDiscard={discard}
                    onEditAndSend={editAndSend}
                  />
                ))}
                {messages.length === 0 && (
                  <p className="py-8 text-center text-sm text-muted-foreground">
                    No messages yet — say hello.
                  </p>
                )}
              </div>
            </div>
            <Composer
              input={input}
              onType={onType}
              onSend={send}
              connected={connected}
              attachIds={attachIds}
              setAttachIds={setAttachIds}
            />
          </>
        )}
      </main>
    </div>
  );
}

function ChatListRow({
  c,
  active,
  onOpen,
}: {
  c: ChatRow;
  active: boolean;
  onOpen: (id: number) => void;
}) {
  const name = c.counterparty?.name || "Unknown";
  return (
    <button
      onClick={() => onOpen(c.id)}
      className={cn(
        "mb-1 flex w-full items-start gap-2.5 rounded-md px-2 py-2 text-left",
        active ? "bg-accent" : "hover:bg-accent/50",
      )}
    >
      <Avatar className="size-8">
        <AvatarImage src={c.counterparty?.avatar_url || undefined} alt={name} />
        <AvatarFallback>{initials(name)}</AvatarFallback>
      </Avatar>
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-1.5">
          <span className={cn("truncate text-sm", c.unread ? "font-semibold" : "font-medium")}>
            {name}
          </span>
          {c.unread && <span className="size-2 shrink-0 rounded-full bg-primary" />}
          {c.terminal && (
            <Badge variant="outline" className="ml-auto shrink-0 text-[10px]">
              {c.terminal.replace(/_/g, " ")}
            </Badge>
          )}
        </span>
        {c.last_message && (
          <span className="mt-0.5 flex items-center gap-1 text-xs text-muted-foreground">
            {c.last_message.kind === "agent" && <Bot className="size-3 shrink-0" />}
            <span className="truncate">{c.last_message.body || "(listing shared)"}</span>
          </span>
        )}
      </span>
    </button>
  );
}

function ChatHeader({
  chat,
  connected,
  counterpartyPresent,
  autoReplyOn,
  onBack,
}: {
  chat: ChatRow;
  connected: boolean;
  counterpartyPresent: boolean;
  autoReplyOn: boolean;
  onBack: () => void;
}) {
  const name = chat.counterparty?.name || "Unknown";
  return (
    <div className="flex h-14 shrink-0 items-center gap-3 border-b px-4">
      <Button variant="ghost" size="icon" className="md:hidden" onClick={onBack} aria-label="Back">
        ←
      </Button>
      <Avatar className="size-8">
        <AvatarImage src={chat.counterparty?.avatar_url || undefined} alt={name} />
        <AvatarFallback>{initials(name)}</AvatarFallback>
      </Avatar>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="truncate text-sm font-medium">{name}</span>
          {chat.status !== "open" && (
            <Badge variant="outline" className="text-[10px]">
              {chat.status}
            </Badge>
          )}
        </div>
        <p className="text-xs text-muted-foreground">
          <span className={counterpartyPresent ? "text-green-600" : ""}>
            {counterpartyPresent ? "● online" : "○ away"}
          </span>
          {" · "}
          <span className={connected ? "" : "text-amber-600"}>
            {connected ? "connected" : "connecting…"}
          </span>
        </p>
      </div>
      <Link
        href="/settings"
        className="flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs text-muted-foreground hover:bg-accent"
        title="Your away-agent setting (change in Settings)"
      >
        <Bot className="size-3.5" />
        Auto-reply {autoReplyOn ? "on" : "off"}
      </Link>
    </div>
  );
}

function MessageRow({
  m,
  myId,
  counterpartyName,
  onApprove,
  onDiscard,
  onEditAndSend,
}: {
  m: ChatMessage;
  myId: number;
  counterpartyName: string;
  onApprove: (id: number) => void;
  onDiscard: (id: number) => void;
  onEditAndSend: (m: ChatMessage) => void;
}) {
  if (m.kind === "system")
    return (
      <div className="py-2 text-center text-xs text-muted-foreground">{m.body}</div>
    );

  const mine = m.sender === myId;
  const isAgent = m.kind === "agent";
  const label = isAgent
    ? mine
      ? "Polaris · on your behalf"
      : `Polaris · ${counterpartyName}'s assistant`
    : mine
      ? "You"
      : counterpartyName;
  const chip = m.action ? ACTION_CHIP[m.action] : undefined;

  return (
    <div className={cn("flex py-1.5", mine ? "justify-end" : "justify-start")}>
      <div className={cn("max-w-[85%] md:max-w-[75%]", mine ? "text-right" : "text-left")}>
        <div
          className={cn(
            "mb-0.5 flex items-center gap-1.5 text-[11px] text-muted-foreground",
            mine && "justify-end",
          )}
        >
          {isAgent && <Bot className="size-3" />}
          <span>{label}</span>
          {chip && (
            <span className={cn("rounded-full px-1.5 py-0.5 text-[10px] font-medium", chip.cls)}>
              {chip.label}
            </span>
          )}
          {m.status === "draft" && (
            <Badge variant="outline" className="border-amber-400 text-[10px] text-amber-600">
              DRAFT — only you see this
            </Badge>
          )}
        </div>
        {m.body && (
          <div
            className={cn(
              "inline-block whitespace-pre-wrap rounded-2xl px-4 py-2 text-left text-sm",
              mine ? "bg-primary text-primary-foreground" : "bg-muted",
              isAgent && "ring-1 ring-primary/40",
              m.status === "draft" && "opacity-80",
            )}
          >
            {m.body}
          </div>
        )}
        {m.attachments
          .filter((a) => a.kind === "listing")
          .map((a) => (
            <ListingAttachmentCard key={a.id} listing={a.listing} />
          ))}
        {m.status === "draft" && (
          <div className={cn("mt-1.5 flex gap-1.5", mine && "justify-end")}>
            <Button size="sm" className="h-7" onClick={() => onApprove(m.id)}>
              <Check /> Approve &amp; send
            </Button>
            <Button size="sm" variant="outline" className="h-7" onClick={() => onEditAndSend(m)}>
              <Pencil /> Edit &amp; send
            </Button>
            <Button size="sm" variant="ghost" className="h-7" onClick={() => onDiscard(m.id)}>
              <Trash2 /> Discard
            </Button>
          </div>
        )}
        <div className={cn("mt-0.5 text-[10px] text-muted-foreground", mine && "text-right")}>
          {fmtDateTime(m.created_at)}
        </div>
      </div>
    </div>
  );
}

function Composer({
  input,
  onType,
  onSend,
  connected,
  attachIds,
  setAttachIds,
}: {
  input: string;
  onType: (v: string) => void;
  onSend: () => void;
  connected: boolean;
  attachIds: number[];
  setAttachIds: (ids: number[]) => void;
}) {
  const [pickerOpen, setPickerOpen] = useState(false);
  const { data: listings = [] } = useQuery({
    queryKey: ["listings"],
    queryFn: listListings,
    enabled: pickerOpen,
  });

  return (
    <div className="shrink-0 border-t p-3 md:p-4">
      <div className="mx-auto max-w-3xl">
        {attachIds.length > 0 && (
          <div className="mb-2 flex flex-wrap gap-1.5">
            {attachIds.map((id) => {
              const l = listings.find((x) => x.id === id);
              return (
                <Badge key={id} variant="secondary" className="gap-1">
                  <Building2 className="size-3" />
                  {l?.title || l?.primary_property?.address_raw || `Listing #${id}`}
                  <button onClick={() => setAttachIds(attachIds.filter((x) => x !== id))}>
                    <X className="size-3" />
                  </button>
                </Badge>
              );
            })}
          </div>
        )}
        <div className="flex gap-2">
          <Popover open={pickerOpen} onOpenChange={setPickerOpen}>
            <PopoverTrigger asChild>
              <Button variant="outline" size="icon" className="h-auto" aria-label="Attach a listing">
                <Paperclip className="size-4" />
              </Button>
            </PopoverTrigger>
            <PopoverContent align="start" className="w-80 p-0">
              <Command>
                <CommandInput placeholder="Attach one of your listings…" />
                <CommandList>
                  <CommandEmpty>No listings.</CommandEmpty>
                  <CommandGroup>
                    {listings.map((l) => (
                      <CommandItem
                        key={l.id}
                        onSelect={() => {
                          if (!attachIds.includes(l.id)) setAttachIds([...attachIds, l.id]);
                          setPickerOpen(false);
                        }}
                      >
                        <Building2 className="size-4" />
                        <span className="truncate">
                          {l.title || l.primary_property?.address_raw || `Listing #${l.id}`}
                        </span>
                      </CommandItem>
                    ))}
                  </CommandGroup>
                </CommandList>
              </Command>
            </PopoverContent>
          </Popover>
          <Textarea
            value={input}
            onChange={(e) => onType(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                onSend();
              }
            }}
            rows={2}
            placeholder="Reply… (opening this chat pauses your Polaris — you've taken over)"
            className="flex-1 resize-none"
          />
          <Button onClick={onSend} disabled={!connected} className="h-auto">
            Send
          </Button>
        </div>
      </div>
    </div>
  );
}

export default function ChatPage() {
  return (
    <Suspense>
      <ChatPageInner />
    </Suspense>
  );
}
