# 读者侧边栏生图功能 - 完整需求

## 1. 业务目标

在阅读器侧边栏 AI 对话中，用户可以选择「问 AI」（文本对话）或「画图」（生图），
生图结果保存在项目内并可长期查看。

---

## 2. 用户操作流程

### 2.1 入口

```
阅读页 → 选中一段原文 → 弹出浮动操作栏
  ├─ [问 AI]          → 进入现有 Reader Chat，走文本对话
  └─ [画图]           → 进入生图流程
```

### 2.2 生图流程

```
用户点击「画图」→ 弹出 prompt 编辑框（原文已填入，可修改补充）
  → 点击「生成」→ 发送请求
  → 等待生成（展示 loading）
  → 生成完成 → 图片显示在聊天记录
  → 用户可以：
      ├─ 点击图片查看大图
      ├─ 保存到本地（浏览器自带右键/长按）
      ├─ 基于当前图片继续修改（换 prompt 重新生成）
      └─ 切换回文本对话模式继续讨论
```

### 2.3 模式切换

侧边栏输入框上方有 tab 切换：

```
[ 💬 问 AI ] [ 🎨 画图 ]
```

- 当前是「问 AI」模式：输入框 + 发送按钮，走现有 Chat Worker
- 当前是「画图」模式：输入框（prompt）+ 发送按钮，走生图管线
- 两种模式的消息混合显示在同一个聊天记录列表里
- 文本消息和图片消息按时间线穿插排列

---

## 3. 路由逻辑

```
用户消息
        │
        ├─ 模式 = "问 AI"
        │     → 调现有 Reader Chat API（POST /api/novels/{id}/chat）
        │     → 返回文本回答，存入 reader_messages 表
        │
        └─ 模式 = "画图"
              → 调生图 API（POST /api/novels/{id}/chat/generate-image）
              → 管线：
                  1. 接收 { selected_text, user_refine }
                  2. 可选：调 LLM 把中文转成英文 prompt（混元英文理解更好）
                  3. 调本地 ZCodeProxy：POST /v1/images/generations
                  4. 把返回图片复制到项目存储目录
                  5. 写入 generated_images 表
                  6. 写入 reader_messages 表（消息类型 = "image"）
                  7. 返回 { message_id, image_url, prompt }
```

---

## 4. 后端接口

### 4.1 新增端点

```
POST /api/novels/{novel_id}/chat/generate-image

Request Body:
{
  "chapter_id": number,                // 当前章节 ID（可选）
  "selected_text": string,             // 用户选中的原文（可选）
  "user_refine": string,               // 用户补充/修改的描述（可选）
  "source_start": number,              // 原文选区起始（可选）
  "source_end": number,                // 原文选区结束（可选）
}

Response 200:
{
  "id": number,                        // generated_images 表主键
  "message_id": number,                // reader_messages 表主键
  "image_url": string,                 // 可长期访问的图片 URL
  "prompt": string,                    // 实际使用的 prompt（英文）
  "prompt_cn": string,                 // 中文 prompt（原始）
  "created_at": string,                // ISO 时间
  "width": number,                     // 图片宽度
  "height": number,                    // 图片高度
  "file_size": number,                 // 文件大小（字节）
}

Error 400:
  { "detail": "prompt 不能为空" }

Error 500:
  { "detail": "图片生成失败: {具体错误}" }
```

### 4.2 生图管线

```
ImageService.generate(novel_id, chapter_id, selected_text, user_refine)
  │
  ├─ 1. 组合 prompt
  │     如果 selected_text 有值，user_refine 可选追加
  │     如果没有 selected_text，直接用 user_refine
  │     └→ 可选：调 LLM 优化为英文 prompt
  │
  ├─ 2. 调 ZCodeProxy
  │     POST http://localhost:3001/v1/images/generations
  │     Body: { "prompt": "最终 prompt", "size": "1024x1024" }
  │     ← 返回图片数据
  │
  ├─ 3. 持久化图片
  │     目标目录：backend/storage/images/{novel_id}/
  │     文件名：{timestamp}_{random}.png
  │     复制图片文件到目标目录
  │
  ├─ 4. 写入 DB
  │     INSERT INTO generated_images (
  │       novel_id, chapter_id, prompt_en, prompt_cn,
  │       source_start, source_end, selected_text,
  │       file_path, file_size, width, height, created_at
  │     )
  │
  ├─ 5. 写入聊天消息
  │     INSERT INTO reader_messages (
  │       conversation_id, role, content,
  │       message_type = "image",
  │       image_generation_id = ↑
  │     )
  │
  └─ 6. 返回前端需要的字段
```

### 4.3 图片提供

```
FastAPI 挂载静态文件目录：
  /storage/images/{novel_id}/{filename}.png
  → 映射到 backend/storage/images/{novel_id}/{filename}.png

Nginx/反向代理（如有）需透传此路径
```

---

## 5. 数据模型

### 5.1 generated_images 表（新建）

| 字段 | 类型 | 说明 |
|---|---|---|
| id | int PK | 自增主键 |
| novel_id | int FK→novels | 所属小说 |
| chapter_id | int? FK→chapters | 来源章节（可选） |
| conversation_id | int FK→reader_conversations | 所属对话 |
| owner_id | int FK→users | 用户 |
| prompt_cn | text | 中文 prompt（用户输入的原始文本） |
| prompt_en | text | 英文 prompt（实际传给生图模型的） |
| source_start | int? | 原文选区起始 |
| source_end | int? | 原文选区结束 |
| selected_text | text? | 选中的原文 |
| file_path | varchar(500) | 图片文件存储路径（相对 storage 目录） |
| file_size | int | 文件大小（字节） |
| width | int | 图片宽度 |
| height | int | 图片高度 |
| model_used | varchar(50) | 生图模型名称（hunyuan） |
| created_at | timestamp | 生成时间 |

### 5.2 reader_messages 扩展

在现有 `reader_messages` 表加字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| message_type | varchar(20) | 现有："text"；新增："image" |
| image_generation_id | int? FK→generated_images | 关联生图记录 |

---

## 6. 前端改动

### 6.1 reader-chat-panel.tsx（侧边栏对话面板）

**顶部 Tab 切换：**

```
┌─────────────────────────────────┐
│ [ 💬 问 AI ]  [ 🎨 画图 ]       │
├─────────────────────────────────┤
│ 输入框 / prompt 编辑区           │
│ [发送]                           │
├─────────────────────────────────┤
│ 消息列表                         │
│  ├─ 文本消息（现有）              │
│  ├─ 图片消息（新增）              │
│  └─ ...                          │
└─────────────────────────────────┘
```

**「画图」模式行为：**
- 输入框 placeholder 改为「输入画面描述，或留空使用选中原文」
- 发送按钮显示「生成图片」
- 选中文本自动填入输入框作为默认 prompt，用户可修改补充
- 发送后展示 loading 状态（进度动画或骨架屏）
- 生成完成 → 图片 inline 显示在消息列表中

**图片消息渲染：**

```
┌─────────────────────┐
│ 用户: 画一下魔王城   │
├─────────────────────┤
│ ┌─────────────────┐ │
│ │    图片          │ │  ← 自适应宽度，最大 100%
│ │                  │ │  ← 点击弹出 lightbox 查看大图
│ └─────────────────┘ │
│ prompt: 魔王城决战   │ ← 小字显示
│ 2026-07-30 12:00    │
└─────────────────────┘
```

### 6.2 reader-content.tsx（正文区域）

**浮动操作栏新增按钮：**

```
当前：
  [问 AI]        ← 已有

改为：
  [问 AI] [画图]  ← 并列两个按钮
```

### 6.3 新增：图片 lightbox 组件

- 点击消息中的图片 → 全屏弹出 lightbox
- 黑暗背景 + 居中大图
- 点击空白处或 X 关闭
- 支持拖拽移动和滚轮缩放（选做）

### 6.4 前端 API 封装

新增 `frontend/src/lib/image-api.ts`：

```typescript
interface GenerateImageRequest {
  chapter_id?: number;
  selected_text?: string;
  user_refine?: string;
  source_start?: number;
  source_end?: number;
}

interface GenerateImageResponse {
  id: number;
  message_id: number;
  image_url: string;
  prompt: string;
  prompt_cn: string;
  created_at: string;
  width: number;
  height: number;
  file_size: number;
}

async function generateImage(
  novelId: string,
  body: GenerateImageRequest
): Promise<GenerateImageResponse> {
  const res = await fetch(`/api/novels/${novelId}/chat/generate-image`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}
```

---

## 7. 图片存储方案

### 7.1 目录结构

```
backend/storage/
  └── images/
       └── {novel_id}/
            ├── 1723456789_abc123.png
            ├── 1723456800_def456.png
            └── ...
```

### 7.2 命名规则

```
{unix_timestamp}_{8位随机字符}.png
```

### 7.3 生命周期

- 图片生成后**永久保存**，不清除
- 用户删除小说时级联删除（`ondelete="CASCADE"`）
- 后台可加管理接口手动清理（非必需）

### 7.4 FastAPI 挂载

```python
from fastapi.staticfiles import StaticFiles

app.mount(
    "/storage/images",
    StaticFiles(directory="storage/images"),
    name="generated_images",
)
```

---

## 8. 依赖关系

### 8.1 外部依赖

| 依赖 | 说明 | 状态 |
|---|---|---|
| ZCodeProxy（port 3001） | 生图代理，调腾讯混元 | ✅ 已有 |
| Tencent Hunyuan（混元） | 实际生图模型 | ✅ 已有，通过 ZCodeProxy 调用 |

### 8.2 项目内依赖

| 组件 | 说明 |
|---|---|
| 现有 Reader Chat 系统 | 复用 conversation 和 message 体系 |
| 现有 auth / owner 隔离 | 生图继承小说的 owner 权限 |
| 现有 upload 存储逻辑 | 参考 upload 的实现模式做图片存储 |

---

## 9. 不做的事

| 事项 | 原因 |
|---|---|
| 纯本地跑 SD/Kolors | 已有 ZCodeProxy + 混元，没必要再搭 |
| LLM 自动判断路由 | 用户明确要求前端按钮切换 |
| 修改现有 Chat Worker | 两条管线独立，不混入 |
| 图生图（以图生图） | 混元 API 本身支持，但第一版不做 |
| 批量生图 | 一次一张，简单够用 |
| 图片删除接口 | 用户没说需要，后续再加 |
| 图片风格选择 | 混元默认风格，后续可加参数 |

---

## 10. 测试要点

| 测试项 | 方法 |
|---|---|
| 生图端点正常返回 | 调 API，检查 200 + 图片可访问 |
| prompt 为空 | 返回 400 |
| 无选中原文 + 无 user_refine | 返回 400 |
| 图片持久化 | 重启后端后图片 URL 仍可访问 |
| 图片关联小说 | 查 DB，novel_id 正确 |
| 消息类型为 image | 查 reader_messages.message_type |
| 权限隔离 | 跨用户不能访问对方小说下的图片 |
| 删除小说级联 | 删除小说后图片文件和记录都清除 |
