"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const LINKS = [
  { href: "/inbound", label: "Inbound" },
  { href: "/outbound", label: "Outbound" },
  { href: "/testing", label: "Testing" },
  { href: "/architecture", label: "Architecture" },
] as const;

export function Nav() {
  const path = usePathname();

  return (
    <header className="border-b border-stone-200 bg-[var(--surface)] px-6 py-2.5 flex items-center gap-6 shrink-0">
      <Link href="/" className="flex items-center gap-2">
        <div className="w-7 h-7 rounded-lg bg-stone-900 flex items-center justify-center">
          <span className="text-white font-bold text-xs">A</span>
        </div>
        <span className="font-semibold text-sm text-stone-900 tracking-tight">
          Aether GTM
        </span>
      </Link>

      <nav className="flex items-center gap-1 ml-2">
        {LINKS.map(({ href, label }) => {
          const active = path.startsWith(href);
          return (
            <Link
              key={href}
              href={href}
              className={`px-3 py-1.5 text-xs font-medium rounded-md transition-colors ${
                active
                  ? "bg-stone-900 text-white"
                  : "text-stone-500 hover:text-stone-900 hover:bg-stone-100"
              }`}
            >
              {label}
            </Link>
          );
        })}
      </nav>

      {/* Three tabs only - no extra nav items */}
    </header>
  );
}
