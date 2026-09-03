"use client";

import { useState } from "react";
import { BookOpenText, Lock, User as UserIcon, Loader2 } from "lucide-react";
import { authApi } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

interface LoginCardProps {
  onSuccess: () => void;
}

export function LoginCard({ onSuccess }: LoginCardProps) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!username.trim() || !password) {
      setError("请输入用户名和密码");
      return;
    }

    setLoading(true);
    setError(null);

    try {
      await authApi.login(username.trim(), password);
      onSuccess();
    } catch (err: unknown) {
      const axiosError = err as { response?: { data?: { detail?: string } } };
      const detail = axiosError.response?.data?.detail;
      setError(detail || "用户名或密码错误，请重试");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center p-4 bg-background">
      <Card className="w-full max-w-sm border-border/80 bg-card/90 shadow-2xl backdrop-blur-xl">
        <CardHeader className="space-y-2 text-center">
          <div className="mx-auto grid size-12 place-items-center rounded-2xl bg-foreground text-background shadow-md">
            <BookOpenText className="size-6" />
          </div>
          <CardTitle className="font-serif text-2xl font-bold tracking-tight text-foreground">
            NovelMind
          </CardTitle>
          <CardDescription className="text-xs text-muted-foreground">
            AI 辅助小说创作与深度理解工作区
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4">
            {error && (
              <div className="rounded-xl bg-destructive/15 border border-destructive/25 p-3 text-xs text-destructive text-center font-medium">
                {error}
              </div>
            )}
            <div className="space-y-1.5 text-left">
              <label className="text-xs font-medium text-muted-foreground" htmlFor="username">
                用户名
              </label>
              <div className="relative">
                <UserIcon className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  id="username"
                  type="text"
                  placeholder="admin"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  disabled={loading}
                  className="pl-9 bg-background/50"
                  autoFocus
                  required
                />
              </div>
            </div>
            <div className="space-y-1.5 text-left">
              <label className="text-xs font-medium text-muted-foreground" htmlFor="password">
                密码
              </label>
              <div className="relative">
                <Lock className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  id="password"
                  type="password"
                  placeholder="••••••••"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  disabled={loading}
                  className="pl-9 bg-background/50"
                  required
                />
              </div>
            </div>
            <Button
              type="submit"
              disabled={loading}
              className="w-full mt-2 font-medium tracking-wide"
            >
              {loading ? (
                <>
                  <Loader2 className="mr-2 size-4 animate-spin" />
                  正在验证身份...
                </>
              ) : (
                "进入工作区"
              )}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
