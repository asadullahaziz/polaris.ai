"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Bell } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  listNotifications,
  readAllNotifications,
  readNotification,
  type Notification,
} from "@/lib/api";
import { fmtDateTime } from "@/lib/hooks";
import { cn } from "@/lib/utils";

function describe(n: Notification): string {
  switch (n.type) {
    case "inbound_message":
      return typeof n.payload.note === "string"
        ? `Your agent: ${n.payload.note}`
        : "New message in a chat";
    case "outreach_received":
      return "A seller reached out with a property";
    case "approval_required":
      if (typeof n.payload.recommendation === "string")
        return n.payload.recommendation; // e.g. "Offer $612,000 clears your floor…"
      return n.payload.campaign_id != null
        ? "Outreach campaign awaiting your approval"
        : "Your agent drafted a reply for approval";
    case "escalation":
      return typeof n.payload.reason === "string"
        ? `Needs you: ${n.payload.reason}`
        : "A chat needs your attention";
    default:
      return (n.type as string).replace(/_/g, " ");
  }
}

export function NotificationsBell() {
  const router = useRouter();
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const { data: notes = [] } = useQuery({
    queryKey: ["notifications"],
    queryFn: listNotifications,
    refetchInterval: 8000,
  });
  const unread = notes.filter((n) => !n.read_at).length;

  async function markAll() {
    await readAllNotifications();
    qc.invalidateQueries({ queryKey: ["notifications"] });
  }

  async function openNote(n: Notification) {
    setOpen(false);
    if (!n.read_at) {
      await readNotification(n.id);
      qc.invalidateQueries({ queryKey: ["notifications"] });
    }
    if (n.chat != null) router.push(`/chat?chat=${n.chat}`);
    else if (n.payload.campaign_id != null) router.push("/polaris-ai");
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button variant="ghost" size="icon" className="relative" aria-label="Notifications">
          <Bell className="size-4" />
          {unread > 0 && (
            <Badge
              variant="destructive"
              className="absolute -right-1 -top-1 h-4 min-w-4 rounded-full px-1 text-[10px]"
            >
              {unread}
            </Badge>
          )}
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-80 p-0">
        <div className="flex items-center justify-between border-b px-3 py-2">
          <span className="text-sm font-medium">Notifications</span>
          <Button variant="ghost" size="sm" className="h-7 text-xs" onClick={markAll}>
            Mark all read
          </Button>
        </div>
        <ScrollArea className="max-h-80">
          <div className="p-1">
            {notes.length === 0 && (
              <p className="px-3 py-6 text-center text-sm text-muted-foreground">
                Nothing yet.
              </p>
            )}
            {notes.map((n) => (
              <button
                key={n.id}
                onClick={() => openNote(n)}
                className={cn(
                  "block w-full rounded-md px-3 py-2 text-left text-sm hover:bg-accent",
                  n.read_at && "text-muted-foreground",
                )}
              >
                <div className="flex items-start gap-2">
                  {!n.read_at && <span className="mt-1.5 size-1.5 shrink-0 rounded-full bg-primary" />}
                  <div className="min-w-0">
                    <p className="truncate">{describe(n)}</p>
                    <p className="text-xs text-muted-foreground">{fmtDateTime(n.created_at)}</p>
                  </div>
                </div>
              </button>
            ))}
          </div>
        </ScrollArea>
      </PopoverContent>
    </Popover>
  );
}
