"use client";

import { useRef } from "react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useDismissableLayer } from "@/lib/use-dismissable-layer";
import {
  BookOpen,
  ChevronDown,
  Maximize2,
  Moon,
  Palette,
  Pause,
  Play,
  Settings2,
  Sun,
  SunMoon,
} from "lucide-react";

export type ReaderMode = "paged" | "scroll";
export type ReaderTheme = "light" | "dark" | "custom" | "system";

export interface ReaderPreferences {
  immersive: boolean;
  mode: ReaderMode;
  autoScroll: boolean;
  autoScrollSpeed: number;
  theme: ReaderTheme;
  customBackground: string;
  /** 正文字号（px） */
  fontSize: number;
  /** 正文行距（倍数） */
  lineHeight: number;
  /** 正文最大行宽（px） */
  contentWidth: number;
}

export const READER_PREFERENCES_KEY = "novelmind:reader-preferences:v1";

export const DEFAULT_READER_PREFERENCES: ReaderPreferences = {
  immersive: false,
  mode: "paged",
  autoScroll: false,
  autoScrollSpeed: 1,
  theme: "light",
  customBackground: "#efe4d1",
  fontSize: 18,
  lineHeight: 2.1,
  contentWidth: 760,
};

/** Accept only validated light|dark|custom|system — never evaluate custom as markup. */
export function parseReaderTheme(value: unknown): ReaderTheme {
  return value === "dark" ||
    value === "custom" ||
    value === "light" ||
    value === "system"
    ? value
    : "light";
}

/** Resolve "system" via prefers-color-scheme; everything else passes through. */
export function resolveReaderTheme(
  theme: ReaderTheme
): "light" | "dark" | "custom" {
  const parsed = parseReaderTheme(theme);
  if (parsed !== "system") return parsed;
  if (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-color-scheme: dark)").matches
  ) {
    return "dark";
  }
  return "light";
}

/** Six-digit hex only; invalid values fall back to the safe default. */
export function parseCustomBackground(value: unknown): string {
  if (typeof value !== "string") {
    return DEFAULT_READER_PREFERENCES.customBackground;
  }
  const normalized = value.trim();
  if (/^#[0-9a-fA-F]{6}$/.test(normalized)) return normalized;
  return DEFAULT_READER_PREFERENCES.customBackground;
}

/** Derive readable foreground HSL channels from a validated #RRGGBB background. */
export function deriveCustomForeground(hex: string): string {
  const normalized = hex.replace("#", "");
  if (!/^[0-9a-fA-F]{6}$/.test(normalized)) {
    return "28 20% 13%";
  }
  const red = Number.parseInt(normalized.slice(0, 2), 16);
  const green = Number.parseInt(normalized.slice(2, 4), 16);
  const blue = Number.parseInt(normalized.slice(4, 6), 16);
  const luminance = (red * 299 + green * 587 + blue * 114) / 1000;
  return luminance > 145 ? "28 20% 13%" : "42 35% 96%";
}

export function loadReaderPreferences(): ReaderPreferences {
  if (typeof window === "undefined") return DEFAULT_READER_PREFERENCES;
  try {
    const raw = window.localStorage.getItem(READER_PREFERENCES_KEY);
    if (!raw) return DEFAULT_READER_PREFERENCES;
    const stored = JSON.parse(raw) as Partial<ReaderPreferences>;
    return {
      ...DEFAULT_READER_PREFERENCES,
      ...stored,
      mode: stored.mode === "scroll" ? "scroll" : "paged",
      theme: parseReaderTheme(stored.theme),
      customBackground: parseCustomBackground(stored.customBackground),
      autoScrollSpeed: normalizeAutoScrollMultiplier(stored.autoScrollSpeed),
      fontSize: normalizeFontSize(stored.fontSize),
      lineHeight: normalizeLineHeight(stored.lineHeight),
      contentWidth: normalizeContentWidth(stored.contentWidth),
    };
  } catch {
    return DEFAULT_READER_PREFERENCES;
  }
}

export function saveReaderPreferences(preferences: ReaderPreferences): void {
  applyReaderTheme(preferences.theme, {
    customBackground: preferences.customBackground,
    enableTransition: true,
  });
  try {
    window.localStorage.setItem(
      READER_PREFERENCES_KEY,
      JSON.stringify(preferences)
    );
  } catch {
    // Reading must remain available when storage is disabled.
  }
}

export interface ApplyReaderThemeOptions {
  customBackground?: string;
  /** When true, allow color-only theme transitions (post-boot user changes). */
  enableTransition?: boolean;
}

/**
 * Apply light/dark/custom/system to the document root.
 * Safe for pre-paint bootstrap and React reconciliation — never injects markup.
 */
export function applyReaderTheme(
  theme: ReaderTheme,
  options: ApplyReaderThemeOptions = {}
): void {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  const resolved = parseReaderTheme(theme);
  const effective = resolveReaderTheme(resolved);

  root.classList.toggle("dark", effective === "dark");
  root.dataset.readerTheme = resolved;
  root.style.colorScheme = effective === "dark" ? "dark" : "light";

  if (resolved === "custom") {
    const background = parseCustomBackground(options.customBackground);
    root.style.setProperty("--reader-custom-background", background);
    root.style.setProperty(
      "--reader-custom-foreground",
      deriveCustomForeground(background)
    );
  } else {
    root.style.removeProperty("--reader-custom-background");
    root.style.removeProperty("--reader-custom-foreground");
  }

  if (options.enableTransition) {
    root.classList.add("theme-transition-ready");
  }
}

/**
 * Dependency-free pre-paint bootstrap source (inlined in root layout).
 * Reads the same storage key as loadReaderPreferences; never evaluates custom as HTML.
 */
export const THEME_BOOT_SCRIPT = `(function(){try{var k=${JSON.stringify(
  READER_PREFERENCES_KEY
)};var raw=localStorage.getItem(k);if(!raw)return;var p=JSON.parse(raw);var t=p&&p.theme;if(t!=="dark"&&t!=="custom"&&t!=="light"&&t!=="system")t="light";var e=t;if(t==="system"){try{e=window.matchMedia&&window.matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light";}catch(err){e="light";}}var r=document.documentElement;r.classList.toggle("dark",e==="dark");r.setAttribute("data-reader-theme",t);r.style.colorScheme=e==="dark"?"dark":"light";if(t==="custom"){var bg=typeof p.customBackground==="string"?p.customBackground.trim():"";if(!/^#[0-9a-fA-F]{6}$/.test(bg))bg=${JSON.stringify(
  DEFAULT_READER_PREFERENCES.customBackground
)};r.style.setProperty("--reader-custom-background",bg);var n=bg.slice(1);var R=parseInt(n.slice(0,2),16),G=parseInt(n.slice(2,4),16),B=parseInt(n.slice(4,6),16);var L=(R*299+G*587+B*114)/1000;r.style.setProperty("--reader-custom-foreground",L>145?"28 20% 13%":"42 35% 96%");}}catch(e){}})();`;

function normalizeAutoScrollMultiplier(value: unknown): number {
  const numeric = Number(value);
  if (!Number.isFinite(numeric) || numeric <= 0) return 1;
  // Migrate the previous pixels-per-second preference to a multiplier.
  const multiplier = numeric > 4 ? numeric / 32 : numeric;
  return Math.min(4, Math.max(0.5, Math.round(multiplier * 4) / 4));
}

function normalizeFontSize(value: unknown): number {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return DEFAULT_READER_PREFERENCES.fontSize;
  return Math.min(24, Math.max(16, Math.round(numeric)));
}

function normalizeLineHeight(value: unknown): number {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return DEFAULT_READER_PREFERENCES.lineHeight;
  return Math.min(2.6, Math.max(1.6, Math.round(numeric * 10) / 10));
}

function normalizeContentWidth(value: unknown): number {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return DEFAULT_READER_PREFERENCES.contentWidth;
  return Math.min(1000, Math.max(600, Math.round(numeric / 20) * 20));
}

interface ReaderPreferencesPanelProps {
  preferences: ReaderPreferences;
  onChange: (next: ReaderPreferences) => void;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  floating?: boolean;
  /** floating 模式下的右侧偏移（px）：桌面 AI 会话打开时让位，避免重叠 */
  floatingOffsetRight?: number;
}

export function ReaderPreferencesPanel({
  preferences,
  onChange,
  open,
  onOpenChange,
  floating = false,
  floatingOffsetRight = 16,
}: ReaderPreferencesPanelProps) {
  const rootRef = useRef<HTMLDivElement>(null);
  const layerRef = useRef<HTMLElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const { present, closing } = useDismissableLayer({
    open,
    onDismiss: () => onOpenChange(false),
    layerRef,
    triggerRef,
  });
  const update = (patch: Partial<ReaderPreferences>) =>
    onChange({ ...preferences, ...patch });

  const customSpeed =
    preferences.autoScrollSpeed !== 1 && preferences.autoScrollSpeed !== 2;

  return (
    <div
      ref={rootRef}
      className={cn("relative", floating && "fixed top-4 z-50")}
      style={floating ? { right: floatingOffsetRight } : undefined}
    >
      <Button
        ref={triggerRef}
        type="button"
        variant={floating ? "outline" : "ghost"}
        size={floating ? "default" : "sm"}
        onClick={() => onOpenChange(!open)}
        aria-expanded={open}
        aria-controls="reader-preferences-panel"
        title="阅读设置"
        className={cn(
          "cursor-pointer",
          floating && "border-white/20 bg-black/65 text-white shadow-lg hover:bg-black/75 hover:text-white"
        )}
      >
        <Settings2 className="size-4" />
        {floating ? "阅读设置" : null}
      </Button>

      {present ? (
        <section
          ref={layerRef}
          id="reader-preferences-panel"
          aria-label="阅读设置"
          aria-hidden={closing || undefined}
          className={cn(
            "absolute right-0 top-[calc(100%+0.6rem)] z-50 w-[min(21rem,calc(100vw-2rem))] rounded-2xl border border-border bg-card p-4 text-card-foreground shadow-2xl transition-[opacity,transform] motion-duration-spatial motion-ease-enter",
            open && !closing
              ? "translate-y-0 opacity-100"
              : "pointer-events-none -translate-y-1 opacity-0 motion-ease-exit"
          )}
        >
          <div className="mb-4 flex items-center justify-between">
            <div>
              <h2 className="text-sm font-semibold">阅读设置</h2>
              <p className="mt-0.5 text-xs text-muted-foreground">设置会自动保存在本机</p>
            </div>
            <ChevronDown className="size-4 text-muted-foreground" />
          </div>

          <SettingGroup label="阅读方式">
            <SegmentButton
              active={preferences.mode === "paged"}
              onClick={() => update({ mode: "paged", autoScroll: false })}
              icon={<BookOpen className="size-4" />}
            >
              翻页
            </SegmentButton>
            <SegmentButton
              active={preferences.mode === "scroll"}
              onClick={() => update({ mode: "scroll" })}
              icon={<ChevronDown className="size-4" />}
            >
              长页
            </SegmentButton>
          </SettingGroup>

          <div className="mb-4 rounded-xl border border-border/70 p-3">
            <p className="mb-3 text-xs font-semibold">排版</p>
            <div className="space-y-3">
              <TypographySlider
                label="字号"
                display={`${preferences.fontSize}px`}
                min={16}
                max={24}
                step={1}
                value={preferences.fontSize}
                ariaLabel="正文字号"
                onChange={(value) => update({ fontSize: value })}
              />
              <TypographySlider
                label="行距"
                display={preferences.lineHeight.toFixed(1)}
                min={1.6}
                max={2.6}
                step={0.1}
                value={preferences.lineHeight}
                ariaLabel="正文行距"
                onChange={(value) => update({ lineHeight: value })}
              />
              <TypographySlider
                label="行宽"
                display={`${preferences.contentWidth}px`}
                min={600}
                max={1000}
                step={20}
                value={preferences.contentWidth}
                ariaLabel="正文行宽"
                onChange={(value) => update({ contentWidth: value })}
              />
            </div>
          </div>

          <SettingGroup label="背景模式" columns={4}>
            <SegmentButton
              active={preferences.theme === "light"}
              onClick={() => update({ theme: "light" })}
              icon={<Sun className="size-4" />}
            >
              浅色
            </SegmentButton>
            <SegmentButton
              active={preferences.theme === "dark"}
              onClick={() => update({ theme: "dark" })}
              icon={<Moon className="size-4" />}
            >
              深色
            </SegmentButton>
            <SegmentButton
              active={preferences.theme === "custom"}
              onClick={() => update({ theme: "custom" })}
              icon={<Palette className="size-4" />}
            >
              自定义
            </SegmentButton>
            <SegmentButton
              active={preferences.theme === "system"}
              onClick={() => update({ theme: "system" })}
              icon={<SunMoon className="size-4" />}
            >
              系统
            </SegmentButton>
          </SettingGroup>

          {preferences.theme === "custom" ? (
            <label className="mb-4 flex items-center justify-between rounded-xl border border-border/70 px-3 py-2 text-xs font-medium">
              阅读背景
              <input
                aria-label="自定义阅读背景"
                type="color"
                value={preferences.customBackground}
                onChange={(event) =>
                  update({ customBackground: event.target.value })
                }
                className="h-7 w-10 cursor-pointer rounded border-0 bg-transparent p-0"
              />
            </label>
          ) : null}

          <div className="mb-4 rounded-xl border border-border/70 p-3">
            <div className="flex items-center justify-between gap-3">
              <div>
                <p className="text-xs font-semibold">自动下滑</p>
                <p className="mt-0.5 text-[11px] text-muted-foreground">仅在长页模式中可用</p>
              </div>
              <Button
                type="button"
                variant={preferences.autoScroll ? "default" : "outline"}
                size="sm"
                disabled={preferences.mode !== "scroll"}
                onClick={() => update({ autoScroll: !preferences.autoScroll })}
                aria-pressed={preferences.autoScroll}
              >
                {preferences.autoScroll ? (
                  <Pause className="size-3.5" />
                ) : (
                  <Play className="size-3.5" />
                )}
                {preferences.autoScroll ? "暂停" : "开始"}
              </Button>
            </div>
            <div className="mt-3 grid grid-cols-3 gap-1 rounded-xl bg-muted/70 p-1">
              <SegmentButton
                active={preferences.autoScrollSpeed === 1}
                onClick={() => update({ autoScrollSpeed: 1 })}
              >
                1 倍速
              </SegmentButton>
              <SegmentButton
                active={preferences.autoScrollSpeed === 2}
                onClick={() => update({ autoScrollSpeed: 2 })}
              >
                2 倍速
              </SegmentButton>
              <SegmentButton
                active={customSpeed}
                onClick={() => update({ autoScrollSpeed: customSpeed ? preferences.autoScrollSpeed : 1.5 })}
              >
                自定义倍速
              </SegmentButton>
            </div>
            {customSpeed ? (
              <label className="mt-3 block text-[11px] text-muted-foreground">
                自定义速度 {preferences.autoScrollSpeed.toFixed(2).replace(/\.00$/, "").replace(/0$/, "")} 倍
                <input
                  aria-label="自定义自动下滑倍速"
                  type="range"
                  min="0.5"
                  max="4"
                  step="0.25"
                  value={preferences.autoScrollSpeed}
                  disabled={preferences.mode !== "scroll"}
                  onChange={(event) =>
                    update({ autoScrollSpeed: Number(event.target.value) })
                  }
                  className="mt-2 h-1.5 w-full cursor-pointer accent-primary disabled:cursor-not-allowed"
                />
              </label>
            ) : null}
          </div>

          <Button
            type="button"
            variant={preferences.immersive ? "default" : "outline"}
            className="w-full cursor-pointer justify-center"
            onClick={() => {
              update({ immersive: !preferences.immersive });
              onOpenChange(false);
            }}
            aria-pressed={preferences.immersive}
          >
            <Maximize2 className="size-4" />
            {preferences.immersive ? "退出沉浸模式" : "进入沉浸模式"}
          </Button>
        </section>
      ) : null}
    </div>
  );
}

function SettingGroup({
  label,
  columns = 3,
  children,
}: {
  label: string;
  columns?: 3 | 4;
  children: React.ReactNode;
}) {
  return (
    <fieldset className="mb-4">
      <legend className="mb-2 text-xs font-semibold">{label}</legend>
      <div
        className={cn(
          "grid gap-1 rounded-xl bg-muted/70 p-1",
          columns === 4 ? "grid-cols-4" : "grid-cols-3"
        )}
      >
        {children}
      </div>
    </fieldset>
  );
}

function SegmentButton({
  active,
  onClick,
  icon,
  children,
}: {
  active: boolean;
  onClick: () => void;
  icon?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        "flex min-h-9 cursor-pointer items-center justify-center gap-1 rounded-lg px-2 text-xs font-medium transition-[color,background-color,box-shadow] motion-duration-fast motion-ease-enter focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        active
          ? "bg-background text-foreground shadow-sm"
          : "text-muted-foreground hover:text-foreground"
      )}
    >
      {icon}
      {children}
    </button>
  );
}

function TypographySlider({
  label,
  display,
  min,
  max,
  step,
  value,
  ariaLabel,
  onChange,
}: {
  label: string;
  display: string;
  min: number;
  max: number;
  step: number;
  value: number;
  ariaLabel: string;
  onChange: (value: number) => void;
}) {
  return (
    <label className="block text-[11px] text-muted-foreground">
      <span className="mb-1.5 flex items-center justify-between">
        <span>{label}</span>
        <span className="tabular-nums">{display}</span>
      </span>
      <input
        aria-label={ariaLabel}
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
        className="h-1.5 w-full cursor-pointer accent-primary"
      />
    </label>
  );
}
