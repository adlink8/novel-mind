# 前端架构

## 路由一览

| 路由 | 页面 | 说明 |
|---|---|---|
| `/` | 首页 | 3D 互动翻页书（桌面）/ 线性内容（移动） |
| `/novels` | 书架 | 小说网格、搜索、筛选、排序 |
| `/novels/[id]` | 阅读器 | 沉浸式阅读 + AI 聊天面板 |
| `/analysis` | 分析工作台 | 结构树 + 时间线/关系/线索切片 |
| `/search` | 搜索页 | 全文/语义混合搜索 |
| `/eval` | 评测页 | RAG 评测数据集 + 指标对比 + 趋势 |
| `/settings` | 设置中心 | AI 模型配置 / 路由策略 / 账户管理 |
| `/writing` | 创作中心 | 分支写作（能力建设中） |
| `/timeline-prototype` | 时间线原型 | 实验性原型，非正式路由 |

## 分析工作台（核心页面）

### 布局架构

```
┌─────────────────────────────────────────────────┐
│ 顶部：小说选择条（NovelPickerStrip）              │
├──────────┬──────────────────────────────────────┤
│ 左轨     │ 右区                                 │
│ 280px    │ ┌─ 视图范围标签 ──────────────────┐   │
│ ├ 结构树  │ │ 视图范围：第 5 章               │   │
│ │ (滚动)  │ ├─ Facet 切换: 时间线/关系/线索 ─┤   │
│ │         │ ├─ TimelineStatus (开始/暂停) ───┤   │
│ ├ 节点    │ ├─ TimelineControls (筛选/排序) ─┤   │
│ │ 详情    │ ├─ 主可视区域 ──────────────────┤   │
│ │ 面板    │ │  时间线图 / 关系图 / 线索面板   │   │
│ └────────┘ └──────────────────────────────────┘   │
└─────────────────────────────────────────────────┘
```

### 数据流

```
selectNovel(id)
  ├─ loadStructure()         ← 拉 NM 版本/树，或 fallback 章节树
  ├─ loadTimeline()          ← 拉已有时间线数据
  └─ timelineApi.status()    ← 查分析任务状态

用户选择结构节点
  ├─ setSelectedNode(s)
  ├─ scope = [chapterStart, chapterEnd] → 传给 TimelineChart + RelationshipWorkspace + ClueWorkspace
  └─ throughChapter ← 剧透上限（显式/自动）

用户点击「开始分析」
  ├─ timelineApi.startOrResume()
  └─ 轮询 2.5s → 增量出图（running_candidate）→ 完成后 promote 到 active
```

### Facet 切换

三个 Tab 共享同一 `selectedNode`：

| Tab | 组件 | 数据源 |
|---|---|---|
| 时间线 | TimelineChart + TimelineControls + TimelineStatus | GET /api/timeline/{id} |
| 人物关系 | RelationshipWorkspace（cytoscape） | GET /api/relationships/graph |
| 线索 | ClueWorkspace | GET /api/clues |

## 互动翻页书（首页）

**组件**：`src/components/flip-book.tsx`（纯 CSS 3D，零额外依赖）

交互：
- **悬停倾斜**：鼠标在书上移动 → `rotateY(±10°) / rotateX(±8°)` 跟随
- **翻页预告**：指针移到右页边缘 → 当前页掀起 14°
- **点击翻页**：点击页缘或 ◀ ▶ 按钮 → CSS `rotateY(-180deg)` 翻到左面
- **reduced-motion**：`matchMedia` 检测后自动禁用倾斜

页面内容（首页共 3 叶 = 6 面 + 封面 + 封底）：
- 封面内侧：品牌 hero（标题 + 描述 + CTA）
- 壹·目录：4 个快捷入口（导入/检索/评测/创作）
- 贰·最近作品：top 3 书架
- 叁·藏书一览：3 项统计
- 封底：结语 + 进入书架链接

## 动效系统

### Motion Contract（Phase 18 设定，受契约测试检查）

仅 3 档时长 + 方向性缓动：

| Token | 值 | 用途 |
|---|---|---|
| `--motion-duration-fast` | 150ms | 按钮/标签反馈 |
| `--motion-duration-standard` | 200ms | 组件出现/消失 |
| `--motion-duration-spatial` | 300ms | 空间移动/翻页/面板展开 |

| Token | 值 | 用途 |
|---|---|---|
| `--motion-ease-enter` | cubic-bezier(0,0,0.2,1) | 出现/置入 |
| `--motion-ease-exit` | cubic-bezier(0.4,0,1,1) | 离开/移除 |

### 禁止项（契约检查，违反则测试失败）
- `transition-all` ❌（必须显式声明过渡属性）
- `ease-linear` ❌（必须用方向性缓动）
- `duration-75/100/500/700/1000` ❌（只能用 3 种 token）
- `animate-pulse/bounce` 装饰性无限循环 ❌（骨架用 `animate-pulse` 封装在 Skeleton 组件中，不是装饰性）
- framer-motion / @react-spring / motion 引用 ❌（纯 CSS）

## 主题系统

### 亮色主题（默认）

- 背景：暖米色 `hsl(42, 35%, 96%)` —— 宣纸质感
- 文字：暖墨色 `hsl(28, 20%, 13%)`
- 主色：柿橙 `hsl(16, 70%, 52%)` —— 朱砂印章色
- 卡片：纸面白 `hsl(40, 43%, 98%)` + `paper-surface` 类（带毛玻璃+阴影）
- 背景装饰：固定 radial-gradient 墨色晕染

### 暗色主题

- 背景：暖墨棕 `hsl(28, 14%, 8%)`
- 文字：米白 `hsl(40, 24%, 90%)`
- 主色：柿橙提亮 `hsl(16, 72%, 58%)`（与亮色同色系）

### 字体系统

| 变量 | 字体 | 用途 |
|---|---|---|
| `--font-sans` | Inter | 正文/UI 文字 |
| `--font-serif` | Noto Serif SC | 标题/阅读器正文 |

加载方式：`next/font/google`，构建时预下载，CSS variable 注入。

## 组件架构

### UI 基元（src/components/ui）

shadcn-like 模式：`@base-ui/react` + `class-variance-authority` + `cn()`。

| 组件 | 说明 |
|---|---|
| `button` | 6 变体 × 8 尺寸，press-down 反馈 |
| `card` | 容器查询自适应，CSS variable 间距 |
| `dialog` | base-ui Dialog + 3D 缩放淡入动画 |
| `sheet` | 侧边滑出面板 + 方向性动画 |
| `dropdown-menu` | 弹出菜单 + 方向性动画 |
| `tabs` | 水平/垂直 Tab，line 指示器 |
| `badge` | 5 变体，pill 形 |
| `skeleton` | 骨架屏（animate-pulse） |

### 业务组件

| 组件 | 用途 |
|---|---|
| `FlipBook` | 首页 3D 翻页书 |
| `NovelCard` | 书架卡片（书脊 + 朱砂印章） |
| `StructureTree` | 分析工作台结构树 |
| `StructureWorkspaceShell` | 分析工作台布局壳 |
| `TimelineChart` | ECharts 时间线图 |
| `RelationshipGraph` | cytoscape 关系力导向图 |
| `ClueWorkspace` | 线索面板 |
| `AppShell` | 全局导航壳（桌面侧导 + 移动底导） |
| `AuthGate` | 登录门禁 + 会话管理 |
| `AppThemeSync` | 主题同步 + 过渡门控 |
