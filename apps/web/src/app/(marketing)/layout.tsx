/**
 * hiremabel.com. Static and server-rendered, per 00-STACK.md.
 *
 * Separate from the (app) group so the marketing pages carry no auth check,
 * no query client, and no portal navigation.
 */

import { SiteShell } from "@/components/site-shell";

export default function MarketingLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return <SiteShell>{children}</SiteShell>;
}
