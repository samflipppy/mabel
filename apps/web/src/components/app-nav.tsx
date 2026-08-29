"use client";

/**
 * The seven-item navigation from 02-PORTAL.md. Seven items, not a mega-menu.
 *
 * Design constraints from that document, applied here rather than left to each
 * screen: 16px minimum text, 48px touch targets, and nothing behind more than
 * two clicks — which is why every one of these is a top-level destination
 * rather than a section inside another.
 */

import Link from "next/link";
import { usePathname } from "next/navigation";

const ITEMS = [
  { href: "/dashboard", label: "Dashboard" },
  { href: "/calls", label: "Calls" },
  { href: "/leads", label: "Leads" },
  { href: "/customers", label: "Customers" },
  { href: "/mabel", label: "Mabel" },
  { href: "/reports", label: "Reports" },
  { href: "/settings", label: "Settings" },
] as const;

export function AppNav() {
  const pathname = usePathname();

  return (
    <nav
      aria-label="Main"
      className="border-b border-[var(--line)] bg-[var(--cream)]"
    >
      <div className="mx-auto flex max-w-6xl items-center gap-1 overflow-x-auto px-4">
        <Link
          href="/dashboard"
          className="mr-4 shrink-0 font-serif text-xl text-[var(--charcoal)]"
        >
          Mabel
        </Link>
        {ITEMS.map((item) => {
          const active =
            pathname === item.href || pathname.startsWith(`${item.href}/`);
          return (
            <Link
              key={item.href}
              href={item.href}
              aria-current={active ? "page" : undefined}
              // 48px minimum touch target, and 16px text. Both are from
              // 02-PORTAL.md and both are about a phone in sunlight.
              className={[
                "flex min-h-[48px] shrink-0 items-center px-4 text-base",
                "border-b-2 transition-colors",
                active
                  ? "border-[var(--charcoal)] font-medium text-[var(--charcoal)]"
                  : "border-transparent text-[var(--taupe)] hover:text-[var(--charcoal)]",
              ].join(" ")}
            >
              {item.label}
            </Link>
          );
        })}
      </div>
    </nav>
  );
}
