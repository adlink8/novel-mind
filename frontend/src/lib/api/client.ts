/**
 * API 客户端基础：axios 实例、鉴权 token 持久化、请求拦截器、auth 域。
 *
 * 基础配置:
 * - baseURL: 默认 "/api"（走 Next.js rewrite 代理）。桌面模式下由
 *   RuntimeEndpointResolver 在运行时解析真实 loopback 端点（D-44-01）——
 *   endpoint 是 session bootstrap 数据，绝不冻结进构建期 NEXT_PUBLIC_*。
 * - timeout: 30 秒
 * - Content-Type: application/json（上传时自动切换为 multipart/form-data）
 */

import axios from "axios";
import { endpointResolver } from "../runtime/endpoint-resolver";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "/api";
const AUTH_TOKEN_KEY = "novelmind_access_token";

export const api = axios.create({
  baseURL: API_BASE,
  timeout: 30000,
  withCredentials: true,
  headers: { "Content-Type": "application/json" },
});

/**
 * 桌面端端点拦截器（44-01）：在每次请求前用运行时 bootstrap 解析真实
 * apiBaseUrl（动态 loopback 端口 + /api 前缀），覆盖构建期默认的相对路径。
 * 注册在 token 拦截器之前；浏览器模式/未就绪/失效时保持默认 baseURL，
 * 请求按现有语义失败（绝不伪造端点或回退明文）。
 */
api.interceptors.request.use(async (config) => {
  const resolution = await endpointResolver.resolve();
  if (resolution.kind === "desktop") {
    config.baseURL = resolution.endpoints.apiBaseUrl;
  }
  return config;
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
