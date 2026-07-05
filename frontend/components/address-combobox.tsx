"use client";

import { Command as CommandPrimitive } from "cmdk";
import { MapPin } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import {
  Command,
  CommandEmpty,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { searchProperties, type PropertySearchResult } from "@/lib/api";
import { cn } from "@/lib/utils";

const fmtPrice = (v: number | null) =>
  v == null ? null : `$${Math.round(v).toLocaleString()}`;

function detailLine(p: PropertySearchResult) {
  const bits = [
    p.beds != null && `${p.beds} bd`,
    p.baths != null && `${p.baths} ba`,
    p.sqft != null && `${p.sqft.toLocaleString()} sqft`,
    p.last_sale_price != null && `last sale ${fmtPrice(p.last_sale_price)}`,
  ].filter(Boolean);
  return bits.join(" · ");
}

/** Closed-world address autocomplete over /api/properties/search — the platform
 * has no geocoder, so suggestions come from the known property universe. Free
 * typing is still allowed; picking a suggestion fires onSelect with the record. */
export function AddressCombobox({
  id,
  value,
  onChange,
  onSelect,
  placeholder,
  disabled,
}: {
  id?: string;
  value: string;
  onChange: (address: string) => void;
  onSelect: (p: PropertySearchResult) => void;
  placeholder?: string;
  disabled?: boolean;
}) {
  const [results, setResults] = useState<PropertySearchResult[]>([]);
  const [open, setOpen] = useState(false);
  const seq = useRef(0); // drop stale responses
  const picked = useRef(false); // suppress the search after a selection

  useEffect(() => {
    if (picked.current) {
      picked.current = false;
      return;
    }
    const q = value.trim();
    if (q.length < 2) {
      setResults([]);
      setOpen(false);
      return;
    }
    const mine = ++seq.current;
    const t = setTimeout(() => {
      searchProperties(q)
        .then((res) => {
          if (seq.current !== mine) return;
          setResults(res.results);
          setOpen(true);
        })
        .catch(() => {
          if (seq.current === mine) setResults([]);
        });
    }, 250);
    return () => clearTimeout(t);
  }, [value]);

  function pick(p: PropertySearchResult) {
    picked.current = true;
    setOpen(false);
    setResults([]);
    onSelect(p);
  }

  return (
    <Command
      shouldFilter={false}
      className="relative overflow-visible bg-transparent"
    >
      <CommandPrimitive.Input
        id={id}
        value={value}
        onValueChange={onChange}
        placeholder={placeholder}
        disabled={disabled}
        onFocus={() => {
          if (results.length > 0) setOpen(true);
        }}
        onBlur={() => {
          // Delay so a mousedown on an item still lands before we close.
          setTimeout(() => setOpen(false), 150);
        }}
        className={cn(
          "h-9 w-full min-w-0 rounded-md border border-input bg-transparent px-3 py-1 text-base shadow-xs transition-[color,box-shadow] outline-none placeholder:text-muted-foreground disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50 md:text-sm dark:bg-input/30",
          "focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50",
        )}
      />
      {open && (
        <div className="absolute top-full z-50 mt-1 w-full rounded-md border bg-popover text-popover-foreground shadow-md">
          <CommandList>
            <CommandEmpty>
              No matching address — you can still search free-text.
            </CommandEmpty>
            {results.map((p) => (
              <CommandItem
                key={p.id}
                value={String(p.id)}
                onSelect={() => pick(p)}
                className="cursor-pointer items-start px-3 py-2"
              >
                <MapPin className="mt-0.5 size-4 shrink-0" />
                <div className="min-w-0">
                  <p className="truncate font-medium">{p.address_raw}</p>
                  <p className="truncate text-xs text-muted-foreground">
                    {detailLine(p)}
                  </p>
                </div>
              </CommandItem>
            ))}
          </CommandList>
        </div>
      )}
    </Command>
  );
}
