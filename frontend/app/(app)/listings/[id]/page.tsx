"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Building2, Lock } from "lucide-react";
import { useParams } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { MandateForm } from "@/components/mandate-form";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
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
import { Switch } from "@/components/ui/switch";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import {
  ApiError,
  getListing,
  getValuation,
  setListingMandate,
  updateListing,
  type ListingDetail,
  type MandateInput,
  type Property,
  type Valuation,
} from "@/lib/api";
import { fmtDate, fmtMoney, useMe } from "@/lib/hooks";

type BundleType = "single" | "package" | "portfolio";

const STATUSES = [
  "draft",
  "active",
  "under_contract",
  "paused",
  "closed",
  "withdrawn",
];

const CONDITION_LABELS: Record<number, string> = {
  1: "1 — Poor",
  2: "2 — Fair",
  3: "3 — Average",
  4: "4 — Good",
  5: "5 — Excellent",
};

function statusVariant(s: string): "default" | "secondary" | "outline" {
  if (s === "active") return "default";
  if (s === "draft") return "secondary";
  return "outline";
}

function PropertyCard({
  p,
  price,
}: {
  p: Property;
  price: string | number | null;
}) {
  const attrs: [string, string][] = [
    ["Beds", p.beds != null ? String(p.beds) : "—"],
    ["Baths", p.baths != null ? String(p.baths) : "—"],
    ["Sqft", p.sqft != null ? p.sqft.toLocaleString() : "—"],
    ["Lot", p.lot_size_sqft != null ? `${p.lot_size_sqft.toLocaleString()} sqft` : "—"],
    ["Year built", p.year_built != null ? String(p.year_built) : "—"],
    ["Condition", p.condition != null ? CONDITION_LABELS[p.condition] ?? String(p.condition) : "—"],
    ["Grade", p.grade != null ? String(p.grade) : "—"],
    ["Waterfront", p.waterfront ? "Yes" : "No"],
  ];
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{p.address_raw}</CardTitle>
        {price != null && (
          <CardDescription>Asking {fmtMoney(price)}</CardDescription>
        )}
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm sm:grid-cols-4">
          {attrs.map(([label, value]) => (
            <div key={label}>
              <p className="text-xs text-muted-foreground">{label}</p>
              <p>{value}</p>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

function EditListingDialog({
  data,
  open,
  onOpenChange,
}: {
  data: ListingDetail;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const qc = useQueryClient();
  const [title, setTitle] = useState(data.title);
  const [description, setDescription] = useState(data.description);
  const [askingPrice, setAskingPrice] = useState(
    data.asking_price != null ? String(Number(data.asking_price)) : "",
  );
  const [status, setStatus] = useState(data.status);
  const [bundleType, setBundleType] = useState<BundleType>(data.bundle_type);
  const [busy, setBusy] = useState(false);

  async function save() {
    const body: Parameters<typeof updateListing>[1] = {
      title,
      description,
      status,
      bundle_type: bundleType,
    };
    const p = askingPrice.trim();
    if (p !== "" && Number.isFinite(Number(p))) body.asking_price = Number(p);
    setBusy(true);
    try {
      await updateListing(data.id, body);
      qc.invalidateQueries({ queryKey: ["listing", data.id] });
      qc.invalidateQueries({ queryKey: ["listings"] });
      toast.success("Listing updated");
      onOpenChange(false);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to update listing");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Edit listing</DialogTitle>
        </DialogHeader>
        <div className="grid gap-4">
          <div className="grid gap-2">
            <Label htmlFor="edit-title">Title</Label>
            <Input
              id="edit-title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="edit-description">Description</Label>
            <Textarea
              id="edit-description"
              rows={4}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="edit-price">Asking price</Label>
            <Input
              id="edit-price"
              type="number"
              value={askingPrice}
              onChange={(e) => setAskingPrice(e.target.value)}
            />
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="grid gap-2">
              <Label>Status</Label>
              <Select value={status} onValueChange={setStatus}>
                <SelectTrigger className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {STATUSES.map((s) => (
                    <SelectItem key={s} value={s} className="capitalize">
                      {s.replace(/_/g, " ")}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="grid gap-2">
              <Label>Bundle type</Label>
              <Select
                value={bundleType}
                onValueChange={(v) => setBundleType(v as BundleType)}
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
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={save} disabled={busy}>
            {busy ? "Saving…" : "Save"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function ValuationCard({ id }: { id: number }) {
  const [arv, setArv] = useState(false);
  const [val, setVal] = useState<Valuation | null>(null);
  const [busy, setBusy] = useState(false);

  async function run() {
    setBusy(true);
    try {
      setVal(await getValuation(id, arv));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Valuation failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Valuation</CardTitle>
        <CardDescription>Comp-based estimate from nearby sales.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Switch id="arv" checked={arv} onCheckedChange={setArv} />
            <Label htmlFor="arv">ARV (after-repair)</Label>
          </div>
          <Button size="sm" onClick={run} disabled={busy}>
            {busy ? "Running…" : "Run valuation"}
          </Button>
        </div>

        {val && (
          <div className="space-y-3">
            <div>
              <p className="text-2xl font-semibold text-green-600">
                {fmtMoney(val.point)}
              </p>
              <p className="text-sm text-muted-foreground">
                {fmtMoney(val.low)} – {fmtMoney(val.high)}
              </p>
              <p className="text-xs text-muted-foreground">
                {[
                  `${val.basis.n_comps} comps`,
                  `${val.basis.radius_mi} mi`,
                  val.basis.relaxed,
                  val.basis.arv ? "ARV" : null,
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </p>
            </div>
            {val.comps.length > 0 && (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Comp</TableHead>
                    <TableHead className="text-right">Price</TableHead>
                    <TableHead className="text-right">$/sf</TableHead>
                    <TableHead className="text-right">mi</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {val.comps.slice(0, 8).map((c) => (
                    <TableRow key={c.id}>
                      <TableCell className="max-w-40">
                        <p className="truncate text-xs">{c.address}</p>
                        <p className="text-xs text-muted-foreground">
                          {fmtDate(c.sold_on)}
                        </p>
                      </TableCell>
                      <TableCell className="text-right text-xs">
                        {fmtMoney(c.price)}
                      </TableCell>
                      <TableCell className="text-right text-xs">
                        {c.ppsf != null ? `$${Math.round(c.ppsf)}` : "—"}
                      </TableCell>
                      <TableCell className="text-right text-xs">
                        {c.distance_mi != null ? c.distance_mi.toFixed(1) : "—"}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function DealSettingsCard({ data }: { data: ListingDetail }) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const mandate = data.mandate;
  if (!mandate) return null; // seller-private — absent on other sellers' listings

  const initial: MandateInput | undefined = mandate.exists
    ? {
        floor_price: mandate.floor_price,
        ceiling_price: mandate.ceiling_price,
        must_haves: mandate.must_haves,
        availability_window: mandate.availability_window,
        instructions: mandate.instructions,
      }
    : undefined;

  async function save(m: MandateInput) {
    await setListingMandate(data.id, m);
    qc.invalidateQueries({ queryKey: ["listing", data.id] });
    toast.success("Deal settings saved");
    setEditing(false);
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <Lock className="size-4" /> Deal settings
        </CardTitle>
        <CardDescription>Private — only your agent sees this.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {editing ? (
          <MandateForm initial={initial} onSubmit={save} />
        ) : (
          <>
            {mandate.exists ? (
              <div className="space-y-2 text-sm">
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Floor price</span>
                  <span>{fmtMoney(mandate.floor_price)}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Ceiling price</span>
                  <span>{fmtMoney(mandate.ceiling_price)}</span>
                </div>
                {mandate.must_haves.length > 0 && (
                  <div className="flex flex-wrap gap-1.5 pt-1">
                    {mandate.must_haves.map((m) => (
                      <Badge key={m} variant="secondary">
                        {m}
                      </Badge>
                    ))}
                  </div>
                )}
                {mandate.availability_window && (
                  <p>
                    <span className="text-muted-foreground">Availability: </span>
                    {mandate.availability_window}
                  </p>
                )}
                {mandate.instructions && (
                  <p className="whitespace-pre-wrap text-muted-foreground">
                    {mandate.instructions}
                  </p>
                )}
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">
                No deal settings yet — set a floor price and instructions so your
                agent can negotiate for you.
              </p>
            )}
            <Button variant="outline" size="sm" onClick={() => setEditing(true)}>
              Edit deal settings
            </Button>
          </>
        )}
      </CardContent>
    </Card>
  );
}

export default function ListingDetailPage() {
  const params = useParams<{ id: string }>();
  const id = Number(params.id);
  const [editOpen, setEditOpen] = useState(false);
  const [lightbox, setLightbox] = useState<string | null>(null);
  const { data: me } = useMe();

  const { data, isLoading, error } = useQuery({
    queryKey: ["listing", id],
    queryFn: () => getListing(id),
    retry: (count, err) =>
      !(err instanceof ApiError && err.status === 404) && count < 2,
  });

  if (isLoading) {
    return (
      <div className="h-full overflow-y-auto">
        <div className="mx-auto max-w-5xl space-y-4 p-6">
          <Skeleton className="h-8 w-64" />
          <Skeleton className="h-40 w-full rounded-xl" />
          <Skeleton className="h-64 w-full rounded-xl" />
        </div>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="flex h-full items-center justify-center p-6">
        <div className="flex flex-col items-center gap-2 text-center">
          <Building2 className="size-10 text-muted-foreground" />
          <p className="font-medium">This listing isn&apos;t available to you.</p>
          <p className="text-sm text-muted-foreground">
            It may have been removed, or it belongs to another seller.
          </p>
        </div>
      </div>
    );
  }

  const address = data.properties[0]?.property.address_raw ?? null;
  const title = data.title || address || `Listing #${data.id}`;
  const isMine = data.seller.id === me?.id;

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-5xl p-6">
        <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="text-2xl font-semibold">{title}</h1>
              <Badge variant={statusVariant(data.status)} className="capitalize">
                {data.status.replace(/_/g, " ")}
              </Badge>
              {data.bundle_type !== "single" && (
                <Badge variant="outline" className="capitalize">
                  {data.bundle_type}
                </Badge>
              )}
            </div>
            {address && data.title && (
              <p className="text-sm text-muted-foreground">{address}</p>
            )}
            {!isMine && (
              <p className="text-sm text-muted-foreground">
                Listed by {data.seller.name}
              </p>
            )}
            <p className="mt-1 text-3xl font-semibold">
              {fmtMoney(data.asking_price)}
            </p>
          </div>
          {isMine && (
            <Button variant="outline" onClick={() => setEditOpen(true)}>
              Edit
            </Button>
          )}
        </div>

        <div className="grid gap-6 lg:grid-cols-3">
          <div className={`space-y-6 ${isMine ? "lg:col-span-2" : "lg:col-span-3"}`}>
            {data.description && (
              <p className="whitespace-pre-wrap text-sm">{data.description}</p>
            )}

            <div className="space-y-3">
              <h2 className="text-lg font-medium">
                {data.properties.length > 1 ? "Properties" : "Property"}
              </h2>
              {data.properties.map((pp) => (
                <PropertyCard
                  key={pp.property.id}
                  p={pp.property}
                  price={pp.asking_price}
                />
              ))}
            </div>

            {data.media.length > 0 && (
              <div className="space-y-3">
                <h2 className="text-lg font-medium">Photos</h2>
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                  {data.media.map((m) => (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      key={m.id}
                      src={m.url}
                      alt={`Listing photo ${m.sort_order + 1}`}
                      className="h-28 w-full cursor-pointer rounded-md object-cover"
                      onClick={() => setLightbox(m.url)}
                    />
                  ))}
                </div>
              </div>
            )}
          </div>

          {isMine && (
            <div className="space-y-6">
              <ValuationCard id={data.id} />
              <DealSettingsCard data={data} />
            </div>
          )}
        </div>

        {isMine && (
          <EditListingDialog data={data} open={editOpen} onOpenChange={setEditOpen} key={data.updated_at} />
        )}

        <Dialog open={!!lightbox} onOpenChange={(o) => !o && setLightbox(null)}>
          <DialogContent className="max-w-3xl p-2 sm:max-w-3xl">
            <DialogTitle className="sr-only">Photo</DialogTitle>
            {lightbox && (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={lightbox}
                alt="Listing photo"
                className="max-h-[80vh] w-full rounded object-contain"
              />
            )}
          </DialogContent>
        </Dialog>
      </div>
    </div>
  );
}
