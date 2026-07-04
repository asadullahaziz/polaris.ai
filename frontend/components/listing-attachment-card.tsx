"use client";

import { Building2 } from "lucide-react";
import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { type ListingBrief } from "@/lib/api";
import { fmtMoney } from "@/lib/hooks";

// A listing shared into a chat (MessageAttachment kind=listing), rendered inline.
// Links to the detail page — non-owners get its graceful "not available" state.
export function ListingAttachmentCard({ listing }: { listing: ListingBrief | null }) {
  if (!listing)
    return (
      <div className="mt-1.5 rounded-lg border border-dashed px-3 py-2 text-xs text-muted-foreground">
        Listing no longer available
      </div>
    );
  return (
    <Link
      href={`/listings/${listing.listing_id}`}
      className="mt-1.5 flex items-center gap-3 rounded-lg border bg-background px-3 py-2 text-left transition-colors hover:bg-accent"
    >
      <span className="flex size-9 shrink-0 items-center justify-center rounded-md bg-muted">
        <Building2 className="size-4 text-muted-foreground" />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm font-medium">
          {listing.title || listing.address || `Listing #${listing.listing_id}`}
        </span>
        <span className="block truncate text-xs text-muted-foreground">
          {listing.address}
        </span>
      </span>
      <span className="shrink-0 text-right">
        <span className="block text-sm font-semibold">{fmtMoney(listing.asking_price)}</span>
        <Badge variant="outline" className="mt-0.5 text-[10px]">
          {listing.status}
        </Badge>
      </span>
    </Link>
  );
}
