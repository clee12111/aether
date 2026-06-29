"use client";

import { useEffect, useRef, useState } from "react";
import { checkReady } from "@/lib/api";

type Status = "checking" | "slow" | "ready" | "unreachable";

/**
 * Shows an amber banner when the backend is taking a long time to respond
 * (Render free-tier cold start). Auto-dismisses when the backend is up.
 */
export function ColdStartBanner() {
  const [status, setStatus] = useState<Status>("checking");
  const [dismissed, setDismissed] = useState(false);
  const slowTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    // Show the "waking up" message after 1.5s if not ready yet
    slowTimer.current = setTimeout(() => {
      setStatus((s) => (s === "checking" ? "slow" : s));
    }, 1_500);

    checkReady(40_000).then((ok) => {
      if (slowTimer.current) clearTimeout(slowTimer.current);
      setStatus(ok ? "ready" : "unreachable");
    });

    return () => {
      if (slowTimer.current) clearTimeout(slowTimer.current);
    };
  }, []);

  // Nothing to show while checking and backend hasn't been slow yet
  if (status === "checking" || dismissed) return null;

  // Backend came up before the slow timer fired — no banner needed
  if (status === "ready") return null;

  const isUnreachable = status === "unreachable";

  return (
    <div
      className={`flex items-center justify-between gap-3 px-4 py-2 text-xs ${
        isUnreachable
          ? "bg-red-50 border-b border-red-200 text-red-700"
          : "bg-amber-50 border-b border-amber-200 text-amber-800"
      }`}
      role="status"
    >
      <div className="flex items-center gap-2 min-w-0">
        {!isUnreachable && (
          <span className="w-3 h-3 shrink-0 border-2 border-amber-400 border-t-transparent rounded-full animate-spin" />
        )}
        <span>
          {isUnreachable
            ? "Backend is unreachable — check that the API server is running."
            : "Backend is waking up from cold start — first requests may take up to 30 seconds. Hang tight."}
        </span>
      </div>
      <button
        onClick={() => setDismissed(true)}
        className="shrink-0 text-[10px] font-medium underline underline-offset-2 opacity-60 hover:opacity-100 transition-opacity"
        aria-label="Dismiss"
      >
        dismiss
      </button>
    </div>
  );
}
