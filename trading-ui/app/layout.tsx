import type { Metadata } from "next";

import AuthGate from "@/components/AuthGate";
import RootLayoutLogger from "@/components/RootLayoutLogger";

import "./globals.css";

export const metadata: Metadata = {
  title: "Trading Intelligence SaaS",
  description: "Institutional-grade market intelligence for disciplined traders.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className="antialiased">
        <RootLayoutLogger />
        <AuthGate>{children}</AuthGate>
      </body>
    </html>
  );
}
