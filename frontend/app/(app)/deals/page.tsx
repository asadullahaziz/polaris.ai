"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Handshake, MessageSquare } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  listDeals,
  updateDealStage,
  type DealRow,
  type DealStage,
} from "@/lib/api";
import { fmtDateTime, fmtMoney } from "@/lib/hooks";

const STAGES: DealStage[] = [
  "contacted",
  "engaged",
  "negotiating",
  "agreed",
  "closed",
  "lost",
];

const STAGE_BADGE: Record<DealStage, string> = {
  contacted: "bg-muted text-muted-foreground",
  engaged: "bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300",
  negotiating: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
  agreed: "bg-green-100 text-green-800 dark:bg-green-950 dark:text-green-300",
  closed: "bg-green-200 text-green-900 dark:bg-green-900 dark:text-green-200",
  lost: "bg-muted text-muted-foreground line-through",
};

function StageChip({ stage }: { stage: DealStage }) {
  return (
    <Badge variant="outline" className={`border-0 ${STAGE_BADGE[stage]}`}>
      {stage}
    </Badge>
  );
}

function offerCell(d: DealRow) {
  if (d.agreed_price != null) return `Agreed at ${fmtMoney(d.agreed_price)}`;
  const theirs = d.side === "selling" ? d.last_offer_by_buyer : d.last_offer_by_seller;
  const mine = d.side === "selling" ? d.last_offer_by_seller : d.last_offer_by_buyer;
  const parts: string[] = [];
  if (theirs != null) parts.push(`Their offer ${fmtMoney(theirs)}`);
  if (mine != null) parts.push(`Ours ${fmtMoney(mine)}`);
  return parts.length ? parts.join(" / ") : "—";
}

export default function DealsPage() {
  const qc = useQueryClient();
  const [side, setSide] = useState<"all" | "selling" | "buying">("all");
  const [stage, setStage] = useState<"all" | DealStage>("all");

  const { data: deals, isLoading } = useQuery({
    queryKey: ["deals", side, stage],
    queryFn: () =>
      listDeals({
        side: side === "all" ? undefined : side,
        stage: stage === "all" ? undefined : stage,
      }),
    refetchInterval: 10000,
  });

  const stageMutation = useMutation({
    mutationFn: ({ id, stage }: { id: number; stage: DealStage }) =>
      updateDealStage(id, stage),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["deals"] });
      toast.success("Deal updated");
    },
    onError: (e: Error) => toast.error(e.message || "Could not update the deal"),
  });

  return (
    <div className="mx-auto w-full max-w-6xl space-y-4 p-4 md:p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="flex items-center gap-2 text-xl font-semibold">
            <Handshake className="size-5" /> Deals
          </h1>
          <p className="text-sm text-muted-foreground">
            Every buyer-listing conversation, tracked from first contact to close.
          </p>
        </div>
        <Tabs value={side} onValueChange={(v) => setSide(v as typeof side)}>
          <TabsList>
            <TabsTrigger value="all">All</TabsTrigger>
            <TabsTrigger value="selling">Selling</TabsTrigger>
            <TabsTrigger value="buying">Buying</TabsTrigger>
          </TabsList>
        </Tabs>
      </div>

      <Tabs value={stage} onValueChange={(v) => setStage(v as typeof stage)}>
        <TabsList className="flex-wrap">
          <TabsTrigger value="all">All stages</TabsTrigger>
          {STAGES.map((s) => (
            <TabsTrigger key={s} value={s}>
              {s}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>

      <Card>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Counterparty</TableHead>
                <TableHead>Listing</TableHead>
                <TableHead>Side</TableHead>
                <TableHead>Stage</TableHead>
                <TableHead>Offers</TableHead>
                <TableHead>Updated</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {isLoading && (
                <TableRow>
                  <TableCell colSpan={7} className="py-8 text-center text-muted-foreground">
                    Loading deals…
                  </TableCell>
                </TableRow>
              )}
              {!isLoading && !deals?.length && (
                <TableRow>
                  <TableCell colSpan={7} className="py-8 text-center text-muted-foreground">
                    No deals yet. Outreach and buyer inquiries land here automatically.
                  </TableCell>
                </TableRow>
              )}
              {deals?.map((d) => (
                <TableRow key={d.id}>
                  <TableCell className="font-medium">{d.counterparty.name}</TableCell>
                  <TableCell>
                    <Link
                      href={`/listings/${d.listing.id}`}
                      className="text-primary hover:underline"
                    >
                      {d.listing.address || d.listing.title || `Listing #${d.listing.id}`}
                    </Link>
                    {d.listing.asking_price != null && (
                      <div className="text-xs text-muted-foreground">
                        Asking {fmtMoney(d.listing.asking_price)}
                      </div>
                    )}
                  </TableCell>
                  <TableCell className="capitalize">{d.side}</TableCell>
                  <TableCell>
                    <StageChip stage={d.stage} />
                  </TableCell>
                  <TableCell className="text-sm">{offerCell(d)}</TableCell>
                  <TableCell className="text-sm text-muted-foreground">
                    {fmtDateTime(d.updated_at)}
                  </TableCell>
                  <TableCell className="text-right">
                    <div className="flex items-center justify-end gap-2">
                      <Select
                        value={d.stage}
                        onValueChange={(v) =>
                          stageMutation.mutate({ id: d.id, stage: v as DealStage })
                        }
                      >
                        <SelectTrigger className="h-8 w-[130px]" size="sm">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {STAGES.map((s) => (
                            <SelectItem key={s} value={s}>
                              {s}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                      {d.chat_id != null && (
                        <Button asChild variant="outline" size="sm">
                          <Link href={`/chat?chat=${d.chat_id}`}>
                            <MessageSquare className="size-4" /> Chat
                          </Link>
                        </Button>
                      )}
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}
