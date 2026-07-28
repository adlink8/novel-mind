---
phase: 18-frontend-motion-and-transition-system
source: user-approved-frontend-motion-addendum
requirements: [UI-MOTION-01, UI-MOTION-02, UI-MOTION-03, UI-MOTION-04, UI-MOTION-05, UI-MOTION-06]
execution: planning-only
---

# Phase 18 Context

## Outcome

为现有 Next.js 前端建立克制、统一、可访问的动效与过渡系统，让页面内容状态、导航、抽屉、设置、对话、分析进度和卡片反馈在不改变业务行为的前提下具有清晰的空间关系与状态连续性。所有动效必须可预测、可关闭、无布局跳动，并通过桌面与 390px 触摸视口验证。

## Locked Decisions

- 仅使用现有 Tailwind、`tw-animate-css`、CSS transitions/animations 与 Base UI 状态属性；不新增 Framer Motion 或其他运行时动画依赖。
- 全站 motion token 只有三档：150ms 快速反馈、200ms 常规状态、300ms 空间面板；进入使用 ease-out，退出使用 ease-in。不得散落任意时长或 linear easing。
- 优先动画 `opacity`、`transform`、颜色、边框色和阴影；不得用 `height/width/top/left` 动画制造 reflow，不得因 hover/active/加载状态改变组件占位尺寸。
- 页面级动效指页面内部稳定容器的内容状态过渡，不对 Next.js route navigation 做滚动劫持、延迟导航或高成本全屏转场。
- sidebar、dialog、settings、reader chat、reader search、relationship/clue evidence panel 使用一致的打开/关闭语义：触发器可切换，点击面板外关闭最上层，点击面板内不关闭，Escape 关闭，关闭后焦点返回触发器；真正 modal 保持焦点约束与 `aria-modal`。
- 深浅/自定义主题必须在首屏绘制前恢复，避免 hydration 后闪白/闪黑。主题切换只过渡主题相关颜色，不给图片、正文布局和自定义背景做缩放或位移动画；初次主题同步和 reduced-motion 下禁用主题过渡。
- `prefers-reduced-motion: reduce` 是硬约束：移除非必要位移/缩放/脉冲，状态仍需立即且清晰地变化，焦点、文本、图标或 ARIA 不依赖动画表达。
- loading spinner、真实进度和短暂 skeleton 可以运动；禁止装饰性常驻漂浮、呼吸、闪烁、无限卡片动画，以及对小说正文的自动视觉扰动。
- 键盘、鼠标和触摸采用同一状态机。hover 不能是唯一反馈；键盘 focus-visible、pressed/expanded/busy 状态和触摸点击反馈必须可感知。
- Phase 18 不改变 API、后端、数据结构、分析任务生命周期、阅读进度、翻页/长页/自动下滑规则、导航目标或权限行为。

## Scope

### In scope

- 全局 motion CSS custom properties、语义 utility/classes、reduced-motion 与主题过渡门禁。
- Base UI primitives 的 dialog、sheet、dropdown、tooltip、tabs 和通用 button/card 状态时序统一。
- AppShell 桌面/移动导航、章节目录、阅读设置、阅读搜索、Reader Chat、关系/线索证据面板的进入/退出、遮罩、焦点和 outside-click 行为。
- 页面和内容状态：加载 → 就绪、空态/错误态、分析工作区 tab、渐进进度、时间线/关系/线索列表与卡片选择反馈。
- Vitest 行为/契约测试，以及 Playwright 桌面 1280×800 与 mobile 390×844 的键盘、触摸、主题、reduced-motion、无布局跳动检查。

### Out of scope

- 滚动劫持、平滑滚动框架、视差、3D、粒子、Lottie、花哨持续动画或全站 route transition 框架。
- 新业务入口、新页面、新后端、新 API、新分析状态或重新设计现有视觉层级。
- 改变自动下滑倍速、翻页模式、聊天生成、分析 worker、时间线聚合和证据内容。
- 以动画掩盖慢请求、延迟交互完成、在退出期间阻塞用户下一次操作。

## Acceptance Boundaries

1. 任何交互 transition 都能映射到 150/200/300ms token，enter/exit easing 方向正确；代码扫描无新增任意 duration 和 linear UI transition。
2. 所有目标浮层都支持 outside click、Escape、显式关闭和焦点返回；嵌套确认框只关闭最上层，不误触底层动作。
3. 深色、浅色和自定义背景从首次可见帧到 hydration 后保持一致；切换期间没有整页闪烁、正文宽度变化或固定控件漂移。
4. reduced-motion 下所有业务状态仍可完成，非必要动画被消除，持续 loading 仍有文字或 `aria-busy/status` 反馈。
5. 分析进度增量、列表/卡片选中与内容替换不引起可见 layout shift；新增/更新内容采用稳定占位和短暂淡入。
6. Playwright 在桌面与 390px 触摸视口验证面板、主题和分析关键路径；不得出现水平滚动、遮挡输入框、底部进度条覆盖聊天或焦点丢失。

## Verification Standard

- 先以 Vitest 验证 motion token、主题启动、reduced-motion、dismissable layer 和组件状态契约。
- 再运行 ESLint、TypeScript/Next production build，确保没有 hydration、客户端边界或样式生成错误。
- 最后运行专用 Playwright motion spec 的 desktop/mobile-390 项目，记录关键面板打开/关闭、主题首帧、分析增量和布局边界截图/trace。
- 使用 bounding-box 与滚动尺寸断言证明关键静态容器无尺寸跳动；不得仅凭截图主观认定“平滑”。

---

*Context derived from the user's approved request to complete planning only and add frontend animation transitions; implementation remains explicitly unapproved.*
