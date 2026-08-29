import type { Metadata } from "next";
import { Fraunces, Source_Sans_3 } from "next/font/google";
import "./globals.css";

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
  title: "Mabel",
  description: "Mabel answers the phone when a contractor can't.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      {/* No chrome here. The (app) and (marketing) groups each bring their
          own, because the portal's navigation has no business on a marketing
          page and vice versa. */}
      <body className={`${display.variable} ${body.variable} min-h-screen antialiased`}>
        {children}
      </body>
    </html>
  );
}
