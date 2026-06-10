/**
 * 创作中心页面 - app/writing/page.tsx
 * ======================================
 * 同人文创作管理页面，支持基于原作进行 AI 辅助的同人创作。
 *
 * 主要职责：
 * 1. 展示同人文草稿列表（目前为占位数据，列表为空）
 * 2. 引导用户了解创作流程（三步：导入原作 -> 选择分支点 -> AI 辅助创作）
 * 3. 展示同人文卡片（包含标题、原作、字数、状态、最后编辑时间）
 * 4. 提供"新建同人文"按钮（当前禁用，MVP 阶段未实现）
 *
 * 创作流程说明：
 * Step 1: 导入原作 - 上传 TXT 文件，AI 自动分析风格和内容
 * Step 2: 选择分支点 - 选择想要改写的剧情节点，或创建全新的故事线
 * Step 3: AI 辅助创作 - 与 AI 协作，生成符合原作风格的新篇章
 *
 * 数据说明：
 * - placeholderDrafts 当前为空数组，后续应接入后端 fanfictionApi
 * - 状态映射：draft（草稿）、writing（创作中）、completed（已完成）
 */

/**
 * 创作中心页 (Writing Page)
 *
 * 功能:
 * 1. 同人文作品列表（卡片网格，当前为空数组占位）
 * 2. 创作引导（三步流程说明：导入原作 → 选择分支点 → AI 辅助创作）
 * 3. 新建同人文按钮（当前 disabled，Phase 6 实现）
 *
 * TODO: 接入后端 /api/fanfiction API，实现同人文 CRUD
 */

"use client";

import React, { useState } from "react";
import Link from "next/link";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  CardDescription,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/empty-state";

/**
 * 同人文草稿数据接口
 * 定义了每篇同人文的基本信息结构
 */
interface FanFictionDraft {
  id: string;
  title: string;           // 同人文标题
  sourceNovel: string;     // 原作小说名称
  sourceNovelId: string;   // 原作小说 ID（用于跳转）
  wordCount: number;       // 字数
  status: "draft" | "writing" | "completed";  // 创作状态
  lastEdited: string;      // 最后编辑时间（ISO 格式）
}

/** 占位草稿数据 - 当前为空数组，后续从 API 获取 */
const placeholderDrafts: FanFictionDraft[] = [];

/** 状态中文标签映射 */
const statusLabels: Record<FanFictionDraft["status"], string> = {
  draft: "草稿",
  writing: "创作中",
  completed: "已完成",
};

/** 状态样式映射 - 不同状态使用不同颜色的 Badge */
const statusStyles: Record<FanFictionDraft["status"], string> = {
  draft: "bg-gray-100 text-gray-700",
  writing: "bg-blue-100 text-blue-700",
  completed: "bg-green-100 text-green-700",
};

/**
 * 格式化字数显示
 * - 大于等于 10000 字时显示为 "X.X万字"
 * - 小于 10000 字时显示为 "X字"
 */
function formatWordCount(count: number): string {
  if (count >= 10000) {
    return `${(count / 10000).toFixed(1)}万字`;
  }
  return `${count}字`;
}

/**
 * 格式化日期显示
 * 将 ISO 日期字符串转换为中文本地格式（如"2024年1月15日"）
 */
function formatDate(dateStr: string): string {
  if (!dateStr) return "";
  const date = new Date(dateStr);
  return date.toLocaleDateString("zh-CN", {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

/**
 * 创作中心页面组件
 * 
 * 渲染结构：
 * 1. 头部：标题 + "新建同人文"按钮
 * 2. 引导卡片（无草稿时显示）：三步创作流程说明
 * 3. 草稿列表 / 空状态
 */
export default function WritingPage() {
  // 草稿列表状态 - 当前使用占位数据
  const [drafts] = useState<FanFictionDraft[]>(placeholderDrafts);

  return (
    <div className="p-6 md:p-8 max-w-6xl mx-auto">
      {/* ========== 页面头部 ========== */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h2 className="text-2xl font-bold">{"创作中心"}</h2>
          <p className="text-muted-foreground mt-1">
            {"基于原作风格，续写你的同人故事"}
          </p>
        </div>
        {/* 新建按钮 - MVP 阶段禁用 */}
        <Button size="lg" disabled>
          + {"新建同人文"}
        </Button>
      </div>

      {/* ========== 创作流程引导卡片 ========== */}
      {/* 仅在没有草稿时显示，引导用户了解创作流程 */}
      {drafts.length === 0 && (
        <Card className="mb-8">
          <CardContent>
            <h3 className="font-semibold mb-4">{"如何开始创作？"}</h3>
            {/* 三步流程说明，桌面端三列布局，移动端单列 */}
            <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
              {/* Step 1: 导入原作 */}
              <div className="flex gap-3">
                <div className="w-10 h-10 rounded-full bg-novel-100 flex items-center justify-center text-novel-600 font-bold shrink-0">
                  1
                </div>
                <div>
                  <h4 className="font-medium text-sm">
                    {"导入原作"}
                  </h4>
                  <p className="text-xs text-muted-foreground mt-1">
                    {"上传你喜欢的小说，AI 会自动分析风格和内容"}
                  </p>
                </div>
              </div>

              {/* Step 2: 选择分支点 */}
              <div className="flex gap-3">
                <div className="w-10 h-10 rounded-full bg-novel-100 flex items-center justify-center text-novel-600 font-bold shrink-0">
                  2
                </div>
                <div>
                  <h4 className="font-medium text-sm">
                    {"选择分支点"}
                  </h4>
                  <p className="text-xs text-muted-foreground mt-1">
                    {"选择想要改写的剧情节点，或创建全新的故事线"}
                  </p>
                </div>
              </div>

              {/* Step 3: AI 辅助创作 */}
              <div className="flex gap-3">
                <div className="w-10 h-10 rounded-full bg-novel-100 flex items-center justify-center text-novel-600 font-bold shrink-0">
                  3
                </div>
                <div>
                  <h4 className="font-medium text-sm">
                    {"AI 辅助创作"}
                  </h4>
                  <p className="text-xs text-muted-foreground mt-1">
                    {"与 AI 协作，生成符合原作风格的新篇章"}
                  </p>
                </div>
              </div>
            </div>

            {/* 跳转到书架导入小说 */}
            <div className="mt-6 text-center">
              <Link href="/novels">
                <Button variant="outline">
                  {"前往书架导入小说"}
                </Button>
              </Link>
            </div>
          </CardContent>
        </Card>
      )}

      {/* ========== 草稿列表 / 空状态 ========== */}
      {drafts.length === 0 ? (
        /* 无草稿时显示空状态提示 */
        <EmptyState
          icon={"✍️"}
          title={"还没有同人文作品"}
          description={
            "导入一本小说后，即可开始 AI 辅助的同人创作"
          }
        />
      ) : (
        /* 有草稿时显示卡片网格 */
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {drafts.map((draft) => (
            <Card
              key={draft.id}
              className="cursor-pointer hover:ring-2 hover:ring-novel-400/50 hover:shadow-lg transition-all"
            >
              <CardHeader>
                <div className="flex items-start justify-between">
                  {/* 标题（单行截断） */}
                  <CardTitle className="line-clamp-1">
                    {draft.title}
                  </CardTitle>
                  {/* 状态 Badge */}
                  <span
                    className={`inline-flex h-5 items-center rounded-4xl px-2 text-xs font-medium shrink-0 ${statusStyles[draft.status]}`}
                  >
                    {statusLabels[draft.status]}
                  </span>
                </div>
                {/* 原作名称 */}
                <CardDescription className="line-clamp-1">
                  {"原作："}{draft.sourceNovel}
                </CardDescription>
              </CardHeader>

              <CardContent>
                <div className="flex items-center justify-between text-xs text-muted-foreground">
                  {/* 字数 */}
                  <span>{formatWordCount(draft.wordCount)}</span>
                  {/* 最后编辑时间 */}
                  <span>
                    {"最后编辑："}{formatDate(draft.lastEdited)}
                  </span>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
