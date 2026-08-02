import {
  CacheFirst,
  ExpirationPlugin,
  NetworkFirst,
  Serwist,
  StaleWhileRevalidate,
} from "serwist";
import type { PrecacheEntry } from "serwist";

declare const self: {
  __SW_MANIFEST: (PrecacheEntry | string)[] | undefined;
};

const shellCache = new CacheFirst({
  cacheName: "novelmind-shell-v1",
  plugins: [new ExpirationPlugin({ maxEntries: 120, maxAgeSeconds: 60 * 60 * 24 * 30 })],
});

const navigationCache = new NetworkFirst({
  cacheName: "novelmind-shell-v1",
  networkTimeoutSeconds: 3,
  plugins: [new ExpirationPlugin({ maxEntries: 30, maxAgeSeconds: 60 * 60 * 24 * 7 })],
});

const apiCache = new NetworkFirst({
  cacheName: "novelmind-api-v1",
  networkTimeoutSeconds: 3,
  plugins: [new ExpirationPlugin({ maxEntries: 60, maxAgeSeconds: 60 * 60 * 24 })],
});

const serwist = new Serwist({
  precacheEntries: self.__SW_MANIFEST,
  skipWaiting: true,
  clientsClaim: true,
  fallbacks: {
    entries: [
      {
        url: "/offline.html",
        matcher: ({ request }) => request.mode === "navigate",
      },
    ],
  },
  runtimeCaching: [
    {
      matcher: ({ request }) => request.mode === "navigate",
      handler: navigationCache,
    },
    {
      matcher: ({ request, url }) =>
        request.destination === "script" ||
        request.destination === "style" ||
        request.destination === "font" ||
        request.destination === "image" ||
        url.pathname.startsWith("/_next/static/"),
      handler: shellCache,
    },
    {
      matcher: ({ url }) => url.pathname.startsWith("/api/"),
      handler: apiCache,
    },
    {
      matcher: ({ request }) => request.destination === "document",
      handler: new StaleWhileRevalidate({
        cacheName: "novelmind-shell-v1",
        plugins: [new ExpirationPlugin({ maxEntries: 30, maxAgeSeconds: 60 * 60 * 24 * 7 })],
      }),
    },
  ],
});

serwist.addEventListeners();
