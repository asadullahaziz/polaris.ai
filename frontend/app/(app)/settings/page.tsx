"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Lock, Pencil, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { BuyBoxForm, geoLabel, strategyLabel } from "@/components/buy-box-form";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
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
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import {
  changePassword,
  deleteBuyBox,
  listBuyBoxes,
  patchMe,
  type BuyBox,
  type Profile,
  type User,
} from "@/lib/api";
import { fmtMoney, useMe } from "@/lib/hooks";

const CHANNELS = [
  ["in_app", "In-app"],
  ["sms", "SMS"],
  ["email", "Email"],
  ["whatsapp", "WhatsApp"],
] as const;

export default function SettingsPage() {
  const { data: me } = useMe();

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-5xl p-6">
        <h1 className="mb-6 text-xl font-semibold">Settings</h1>
        {!me ? (
          <Skeleton className="h-64 w-full" />
        ) : (
          <Tabs defaultValue="account">
            <TabsList className="mb-4">
              <TabsTrigger value="account">Account</TabsTrigger>
              <TabsTrigger value="ai">AI</TabsTrigger>
              <TabsTrigger value="buy-boxes">Buy-boxes</TabsTrigger>
            </TabsList>
            <TabsContent value="account" className="grid gap-4">
              <ProfileCard me={me} />
              <PasswordCard />
              <EmailCard me={me} />
            </TabsContent>
            <TabsContent value="ai">
              <AiAgentCard me={me} />
            </TabsContent>
            <TabsContent value="buy-boxes">
              <BuyBoxesTab />
            </TabsContent>
          </Tabs>
        )}
      </div>
    </div>
  );
}

function ProfileCard({ me }: { me: User }) {
  const qc = useQueryClient();
  const [fullName, setFullName] = useState(me.full_name);
  const [phone, setPhone] = useState(me.phone);
  const [company, setCompany] = useState(me.profile.company);
  const [bio, setBio] = useState(me.profile.bio);
  const [avatarUrl, setAvatarUrl] = useState(me.profile.avatar_url);
  const [channel, setChannel] = useState<User["preferred_channel"]>(
    me.preferred_channel,
  );
  const [busy, setBusy] = useState(false);

  async function onSave(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      const updated = await patchMe({
        full_name: fullName,
        phone,
        company,
        bio,
        avatar_url: avatarUrl,
        preferred_channel: channel,
      });
      qc.setQueryData(["me"], updated);
      toast.success("Profile saved");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Save failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Profile</CardTitle>
        <CardDescription>How you appear to counterparties.</CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={onSave} className="grid gap-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="grid gap-1.5">
              <Label htmlFor="full-name">Full name</Label>
              <Input
                id="full-name"
                value={fullName}
                onChange={(e) => setFullName(e.target.value)}
              />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="phone">Phone</Label>
              <Input
                id="phone"
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
              />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="company">Company</Label>
              <Input
                id="company"
                value={company}
                onChange={(e) => setCompany(e.target.value)}
              />
            </div>
            <div className="grid gap-1.5">
              <Label>Preferred channel</Label>
              <Select
                value={channel}
                onValueChange={(v) => setChannel(v as User["preferred_channel"])}
              >
                <SelectTrigger className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {CHANNELS.map(([v, label]) => (
                    <SelectItem key={v} value={v}>
                      {label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="avatar-url">Avatar URL</Label>
            <Input
              id="avatar-url"
              value={avatarUrl}
              onChange={(e) => setAvatarUrl(e.target.value)}
            />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="bio">Bio</Label>
            <Textarea
              id="bio"
              rows={3}
              value={bio}
              onChange={(e) => setBio(e.target.value)}
            />
          </div>
          <div>
            <Button type="submit" disabled={busy}>
              {busy ? "Saving…" : "Save"}
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}

function PasswordCard() {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      await changePassword(current, next);
      toast.success("Password changed");
      setCurrent("");
      setNext("");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Password change failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Password</CardTitle>
      </CardHeader>
      <CardContent>
        <form onSubmit={onSubmit} className="grid gap-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="grid gap-1.5">
              <Label htmlFor="pw-current">Current password</Label>
              <Input
                id="pw-current"
                type="password"
                required
                autoComplete="current-password"
                value={current}
                onChange={(e) => setCurrent(e.target.value)}
              />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="pw-new">New password</Label>
              <Input
                id="pw-new"
                type="password"
                required
                autoComplete="new-password"
                value={next}
                onChange={(e) => setNext(e.target.value)}
              />
            </div>
          </div>
          <div>
            <Button type="submit" disabled={busy}>
              {busy ? "Changing…" : "Change password"}
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}

function EmailCard({ me }: { me: User }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Email</CardTitle>
      </CardHeader>
      <CardContent className="flex items-center gap-2">
        <span className="text-sm">{me.email}</span>
        <Badge variant={me.is_email_verified ? "secondary" : "outline"}>
          {me.is_email_verified ? "Verified" : "Unverified"}
        </Badge>
      </CardContent>
    </Card>
  );
}

function AiAgentCard({ me }: { me: User }) {
  const qc = useQueryClient();
  const [autoReply, setAutoReply] = useState(me.profile.auto_reply_when_away);
  const [autonomy, setAutonomy] = useState<Profile["agent_autonomy"]>(
    me.profile.agent_autonomy,
  );
  const [replyCap, setReplyCap] = useState(me.profile.agent_reply_cap);
  const [instructions, setInstructions] = useState(
    me.profile.agent_instructions,
  );
  const [busy, setBusy] = useState(false);

  async function onSave(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      const updated = await patchMe({
        auto_reply_when_away: autoReply,
        agent_autonomy: autonomy,
        agent_reply_cap: replyCap,
        agent_instructions: instructions,
      });
      qc.setQueryData(["me"], updated);
      toast.success("AI settings saved");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Save failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Your AI agent</CardTitle>
        <CardDescription>
          Govern how Polaris acts on your behalf.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={onSave} className="grid gap-6">
          <div className="flex items-start justify-between gap-4">
            <div>
              <p className="text-sm font-medium">Auto-reply when I&apos;m away</p>
              <p className="text-sm text-muted-foreground">
                When you&apos;re offline, Polaris answers incoming chat messages on
                your behalf.
              </p>
            </div>
            <Switch checked={autoReply} onCheckedChange={setAutoReply} />
          </div>

          <div className="grid gap-1.5">
            <Label>Autonomy</Label>
            <Select
              value={autonomy}
              onValueChange={(v) => setAutonomy(v as Profile["agent_autonomy"])}
            >
              <SelectTrigger className="w-56">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="draft_for_approval">
                  Draft for my approval
                </SelectItem>
                <SelectItem value="auto_send">Send automatically</SelectItem>
              </SelectContent>
            </Select>
            <p className="text-sm text-muted-foreground">
              Whether agent replies wait for your sign-off or go out on their
              own.
            </p>
          </div>

          <div className="grid gap-1.5">
            <div className="flex items-center justify-between">
              <Label>Reply cap</Label>
              <span className="text-sm tabular-nums">{replyCap}</span>
            </div>
            <Slider
              min={1}
              max={10}
              step={1}
              value={[replyCap]}
              onValueChange={([v]) => setReplyCap(v ?? 1)}
            />
            <p className="text-sm text-muted-foreground">
              How many replies your agent may send in a row before escalating to
              you (bounds agent-to-agent exchanges).
            </p>
          </div>

          <div className="grid gap-1.5">
            <Label htmlFor="agent-instructions">Instructions</Label>
            <Textarea
              id="agent-instructions"
              rows={4}
              value={instructions}
              onChange={(e) => setInstructions(e.target.value)}
            />
            <p className="text-sm text-muted-foreground">
              Global guidance injected into your copilot and away-agent (e.g.
              tone, priorities). Per-deal instructions live on each
              listing/buy-box.
            </p>
          </div>

          <div>
            <Button type="submit" disabled={busy}>
              {busy ? "Saving…" : "Save"}
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}

function criteriaSummary(b: BuyBox): string {
  const parts: string[] = [];
  if (b.price_min != null || b.price_max != null)
    parts.push(`${fmtMoney(b.price_min)} – ${fmtMoney(b.price_max)}`);
  if (b.beds_min != null) parts.push(`${b.beds_min}+ bd`);
  if (b.baths_min != null) parts.push(`${b.baths_min}+ ba`);
  if (b.sqft_min != null || b.sqft_max != null)
    parts.push(
      `${b.sqft_min?.toLocaleString() ?? "?"}–${b.sqft_max?.toLocaleString() ?? "?"} sqft`,
    );
  if (b.year_built_min != null) parts.push(`built ${b.year_built_min}+`);
  if (b.max_rehab_cost != null)
    parts.push(`rehab ≤ ${fmtMoney(b.max_rehab_cost)}`);
  return parts.join(" · ");
}

function BuyBoxesTab() {
  const qc = useQueryClient();
  const { data: boxes, isLoading } = useQuery({
    queryKey: ["buy-boxes"],
    queryFn: listBuyBoxes,
  });
  const [createOpen, setCreateOpen] = useState(false);
  const [editing, setEditing] = useState<BuyBox | null>(null);
  const [deleting, setDeleting] = useState<BuyBox | null>(null);

  function refresh() {
    qc.invalidateQueries({ queryKey: ["buy-boxes"] });
  }

  async function onDelete() {
    if (!deleting) return;
    try {
      await deleteBuyBox(deleting.buy_box_id);
      toast.success("Buy-box deleted");
      refresh();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Delete failed");
    } finally {
      setDeleting(null);
    }
  }

  return (
    <div className="grid gap-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">
          What you buy — Polaris uses these to match and rank deals for you.
        </p>
        <Button size="sm" onClick={() => setCreateOpen(true)}>
          <Plus className="size-4" />
          New buy-box
        </Button>
      </div>

      {isLoading && <Skeleton className="h-40 w-full" />}
      {!isLoading && (boxes?.length ?? 0) === 0 && (
        <p className="py-12 text-center text-sm text-muted-foreground">
          No buy-boxes yet — create one so Polaris can match deals to you.
        </p>
      )}

      {boxes?.map((b) => (
        <Card key={b.buy_box_id}>
          <CardHeader>
            <CardTitle className="flex flex-wrap items-center gap-2">
              {b.name}
              <Badge variant="secondary">{strategyLabel(b.strategy)}</Badge>
              <Badge variant={b.is_active ? "default" : "outline"}>
                {b.is_active ? "Active" : "Inactive"}
              </Badge>
              {b.is_primary && <Badge variant="outline">Primary</Badge>}
            </CardTitle>
            <CardDescription>{criteriaSummary(b)}</CardDescription>
            <CardAction className="flex gap-1">
              <Button
                variant="ghost"
                size="icon"
                aria-label="Edit"
                onClick={() => setEditing(b)}
              >
                <Pencil className="size-4" />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                aria-label="Delete"
                onClick={() => setDeleting(b)}
              >
                <Trash2 className="size-4" />
              </Button>
            </CardAction>
          </CardHeader>
          <CardContent className="grid gap-3">
            {b.property_types.length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                {b.property_types.map((t) => (
                  <Badge key={t} variant="secondary">
                    {t}
                  </Badge>
                ))}
              </div>
            )}
            {b.geos.length > 0 && (
              <div>
                <div className="flex flex-wrap gap-1.5">
                  {b.geos.map((g) => (
                    <Badge key={g.id} variant="outline">
                      {geoLabel(g)}
                    </Badge>
                  ))}
                </div>
                <p className="mt-1 text-xs text-muted-foreground">
                  Areas are read-only — manage additional areas by adding one
                  per save.
                </p>
              </div>
            )}
            {b.mandate && (
              <div className="rounded-md border border-dashed p-3">
                <div className="mb-1.5 flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
                  <Lock className="size-3" />
                  Private
                </div>
                <div className="flex flex-wrap items-center gap-1.5 text-sm">
                  {b.mandate.ceiling_price != null && (
                    <span>Ceiling {fmtMoney(b.mandate.ceiling_price)}</span>
                  )}
                  {b.mandate.must_haves.map((m) => (
                    <Badge key={m} variant="secondary">
                      {m}
                    </Badge>
                  ))}
                </div>
                {b.mandate.instructions && (
                  <p className="mt-1 truncate text-xs text-muted-foreground">
                    {b.mandate.instructions}
                  </p>
                )}
              </div>
            )}
          </CardContent>
        </Card>
      ))}

      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-xl">
          <DialogHeader>
            <DialogTitle>New buy-box</DialogTitle>
          </DialogHeader>
          <BuyBoxForm
            onDone={() => {
              setCreateOpen(false);
              refresh();
            }}
          />
        </DialogContent>
      </Dialog>

      <Dialog open={!!editing} onOpenChange={(o) => !o && setEditing(null)}>
        <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-xl">
          <DialogHeader>
            <DialogTitle>Edit buy-box</DialogTitle>
          </DialogHeader>
          {editing && (
            <BuyBoxForm
              key={editing.buy_box_id}
              initial={editing}
              onDone={() => {
                setEditing(null);
                refresh();
              }}
            />
          )}
        </DialogContent>
      </Dialog>

      <AlertDialog open={!!deleting} onOpenChange={(o) => !o && setDeleting(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete buy-box?</AlertDialogTitle>
            <AlertDialogDescription>
              &quot;{deleting?.name}&quot; and its areas and deal settings will
              be permanently removed.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={onDelete}>Delete</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
