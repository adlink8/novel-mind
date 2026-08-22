"use client";

import { ReactNode, useEffect, useState } from "react";

import { authApi } from "@/lib/api";

/**
 * Establish the local single-user session before product requests start.
 *
 * The desktop/local product has no interactive sign-in gate. Backend user
 * identity is still bootstrapped so owner-scoped books and model settings keep
 * their existing isolation semantics.
 */
export function AuthGate({ children }: { children: ReactNode }) {
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const timer = window.setTimeout(() => {
      if (!cancelled) setReady(true);
    }, 8000);

    void (async () => {
      try {
        await authApi.me();
      } catch {
        // Local development and the desktop app use the configured workspace
        // identity. There is intentionally no username/password fallback UI.
        try {
          await authApi.localAutoLogin();
        } catch {
          // Do not revive the removed login gate. Individual API calls retain
          // their normal error handling if local session bootstrap is broken.
        }
      } finally {
        if (!cancelled) {
          window.clearTimeout(timer);
          setReady(true);
        }
      }
    })();

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, []);

  if (!ready) {
    return (
      <div className="grid min-h-screen place-items-center text-sm text-muted-foreground motion-transition-content">
        正在启动工作区...
      </div>
    );
  }

  return <>{children}</>;
}
