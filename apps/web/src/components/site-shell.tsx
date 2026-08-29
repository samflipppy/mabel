import Link from "next/link";
import { Button } from "@/components/ui/button";

export function SiteShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen">
      <header className="border-b border-[color:var(--line)]">
        <div className="mx-auto flex max-w-4xl items-center justify-between gap-4 px-6 py-4">
          <Link href="/" className="font-serif text-2xl tracking-tight">
            Mabel
          </Link>
          <nav className="flex items-center gap-2 text-sm">
            <Button variant="ghost" size="sm" asChild>
              <Link href="/">Overnight</Link>
            </Button>
            <Button variant="ghost" size="sm" asChild>
              <Link href="/settings">Settings</Link>
            </Button>
            <Button variant="ghost" size="sm" asChild>
              <Link href="/report">Monthly report</Link>
            </Button>
            <Button size="sm" asChild>
              <a href="https://hiremabel.com">Hire Mabel</a>
            </Button>
          </nav>
        </div>
      </header>
      <main className="mx-auto max-w-4xl px-6 py-10">{children}</main>
    </div>
  );
}
