import type { Metadata } from "next";
import "./globals.css";

import { Toaster } from "sonner";

import { Providers } from "./providers";

export const metadata: Metadata = {
  title: "Polaris AI",
  description: "AI-powered property & real-estate portal",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body>
        <Providers>{children}</Providers>
        <Toaster richColors position="top-center" />
      </body>
    </html>
  );
}
