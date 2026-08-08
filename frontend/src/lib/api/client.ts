/**
 * API 客户端基础：axios 实例、鉴权 token 持久化、请求拦截器、auth 域。
 *
 * 基础配置:
 * - baseURL: 通过 NEXT_PUBLIC_API_URL 环境变量配置，默认 "/api"（走 Next.js rewrite 代理）
 * - timeout: 30 秒
 * - Content-Type: application/json（上传时自动切换为 multipart/form-data）
 */

import axios from "axios";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "/api";
const AUTH_TOKEN_KEY = "novelmind_access_token";

export const api = axios.create({
  baseURL: API_BASE,
  timeout: 30000,
  withCredentials: true,
  headers: { "Content-Type": "application/json" },
});

/** Persist JWT so API calls can use Bearer (avoids cookie CSRF Origin mismatches). */
export function setAccessToken(token: string | null) {
  if (typeof window === "undefined") return;
  if (token) {
    window.sessionStorage.setItem(AUTH_TOKEN_KEY, token);
  } else {
    window.sessionStorage.removeItem(AUTH_TOKEN_KEY);
  }
}

export function getAccessToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.sessionStorage.getItem(AUTH_TOKEN_KEY);
}

api.interceptors.request.use((config) => {
  const token = getAccessToken();
  if (token) {
    config.headers = config.headers ?? {};
    config.headers.Authorization = `Bearer ${token}`;
  }
  // Let the browser set multipart boundary; a fixed Content-Type breaks uploads.
  if (typeof FormData !== "undefined" && config.data instanceof FormData) {
    if (config.headers && "Content-Type" in config.headers) {
      delete (config.headers as Record<string, unknown>)["Content-Type"];
    }
  }
  return config;
});

export interface AuthUser {
  id: number;
  username: string;
  email: string;
  is_active: boolean;
}

export interface LoginResponse {
  access_token: string;
  token_type: string;
  user_id: number;
  username: string;
}

export const authApi = {
  me: () => api.get<AuthUser>("/auth/me"),
  login: async (username: string, password: string) => {
    const res = await api.post<LoginResponse>("/auth/login", { username, password });
    setAccessToken(res.data.access_token);
    return res;
  },
  register: (username: string, email: string, password: string) =>
    api.post<AuthUser>("/auth/register", { username, email, password }),
  logout: async () => {
    try {
      await api.post("/auth/logout");
    } finally {
      setAccessToken(null);
    }
  },
};
