"use client";

/**
 * 设置中心 · 账户区块 — 当前登录用户展示与退出登录。
 */

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { LoaderCircle, LogOut, UserRound } from "lucide-react";

import { Button } from "@/components/ui/button";
import { authApi, type AuthUser } from "@/lib/api";
import { SettingsSection } from "./settings-section";

export function AccountSection({ chapter }: { chapter: string }) {
  const router = useRouter();
  const [user, setUser] = useState<AuthUser | null>(null);
  const [userLoading, setUserLoading] = useState(true);
  const [logoutLoading, setLogoutLoading] = useState(false);
  const [logoutError, setLogoutError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const res = await authApi.me();
        if (!cancelled) setUser(res.data);
      } catch {
        if (!cancelled) setUser(null);
      } finally {
        if (!cancelled) setUserLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const handleLogout = useCallback(async () => {
    setLogoutLoading(true);
    setLogoutError(null);
    try {
      await authApi.logout();
      // Push + refresh so AuthGate re-validates the session on the next request.
      router.push("/");
      router.refresh();
    } catch {
      setLogoutError("退出失败，请重试");
      setLogoutLoading(false);
    }
  }, [router]);

  return (
    <SettingsSection chapter={chapter} title="账户">
      <div className="paper-surface motion-transition-feedback flex flex-col gap-4 rounded-3xl p-5 sm:flex-row sm:items-center sm:justify-between sm:p-6">
        <div className="flex items-center gap-3">
          <span className="grid size-11 place-items-center rounded-2xl bg-secondary text-primary">
            <UserRound className="size-5" />
          </span>
          <div>
            <p className="font-serif text-base font-semibold">
              {userLoading ? "加载账户…" : user ? user.username : "未登录"}
            </p>
            {user?.email ? (
              <p className="text-xs text-muted-foreground">{user.email}</p>
            ) : (
              <p className="text-xs text-muted-foreground">
                退出后需重新登录才能访问书架与分析
              </p>
            )}
          </div>
        </div>
        <div className="flex flex-col items-stretch gap-2 sm:items-end">
          {logoutError ? (
            <p className="text-xs text-destructive" role="alert">
              {logoutError}
            </p>
          ) : null}
          <Button
            type="button"
            variant="outline"
            className="rounded-full motion-transition-feedback"
            disabled={logoutLoading || userLoading || !user}
            onClick={() => void handleLogout()}
            data-testid="settings-logout"
          >
            {logoutLoading ? (
              <LoaderCircle className="mr-2 size-4 animate-spin" />
            ) : (
              <LogOut className="mr-2 size-4" />
            )}
            退出登录
          </Button>
        </div>
      </div>
    </SettingsSection>
  );
}
