"use client";

import { X } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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
import { Separator } from "@/components/ui/separator";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import {
  createBuyBox,
  updateBuyBox,
  type BuyBox,
  type BuyBoxGeo,
  type BuyBoxInput,
} from "@/lib/api";

export const STRATEGIES = [
  ["fix_flip", "Fix & flip"],
  ["buy_hold", "Buy & hold"],
  ["brrrr", "BRRRR"],
  ["wholesale", "Wholesale"],
  ["new_construction", "New construction"],
] as const;

export const strategyLabel = (s: string) =>
  STRATEGIES.find(([v]) => v === s)?.[1] ?? s;

export const PROPERTY_TYPES = ["sfr", "townhouse", "condo", "multi"] as const;

export function geoLabel(g: BuyBoxGeo): string {
  switch (g.geo_type) {
    case "radius":
      return `radius ${g.radius_mi ?? "?"}mi @${g.center_lat != null ? g.center_lat.toFixed(2) : "?"},${g.center_lon != null ? g.center_lon.toFixed(2) : "?"}`;
    case "city":
      return `city ${g.city}${g.state_code ? `, ${g.state_code}` : ""}`;
    case "zip":
      return `zip ${g.zip}`;
    case "state":
      return `state ${g.state_code}`;
    case "county":
      return `county ${g.county_fips}`;
    default:
      return g.geo_type;
  }
}

const NUM_FIELDS = [
  ["price_min", "Price min"],
  ["price_max", "Price max"],
  ["arv_min", "ARV min"],
  ["arv_max", "ARV max"],
  ["beds_min", "Beds min"],
  ["baths_min", "Baths min"],
  ["sqft_min", "Sqft min"],
  ["sqft_max", "Sqft max"],
  ["year_built_min", "Year built min"],
  ["max_rehab_cost", "Max rehab cost"],
] as const;

type NumKey = (typeof NUM_FIELDS)[number][0];

// Create/edit form for a buy-box. Writes support at most ONE new geo per save;
// existing geos are read-only over REST.
export function BuyBoxForm({
  initial,
  onDone,
}: {
  initial?: BuyBox;
  onDone: () => void;
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [strategy, setStrategy] = useState(initial?.strategy ?? "fix_flip");
  const [isActive, setIsActive] = useState(initial?.is_active ?? true);
  const [isPrimary, setIsPrimary] = useState(initial?.is_primary ?? false);
  const [nums, setNums] = useState<Record<NumKey, string>>(() => {
    const out = {} as Record<NumKey, string>;
    for (const [key] of NUM_FIELDS) {
      const v = initial ? initial[key] : null;
      out[key] = v == null ? "" : String(v);
    }
    return out;
  });
  const [propertyTypes, setPropertyTypes] = useState<string[]>(
    initial?.property_types ?? [],
  );
  const [ceilingPrice, setCeilingPrice] = useState(
    initial?.mandate?.ceiling_price != null
      ? String(initial.mandate.ceiling_price)
      : "",
  );
  const [mustHaves, setMustHaves] = useState<string[]>(
    initial?.mandate?.must_haves ?? [],
  );
  const [mustHaveDraft, setMustHaveDraft] = useState("");
  const [instructions, setInstructions] = useState(
    initial?.mandate?.instructions ?? "",
  );
  // "Add area" — one geo per save.
  const [geoType, setGeoType] = useState("");
  const [centerLat, setCenterLat] = useState("");
  const [centerLon, setCenterLon] = useState("");
  const [radiusMi, setRadiusMi] = useState("");
  const [city, setCity] = useState("");
  const [stateCode, setStateCode] = useState("");
  const [countyFips, setCountyFips] = useState("");
  const [zip, setZip] = useState("");
  const [busy, setBusy] = useState(false);

  function setNum(key: NumKey, value: string) {
    setNums((prev) => ({ ...prev, [key]: value }));
  }

  function toggleType(t: string, checked: boolean) {
    setPropertyTypes((prev) =>
      checked ? [...prev, t] : prev.filter((x) => x !== t),
    );
  }

  function addMustHave() {
    const v = mustHaveDraft.trim();
    if (v && !mustHaves.includes(v)) setMustHaves([...mustHaves, v]);
    setMustHaveDraft("");
  }

  function buildGeo(): BuyBoxInput["geo"] {
    if (geoType === "radius") {
      if (!centerLat.trim() || !centerLon.trim() || !radiusMi.trim()) return undefined;
      return {
        geo_type: "radius",
        center_lat: Number(centerLat),
        center_lon: Number(centerLon),
        radius_mi: Number(radiusMi),
      };
    }
    if (geoType === "city") {
      if (!city.trim()) return undefined;
      return {
        geo_type: "city",
        city: city.trim(),
        ...(stateCode.trim() ? { state_code: stateCode.trim() } : {}),
      };
    }
    if (geoType === "zip")
      return zip.trim() ? { geo_type: "zip", zip: zip.trim() } : undefined;
    if (geoType === "state")
      return stateCode.trim()
        ? { geo_type: "state", state_code: stateCode.trim() }
        : undefined;
    if (geoType === "county")
      return countyFips.trim()
        ? { geo_type: "county", county_fips: countyFips.trim() }
        : undefined;
    return undefined;
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      const payload: BuyBoxInput = {
        name: name.trim(),
        strategy,
        is_active: isActive,
        is_primary: isPrimary,
        property_types: propertyTypes,
        must_haves: mustHaves,
        instructions,
      };
      for (const [key] of NUM_FIELDS) {
        const raw = nums[key].trim();
        if (raw !== "") payload[key] = Number(raw);
      }
      if (ceilingPrice.trim() !== "") payload.ceiling_price = Number(ceilingPrice);
      const geo = buildGeo();
      if (geo) payload.geo = geo;

      if (initial) {
        await updateBuyBox(initial.buy_box_id, payload);
        toast.success("Buy-box updated");
      } else {
        await createBuyBox(payload);
        toast.success("Buy-box created");
      }
      onDone();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Save failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={onSubmit} className="grid gap-4">
      <div className="grid gap-2">
        <Label htmlFor="bb-name">Name</Label>
        <Input
          id="bb-name"
          required
          placeholder="e.g. Seattle flips"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
      </div>

      <div className="grid grid-cols-2 items-end gap-3">
        <div className="grid gap-2">
          <Label>Strategy</Label>
          <Select value={strategy} onValueChange={setStrategy}>
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {STRATEGIES.map(([v, label]) => (
                <SelectItem key={v} value={v}>
                  {label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="flex items-center gap-4 pb-2">
          <label className="flex items-center gap-2 text-sm">
            <Switch checked={isActive} onCheckedChange={setIsActive} />
            Active
          </label>
          <label className="flex items-center gap-2 text-sm">
            <Switch checked={isPrimary} onCheckedChange={setIsPrimary} />
            Primary
          </label>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        {NUM_FIELDS.map(([key, label]) => (
          <div key={key} className="grid gap-1.5">
            <Label htmlFor={`bb-${key}`} className="text-xs">
              {label}
            </Label>
            <Input
              id={`bb-${key}`}
              type="number"
              inputMode="numeric"
              className="h-8"
              value={nums[key]}
              onChange={(e) => setNum(key, e.target.value)}
            />
          </div>
        ))}
      </div>

      <div className="grid gap-2">
        <Label>Property types</Label>
        <div className="flex flex-wrap gap-4">
          {PROPERTY_TYPES.map((t) => (
            <label key={t} className="flex items-center gap-2 text-sm">
              <Checkbox
                checked={propertyTypes.includes(t)}
                onCheckedChange={(c) => toggleType(t, c === true)}
              />
              {t}
            </label>
          ))}
        </div>
      </div>

      <Separator />

      <div className="grid gap-3">
        <div>
          <p className="text-sm font-medium">Private deal settings</p>
          <p className="text-xs text-muted-foreground">
            Never shared with counterparties — guides your AI agent only.
          </p>
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor="bb-ceiling" className="text-xs">
            Ceiling price
          </Label>
          <Input
            id="bb-ceiling"
            type="number"
            inputMode="numeric"
            className="h-8"
            value={ceilingPrice}
            onChange={(e) => setCeilingPrice(e.target.value)}
          />
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor="bb-musthave" className="text-xs">
            Must-haves
          </Label>
          <div className="flex gap-2">
            <Input
              id="bb-musthave"
              className="h-8"
              placeholder="e.g. garage"
              value={mustHaveDraft}
              onChange={(e) => setMustHaveDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  addMustHave();
                }
              }}
            />
            <Button type="button" variant="outline" size="sm" onClick={addMustHave}>
              Add
            </Button>
          </div>
          {mustHaves.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {mustHaves.map((m) => (
                <Badge key={m} variant="secondary" className="gap-1">
                  {m}
                  <button
                    type="button"
                    aria-label={`Remove ${m}`}
                    onClick={() =>
                      setMustHaves(mustHaves.filter((x) => x !== m))
                    }
                  >
                    <X className="size-3" />
                  </button>
                </Badge>
              ))}
            </div>
          )}
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor="bb-instructions" className="text-xs">
            Instructions
          </Label>
          <Textarea
            id="bb-instructions"
            rows={3}
            placeholder="Guidance for your agent on deals matching this buy-box"
            value={instructions}
            onChange={(e) => setInstructions(e.target.value)}
          />
        </div>
      </div>

      <Separator />

      <div className="grid gap-3">
        <div>
          <p className="text-sm font-medium">Add area</p>
          <p className="text-xs text-muted-foreground">
            One area per save — existing areas are read-only; manage additional
            areas by adding one per save.
          </p>
        </div>
        {initial && initial.geos.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {initial.geos.map((g) => (
              <Badge key={g.id} variant="outline">
                {geoLabel(g)}
              </Badge>
            ))}
          </div>
        )}
        <div className="grid gap-2">
          <Select value={geoType || undefined} onValueChange={setGeoType}>
            <SelectTrigger className="w-44">
              <SelectValue placeholder="Area type" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="radius">Radius</SelectItem>
              <SelectItem value="city">City</SelectItem>
              <SelectItem value="zip">Zip</SelectItem>
              <SelectItem value="state">State</SelectItem>
              <SelectItem value="county">County</SelectItem>
            </SelectContent>
          </Select>
          {geoType === "radius" && (
            <div className="grid grid-cols-3 gap-3">
              <div className="grid gap-1.5">
                <Label className="text-xs">Center lat</Label>
                <Input
                  type="number"
                  step="any"
                  className="h-8"
                  value={centerLat}
                  onChange={(e) => setCenterLat(e.target.value)}
                />
              </div>
              <div className="grid gap-1.5">
                <Label className="text-xs">Center lon</Label>
                <Input
                  type="number"
                  step="any"
                  className="h-8"
                  value={centerLon}
                  onChange={(e) => setCenterLon(e.target.value)}
                />
              </div>
              <div className="grid gap-1.5">
                <Label className="text-xs">Radius (mi)</Label>
                <Input
                  type="number"
                  step="any"
                  className="h-8"
                  value={radiusMi}
                  onChange={(e) => setRadiusMi(e.target.value)}
                />
              </div>
            </div>
          )}
          {geoType === "city" && (
            <div className="grid grid-cols-2 gap-3">
              <div className="grid gap-1.5">
                <Label className="text-xs">City</Label>
                <Input
                  className="h-8"
                  value={city}
                  onChange={(e) => setCity(e.target.value)}
                />
              </div>
              <div className="grid gap-1.5">
                <Label className="text-xs">State code</Label>
                <Input
                  className="h-8"
                  placeholder="WA"
                  value={stateCode}
                  onChange={(e) => setStateCode(e.target.value)}
                />
              </div>
            </div>
          )}
          {geoType === "zip" && (
            <div className="grid gap-1.5">
              <Label className="text-xs">Zip</Label>
              <Input
                className="h-8 w-32"
                value={zip}
                onChange={(e) => setZip(e.target.value)}
              />
            </div>
          )}
          {geoType === "state" && (
            <div className="grid gap-1.5">
              <Label className="text-xs">State code</Label>
              <Input
                className="h-8 w-32"
                placeholder="WA"
                value={stateCode}
                onChange={(e) => setStateCode(e.target.value)}
              />
            </div>
          )}
          {geoType === "county" && (
            <div className="grid gap-1.5">
              <Label className="text-xs">County FIPS</Label>
              <Input
                className="h-8 w-32"
                placeholder="53033"
                value={countyFips}
                onChange={(e) => setCountyFips(e.target.value)}
              />
            </div>
          )}
        </div>
      </div>

      <Button type="submit" disabled={busy || !name.trim()}>
        {busy ? "Saving…" : initial ? "Save changes" : "Create buy-box"}
      </Button>
    </form>
  );
}
