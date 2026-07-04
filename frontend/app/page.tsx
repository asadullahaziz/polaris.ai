"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { useMe } from "@/lib/hooks";

// `/` is a pure switch: logged-out → /login, authenticated → /polaris-ai (the app home).
export default function Home() {
  const router = useRouter();
  const { data: me, isLoading } = useMe();

  useEffect(() => {
    if (isLoading) return;
    router.replace(me ? "/polaris-ai" : "/login");
  }, [me, isLoading, router]);

  return (
    <main className="flex min-h-svh items-center justify-center">
      <div className="w-64 space-y-3">
        <Skeleton className="h-6 w-40" />
        <Skeleton className="h-4 w-64" />
      </div>
    </main>
  );
}
