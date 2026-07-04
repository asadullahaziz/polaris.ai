"use client";

import { useQuery } from "@tanstack/react-query";
import { Building2, Plus } from "lucide-react";
import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { listListings, type ListingSummary, type Property } from "@/lib/api";
import { fmtMoney } from "@/lib/hooks";

function statusVariant(s: string): "default" | "secondary" | "outline" {
  if (s === "active") return "default";
  if (s === "draft") return "secondary";
  return "outline";
}

function attrsLine(p: Property | null): string {
  if (!p) return "";
  const parts: string[] = [];
  if (p.beds != null) parts.push(`${p.beds} bd`);
  if (p.baths != null) parts.push(`${p.baths} ba`);
  if (p.sqft != null) parts.push(`${p.sqft.toLocaleString()} sqft`);
  if (p.property_type) parts.push(p.property_type);
  return parts.join(" · ");
}

function ListingCard({ l }: { l: ListingSummary }) {
  const title = l.title || l.primary_property?.address_raw || `Listing #${l.id}`;
  const attrs = attrsLine(l.primary_property);
  return (
    <Link href={`/listings/${l.id}`} className="block">
      <Card className="h-full gap-3 overflow-hidden pt-0 transition-shadow hover:shadow-md">
        {l.cover_url ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={l.cover_url}
            alt={title}
            className="h-40 w-full rounded-t-xl object-cover"
          />
        ) : (
          <div className="flex h-40 w-full items-center justify-center rounded-t-xl bg-muted">
            <Building2 className="size-8 text-muted-foreground" />
          </div>
        )}
        <CardContent className="space-y-1.5 pb-4">
          <div className="flex items-start justify-between gap-2">
            <p className="truncate font-medium">{title}</p>
            <div className="flex shrink-0 gap-1">
              {l.bundle_type !== "single" && (
                <Badge variant="outline" className="capitalize">
                  {l.bundle_type}
                </Badge>
              )}
              <Badge variant={statusVariant(l.status)} className="capitalize">
                {l.status.replace(/_/g, " ")}
              </Badge>
            </div>
          </div>
          {l.primary_property && (
            <p className="truncate text-sm text-muted-foreground">
              {l.primary_property.address_raw}
            </p>
          )}
          {attrs && <p className="text-sm text-muted-foreground">{attrs}</p>}
          <p className="text-lg font-semibold">{fmtMoney(l.asking_price)}</p>
        </CardContent>
      </Card>
    </Link>
  );
}

export default function ListingsPage() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["listings"],
    queryFn: listListings,
  });

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-5xl p-6">
        <div className="mb-6 flex items-center justify-between">
          <h1 className="text-2xl font-semibold">Listings</h1>
          <Button asChild>
            <Link href="/listings/new">
              <Plus /> New listing
            </Link>
          </Button>
        </div>

        {isLoading ? (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {[...Array(6)].map((_, i) => (
              <Skeleton key={i} className="h-64 rounded-xl" />
            ))}
          </div>
        ) : error ? (
          <p className="text-sm text-muted-foreground">
            Couldn&apos;t load your listings. Try again in a moment.
          </p>
        ) : !data || data.length === 0 ? (
          <Card>
            <CardContent className="flex flex-col items-center gap-3 py-12 text-center">
              <Building2 className="size-10 text-muted-foreground" />
              <div>
                <p className="font-medium">No listings yet</p>
                <p className="text-sm text-muted-foreground">
                  List a property and let your AI agent find the right buyers.
                </p>
              </div>
              <Button asChild>
                <Link href="/listings/new">
                  <Plus /> Create your first listing
                </Link>
              </Button>
            </CardContent>
          </Card>
        ) : (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {data.map((l) => (
              <ListingCard key={l.id} l={l} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
