"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Building2, ImagePlus, Lock, MessageCircle, Trash2 } from "lucide-react";
import { useParams, useRouter } from "next/navigation";
import { useRef, useState } from "react";
import { toast } from "sonner";

import { MandateForm } from "@/components/mandate-form";
import { ACCEPT, uploadPhotoFiles } from "@/components/photo-uploader";
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
  attachListingMedia,
  deleteListingMedia,
  getListing,
  getValuation,
  openChatWith,
  setListingMandate,
  updateListing,
  updateListingProperty,
  type ListingDetail,
  type MandateInput,
  type PropertyOverrides,
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

type ListingProp = ListingDetail["properties"][number];

function OvField({
  label,
  value,
  setValue,
  placeholder,
}: {
  label: string;
  value: string;
  setValue: (v: string) => void;
  placeholder: number | null;
}) {
  return (
    <div className="grid gap-2">
      <Label>{label}</Label>
      <Input
        type="number"
        value={value}
        placeholder={placeholder != null ? String(placeholder) : ""}
        onChange={(e) => setValue(e.target.value)}
      />
    </div>
  );
}

function PropertyOverrideDialog({
  listingId,
  pp,
  open,
  onOpenChange,
  onUpdated,
}: {
  listingId: number;
  pp: ListingProp;
  open: boolean;
  onOpenChange: (o: boolean) => void;
  onUpdated: () => void;
}) {
  const p = pp.property;
  const ov = pp.overrides;
  const s = (v: number | null | undefined) => (v == null ? "" : String(v));
  const [condition, setCondition] = useState(s(ov.condition));
  const [sqft, setSqft] = useState(s(ov.sqft));
  const [beds, setBeds] = useState(s(ov.beds));
  const [baths, setBaths] = useState(s(ov.baths));
  const [year, setYear] = useState(s(ov.year_built));
  const [reno, setReno] = useState(s(ov.yr_renovated));
  const [busy, setBusy] = useState(false);

  const numOrNull = (v: string): number | null => {
    const t = v.trim();
    if (t === "") return null;
    const n = Number(t);
    return Number.isFinite(n) ? n : null;
  };

  async function save() {
    // A filled field sets an override; a blank clears it (falls back to the base record).
    const overrides: PropertyOverrides = {
      condition: condition ? parseInt(condition, 10) : null,
      sqft: numOrNull(sqft),
      beds: numOrNull(beds),
      baths: numOrNull(baths),
      year_built: numOrNull(year),
      yr_renovated: numOrNull(reno),
    };
    setBusy(true);
    try {
      await updateListingProperty(listingId, p.id, overrides);
      toast.success("Current state updated");
      onOpenChange(false);
      onUpdated();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Update failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Current state — {p.address_raw}</DialogTitle>
        </DialogHeader>
        <p className="text-xs text-muted-foreground">
          Restate this property&apos;s current condition for this listing (for example after
          a renovation). A blank field keeps the value on record. The shared property record
          is never changed, and any figure derived from these is shown to buyers as
          seller-stated.
        </p>
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="grid gap-2">
            <Label>Condition</Label>
            <Select value={condition} onValueChange={setCondition}>
              <SelectTrigger className="w-full">
                <SelectValue
                  placeholder={
                    p.condition != null ? `${p.condition}/5 on record` : "Select…"
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
          <OvField label="Sqft" value={sqft} setValue={setSqft} placeholder={p.sqft} />
          <OvField label="Beds" value={beds} setValue={setBeds} placeholder={p.beds} />
          <OvField label="Baths" value={baths} setValue={setBaths} placeholder={p.baths} />
          <OvField
            label="Year built"
            value={year}
            setValue={setYear}
            placeholder={p.year_built}
          />
          <OvField label="Renovated (year)" value={reno} setValue={setReno} placeholder={null} />
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
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

function PropertyCard({
  pp,
  listingId,
  isMine,
  onUpdated,
}: {
  pp: ListingProp;
  listingId: number;
  isMine: boolean;
  onUpdated: () => void;
}) {
  const [editOpen, setEditOpen] = useState(false);
  const p = pp.property;
  const eff = pp.effective;
  const stated = new Set(pp.seller_stated_fields);
  const cond = eff.condition;
  const attrs: { key: string; label: string; value: string }[] = [
    { key: "beds", label: "Beds", value: eff.beds != null ? String(eff.beds) : "—" },
    { key: "baths", label: "Baths", value: eff.baths != null ? String(eff.baths) : "—" },
    { key: "sqft", label: "Sqft", value: eff.sqft != null ? eff.sqft.toLocaleString() : "—" },
    {
      key: "lot",
      label: "Lot",
      value: p.lot_size_sqft != null ? `${p.lot_size_sqft.toLocaleString()} sqft` : "—",
    },
    {
      key: "year_built",
      label: "Year built",
      value: eff.year_built != null ? String(eff.year_built) : "—",
    },
    {
      key: "condition",
      label: "Condition",
      value: cond != null ? CONDITION_LABELS[cond] ?? String(cond) : "—",
    },
    { key: "grade", label: "Grade", value: eff.grade != null ? String(eff.grade) : "—" },
    { key: "waterfront", label: "Waterfront", value: p.waterfront ? "Yes" : "No" },
  ];
  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-2">
          <div>
            <CardTitle className="text-base">{p.address_raw}</CardTitle>
            {pp.asking_price != null && (
              <CardDescription>Asking {fmtMoney(pp.asking_price)}</CardDescription>
            )}
          </div>
          {isMine && (
            <Button variant="outline" size="sm" onClick={() => setEditOpen(true)}>
              Edit current state
            </Button>
          )}
        </div>
        {stated.size > 0 && (
          <Badge variant="secondary" className="mt-1 w-fit">
            Seller-stated: {[...stated].join(", ")}
          </Badge>
        )}
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm sm:grid-cols-4">
          {attrs.map((a) => (
            <div key={a.label}>
              <p className="text-xs text-muted-foreground">{a.label}</p>
              <p className={stated.has(a.key) ? "font-medium" : undefined}>
                {a.value}
                {stated.has(a.key) && (
                  <span className="ml-1 text-xs text-amber-600" title="seller-stated">
                    •
                  </span>
                )}
              </p>
            </div>
          ))}
        </div>
      </CardContent>
      {isMine && (
        <PropertyOverrideDialog
          listingId={listingId}
          pp={pp}
          open={editOpen}
          onOpenChange={setEditOpen}
          onUpdated={onUpdated}
        />
      )}
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
            {val.current_value?.point != null && (
              <div className="rounded-md border p-3">
                <p className="text-xs text-muted-foreground">
                  Current value (condition-adjusted)
                </p>
                <p className="text-lg font-semibold">
                  {fmtMoney(val.current_value.point)}
                </p>
                <p className="text-xs text-muted-foreground">
                  {[
                    val.current_value.arv != null &&
                      `ARV ${fmtMoney(val.current_value.arv)}`,
                    val.current_value.est_rehab != null &&
                      `less rehab ${fmtMoney(val.current_value.est_rehab)}`,
                    val.current_value.condition != null &&
                      `condition ${val.current_value.condition}/5`,
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                </p>
                {val.current_value.seller_stated && (
                  <p className="mt-1 text-xs text-amber-600">
                    Reflects seller-stated condition (unverified).
                  </p>
                )}
              </div>
            )}
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

function ContactSellerDialog({
  sellerId,
  sellerName,
  listingId,
  listingTitle,
}: {
  sellerId: number;
  sellerName: string;
  listingId: number;
  listingTitle: string;
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [body, setBody] = useState("");
  const [sending, setSending] = useState(false);

  async function send() {
    setSending(true);
    try {
      // One chat per user-pair: this reopens the existing chat if there is one.
      const row = await openChatWith({
        counterparty_id: sellerId,
        body: body.trim(),
        listing_id: listingId,
      });
      setOpen(false);
      router.push(`/chat?chat=${row.id}`);
    } catch {
      toast.error("Couldn't send your message. Try again.");
      setSending(false);
    }
  }

  return (
    <>
      <Button onClick={() => setOpen(true)}>
        <MessageCircle /> Message seller
      </Button>
      <Dialog open={open} onOpenChange={(v) => !sending && setOpen(v)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Message {sellerName}</DialogTitle>
          </DialogHeader>
          <div className="space-y-2">
            <Textarea
              value={body}
              onChange={(e) => setBody(e.target.value)}
              placeholder={`Hi — I'm interested in ${listingTitle}. Is it still available?`}
              rows={4}
              autoFocus
            />
            <p className="text-xs text-muted-foreground">
              The listing is attached automatically so {sellerName} sees what
              you&apos;re asking about.
            </p>
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setOpen(false)}
              disabled={sending}
            >
              Cancel
            </Button>
            <Button onClick={send} disabled={sending || !body.trim()}>
              {sending ? "Sending…" : "Send message"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

function ListingPhotos({
  data,
  isMine,
  onOpen,
}: {
  data: ListingDetail;
  isMine: boolean;
  onOpen: (url: string) => void;
}) {
  const qc = useQueryClient();
  const inputRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [deletingId, setDeletingId] = useState<number | null>(null);

  if (data.media.length === 0 && !isMine) return null;

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["listing", data.id] });
    qc.invalidateQueries({ queryKey: ["listings"] }); // cover_url may change
  };

  const onFiles = async (list: FileList | null) => {
    const files = Array.from(list ?? []);
    if (files.length === 0) return;
    setBusy(true);
    try {
      const urls = await uploadPhotoFiles(files);
      if (urls.length > 0) {
        await attachListingMedia(
          data.id,
          urls.map((url) => ({ kind: "photo" as const, url })),
        );
        refresh();
        toast.success(urls.length === 1 ? "Photo added" : "Photos added");
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not add photos");
    } finally {
      setBusy(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  };

  const onDelete = async (mediaId: number) => {
    setDeletingId(mediaId);
    try {
      await deleteListingMedia(data.id, mediaId);
      refresh();
      toast.success("Photo removed");
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : "Could not remove the photo",
      );
    } finally {
      setDeletingId(null);
    }
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-medium">Photos</h2>
        {isMine && (
          <>
            <input
              ref={inputRef}
              type="file"
              multiple
              accept={ACCEPT}
              className="hidden"
              onChange={(e) => onFiles(e.target.files)}
            />
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={busy}
              onClick={() => inputRef.current?.click()}
            >
              <ImagePlus className="mr-1.5 h-4 w-4" />
              {busy ? "Uploading…" : "Add photos"}
            </Button>
          </>
        )}
      </div>
      {data.media.length === 0 ? (
        <p className="text-sm text-muted-foreground">No photos yet.</p>
      ) : (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          {data.media.map((m) => (
            <div key={m.id} className="group relative">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={m.url}
                alt={`Listing photo ${m.sort_order + 1}`}
                className="h-28 w-full cursor-pointer rounded-md object-cover"
                onClick={() => onOpen(m.url)}
              />
              {isMine && (
                <Button
                  type="button"
                  variant="destructive"
                  size="icon"
                  aria-label="Remove photo"
                  className="absolute right-1 top-1 h-6 w-6 opacity-0 transition-opacity group-hover:opacity-100"
                  disabled={deletingId === m.id}
                  onClick={(e) => {
                    e.stopPropagation();
                    onDelete(m.id);
                  }}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </Button>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function ListingDetailPage() {
  const params = useParams<{ id: string }>();
  const id = Number(params.id);
  const qc = useQueryClient();
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
          {isMine ? (
            <Button variant="outline" onClick={() => setEditOpen(true)}>
              Edit
            </Button>
          ) : (
            <ContactSellerDialog
              sellerId={data.seller.id}
              sellerName={data.seller.name}
              listingId={data.id}
              listingTitle={title}
            />
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
                  pp={pp}
                  listingId={data.id}
                  isMine={isMine}
                  onUpdated={() =>
                    qc.invalidateQueries({ queryKey: ["listing", id] })
                  }
                />
              ))}
            </div>

            <ListingPhotos data={data} isMine={isMine} onOpen={setLightbox} />
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
