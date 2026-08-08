"use client";

/**
 * 阅读器 AI 会话列布局 Hook。
 *
 * 从 `app/novels/[id]/page.tsx` 拆分而来：负责会话列的开合 / 折叠 / 宽度
 * 状态（presentation only，真相在 PostgreSQL，Phase 10），桌面判定，
 * 宽度拖拽 resize 的 3 个 pointer handler，以及 localStorage 持久化 effect。
 */

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from "react";
import {
  clampReaderChatWidth,
  loadReaderChatPresentation,
  READER_CHAT_WIDTH_DEFAULT,
  saveReaderChatPresentation,
} from "@/lib/reader-selection";

export function useReaderChatLayout(novelId: string) {
  const [isDesktop, setIsDesktop] = useState(() => {
    if (typeof window === "undefined") return true;
    return window.innerWidth >= 1280;
  });

  useEffect(() => {
    const onResize = () => setIsDesktop(window.innerWidth >= 1280);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  // Phase 10 reader chat — presentation only in localStorage; truth is PostgreSQL
  const [chatOpen, setChatOpen] = useState(() => {
    if (typeof window === "undefined") return false;
    return Boolean(loadReaderChatPresentation(novelId).open);
  });
  const [chatCollapsed, setChatCollapsed] = useState(() => {
    if (typeof window === "undefined") return false;
    const saved = loadReaderChatPresentation(novelId).collapsed;
    // 无存档时，较窄桌面（<1536px）默认收成轨道：目录 + 面板同开会挤窄正文
    return saved ?? window.innerWidth < 1536;
  });
  const [chatWidthPx, setChatWidthPx] = useState(() => {
    if (typeof window === "undefined") return READER_CHAT_WIDTH_DEFAULT;
    const saved = loadReaderChatPresentation(novelId).panelWidthPx;
    return clampReaderChatWidth(saved ?? READER_CHAT_WIDTH_DEFAULT);
  });
  const chatResizeRef = useRef<{ startX: number; startW: number } | null>(null);

  // Persist chat presentation width (desktop).
  useEffect(() => {
    const prev = loadReaderChatPresentation(novelId);
    saveReaderChatPresentation(novelId, {
      ...prev,
      open: chatOpen,
      collapsed: chatCollapsed,
      panelWidthPx: chatWidthPx,
    });
  }, [novelId, chatOpen, chatCollapsed, chatWidthPx]);

  const onChatResizePointerDown = useCallback(
    (e: ReactPointerEvent<HTMLDivElement>) => {
      e.preventDefault();
      chatResizeRef.current = { startX: e.clientX, startW: chatWidthPx };
      const target = e.currentTarget;
      target.setPointerCapture(e.pointerId);
      document.body.style.cursor = "col-resize";
      document.body.style.userSelect = "none";
    },
    [chatWidthPx]
  );

  const onChatResizePointerMove = useCallback(
    (e: ReactPointerEvent<HTMLDivElement>) => {
      const drag = chatResizeRef.current;
      if (!drag) return;
      // Dragging the left handle: move left → wider panel.
      const delta = drag.startX - e.clientX;
      setChatWidthPx(clampReaderChatWidth(drag.startW + delta));
    },
    []
  );

  const onChatResizePointerUp = useCallback(
    (e: ReactPointerEvent<HTMLDivElement>) => {
      if (!chatResizeRef.current) return;
      chatResizeRef.current = null;
      try {
        e.currentTarget.releasePointerCapture(e.pointerId);
      } catch {
        /* ignore */
      }
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    },
    []
  );

  return {
    isDesktop,
    chatOpen,
    setChatOpen,
    chatCollapsed,
    setChatCollapsed,
    chatWidthPx,
    setChatWidthPx,
    onChatResizePointerDown,
    onChatResizePointerMove,
    onChatResizePointerUp,
  };
}
