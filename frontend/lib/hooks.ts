"use client";

import { useQuery } from "@tanstack/react-query";

import { fetchMe } from "@/lib/api";

/** The current session user (null = not logged in). Shared cache key: ["me"]. */
export function useMe() {
  return useQuery({ queryKey: ["me"], queryFn: fetchMe, staleTime: 60_000 });
}

export const fmtMoney = (n: number | string | null | undefined) =>
  n == null || n === ""
    ? "—"
    : `$${Math.round(Number(n)).toLocaleString()}`;

export const fmtDate = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleDateString() : "";

export const fmtDateTime = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleString() : "";

export const initials = (name: string) =>
  name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]!.toUpperCase())
    .join("") || "?";

export function uuid(): string {
  if (typeof crypto !== "undefined" && crypto.randomUUID) return crypto.randomUUID();
  return `${Date.now()}-${Math.random()}`;
}
