"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { AuthShell } from "@/components/auth-shell";
import { Button } from "@/components/ui/button";
import { primeCsrf, verifyEmail } from "@/lib/api";

function VerifyInner() {
  const token = useSearchParams().get("token");
  const [state, setState] = useState<"working" | "ok" | "error">("working");
  const [detail, setDetail] = useState("");

  useEffect(() => {
    if (!token) {
      setState("error");
      setDetail("Missing verification token.");
      return;
    }
    primeCsrf()
      .then(() => verifyEmail(token))
      .then((res) => {
        setState("ok");
        setDetail(res.detail);
      })
      .catch((err) => {
        setState("error");
        setDetail(err instanceof Error ? err.message : "Verification failed.");
      });
  }, [token]);

  return (
    <AuthShell title="Email verification">
      <div className="grid gap-4 text-sm">
        {state === "working" && <p className="text-muted-foreground">Verifying…</p>}
        {state === "ok" && <p>{detail || "Email verified — you can sign in now."}</p>}
        {state === "error" && <p className="text-destructive">{detail}</p>}
        {state !== "working" && (
          <Button asChild>
            <Link href="/login">Go to sign in</Link>
          </Button>
        )}
      </div>
    </AuthShell>
  );
}

export default function VerifyPage() {
  return (
    <Suspense>
      <VerifyInner />
    </Suspense>
  );
}
