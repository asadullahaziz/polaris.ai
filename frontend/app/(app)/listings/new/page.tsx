"use client";

import { useQueryClient } from "@tanstack/react-query";
import { ImageIcon, Lock, Plus, Search, Trash2 } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { AddressCombobox } from "@/components/address-combobox";
import { MandateForm } from "@/components/mandate-form";
import { PhotoUploader } from "@/components/photo-uploader";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import {
  createListing,
  lookupProperty,
  type ListingCreateInput,
  type MandateInput,
  type Property,
  type PropertyItemInput,
  type PropertyLookup,
  type PropertyOverrides,
  type PropertySearchResult,
} from "@/lib/api";
import { fmtMoney, uuid } from "@/lib/hooks";

type BundleType = "single" | "package" | "portfolio";

type PropRow = {
  key: string;
  matched: boolean;
  address: string;
  detail: Property | null; // matched rows carry the server attributes
  item: PropertyItemInput;
  askingPrice: string;
};

const CONDITION_LABELS: Record<string, string> = {
  "1": "1 — Poor",
  "2": "2 — Fair",
  "3": "3 — Average",
  "4": "4 — Good",
  "5": "5 — Excellent",
};

const PROPERTY_TYPES = [
  ["sfr", "Single-family (SFR)"],
  ["townhouse", "Townhouse"],
  ["condo", "Condo"],
  ["multi", "Multi-family"],
] as const;

function num(s: string): number | undefined {
  const t = s.trim();
  if (t === "") return undefined;
  const n = Number(t);
  return Number.isFinite(n) ? n : undefined;
}

function attrsLine(row: PropRow): string {
  const src = row.matched ? row.detail : null;
  const ov = row.item.overrides ?? {};
  const beds = ov.beds ?? (src ? src.beds : row.item.beds) ?? null;
  const baths = ov.baths ?? (src ? src.baths : row.item.baths) ?? null;
  const sqft = ov.sqft ?? (src ? src.sqft : row.item.sqft) ?? null;
  const type = src ? src.property_type : row.item.property_type ?? null;
  const parts: string[] = [];
  if (beds != null) parts.push(`${beds} bd`);
  if (baths != null) parts.push(`${baths} ba`);
  if (sqft != null) parts.push(`${sqft.toLocaleString()} sqft`);
  if (type) parts.push(type);
  if (Object.keys(ov).length > 0) parts.push("edited");
  return parts.join(" · ");
}

function NewListingInner() {
  const router = useRouter();
  const qc = useQueryClient();
  const searchParams = useSearchParams();

  // Listing scalars
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [askingPrice, setAskingPrice] = useState("");
  const [bundleType, setBundleType] = useState<BundleType>("single");
  const [bundleTouched, setBundleTouched] = useState(false);
  const [status, setStatus] = useState("active");

  // Property lookup / dedup flow
  const [lookupAddress, setLookupAddress] = useState(
    searchParams.get("address") ?? "",
  );
  const [lookupBusy, setLookupBusy] = useState(false);
  const [lookup, setLookup] = useState<PropertyLookup | null>(null);
  const [rows, setRows] = useState<PropRow[]>([]);

  // New-property sub-form (shown when the lookup misses)
  const [npAddress, setNpAddress] = useState("");
  const [npType, setNpType] = useState("");
  const [npBeds, setNpBeds] = useState("");
  const [npBaths, setNpBaths] = useState("");
  const [npSqft, setNpSqft] = useState("");
  const [npLot, setNpLot] = useState("");
  const [npYear, setNpYear] = useState("");
  const [npCondition, setNpCondition] = useState("");
  const [npWaterfront, setNpWaterfront] = useState(false);

  // Matched-property override sub-form (current-state restated for THIS listing; base
  // value shows as the placeholder, a blank field keeps the value on record).
  const [ovCondition, setOvCondition] = useState("");
  const [ovBeds, setOvBeds] = useState("");
  const [ovBaths, setOvBaths] = useState("");
  const [ovSqft, setOvSqft] = useState("");
  const [ovYear, setOvYear] = useState("");
  const [ovReno, setOvReno] = useState("");

  const [photos, setPhotos] = useState<string[]>([]);
  const [mandate, setMandate] = useState<MandateInput | null>(null);
  const [busy, setBusy] = useState(false);

  function resetSubForm() {
    setNpType("");
    setNpBeds("");
    setNpBaths("");
    setNpSqft("");
    setNpLot("");
    setNpYear("");
    setNpCondition("");
    setNpWaterfront(false);
  }

  async function doLookup() {
    const addr = lookupAddress.trim();
    if (!addr) return;
    setLookupBusy(true);
    setLookup(null);
    try {
      const res = await lookupProperty(addr);
      setLookup(res);
      if (!res.found) {
        setNpAddress(res.normalized || addr);
        resetSubForm();
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Lookup failed");
    } finally {
      setLookupBusy(false);
    }
  }

  // Arriving with ?address= (the buyers-page handoff) auto-runs the lookup on mount.
  const autoLooked = useRef(false);
  useEffect(() => {
    if (autoLooked.current) return;
    autoLooked.current = true;
    if (searchParams.get("address")) doLookup();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Reset the override sub-form whenever a different property is matched.
  const matchedId = lookup?.found ? lookup.property.id : null;
  useEffect(() => {
    setOvCondition("");
    setOvBeds("");
    setOvBaths("");
    setOvSqft("");
    setOvYear("");
    setOvReno("");
  }, [matchedId]);

  function pushRow(row: PropRow) {
    const next = [...rows, row];
    setRows(next);
    if (next.length >= 2 && !bundleTouched && bundleType === "single") {
      setBundleType("package");
      toast.info("Bundle type set to package — change it back if this is wrong.");
    }
    setLookup(null);
    setLookupAddress("");
  }

  function attachMatched() {
    if (!lookup?.found) return;
    const p = lookup.property;
    if (rows.some((r) => r.item.property_id === p.id)) {
      toast.error("That property is already on this listing.");
      return;
    }
    // Any restated current-state becomes a per-listing override (base stays untouched).
    const overrides: PropertyOverrides = {};
    if (ovCondition) overrides.condition = parseInt(ovCondition, 10);
    const beds = num(ovBeds);
    if (beds !== undefined) overrides.beds = beds;
    const baths = num(ovBaths);
    if (baths !== undefined) overrides.baths = baths;
    const sqft = num(ovSqft);
    if (sqft !== undefined) overrides.sqft = sqft;
    const year = num(ovYear);
    if (year !== undefined) overrides.year_built = year;
    const reno = num(ovReno);
    if (reno !== undefined) overrides.yr_renovated = reno;
    const item: PropertyItemInput = { property_id: p.id };
    if (Object.keys(overrides).length > 0) item.overrides = overrides;
    pushRow({
      key: uuid(),
      matched: true,
      address: p.address_raw,
      detail: p,
      item,
      askingPrice: "",
    });
  }

  function addNewProperty() {
    const address = npAddress.trim();
    if (!address) {
      toast.error("Address is required.");
      return;
    }
    const item: PropertyItemInput = { address };
    if (npType) item.property_type = npType;
    const beds = num(npBeds);
    if (beds !== undefined) item.beds = beds;
    const baths = num(npBaths);
    if (baths !== undefined) item.baths = baths;
    const sqft = num(npSqft);
    if (sqft !== undefined) item.sqft = sqft;
    const lot = num(npLot);
    if (lot !== undefined) item.lot_size_sqft = lot;
    const year = num(npYear);
    if (year !== undefined) item.year_built = year;
    if (npCondition) item.condition = parseInt(npCondition, 10);
    if (npWaterfront) item.waterfront = true;
    pushRow({
      key: uuid(),
      matched: false,
      address,
      detail: null,
      item,
      askingPrice: "",
    });
  }

  async function onCreate() {
    if (rows.length === 0) {
      toast.error("Add at least one property first.");
      return;
    }
    const body: ListingCreateInput = {
      status,
      bundle_type: bundleType,
      properties: rows.map((r, i) => {
        const it: PropertyItemInput = { ...r.item, sort_order: i };
        const ap = num(r.askingPrice);
        if (ap !== undefined) it.asking_price = ap;
        return it;
      }),
    };
    if (title.trim()) body.title = title.trim();
    if (description.trim()) body.description = description.trim();
    const price = num(askingPrice);
    if (price !== undefined) body.asking_price = price;
    if (photos.length > 0) {
      body.media = photos.map((url, i) => ({ kind: "photo", url, sort_order: i }));
    }
    if (mandate) body.mandate = mandate;

    setBusy(true);
    try {
      const created = await createListing(body);
      qc.invalidateQueries({ queryKey: ["listings"] });
      toast.success("Listing created");
      router.push(`/listings/${created.id}`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to create listing");
      setBusy(false);
    }
  }

  const matchedProperty = lookup?.found ? lookup.property : null;

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-6">
      <div>
        <h1 className="text-2xl font-semibold">New listing</h1>
        <p className="text-sm text-muted-foreground">
          Add the property (or properties), photos and private deal settings.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Listing</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4">
          <div className="grid gap-2">
            <Label htmlFor="title">Title</Label>
            <Input
              id="title"
              placeholder="e.g. Renovated craftsman near Green Lake"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="description">Description</Label>
            <Textarea
              id="description"
              rows={4}
              placeholder="What makes this deal interesting?"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>
          <div className="grid gap-4 sm:grid-cols-3">
            <div className="grid gap-2">
              <Label htmlFor="asking">Asking price</Label>
              <Input
                id="asking"
                type="number"
                placeholder="e.g. 495000"
                value={askingPrice}
                onChange={(e) => setAskingPrice(e.target.value)}
              />
            </div>
            <div className="grid gap-2">
              <Label>Bundle type</Label>
              <Select
                value={bundleType}
                onValueChange={(v) => {
                  setBundleType(v as BundleType);
                  setBundleTouched(true);
                }}
              >
                <SelectTrigger className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="single">Single</SelectItem>
                  <SelectItem value="package">Package</SelectItem>
                  <SelectItem value="portfolio">Portfolio</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="grid gap-2">
              <Label>Status</Label>
              <Select value={status} onValueChange={setStatus}>
                <SelectTrigger className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="active">Active</SelectItem>
                  <SelectItem value="draft">Draft</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Properties</CardTitle>
          <CardDescription>
            Look the address up first — if it already exists in Polaris we attach
            it instead of creating a duplicate.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex gap-2">
            <div className="flex-1">
              <AddressCombobox
                placeholder="Start typing an address, street, or town…"
                value={lookupAddress}
                onChange={setLookupAddress}
                onSelect={(p: PropertySearchResult) => {
                  // A picked suggestion is already a lookup hit — skip the roundtrip.
                  setLookupAddress(p.address_raw);
                  setLookup({ found: true, normalized: p.address_norm, property: p });
                }}
              />
            </div>
            <Button
              type="button"
              variant="secondary"
              onClick={doLookup}
              disabled={!lookupAddress.trim() || lookupBusy}
            >
              <Search /> {lookupBusy ? "Looking up…" : "Look up"}
            </Button>
          </div>

          {matchedProperty && (
            <div className="space-y-3 rounded-md border p-4">
              <div className="flex items-center gap-2">
                <Badge>Matched</Badge>
                <span className="text-sm text-muted-foreground">
                  Existing property — update its current state if it has changed
                </span>
              </div>
              <p className="font-medium">{matchedProperty.address_raw}</p>
              <p className="text-sm text-muted-foreground">
                On record:{" "}
                {[
                  matchedProperty.beds != null && `${matchedProperty.beds} bd`,
                  matchedProperty.baths != null && `${matchedProperty.baths} ba`,
                  matchedProperty.sqft != null &&
                    `${matchedProperty.sqft.toLocaleString()} sqft`,
                  matchedProperty.year_built != null &&
                    `built ${matchedProperty.year_built}`,
                  matchedProperty.condition != null &&
                    `condition ${matchedProperty.condition}/5`,
                  matchedProperty.property_type,
                  matchedProperty.waterfront && "waterfront",
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </p>
              <p className="text-xs text-muted-foreground">
                Leave a field blank to keep the value on record. Anything you change here
                applies to this listing only (the shared record is never altered) and is
                shown to buyers as seller-stated.
              </p>
              <div className="grid gap-4 sm:grid-cols-3">
                <div className="grid gap-2">
                  <Label>Condition</Label>
                  <Select value={ovCondition} onValueChange={setOvCondition}>
                    <SelectTrigger className="w-full">
                      <SelectValue
                        placeholder={
                          matchedProperty.condition != null
                            ? `${matchedProperty.condition}/5 on record`
                            : "Select…"
                        }
                      />
                    </SelectTrigger>
                    <SelectContent>
                      {Object.entries(CONDITION_LABELS).map(([v, label]) => (
                        <SelectItem key={v} value={v}>
                          {label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="grid gap-2">
                  <Label htmlFor="ov-sqft">Sqft</Label>
                  <Input
                    id="ov-sqft"
                    type="number"
                    value={ovSqft}
                    placeholder={matchedProperty.sqft?.toString() ?? ""}
                    onChange={(e) => setOvSqft(e.target.value)}
                  />
                </div>
                <div className="grid gap-2">
                  <Label htmlFor="ov-beds">Beds</Label>
                  <Input
                    id="ov-beds"
                    type="number"
                    value={ovBeds}
                    placeholder={matchedProperty.beds?.toString() ?? ""}
                    onChange={(e) => setOvBeds(e.target.value)}
                  />
                </div>
                <div className="grid gap-2">
                  <Label htmlFor="ov-baths">Baths</Label>
                  <Input
                    id="ov-baths"
                    type="number"
                    value={ovBaths}
                    placeholder={matchedProperty.baths?.toString() ?? ""}
                    onChange={(e) => setOvBaths(e.target.value)}
                  />
                </div>
                <div className="grid gap-2">
                  <Label htmlFor="ov-year">Year built</Label>
                  <Input
                    id="ov-year"
                    type="number"
                    value={ovYear}
                    placeholder={matchedProperty.year_built?.toString() ?? ""}
                    onChange={(e) => setOvYear(e.target.value)}
                  />
                </div>
                <div className="grid gap-2">
                  <Label htmlFor="ov-reno">Renovated (year)</Label>
                  <Input
                    id="ov-reno"
                    type="number"
                    value={ovReno}
                    placeholder="e.g. 2026"
                    onChange={(e) => setOvReno(e.target.value)}
                  />
                </div>
              </div>
              <Button type="button" size="sm" onClick={attachMatched}>
                <Plus /> Attach
              </Button>
            </div>
          )}

          {lookup && !lookup.found && (
            <div className="space-y-4 rounded-md border p-4">
              <p className="text-sm text-muted-foreground">
                No match found — add it as a new property.
              </p>
              <div className="grid gap-2">
                <Label htmlFor="np-address">Address</Label>
                <Input
                  id="np-address"
                  value={npAddress}
                  onChange={(e) => setNpAddress(e.target.value)}
                />
              </div>
              <div className="grid gap-4 sm:grid-cols-3">
                <div className="grid gap-2">
                  <Label>Property type</Label>
                  <Select value={npType} onValueChange={setNpType}>
                    <SelectTrigger className="w-full">
                      <SelectValue placeholder="Select…" />
                    </SelectTrigger>
                    <SelectContent>
                      {PROPERTY_TYPES.map(([v, label]) => (
                        <SelectItem key={v} value={v}>
                          {label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="grid gap-2">
                  <Label htmlFor="np-beds">Beds</Label>
                  <Input
                    id="np-beds"
                    type="number"
                    value={npBeds}
                    onChange={(e) => setNpBeds(e.target.value)}
                  />
                </div>
                <div className="grid gap-2">
                  <Label htmlFor="np-baths">Baths</Label>
                  <Input
                    id="np-baths"
                    type="number"
                    value={npBaths}
                    onChange={(e) => setNpBaths(e.target.value)}
                  />
                </div>
                <div className="grid gap-2">
                  <Label htmlFor="np-sqft">Sqft</Label>
                  <Input
                    id="np-sqft"
                    type="number"
                    value={npSqft}
                    onChange={(e) => setNpSqft(e.target.value)}
                  />
                </div>
                <div className="grid gap-2">
                  <Label htmlFor="np-lot">Lot size (sqft)</Label>
                  <Input
                    id="np-lot"
                    type="number"
                    value={npLot}
                    onChange={(e) => setNpLot(e.target.value)}
                  />
                </div>
                <div className="grid gap-2">
                  <Label htmlFor="np-year">Year built</Label>
                  <Input
                    id="np-year"
                    type="number"
                    value={npYear}
                    onChange={(e) => setNpYear(e.target.value)}
                  />
                </div>
                <div className="grid gap-2">
                  <Label>Condition</Label>
                  <Select value={npCondition} onValueChange={setNpCondition}>
                    <SelectTrigger className="w-full">
                      <SelectValue placeholder="Select…" />
                    </SelectTrigger>
                    <SelectContent>
                      {Object.entries(CONDITION_LABELS).map(([v, label]) => (
                        <SelectItem key={v} value={v}>
                          {label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="flex items-end gap-2 pb-2.5">
                  <Checkbox
                    id="np-waterfront"
                    checked={npWaterfront}
                    onCheckedChange={(v) => setNpWaterfront(v === true)}
                  />
                  <Label htmlFor="np-waterfront">Waterfront</Label>
                </div>
              </div>
              <Button type="button" size="sm" onClick={addNewProperty}>
                <Plus /> Add property
              </Button>
            </div>
          )}

          {rows.length > 0 && (
            <div className="space-y-2">
              {rows.map((r) => (
                <div
                  key={r.key}
                  className="flex items-center gap-3 rounded-md border p-3"
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <p className="truncate text-sm font-medium">{r.address}</p>
                      <Badge variant={r.matched ? "default" : "secondary"}>
                        {r.matched ? "matched" : "new"}
                      </Badge>
                    </div>
                    {attrsLine(r) && (
                      <p className="truncate text-xs text-muted-foreground">
                        {attrsLine(r)}
                      </p>
                    )}
                  </div>
                  <Input
                    type="number"
                    placeholder="Price (optional)"
                    className="w-36"
                    value={r.askingPrice}
                    onChange={(e) =>
                      setRows(
                        rows.map((x) =>
                          x.key === r.key
                            ? { ...x, askingPrice: e.target.value }
                            : x,
                        ),
                      )
                    }
                  />
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    aria-label="Remove property"
                    onClick={() => setRows(rows.filter((x) => x.key !== r.key))}
                  >
                    <Trash2 />
                  </Button>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <ImageIcon className="size-4" /> Photos
          </CardTitle>
          <CardDescription>
            Upload photos of the property — they&apos;re attached when you create
            the listing.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <PhotoUploader photos={photos} onChange={setPhotos} disabled={busy} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Lock className="size-4" /> Deal settings (private to your agent)
          </CardTitle>
          <CardDescription>
            Floor price, must-haves, availability and instructions guide your AI
            agent and are never shared with buyers.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {mandate && (
            <p className="text-sm text-muted-foreground">
              Attached: floor {fmtMoney(mandate.floor_price)} · ceiling{" "}
              {fmtMoney(mandate.ceiling_price)} ·{" "}
              {mandate.must_haves?.length ?? 0} must-have(s). Saved with the
              listing on create.
            </p>
          )}
          <MandateForm
            onSubmit={async (m) => {
              setMandate(m);
              toast.success("Deal settings attached — they save with the listing.");
            }}
            submitLabel={mandate ? "Update deal settings" : "Attach deal settings"}
          />
        </CardContent>
      </Card>

      <div className="flex justify-end gap-2 pb-6">
        <Button type="button" variant="outline" onClick={() => router.push("/listings")}>
          Cancel
        </Button>
        <Button
          type="button"
          onClick={onCreate}
          disabled={busy || rows.length === 0}
        >
          {busy ? "Creating…" : "Create listing"}
        </Button>
      </div>
    </div>
  );
}

export default function NewListingPage() {
  return (
    <div className="h-full overflow-y-auto">
      <Suspense
        fallback={
          <div className="mx-auto max-w-5xl p-6">
            <Skeleton className="h-8 w-48" />
          </div>
        }
      >
        <NewListingInner />
      </Suspense>
    </div>
  );
}
