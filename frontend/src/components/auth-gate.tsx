"use client";

import { ReactNode, useEffect, useState } from "react";
import { authApi } from "@/lib/api";
import { LoginCard } from "@/components/login-card";

/**
 * 统一认证门禁组件：
 * 1. 尝试使用现有 Cookie / Token 校验会话（authApi.me()）；
 * 2. 若失败，尝试本地/桌面免密登录模式（authApi.localAutoLogin()）；
 * 3. 若均未通过，则展示登录卡片，用户验证成功后方可进入系统。
 */
export function AuthGate({ children }: { children: ReactNode }) {
  const [ready, setReady] = useState(false);
  const [authed, setAuthed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const timer = window.setTimeout(() => {
      if (!cancelled) setReady(true);
    }, 8000);

    void (async () => {
      try {
        await authApi.me();
        if (!cancelled) setAuthed(true);
      } catch {
        try {
          await authApi.localAutoLogin();
          if (!cancelled) setAuthed(true);
        } catch {
          if (!cancelled) setAuthed(false);
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

  if (!authed) {
    return <LoginCard onSuccess={() => setAuthed(true)} />;
  }

  return <>{children}</>;
}
