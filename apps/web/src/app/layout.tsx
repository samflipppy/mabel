import type { Metadata } from "next";
import { Fraunces, Source_Sans_3 } from "next/font/google";
import "./globals.css";
import { SiteShell } from "@/components/site-shell";

const display = Fraunces({
  variable: "--font-display",
  subsets: ["latin"],
  style: ["normal"],
});

const body = Source_Sans_3({
  variable: "--font-body",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Mabel — office",
  description: "Overnight recap and monthly report. The owner does not have to log in.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className={`${display.variable} ${body.variable} min-h-screen antialiased`}>
        <SiteShell>{children}</SiteShell>
      </body>
    </html>
  );
}
