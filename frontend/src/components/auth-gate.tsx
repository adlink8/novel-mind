"use client";

import { FormEvent, ReactNode, useEffect, useState } from "react";
import { LogIn, LogOut, UserPlus } from "lucide-react";

import { authApi, type AuthUser } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

export function AuthGate({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [registerMode, setRegisterMode] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    authApi.me()
      .then((response) => setUser(response.data))
      .catch(() => setUser(null))
      .finally(() => setLoading(false));
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    const form = new FormData(event.currentTarget);
    const username = String(form.get("username") || "");
    const password = String(form.get("password") || "");
    try {
      if (registerMode) {
        await authApi.register(username, String(form.get("email") || ""), password);
      }
      await authApi.login(username, password);
      const response = await authApi.me();
      setUser(response.data);
    } catch {
      setError(registerMode ? "注册或登录失败，请检查输入" : "用户名或密码错误");
    }
  }

  async function logout() {
    await authApi.logout();
    setUser(null);
  }

  if (loading) {
    return <div className="grid min-h-screen place-items-center text-sm text-muted-foreground">正在验证会话...</div>;
  }

  if (!user) {
    return (
      <main className="grid min-h-screen place-items-center bg-background px-4">
        <section className="w-full max-w-sm border bg-card p-6 shadow-sm">
          <h1 className="text-xl font-semibold">NovelMind</h1>
          <p className="mt-1 text-sm text-muted-foreground">{registerMode ? "创建账户" : "登录后访问你的小说库"}</p>
          <form className="mt-6 space-y-4" onSubmit={submit}>
            <Input name="username" placeholder="用户名" minLength={3} maxLength={50} required />
            {registerMode && <Input name="email" type="email" placeholder="邮箱" required />}
            <Input name="password" type="password" placeholder="密码" minLength={8} maxLength={100} required />
            {error && <p className="text-sm text-destructive">{error}</p>}
            <Button className="w-full" type="submit">
              {registerMode ? <UserPlus className="mr-2 h-4 w-4" /> : <LogIn className="mr-2 h-4 w-4" />}
              {registerMode ? "注册并登录" : "登录"}
            </Button>
          </form>
          <Button className="mt-2 w-full" variant="ghost" onClick={() => setRegisterMode((value) => !value)}>
            {registerMode ? "已有账户" : "创建账户"}
          </Button>
        </section>
      </main>
    );
  }

  return (
    <>
      {children}
      <Button className="fixed right-4 top-14 z-40" size="sm" variant="outline" onClick={logout} title="退出登录">
        <LogOut className="h-4 w-4" />
        <span className="ml-2 hidden sm:inline">{user.username}</span>
      </Button>
    </>
  );
}
