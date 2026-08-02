import type { MetadataRoute } from "next";

const manifest: MetadataRoute.Manifest = {
  name: "NovelMind - AI 辅助小说创作与理解",
  short_name: "NovelMind",
  description: "让 AI 成为你的小说伙伴 —— 读懂故事、理清脉络、续写篇章",
  start_url: "/",
  scope: "/",
  display: "standalone",
  orientation: "portrait-primary",
  background_color: "#f8f5ed",
  theme_color: "#d96b42",
  icons: [
    { src: "/icons/icon-192.png", sizes: "192x192", type: "image/png" },
    { src: "/icons/icon-512.png", sizes: "512x512", type: "image/png" },
    {
      src: "/icons/maskable-512.png",
      sizes: "512x512",
      type: "image/png",
      purpose: "maskable",
    },
  ],
};

export default function getManifest(): MetadataRoute.Manifest {
  return manifest;
}
