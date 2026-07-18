"use client";

/**
 * 互动翻页书 — 纯 CSS 3D + 指针事件，无动画运行时依赖。
 *
 * 交互：
 * - 悬停右页边缘：当前页轻微掀起（翻页预告）
 * - 点击左右页边缘或 ◀ ▶ 按钮：翻页 / 回翻
 * - autoFlipMs：按间隔自动翻页（到底后合书重来）；悬停/聚焦/页面隐藏时暂停
 *
 * 注：曾有「指针移动整书 3D 倾斜」与「整书漂浮」动画，二者都会让
 * 链接在指针接近/按下时位移、真实点击落空，均已移除 —— 页内可点内容优先。
 *
 * 布局：
 * - spread（默认）：左右对开的摊开书，左半为封面内侧
 * - single：单页竖版书（窄屏），封面作为第一张书叶，翻完露出封底
 *
 * ambient（首页专武风格）：倾斜开卷姿态（非平铺）、桌面投影、纸纹颗粒、
 * 书页厚度侧棱、纸张感翻页（0.85s 缓动 + 页面弯折 + 掠过书影）、
 * 金色光环、光尘、书脊辉光、翻页扫光、描金内框。
 * reduced-motion 下动画全部静止，静态装饰保留。
 *
 * 翻页节奏为纸张感的 0.85s 自定义缓动；其余交互时长引用 Phase 18 motion tokens。
 */

import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";

import { cn } from "@/lib/utils";
import ambientStyles from "./flip-book-ambient.module.css";

export type FlipBookPage = {
  id: string;
  /** 右页（正面）内容 */
  front: ReactNode;
  /** 翻到左侧后的背面内容；缺省为饰纹页 */
  back?: ReactNode;
};

type Props = {
  pages: FlipBookPage[];
  /** 封面内侧（初始左半 / 单页模式首张书叶）与封底内侧（翻完后露出）内容 */
  insideCover?: ReactNode;
  insideBackCover?: ReactNode;
  /** 控制条配色：on-dark 用于深色底（hero），on-light 用于纸面底 */
  tone?: "on-dark" | "on-light";
  /** spread = 对开摊开书（桌面）；single = 单页竖版书（窄屏） */
  layout?: "spread" | "single";
  /** 自动翻页间隔（毫秒）；缺省不自动翻。reduced-motion 下始终不自动翻 */
  autoFlipMs?: number;
  /** 环境动效与真实书本质感：倾斜姿态 + 纸纹 + 书页侧棱 + 纸张感翻页 + 金光粒子 */
  ambient?: boolean;
  className?: string;
  ariaLabel?: string;
};

/** 纸张翻页节奏：较慢的 0.85s，加速柔和、落页安稳 */
const FLIP_TRANSITION = "transform 0.85s cubic-bezier(0.32, 0.72, 0.35, 1)";

/** 一次翻页的特效标记：哪张书叶、方向、序号（用于重放 CSS 动画） */
type FlipFx = { seq: number; index: number; dir: 1 | -1 };

/** 页背默认饰纹：横线稿纸 + 章节花押 */
function DefaultLeafBack() {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-4 p-5 text-muted-foreground/40">
      <span className="font-serif text-lg">❦</span>
      <div className="w-full space-y-2.5">
        {Array.from({ length: 5 }).map((_, i) => (
          <div key={i} className="h-px w-full bg-border/60" />
        ))}
      </div>
    </div>
  );
}

/** 页面上靠近书脊的阴影，营造中缝立体感 */
function SpineShade({ side }: { side: "left" | "right" }) {
  return (
    <div
      aria-hidden
      className={cn(
        "pointer-events-none absolute inset-y-0 w-8",
        side === "left"
          ? "left-0 bg-gradient-to-r from-black/10 to-transparent"
          : "right-0 bg-gradient-to-l from-black/10 to-transparent"
      )}
    />
  );
}

/** 光尘粒子参数（按索引确定性生成，保证 SSR/客户端一致） */
function buildParticles(count: number) {
  return Array.from({ length: count }, (_, i) => ({
    left: (i * 53 + 7) % 100, // 横向位置 %
    size: 3 + ((i * 29) % 4), // 3–6px
    duration: 7 + ((i * 37) % 50) / 10, // 7–11.9s
    delay: -((i * 61) % 120) / 10, // 负延迟 → 初始即分布在中途
    drift: ((i * 43) % 41) - 20, // -20–20px 横向漂移
    peak: 0.55 + ((i * 17) % 40) / 100, // 0.55–0.95 峰值透明度
  }));
}

export function FlipBook({
  pages,
  insideCover,
  insideBackCover,
  tone = "on-dark",
  layout = "spread",
  autoFlipMs,
  ambient = false,
  className,
  ariaLabel = "互动翻页书",
}: Props) {
  const [flipped, setFlipped] = useState(0);
  const [peeking, setPeeking] = useState(false);
  const [flipFx, setFlipFx] = useState<FlipFx | null>(null);
  const autoFlipPausedRef = useRef(false);
  const flippedRef = useRef(0);
  const flipSeqRef = useRef(0);

  const isSingle = layout === "single";
  // 单页模式：封面内侧作为第一张书叶；展开模式：封面内侧是静态左半
  const leaves = useMemo<FlipBookPage[]>(
    () =>
      isSingle
        ? [{ id: "__inside-cover", front: insideCover }, ...pages]
        : pages,
    [isSingle, insideCover, pages]
  );
  const total = leaves.length;
  const canPrev = flipped > 0;
  const canNext = flipped < total;

  const particles = useMemo(
    () => (ambient ? buildParticles(14) : []),
    [ambient]
  );

  /** 所有翻页入口统一走这里：夹紧边界 + 记录翻页特效（合书重置不播特效） */
  function flipTo(next: number) {
    const prev = flippedRef.current;
    const clamped = Math.max(0, Math.min(total, next));
    if (clamped === prev) return;
    const isWrapReset = prev === total && clamped === 0;
    if (!isWrapReset) {
      flipSeqRef.current += 1;
      setFlipFx({
        seq: flipSeqRef.current,
        // 向前翻：翻动的是当前页 prev；回翻：翻动的是目标页 clamped
        index: clamped > prev ? prev : clamped,
        dir: clamped > prev ? 1 : -1,
      });
    }
    flippedRef.current = clamped;
    setFlipped(clamped);
  }

  // 自动翻页：到底后合书回到封面；悬停/聚焦/标签页隐藏/reduced-motion 时暂停
  useEffect(() => {
    if (!autoFlipMs || autoFlipMs < 1000) return;
    if (
      typeof window !== "undefined" &&
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches
    ) {
      return;
    }
    const timer = window.setInterval(() => {
      if (typeof document !== "undefined" && document.hidden) return;
      if (autoFlipPausedRef.current) return;
      flipTo(flippedRef.current >= total ? 0 : flippedRef.current + 1);
    }, autoFlipMs);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoFlipMs, total]);

  const goldFrame = ambient ? (
    <div aria-hidden className={ambientStyles.goldFrame} />
  ) : null;

  return (
    <div
      className={cn(
        "flex flex-col items-center gap-3",
        ambient && ambientStyles.ambientScene,
        className
      )}
    >
      {ambient && <div aria-hidden className={ambientStyles.aura} />}
      {ambient && <div aria-hidden={true} className={ambientStyles.tableShadow} />}
      <div className={cn("w-full", ambient && ambientStyles.floatWrap)}>
        <div
          data-testid="flip-book-scene"
          data-flipped={flipped}
          role="group"
          aria-label={ariaLabel}
          className={cn(
            "relative w-full select-none",
            isSingle ? "aspect-[3/4]" : "aspect-[3/2]"
          )}
          onPointerEnter={() => {
            autoFlipPausedRef.current = true;
          }}
          onPointerLeave={() => {
            autoFlipPausedRef.current = false;
            setPeeking(false);
          }}
          onFocusCapture={() => {
            autoFlipPausedRef.current = true;
          }}
          onBlurCapture={() => {
            autoFlipPausedRef.current = false;
          }}
        >
          {/* 倾斜开卷姿态（ambient）：2D 近似斜放（3D 祖变换会破坏点击命中） */}
          <div className={cn("h-full w-full", ambient && ambientStyles.bookPose)}>
            {/* perspective 只作用于书叶翻转；容器保持扁平，保证页内链接可点 */}
            <div
              className="relative h-full w-full"
              style={{ perspective: "1600px" }}
            >
              {/* 封底内侧（翻完所有页后露出） */}
              <div
                className={cn(
                  "absolute overflow-hidden border border-border/70 bg-gradient-to-br from-card to-secondary",
                  isSingle
                    ? "inset-0 rounded-lg"
                    : "inset-y-0 right-0 w-1/2 rounded-r-lg",
                  ambient
                    ? ambientStyles.gilded
                    : "shadow-[0_24px_50px_-24px_rgba(20,14,8,0.6)]"
                )}
              >
                {!isSingle && <SpineShade side="left" />}
                {insideBackCover}
                {goldFrame}
              </div>
              {/* 封面内侧（展开模式的静态左半；单页模式为书叶，见下方 leaves） */}
              {!isSingle && (
                <div
                  className={cn(
                    "absolute inset-y-0 left-0 w-1/2 overflow-hidden rounded-l-lg border border-border/70 bg-gradient-to-bl from-card to-secondary",
                    ambient
                      ? ambientStyles.gilded
                      : "shadow-[0_24px_50px_-24px_rgba(20,14,8,0.6)]"
                  )}
                >
                  <SpineShade side="right" />
                  {insideCover}
                  {goldFrame}
                </div>
              )}
              {/* 中缝（展开模式） */}
              {!isSingle && (
                <div
                  aria-hidden
                  className="absolute inset-y-1 left-1/2 w-px -translate-x-1/2 bg-black/15"
                />
              )}

              {leaves.map((page, i) => {
                const isFlipped = i < flipped;
                const isTopRight = i === flipped;
                const fx = flipFx && flipFx.index === i ? flipFx : null;
                return (
                  <div
                    key={page.id}
                    data-testid={`flip-leaf-${i}`}
                    data-flipped={isFlipped ? "true" : "false"}
                    className={cn(
                      "absolute",
                      isSingle ? "inset-0 w-full" : "inset-y-0 left-1/2 w-1/2"
                    )}
                    style={{
                      // 书叶保持 preserve-3d 让正反两面背向剔除（翻牌效果）；
                      // 父容器扁平，用 z-index 分层：未翻页递减、已翻页递增。
                      transformStyle: "preserve-3d",
                      transformOrigin: "left center",
                      transform: `rotateY(${
                        isFlipped ? -180 : peeking && isTopRight ? -14 : 0
                      }deg)`,
                      transition: FLIP_TRANSITION,
                      zIndex: isFlipped ? i + 1 : total - i + 1,
                    }}
                  >
                    {/* 正面（右页 / 单页） */}
                    <div
                      key={fx ? `front-${fx.seq}` : "front"}
                      className={cn(
                        "absolute inset-0 overflow-hidden border border-border/60 bg-gradient-to-bl from-card to-secondary",
                        isSingle ? "rounded-lg" : "rounded-r-lg",
                        ambient && ambientStyles.paperFace,
                        fx && ambientStyles.paperFlexFront
                      )}
                      style={{ backfaceVisibility: "hidden" }}
                    >
                      {!isSingle && <SpineShade side="left" />}
                      {page.front}
                      {goldFrame}
                      {fx && (
                        <div
                          aria-hidden
                          className={cn(
                            ambientStyles.flipShade,
                            ambientStyles.shadeLiftFront
                          )}
                        />
                      )}
                    </div>
                    {/* 背面（翻到左侧 / 单页模式转出视野） */}
                    <div
                      key={fx ? `back-${fx.seq}` : "back"}
                      className={cn(
                        "absolute inset-0 overflow-hidden border border-border/60 bg-gradient-to-br from-card to-secondary",
                        isSingle ? "rounded-lg" : "rounded-l-lg",
                        ambient && ambientStyles.paperFace,
                        fx && ambientStyles.paperFlexBack
                      )}
                      style={{
                        backfaceVisibility: "hidden",
                        transform: "rotateY(180deg)",
                      }}
                    >
                      {!isSingle && <SpineShade side="right" />}
                      {page.back ?? <DefaultLeafBack />}
                      {fx && (
                        <div
                          aria-hidden
                          className={cn(
                            ambientStyles.flipShade,
                            ambientStyles.shadeLandBack
                          )}
                        />
                      )}
                    </div>
                  </div>
                );
              })}

              {/* 书页厚度侧棱：右侧未翻页堆 / 左侧已翻页堆（ambient） */}
              {ambient && canNext && (
                <div
                  aria-hidden
                  className={cn(
                    ambientStyles.foreEdge,
                    ambientStyles.foreEdgeRight
                  )}
                />
              )}
              {ambient && !isSingle && flipped > 0 && (
                <div
                  aria-hidden
                  className={cn(
                    ambientStyles.foreEdge,
                    ambientStyles.foreEdgeLeft
                  )}
                />
              )}
            </div>
          </div>

          {/* 书脊辉光 + 翻页扫光 + 翻页书影（ambient 装饰层，不参与交互） */}
          {ambient && (
            <>
              <div
                aria-hidden
                className={cn(
                  ambientStyles.spineBeam,
                  isSingle && ambientStyles.spineBeamSingle
                )}
              />
              <div
                key={`shimmer-${flipped}`}
                aria-hidden
                className={ambientStyles.shimmer}
              />
              {flipFx && (
                <div
                  key={`turn-${flipFx.seq}`}
                  aria-hidden
                  className={cn(
                    ambientStyles.turnShadow,
                    flipFx.dir === -1 && ambientStyles.turnShadowBack
                  )}
                />
              )}
            </>
          )}

          {/* 页缘点击热区（窄条，不遮挡页内链接） */}
          <button
            type="button"
            aria-label="上一页"
            data-testid="flip-prev-zone"
            disabled={!canPrev}
            onClick={() => flipTo(flipped - 1)}
            className={cn(
              "absolute inset-y-2 left-0 z-40 w-[4%] min-w-3 cursor-pointer rounded-l-lg",
              !canPrev && "pointer-events-none"
            )}
          />
          <button
            type="button"
            aria-label="下一页"
            data-testid="flip-next-zone"
            disabled={!canNext}
            onClick={() => flipTo(flipped + 1)}
            onPointerEnter={() => canNext && setPeeking(true)}
            onPointerLeave={() => setPeeking(false)}
            className={cn(
              "absolute inset-y-2 right-0 z-40 w-[4%] min-w-3 cursor-pointer rounded-r-lg",
              !canNext && "pointer-events-none"
            )}
          />
        </div>

        {/* 光尘粒子：环绕书本升起（ambient） */}
        {ambient &&
          particles.map((p, i) => (
            <span
              key={i}
              aria-hidden
              className={ambientStyles.particle}
              style={{
                left: `${p.left}%`,
                width: p.size,
                height: p.size,
                ["--rise-duration" as string]: `${p.duration}s`,
                ["--rise-delay" as string]: `${p.delay}s`,
                ["--drift" as string]: `${p.drift}px`,
                ["--peak-opacity" as string]: p.peak,
              }}
            />
          ))}
      </div>

      {/* 控制条：页码 + 按钮（键盘可达） */}
      <div
        className={cn(
          "flex items-center gap-3 text-xs",
          tone === "on-dark" ? "text-white/60" : "text-muted-foreground"
        )}
      >
        <button
          type="button"
          aria-label="上一页"
          data-testid="flip-prev-btn"
          disabled={!canPrev}
          onClick={() => flipTo(flipped - 1)}
          className={cn(
            "grid size-7 place-items-center rounded-full border transition-[color,border-color,background-color] motion-duration-fast motion-ease-enter disabled:pointer-events-none disabled:opacity-30",
            tone === "on-dark"
              ? "border-white/15 text-white/70 hover:bg-white/10 hover:text-white"
              : "border-border text-muted-foreground hover:bg-muted hover:text-foreground"
          )}
        >
          <ChevronLeft className="size-4" />
        </button>
        <span aria-live="polite" data-testid="flip-page-indicator">
          {flipped + 1} / {total + 1}
        </span>
        <button
          type="button"
          aria-label="下一页"
          data-testid="flip-next-btn"
          disabled={!canNext}
          onClick={() => flipTo(flipped + 1)}
          className={cn(
            "grid size-7 place-items-center rounded-full border transition-[color,border-color,background-color] motion-duration-fast motion-ease-enter disabled:pointer-events-none disabled:opacity-30",
            tone === "on-dark"
              ? "border-white/15 text-white/70 hover:bg-white/10 hover:text-white"
              : "border-border text-muted-foreground hover:bg-muted hover:text-foreground"
          )}
        >
          <ChevronRight className="size-4" />
        </button>
      </div>
    </div>
  );
}
