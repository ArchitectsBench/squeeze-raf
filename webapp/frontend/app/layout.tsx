import "./globals.css";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Squeeze Screener",
  description: "Multi-timeframe squeeze confluence matrix",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
