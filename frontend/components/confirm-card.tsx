"use client";

import { AlertTriangle, Check, Clock, ShieldQuestion, X } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { fmtMoney } from "@/lib/hooks";

// The copilot's confirm-every-write card (copilot.confirm ↔ copilot.confirm_response).
export type ConfirmPayload = {
  kind: "confirm_write";
  action:
    | "create_listing"
    | "update_listing"
    | "set_mandate"
    | "create_buy_box"
    | "update_buy_box"
    | "delete_buy_box"
    | "launch_outreach"
    | string;
  summary: string;
  proposal: Record<string, unknown>;
};

const MONEY_KEYS = new Set([
  "asking_price",
  "floor_price",
  "ceiling_price",
  "price_min",
  "price_max",
  "arv_min",
  "arv_max",
  "max_rehab_cost",
]);

function FieldGrid({ fields }: { fields: Record<string, unknown> }) {
  const entries = Object.entries(fields).filter(
    ([, v]) => v !== undefined && v !== null && v !== "",
  );
  if (entries.length === 0)
    return <p className="text-sm text-muted-foreground">No fields provided.</p>;
  return (
    <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-sm">
      {entries.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-muted-foreground">{k.replace(/_/g, " ")}</dt>
          <dd className="font-medium">
            {MONEY_KEYS.has(k) && typeof v !== "object"
              ? fmtMoney(v as number)
              : Array.isArray(v)
                ? v.join(", ")
                : String(v)}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function OutreachProposal({ proposal }: { proposal: Record<string, unknown> }) {
  const buyers = (proposal.buyers as { name: string; score: number; reason: string }[]) ?? [];
  return (
    <div className="space-y-2">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Buyer</TableHead>
            <TableHead className="w-16">Score</TableHead>
            <TableHead>Why</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {buyers.map((b, i) => (
            <TableRow key={i}>
              <TableCell className="font-medium">{b.name}</TableCell>
              <TableCell>{Number(b.score).toFixed(2)}</TableCell>
              <TableCell className="text-muted-foreground">{b.reason}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <p className="flex items-center gap-1.5 text-xs text-amber-600 dark:text-amber-500">
        <AlertTriangle className="size-3.5" />
        Approving sends this outreach immediately.
      </p>
    </div>
  );
}

export function ConfirmCard({
  payload,
  resolution,
  onRespond,
}: {
  payload: ConfirmPayload;
  resolution?: "approved" | "declined" | "expired";
  onRespond?: (approved: boolean) => void;
}) {
  const { action, summary, proposal } = payload;
  const fields = (proposal.fields as Record<string, unknown>) ?? proposal;
  const destructive = action === "delete_buy_box";

  return (
    <Card className="my-3 gap-3 border-primary/30 py-4 shadow-none">
      <CardHeader className="px-4">
        <CardTitle className="flex items-center gap-2 text-sm font-medium">
          <ShieldQuestion className="size-4 text-primary" />
          {summary || action.replace(/_/g, " ")}
          <Badge variant={destructive ? "destructive" : "secondary"} className="ml-auto">
            {action.replace(/_/g, " ")}
          </Badge>
        </CardTitle>
      </CardHeader>
      <CardContent className="px-4">
        {action === "launch_outreach" ? (
          <OutreachProposal proposal={proposal} />
        ) : (
          <FieldGrid fields={fields} />
        )}
      </CardContent>
      <CardFooter className="gap-2 px-4">
        {resolution ? (
          <Badge variant={resolution === "approved" ? "default" : "outline"}>
            {resolution === "approved" ? (
              <Check className="size-3" />
            ) : resolution === "declined" ? (
              <X className="size-3" />
            ) : (
              <Clock className="size-3" />
            )}
            {resolution}
          </Badge>
        ) : (
          <>
            <Button size="sm" variant={destructive ? "destructive" : "default"} onClick={() => onRespond?.(true)}>
              <Check /> Approve
            </Button>
            <Button size="sm" variant="outline" onClick={() => onRespond?.(false)}>
              <X /> Decline
            </Button>
          </>
        )}
      </CardFooter>
    </Card>
  );
}
