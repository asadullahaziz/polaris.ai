"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, CircleDashed, Megaphone, Send, SkipForward, XCircle } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  approveCampaign,
  cancelCampaign,
  listCampaigns,
  type OutreachCampaign,
  type OutreachRecipient,
} from "@/lib/api";
import { cn } from "@/lib/utils";

const STATUS_BADGE: Record<OutreachCampaign["status"], "default" | "secondary" | "destructive" | "outline"> = {
  awaiting_approval: "secondary",
  sending: "default",
  done: "outline",
  cancelled: "outline",
};

function campaignTitle(c: OutreachCampaign): string {
  if (c.listing_address) return c.listing_address;
  if (c.listing_addresses.length > 1)
    return `${c.listing_addresses.length} listings — ${c.listing_addresses.join(" · ")}`;
  return c.listing_addresses[0] || (c.listing ? `Listing #${c.listing}` : "Outreach");
}

function RecipientRow({ r, showListing }: { r: OutreachRecipient; showListing?: boolean }) {
  const icon =
    r.status === "sent" ? (
      <Check className="size-3 text-green-600" />
    ) : r.status === "pending" ? (
      <CircleDashed className="size-3 text-muted-foreground" />
    ) : r.status === "failed" ? (
      <XCircle className="size-3 text-destructive" />
    ) : (
      <SkipForward className="size-3 text-muted-foreground" />
    );
  return (
    <li className="rounded-md bg-muted/60 px-2 py-1.5 text-xs">
      <div className="flex items-center justify-between gap-2">
        <span className="truncate font-medium">{r.name}</span>
        <span className="flex shrink-0 items-center gap-1 text-muted-foreground">
          {r.rank_score != null && Number(r.rank_score).toFixed(2)}
          {icon}
        </span>
      </div>
      {showListing && r.listing_address && (
        <p className="mt-0.5 truncate text-[11px] text-muted-foreground/80">
          ↳ {r.listing_address}
        </p>
      )}
      {r.rank_reason && <p className="mt-0.5 text-muted-foreground">{r.rank_reason}</p>}
    </li>
  );
}

// The staged-campaign rail: `awaiting_approval` campaigns (launched by the copilot or
// REST) reviewed/approved/cancelled here; live fan-out progress rides the copilot socket.
export function OutreachRail({ className }: { className?: string }) {
  const qc = useQueryClient();
  const { data: campaigns = [] } = useQuery({
    queryKey: ["campaigns"],
    queryFn: listCampaigns,
    refetchInterval: 5000, // reflect status flips as the fan-out runs
  });
  const [busyId, setBusyId] = useState<number | null>(null);

  async function act(id: number, fn: (id: number) => Promise<unknown>) {
    setBusyId(id);
    try {
      await fn(id);
      qc.invalidateQueries({ queryKey: ["campaigns"] });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Action failed");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <aside className={cn("flex w-80 flex-col border-l", className)}>
      <div className="flex h-11 shrink-0 items-center gap-2 border-b px-3 text-sm font-medium">
        <Megaphone className="size-4" /> Outreach
      </div>
      <ScrollArea className="min-h-0 flex-1">
        <div className="space-y-3 p-3">
          <p className="text-xs text-muted-foreground">
            Ask Polaris to “reach out to the best buyers for a listing”. Campaigns staged
            for review land here — nothing sends until approved.
          </p>
          {campaigns.length === 0 && (
            <p className="text-sm text-muted-foreground">No outreach yet.</p>
          )}
          {campaigns.map((c) => {
            // Multi-listing: several ledger rows per buyer. Count/act on distinct BUYERS
            // and show which listing each row covers.
            const multi = !c.listing_address && c.listing_addresses.length > 1;
            const pending = new Set(
              c.recipients.filter((r) => r.status === "pending").map((r) => r.recipient_user),
            ).size;
            return (
              <div key={c.id} className="rounded-lg border p-2.5">
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate text-sm font-medium">{campaignTitle(c)}</span>
                  <Badge variant={STATUS_BADGE[c.status]} className="shrink-0">
                    {c.status.replace(/_/g, " ")}
                  </Badge>
                </div>
                <ul className="mt-2 space-y-1">
                  {c.recipients.map((r) => (
                    <RecipientRow key={r.id} r={r} showListing={multi} />
                  ))}
                </ul>
                {c.status === "awaiting_approval" && (
                  <div className="mt-2 flex gap-2">
                    <Button
                      size="sm"
                      className="flex-1"
                      disabled={busyId === c.id || pending === 0}
                      onClick={() => act(c.id, approveCampaign)}
                    >
                      <Send /> Approve &amp; send {pending}
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={busyId === c.id}
                      onClick={() => act(c.id, cancelCampaign)}
                    >
                      Cancel
                    </Button>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </ScrollArea>
    </aside>
  );
}
