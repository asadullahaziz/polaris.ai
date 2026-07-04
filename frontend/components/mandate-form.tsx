"use client";

import { Plus, X } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import type { MandateInput } from "@/lib/api";

// Empty number input → omit the key; if the saved mandate had a value, null clears it.
function numField(raw: string, hadValue: boolean): number | null | undefined {
  const t = raw.trim();
  if (t === "") return hadValue ? null : undefined;
  const n = Number(t);
  return Number.isFinite(n) ? n : undefined;
}

export function MandateForm({
  initial,
  onSubmit,
  submitLabel = "Save deal settings",
}: {
  initial?: MandateInput;
  onSubmit: (m: MandateInput) => Promise<void>;
  submitLabel?: string;
}) {
  const [floor, setFloor] = useState(
    initial?.floor_price != null ? String(initial.floor_price) : "",
  );
  const [ceiling, setCeiling] = useState(
    initial?.ceiling_price != null ? String(initial.ceiling_price) : "",
  );
  const [mustHaves, setMustHaves] = useState<string[]>(initial?.must_haves ?? []);
  const [pending, setPending] = useState("");
  const [availability, setAvailability] = useState(
    initial?.availability_window ?? "",
  );
  const [instructions, setInstructions] = useState(initial?.instructions ?? "");
  const [busy, setBusy] = useState(false);

  function addMustHave() {
    const v = pending.trim();
    if (v && !mustHaves.includes(v)) setMustHaves([...mustHaves, v]);
    setPending("");
  }

  async function submit() {
    const body: MandateInput = {
      must_haves: mustHaves,
      availability_window: availability.trim(),
      instructions: instructions.trim(),
    };
    const f = numField(floor, initial?.floor_price != null);
    if (f !== undefined) body.floor_price = f;
    const c = numField(ceiling, initial?.ceiling_price != null);
    if (c !== undefined) body.ceiling_price = c;
    setBusy(true);
    try {
      await onSubmit(body);
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : "Failed to save deal settings",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid gap-4">
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="grid gap-2">
          <Label htmlFor="mandate-floor">Floor price</Label>
          <Input
            id="mandate-floor"
            type="number"
            placeholder="e.g. 450000"
            value={floor}
            onChange={(e) => setFloor(e.target.value)}
          />
        </div>
        <div className="grid gap-2">
          <Label htmlFor="mandate-ceiling">Ceiling price</Label>
          <Input
            id="mandate-ceiling"
            type="number"
            placeholder="e.g. 520000"
            value={ceiling}
            onChange={(e) => setCeiling(e.target.value)}
          />
        </div>
      </div>

      <div className="grid gap-2">
        <Label htmlFor="mandate-musthave">Must-haves</Label>
        <div className="flex gap-2">
          <Input
            id="mandate-musthave"
            placeholder="e.g. cash offer, quick close"
            value={pending}
            onChange={(e) => setPending(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                addMustHave();
              }
            }}
          />
          <Button
            type="button"
            variant="secondary"
            onClick={addMustHave}
            disabled={!pending.trim()}
          >
            <Plus /> Add
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
                  className="hover:text-destructive"
                  onClick={() => setMustHaves(mustHaves.filter((x) => x !== m))}
                >
                  <X className="size-3" />
                </button>
              </Badge>
            ))}
          </div>
        )}
      </div>

      <div className="grid gap-2">
        <Label htmlFor="mandate-availability">Availability window</Label>
        <Input
          id="mandate-availability"
          placeholder="e.g. weekdays after 5pm, close within 30 days"
          value={availability}
          onChange={(e) => setAvailability(e.target.value)}
        />
      </div>

      <div className="grid gap-2">
        <Label htmlFor="mandate-instructions">Instructions for your agent</Label>
        <Textarea
          id="mandate-instructions"
          rows={3}
          placeholder="Anything your agent should factor in when negotiating…"
          value={instructions}
          onChange={(e) => setInstructions(e.target.value)}
        />
      </div>

      <div>
        <Button type="button" onClick={submit} disabled={busy}>
          {busy ? "Saving…" : submitLabel}
        </Button>
      </div>
    </div>
  );
}
