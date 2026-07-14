"use client";

import { ChevronDown, MessageSquare, Search, TriangleAlert } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { AddressCombobox } from "@/components/address-combobox";
import { STRATEGIES } from "@/components/buy-box-form";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
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
import {
  openChatWith,
  rankBuyers,
  type BuyerRankResult,
  type PropertySearchResult,
} from "@/lib/api";

const FEATURES: [string, string][] = [
  ["bought_in_area", "Bought in area"],
  ["price_band", "Price band"],
  ["strategy", "Strategy"],
  ["recency", "Recency"],
  ["volume", "Volume"],
  ["cash", "Cash"],
  ["relationship", "Relationship"],
];

const pct = (v: number | undefined) =>
  Math.min(100, Math.max(0, Math.round((v ?? 0) * 100)));

export default function BuyersPage() {
  const router = useRouter();
  const [address, setAddress] = useState("");
  const [price, setPrice] = useState("");
  const [strategy, setStrategy] = useState(""); // advisory only — never sent
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [beds, setBeds] = useState("");
  const [sqft, setSqft] = useState("");
  const [condition, setCondition] = useState("any");
  const [propertyType, setPropertyType] = useState("any");
  const [limit, setLimit] = useState("10");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<BuyerRankResult | null>(null);
  const [searchedAddress, setSearchedAddress] = useState("");
  const [msgBusy, setMsgBusy] = useState<number | null>(null);
  const [selected, setSelected] = useState<PropertySearchResult | null>(null);

  // Picking a suggestion auto-fills the deal form from the known property record.
  function onPick(p: PropertySearchResult) {
    setAddress(p.address_raw);
    setSelected(p);
    if (p.last_sale_price != null) setPrice(String(Math.round(p.last_sale_price)));
    if (p.beds != null) setBeds(String(p.beds));
    if (p.sqft != null) setSqft(String(p.sqft));
    if (p.condition != null) setCondition(String(p.condition));
    if (p.property_type) setPropertyType(p.property_type);
  }

  async function onSearch(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      const res = await rankBuyers({
        address: address.trim(),
        price: price.trim() ? Number(price) : undefined,
        beds: beds.trim() ? Number(beds) : undefined,
        sqft: sqft.trim() ? Number(sqft) : undefined,
        condition: condition === "any" ? undefined : Number(condition),
        property_type: propertyType === "any" ? undefined : propertyType,
        limit: limit.trim() ? Number(limit) : 10,
      });
      setResult(res);
      setSearchedAddress(address.trim());
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Search failed");
    } finally {
      setBusy(false);
    }
  }

  async function message(userId: number) {
    setMsgBusy(userId);
    try {
      const row = await openChatWith({ counterparty_id: userId });
      router.push(`/chat?chat=${row.id}`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not open chat");
      setMsgBusy(null);
    }
  }

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-5xl p-6">
        <h1 className="text-xl font-semibold">Find buyers</h1>
        <p className="mb-6 text-sm text-muted-foreground">
          Rank likely buyers for a deal — no listing required.
        </p>

        <Card className="mb-6">
          <CardContent>
            <form onSubmit={onSearch} className="grid gap-4">
              <div className="grid gap-3 sm:grid-cols-[1fr_10rem_12rem]">
                <div className="grid gap-1.5">
                  <Label htmlFor="address">Address</Label>
                  <AddressCombobox
                    id="address"
                    placeholder="Start typing an address, street, or town…"
                    value={address}
                    onChange={(v) => {
                      setAddress(v);
                      setSelected(null);
                    }}
                    onSelect={onPick}
                  />
                </div>
                <div className="grid gap-1.5">
                  <Label htmlFor="price">Price</Label>
                  <Input
                    id="price"
                    type="number"
                    inputMode="numeric"
                    placeholder="450000"
                    value={price}
                    onChange={(e) => setPrice(e.target.value)}
                  />
                </div>
                <div className="grid gap-1.5">
                  <Label>Strategy</Label>
                  <Select
                    value={strategy || undefined}
                    onValueChange={setStrategy}
                  >
                    <SelectTrigger className="w-full">
                      <SelectValue placeholder="Any" />
                    </SelectTrigger>
                    <SelectContent>
                      {STRATEGIES.map(([v, label]) => (
                        <SelectItem key={v} value={v}>
                          {label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <p className="text-xs text-muted-foreground">
                    Advisory only — strategy fit is derived from each buyer&apos;s
                    history.
                  </p>
                </div>
              </div>

              <Collapsible open={filtersOpen} onOpenChange={setFiltersOpen}>
                <CollapsibleTrigger asChild>
                  <Button type="button" variant="ghost" size="sm" className="-ml-2">
                    <ChevronDown
                      className={`size-4 transition-transform ${filtersOpen ? "rotate-180" : ""}`}
                    />
                    More filters
                  </Button>
                </CollapsibleTrigger>
                <CollapsibleContent>
                  <div className="mt-3 grid gap-3 sm:grid-cols-5">
                    <div className="grid gap-1.5">
                      <Label htmlFor="beds" className="text-xs">
                        Beds
                      </Label>
                      <Input
                        id="beds"
                        type="number"
                        inputMode="numeric"
                        className="h-8"
                        value={beds}
                        onChange={(e) => setBeds(e.target.value)}
                      />
                    </div>
                    <div className="grid gap-1.5">
                      <Label htmlFor="sqft" className="text-xs">
                        Sqft
                      </Label>
                      <Input
                        id="sqft"
                        type="number"
                        inputMode="numeric"
                        className="h-8"
                        value={sqft}
                        onChange={(e) => setSqft(e.target.value)}
                      />
                    </div>
                    <div className="grid gap-1.5">
                      <Label className="text-xs">Condition</Label>
                      <Select value={condition} onValueChange={setCondition}>
                        <SelectTrigger size="sm" className="w-full">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="any">Any</SelectItem>
                          {["1", "2", "3", "4", "5"].map((c) => (
                            <SelectItem key={c} value={c}>
                              {c}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                    <div className="grid gap-1.5">
                      <Label className="text-xs">Property type</Label>
                      <Select value={propertyType} onValueChange={setPropertyType}>
                        <SelectTrigger size="sm" className="w-full">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="any">Any</SelectItem>
                          <SelectItem value="sfr">sfr</SelectItem>
                          <SelectItem value="townhouse">townhouse</SelectItem>
                          <SelectItem value="condo">condo</SelectItem>
                          <SelectItem value="multi">multi</SelectItem>
                        </SelectContent>
                      </Select>
                    </div>
                    <div className="grid gap-1.5">
                      <Label htmlFor="limit" className="text-xs">
                        Limit
                      </Label>
                      <Input
                        id="limit"
                        type="number"
                        inputMode="numeric"
                        className="h-8"
                        min={1}
                        value={limit}
                        onChange={(e) => setLimit(e.target.value)}
                      />
                    </div>
                  </div>
                </CollapsibleContent>
              </Collapsible>

              <div>
                <Button type="submit" disabled={busy || !address.trim()}>
                  <Search className="size-4" />
                  {busy ? "Searching…" : "Search"}
                </Button>
              </div>
            </form>
          </CardContent>
        </Card>

        {selected && (
          <Card className="mb-6">
            <CardContent className="flex flex-wrap items-baseline gap-x-6 gap-y-1 text-sm">
              <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                County record
              </span>
              <span className="font-medium">{selected.address_raw}</span>
              <span className="text-muted-foreground">
                {[
                  selected.beds != null && `${selected.beds} bd`,
                  selected.baths != null && `${selected.baths} ba`,
                  selected.sqft != null &&
                    `${selected.sqft.toLocaleString()} sqft`,
                  selected.year_built != null && `built ${selected.year_built}`,
                  selected.condition != null &&
                    `condition ${selected.condition}/5`,
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </span>
              {selected.last_sale_price != null && (
                <span className="text-muted-foreground">
                  Last sold ${Math.round(selected.last_sale_price).toLocaleString()}
                  {selected.last_sale_date ? ` on ${selected.last_sale_date}` : ""}
                </span>
              )}
            </CardContent>
          </Card>
        )}

        {!result && (
          <p className="py-16 text-center text-sm text-muted-foreground">
            Enter a deal address above to rank likely buyers.
          </p>
        )}

        {result && !result.resolved && (
          <div className="flex items-start gap-2 rounded-md border border-amber-500/50 bg-amber-500/10 px-4 py-3 text-sm text-amber-700 dark:text-amber-400">
            <TriangleAlert className="mt-0.5 size-4 shrink-0" />
            Address not found in our records — pick a suggested address to rank
            buyers with location signals.
          </div>
        )}

        {result?.resolved && (
          <>
            <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
              <p className="text-sm text-muted-foreground">
                {result.ranked.length} of {result.n_candidates} candidates
                {result.radius_mi ? ` · ${result.radius_mi} mi radius` : ""}
              </p>
              <Button variant="link" size="sm" className="text-muted-foreground" asChild>
                <Link
                  href={`/listings/new?address=${encodeURIComponent(searchedAddress)}`}
                >
                  Create a listing from this
                </Link>
              </Button>
            </div>
            {result.note && (
              <p className="mb-2 text-xs text-muted-foreground">{result.note}</p>
            )}

            {result.ranked.length === 0 ? (
              <p className="py-12 text-center text-sm text-muted-foreground">
                No matching buyers found in range.
              </p>
            ) : (
              <div className="overflow-x-auto rounded-md border">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="w-10">#</TableHead>
                      <TableHead>Buyer</TableHead>
                      <TableHead className="w-44">Score</TableHead>
                      <TableHead className="w-32" />
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {result.ranked.map((b, i) => (
                      <TableRow key={b.user_id}>
                        <TableCell className="text-muted-foreground">
                          {i + 1}
                        </TableCell>
                        <TableCell>
                          <div className="flex items-center gap-2">
                            <span className="font-medium">{b.name}</span>
                            {b.cash && <Badge variant="secondary">cash buyer</Badge>}
                          </div>
                          <p className="mt-0.5 max-w-md text-xs text-muted-foreground">
                            {b.reason}
                          </p>
                          <p className="mt-0.5 text-xs text-muted-foreground">
                            {b.n_purchases} purchases · {b.n_nearby} nearby
                          </p>
                        </TableCell>
                        <TableCell>
                          <HoverCard>
                            <HoverCardTrigger asChild>
                              <div className="flex items-center gap-2">
                                <Progress value={pct(b.score)} className="h-2 w-24" />
                                <span className="text-sm tabular-nums">
                                  {pct(b.score)}
                                </span>
                              </div>
                            </HoverCardTrigger>
                            <HoverCardContent className="w-64" align="end">
                              <p className="mb-2 text-xs font-medium">
                                Score breakdown
                              </p>
                              <div className="space-y-1.5">
                                {FEATURES.map(([key, label]) => (
                                  <div
                                    key={key}
                                    className="grid grid-cols-[6rem_1fr_2rem] items-center gap-2"
                                  >
                                    <span className="text-xs text-muted-foreground">
                                      {label}
                                    </span>
                                    <Progress
                                      value={pct(b.features[key])}
                                      className="h-1.5"
                                    />
                                    <span className="text-right text-xs tabular-nums">
                                      {pct(b.features[key])}
                                    </span>
                                  </div>
                                ))}
                              </div>
                            </HoverCardContent>
                          </HoverCard>
                        </TableCell>
                        <TableCell className="text-right">
                          <Button
                            variant="outline"
                            size="sm"
                            disabled={msgBusy === b.user_id}
                            onClick={() => message(b.user_id)}
                          >
                            <MessageSquare className="size-4" />
                            Message
                          </Button>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
