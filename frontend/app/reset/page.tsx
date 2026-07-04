"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { toast } from "sonner";

import { AuthShell } from "@/components/auth-shell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { confirmPasswordReset, requestPasswordReset } from "@/lib/api";

// Two modes: without ?token= it requests a reset email; with ?token= (the emailed
// link) it sets the new password.
function ResetInner() {
  const token = useSearchParams().get("token");
  return token ? <ConfirmForm token={token} /> : <RequestForm />;
}

function RequestForm() {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      await requestPasswordReset(email);
      setSent(true);
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthShell title="Reset password" description="We'll email you a reset link">
      {sent ? (
        <p className="text-sm text-muted-foreground">
          If an account exists for {email}, a reset link is on its way.
        </p>
      ) : (
        <form onSubmit={onSubmit} className="grid gap-4">
          <div className="grid gap-2">
            <Label htmlFor="email">Email</Label>
            <Input
              id="email"
              type="email"
              required
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </div>
          <Button type="submit" disabled={busy}>
            {busy ? "Sending…" : "Send reset link"}
          </Button>
          <p className="text-center text-sm text-muted-foreground">
            <Link href="/login" className="underline underline-offset-4">
              Back to sign in
            </Link>
          </p>
        </form>
      )}
    </AuthShell>
  );
}

function ConfirmForm({ token }: { token: string }) {
  const router = useRouter();
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await confirmPasswordReset(token, password);
      toast.success("Password reset — sign in with your new password.");
      router.push("/login");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Reset failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthShell title="Choose a new password">
      <form onSubmit={onSubmit} className="grid gap-4">
        <div className="grid gap-2">
          <Label htmlFor="password">New password</Label>
          <Input
            id="password"
            type="password"
            required
            autoComplete="new-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </div>
        {error && <p className="text-sm text-destructive">{error}</p>}
        <Button type="submit" disabled={busy}>
          {busy ? "Saving…" : "Set new password"}
        </Button>
      </form>
    </AuthShell>
  );
}

export default function ResetPage() {
  return (
    <Suspense>
      <ResetInner />
    </Suspense>
  );
}
