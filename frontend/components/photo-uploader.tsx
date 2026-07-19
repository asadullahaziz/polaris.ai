"use client";

// Listing photo upload: presign → PUT direct to MinIO/S3 → collect public URL.
// The file bytes never touch the backend; see lib/api.ts uploadToStorage.

import { useRef, useState } from "react";
import { ImagePlus, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { presignUpload, uploadToStorage } from "@/lib/api";

export const ACCEPT = "image/jpeg,image/png,image/webp,image/gif";
// Mirrors STORAGE_MAX_UPLOAD_MB — the presign endpoint rejects larger anyway;
// this just fails fast before the file leaves the browser.
const MAX_UPLOAD_MB = 15;

export async function uploadPhotoFiles(
  files: File[],
  onProgress?: (done: number, total: number) => void,
): Promise<string[]> {
  const urls: string[] = [];
  let done = 0;
  for (const file of files) {
    try {
      if (file.size > MAX_UPLOAD_MB * 1024 * 1024) {
        toast.error(`${file.name}: larger than the ${MAX_UPLOAD_MB} MB limit`);
        continue;
      }
      const presign = await presignUpload({
        filename: file.name,
        content_type: file.type,
        size: file.size,
      });
      urls.push(await uploadToStorage(presign, file));
    } catch (e) {
      toast.error(
        `${file.name}: ${e instanceof Error ? e.message : "upload failed"}`,
      );
    } finally {
      done += 1;
      onProgress?.(done, files.length);
    }
  }
  return urls;
}

export function PhotoUploader({
  photos,
  onChange,
  disabled,
}: {
  photos: string[]; // public URLs in submit order
  onChange: (next: string[]) => void;
  disabled?: boolean;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(
    null,
  );

  const onFiles = async (list: FileList | null) => {
    const files = Array.from(list ?? []);
    if (files.length === 0) return;
    setProgress({ done: 0, total: files.length });
    try {
      const urls = await uploadPhotoFiles(files, (done, total) =>
        setProgress({ done, total }),
      );
      if (urls.length > 0) onChange([...photos, ...urls]);
    } finally {
      setProgress(null);
      if (inputRef.current) inputRef.current.value = "";
    }
  };

  const busy = progress !== null;

  return (
    <div className="space-y-3">
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
        disabled={disabled || busy}
        onClick={() => inputRef.current?.click()}
      >
        <ImagePlus className="mr-1.5 h-4 w-4" />
        {busy
          ? `Uploading ${Math.min(progress.done + 1, progress.total)}/${progress.total}…`
          : "Upload photos"}
      </Button>
      {(photos.length > 0 || busy) && (
        <div className="grid grid-cols-3 gap-2">
          {photos.map((url, i) => (
            <div key={url} className="group relative">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={url}
                alt={`Photo ${i + 1}`}
                className="h-24 w-full rounded-md object-cover"
              />
              <Button
                type="button"
                variant="destructive"
                size="icon"
                className="absolute right-1 top-1 h-6 w-6 opacity-0 transition-opacity group-hover:opacity-100"
                disabled={disabled || busy}
                onClick={() => onChange(photos.filter((u) => u !== url))}
              >
                <Trash2 className="h-3.5 w-3.5" />
              </Button>
            </div>
          ))}
          {busy &&
            Array.from({ length: progress.total - progress.done }, (_, i) => (
              <Skeleton key={`up-${i}`} className="h-24 w-full rounded-md" />
            ))}
        </div>
      )}
    </div>
  );
}
