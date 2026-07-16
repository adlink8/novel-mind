"use client";

import {
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type RefObject,
} from "react";

/** Spatial token (300ms) — keep exit presence aligned with --motion-duration-spatial. */
export const DISMISSABLE_PRESENCE_MS = 300;

export type DismissableLayerOptions = {
  /** Controlled open from the business owner — always authoritative. */
  open: boolean;
  /** Called when the layer should close (outside / Escape). Never invents open state. */
  onDismiss: () => void;
  /** Panel root; outside detection uses composed path / containment. */
  layerRef: RefObject<HTMLElement | null>;
  /** Optional trigger; clicks on the trigger are not treated as outside. */
  triggerRef?: RefObject<HTMLElement | null>;
  /** Extra elements that must not dismiss (e.g. [data-reader-chat-toggle]). */
  ignoreSelectors?: string[];
  /** Default true. Backdrop-owned surfaces may set false and handle outside via backdrop. */
  closeOnOutside?: boolean;
  /** Default true. */
  closeOnEscape?: boolean;
  /** Restore focus to the trigger (or last focused) after close. Default true. */
  restoreFocus?: boolean;
  /**
   * How long to keep the node mounted after open→false so CSS exit can play.
   * Defaults to spatial 300ms. Set 0 for instant unmount.
   */
  presenceMs?: number;
  /** When false, layer does not register on the global topmost stack. */
  enabled?: boolean;
};

export type DismissableLayerResult = {
  /** True while the layer should stay in the DOM (open or exiting). */
  present: boolean;
  /** True during the exit window — content is non-interactive. */
  closing: boolean;
  /** open && !closing — safe for pointer/keyboard interaction. */
  interactive: boolean;
};

type StackEntry = {
  id: string;
  layerRef: RefObject<HTMLElement | null>;
  triggerRef?: RefObject<HTMLElement | null>;
  ignoreSelectors: string[];
  closeOnOutside: boolean;
  closeOnEscape: boolean;
  onDismiss: () => void;
  /** When true, ignore outside pointers until the opening frame settles. */
  suppressOutside: boolean;
};

/** Module-level stack so nested surfaces dismiss topmost-first. */
const layerStack: StackEntry[] = [];

function isInside(
  event: Event,
  layerRef: RefObject<HTMLElement | null>,
  triggerRef?: RefObject<HTMLElement | null>,
  ignoreSelectors: string[] = []
): boolean {
  const path =
    typeof event.composedPath === "function" ? event.composedPath() : [];

  if (layerRef.current) {
    if (path.includes(layerRef.current)) return true;
    if (event.target instanceof Node && layerRef.current.contains(event.target)) {
      return true;
    }
  }

  if (triggerRef?.current) {
    if (path.includes(triggerRef.current)) return true;
    if (
      event.target instanceof Node &&
      triggerRef.current.contains(event.target)
    ) {
      return true;
    }
  }

  if (event.target instanceof Element) {
    for (const selector of ignoreSelectors) {
      if (event.target.closest(selector)) return true;
    }
  }

  return false;
}

function topmost(): StackEntry | undefined {
  return layerStack[layerStack.length - 1];
}

/**
 * Controlled dismissable surface helper for bespoke (non-Base-UI) panels.
 * Presence exists only to render an exit transition — business `open` is authority.
 */
export function useDismissableLayer(
  options: DismissableLayerOptions
): DismissableLayerResult {
  const {
    open,
    onDismiss,
    layerRef,
    triggerRef,
    ignoreSelectors = [],
    closeOnOutside = true,
    closeOnEscape = true,
    restoreFocus = true,
    presenceMs = DISMISSABLE_PRESENCE_MS,
    enabled = true,
  } = options;

  const reactId = useId();
  const idRef = useRef(`dismissable-${reactId}`);
  const onDismissRef = useRef(onDismiss);
  const ignoreKey = ignoreSelectors.join("\0");
  const ignoreList = ignoreSelectors;

  const [present, setPresent] = useState(open);
  const [closing, setClosing] = useState(false);
  const focusRestoreRef = useRef<HTMLElement | null>(null);
  const suppressOutsideRef = useRef(false);

  useEffect(() => {
    onDismissRef.current = onDismiss;
  }, [onDismiss]);

  // Presence state machine: hold mount after open→false so CSS exit can finish.
  useEffect(() => {
    if (open) {
      // Deferred unmount for CSS exit is intentional; not a render cascade smell.
      /* eslint-disable react-hooks/set-state-in-effect -- exit presence requires deferred unmount */
      setPresent(true);
      setClosing(false);
      /* eslint-enable react-hooks/set-state-in-effect */
      return;
    }
    if (!present) return;
    if (presenceMs <= 0) {
      setPresent(false);
      setClosing(false);
      return;
    }
    setClosing(true);
    const timer = window.setTimeout(() => {
      setPresent(false);
      setClosing(false);
    }, presenceMs);
    return () => window.clearTimeout(timer);
    // present intentionally omitted: only react to open/presenceMs changes
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, presenceMs]);

  // Capture focus on open; restore after presence fully ends.
  // Opening-pointer protection: suppress only until the next animation frame.
  useEffect(() => {
    if (!enabled) return;
    if (open) {
      const active = document.activeElement;
      if (active instanceof HTMLElement) {
        focusRestoreRef.current = active;
      }
      suppressOutsideRef.current = true;
      const entry = layerStack.find((e) => e.id === idRef.current);
      if (entry) entry.suppressOutside = true;
      const raf = requestAnimationFrame(() => {
        suppressOutsideRef.current = false;
        const live = layerStack.find((e) => e.id === idRef.current);
        if (live) live.suppressOutside = false;
      });
      return () => cancelAnimationFrame(raf);
    }
    if (!present && restoreFocus) {
      const target = triggerRef?.current ?? focusRestoreRef.current;
      if (target && typeof target.focus === "function") {
        try {
          target.focus({ preventScroll: true });
        } catch {
          /* ignore */
        }
      }
      focusRestoreRef.current = null;
    }
  }, [open, present, restoreFocus, triggerRef, enabled]);

  const dismiss = useCallback(() => {
    onDismissRef.current();
  }, []);

  // Register while open (not during exit-only presence).
  useEffect(() => {
    if (!enabled || !open) return;

    const entry: StackEntry = {
      id: idRef.current,
      layerRef,
      triggerRef,
      ignoreSelectors: ignoreList,
      closeOnOutside,
      closeOnEscape,
      onDismiss: dismiss,
      suppressOutside: suppressOutsideRef.current,
    };
    layerStack.push(entry);

    return () => {
      const idx = layerStack.findIndex((e) => e.id === entry.id);
      if (idx >= 0) layerStack.splice(idx, 1);
    };
    // ignoreKey tracks ignoreSelectors content without identity churn
    // eslint-disable-next-line react-hooks/exhaustive-deps -- ignoreList via ignoreKey
  }, [
    enabled,
    open,
    layerRef,
    triggerRef,
    ignoreKey,
    closeOnOutside,
    closeOnEscape,
    dismiss,
  ]);

  useEffect(() => {
    if (!enabled || !open) return;
    const entry = layerStack.find((e) => e.id === idRef.current);
    if (entry) {
      entry.suppressOutside = suppressOutsideRef.current;
      entry.ignoreSelectors = ignoreList;
    }
  }, [enabled, open, ignoreKey, ignoreList]);

  useEffect(() => {
    if (!enabled || !open || !closeOnOutside) return;

    const onPointerDown = (event: PointerEvent) => {
      const top = topmost();
      if (!top || top.id !== idRef.current) return;
      if (top.suppressOutside) return;
      if (isInside(event, top.layerRef, top.triggerRef, top.ignoreSelectors)) {
        return;
      }
      top.onDismiss();
    };

    document.addEventListener("pointerdown", onPointerDown, true);
    return () =>
      document.removeEventListener("pointerdown", onPointerDown, true);
  }, [enabled, open, closeOnOutside]);

  useEffect(() => {
    if (!enabled || !open || !closeOnEscape) return;

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      const top = topmost();
      if (!top || top.id !== idRef.current) return;
      if (!top.closeOnEscape) return;
      event.preventDefault();
      event.stopPropagation();
      top.onDismiss();
    };

    document.addEventListener("keydown", onKeyDown, true);
    return () => document.removeEventListener("keydown", onKeyDown, true);
  }, [enabled, open, closeOnEscape]);

  return {
    present: enabled ? present : open,
    closing: enabled ? closing : false,
    interactive: open && !(enabled && closing),
  };
}

/** Test-only: clear residual stack entries between tests. */
export function __resetDismissableLayerStackForTests(): void {
  layerStack.length = 0;
}
