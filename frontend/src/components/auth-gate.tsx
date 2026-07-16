"use client";

import { FormEvent, ReactNode, useEffect, useState } from "react";
import { BookOpenText, LogIn, Sparkles, UserPlus } from "lucide-react";

import { authApi, type AuthUser } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import axios from "axios";

function extractApiError(err: unknown): string {
  if (axios.isAxiosError(err)) {
    const data = err.response?.data as { detail?: unknown } | undefined;
    const detail = data?.detail;
    if (typeof detail === "string" && detail.trim()) {
      return detail;
    }
    if (Array.isArray(detail)) {
      // FastAPI validation errors: [{loc, msg, type}, ...]
      return detail
        .map((item) => {
          if (item && typeof item === "object" && "msg" in item) {
            return String((item as { msg: string }).msg);
          }
          return String(item);
        })
        .filter(Boolean)
        .join("；");
    }
    if (err.response?.status === 403) {
      return "请求来源不被允许（CORS/CSRF）。请确认从当前前端地址访问。";
    }
    if (err.response?.status === 401) {
      return "用户名或密码错误";
    }
  }
  if (err instanceof Error && err.message) {
    return err.message;
  }
  return "";
}

export function AuthGate({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [registerMode, setRegisterMode] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    // Never stick on "正在验证会话": public tunnel / slow rewrite can hang without this.
    const timer = window.setTimeout(() => {
      if (!cancelled) {
        setUser(null);
        setLoading(false);
      }
    }, 8000);

    authApi
      .me()
      .then((response) => {
        if (!cancelled) setUser(response.data);
      })
      .catch(() => {
        if (!cancelled) setUser(null);
      })
      .finally(() => {
        if (!cancelled) {
          window.clearTimeout(timer);
          setLoading(false);
        }
      });

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    const form = new FormData(event.currentTarget);
    const username = String(form.get("username") || "").trim();
    const password = String(form.get("password") || "");
    const email = String(form.get("email") || "").trim();
    try {
      if (registerMode) {
        await authApi.register(username, email, password);
      }
      await authApi.login(username, password);
      const response = await authApi.me();
      setUser(response.data);
    } catch (err: unknown) {
      // Surface backend detail (400 username taken, 401 bad password, 403 origin, etc.)
      const detail = extractApiError(err);
      if (registerMode) {
        setError(detail || "注册或登录失败，请检查用户名/邮箱是否已被占用，密码至少 8 位");
      } else {
        setError(detail || "用户名或密码错误");
      }
    }
  }

  if (loading) {
    return (
      <div className="grid min-h-screen place-items-center text-sm text-muted-foreground motion-transition-content">
        正在验证会话...
      </div>
    );
  }

  if (!user) {
    return (
      <main className="relative grid min-h-screen place-items-center overflow-hidden bg-foreground px-4 text-background">
        <div className="absolute inset-0 opacity-30 [background-image:linear-gradient(hsl(42_35%_96%/0.1)_1px,transparent_1px),linear-gradient(90deg,hsl(42_35%_96%/0.1)_1px,transparent_1px)] [background-size:44px_44px]" />
        <div className="absolute left-[-8rem] top-[-8rem] size-[28rem] rounded-full bg-primary/20 blur-3xl" />
        <section className="relative w-full max-w-md rounded-[30px] border border-white/15 bg-white/[0.08] p-6 shadow-2xl backdrop-blur-xl motion-transition-spatial sm:p-8">
          <div className="mb-8 flex items-center gap-3"><span className="grid size-11 place-items-center rounded-2xl bg-background text-foreground"><BookOpenText className="size-5" /></span><div><h1 className="font-serif text-2xl font-semibold">NovelMind</h1><p className="text-xs uppercase tracking-[0.16em] text-white/45">Story intelligence</p></div></div>
          <div className="mb-6"><p className="flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.18em] text-[#f2a27b]"><Sparkles className="size-3.5" />Private workspace</p><h2 className="mt-2 font-serif text-3xl font-semibold">{registerMode ? "建立你的故事库" : "回到你的故事里"}</h2><p className="mt-2 text-sm text-white/55">{registerMode ? "创建账户，开始积累可检索的原文记忆。" : "登录后继续阅读、检索和评测。"}</p></div>
          <form className="mt-6 space-y-4" onSubmit={submit}>
            <Input className="h-11 border-white/15 bg-white/10 text-white placeholder:text-white/35" name="username" aria-label="用户名" placeholder="用户名" minLength={3} maxLength={50} required />
            {registerMode && <Input className="h-11 border-white/15 bg-white/10 text-white placeholder:text-white/35" name="email" aria-label="邮箱" type="email" placeholder="邮箱" required />}
            <Input className="h-11 border-white/15 bg-white/10 text-white placeholder:text-white/35" name="password" aria-label="密码" type="password" placeholder="密码" minLength={8} maxLength={100} required />
            {error && <p className="text-sm text-destructive">{error}</p>}
            <Button className="h-11 w-full rounded-xl" type="submit">
              {registerMode ? <UserPlus className="mr-2 h-4 w-4" /> : <LogIn className="mr-2 h-4 w-4" />}
              {registerMode ? "注册并登录" : "登录"}
            </Button>
          </form>
          <Button className="mt-2 w-full text-white/65 hover:bg-white/10 hover:text-white" variant="ghost" onClick={() => setRegisterMode((value) => !value)}>
            {registerMode ? "已有账户" : "创建账户"}
          </Button>
        </section>
      </main>
    );
  }

  // Logout lives on /settings (设置中心) — no floating chrome over reading/workspace.
  return <>{children}</>;
}
