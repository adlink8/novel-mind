/**
 * 首页仪表盘 (Next.js Client Component)
 *
 * 展示:
 * 1. Hero Section: 应用介绍 + 快速入口按钮
 * 2. 统计卡片: 小说总数/章节数/AI分析次数/同人文作品（当前为占位数据）
 * 3. 快捷操作: 4 个功能入口卡片（导入/书架/创作/设置）
 * 4. 最近活动: 活动时间线（当前为占位数据）
 *
 * TODO: 统计数据和最近活动需要接入后端 API（当前是硬编码占位）
 */

"use client";

import React from "react";
import Link from "next/link";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";

/** 统计卡片数据（占位，未来接入 API） */
const stats = [
  { label: "小说总数", value: "0", icon: "📚" },
  { label: "章节总数", value: "0", icon: "📝" },
  { label: "AI 分析次数", value: "0", icon: "🤖" },
  { label: "同人文作品", value: "0", icon: "✍️" },
];

/** 快捷操作入口配置 */
const quickActions = [
  { title: "导入小说", description: "上传 TXT 文件，AI 自动分析", icon: "📖", href: "/novels?action=import", color: "from-novel-400 to-novel-600" },
  { title: "我的书架", description: "浏览和管理你的小说库", icon: "📚", href: "/novels", color: "from-blue-400 to-indigo-600" },
  { title: "创作中心", description: "基于原作风格续写新篇章", icon: "✍️", href: "/writing", color: "from-pink-400 to-rose-600" },
  { title: "AI 设置", description: "配置 AI 模型和路由策略", icon: "⚙️", href: "/settings", color: "from-emerald-400 to-teal-600" },
];

/** 最近活动数据（占位） */
const recentActivities = [
  { icon: "📖", text: "尚无活动记录", time: "", description: "导入你的第一本小说开始体验" },
];

export default function HomePage() {
  return (
    <div className="p-6 md:p-8 max-w-6xl mx-auto">
      {/* Hero Section: 渐变背景 + 应用介绍 */}
      <section className="mb-10">
        <div className="rounded-2xl bg-gradient-to-br from-novel-500 via-novel-600 to-purple-700 p-8 md:p-12 text-white relative overflow-hidden">
          {/* 装饰性半透明圆形 */}
          <div className="absolute -top-10 -right-10 w-40 h-40 rounded-full bg-white/10" />
          <div className="absolute -bottom-6 -left-6 w-28 h-28 rounded-full bg-white/10" />

          <div className="relative z-10">
            <h2 className="text-3xl md:text-4xl font-bold mb-3">NovelMind</h2>
            <p className="text-lg md:text-xl text-white/80 mb-2">{"让 AI 成为你的小说伙伴"}</p>
            <p className="text-sm text-white/60 max-w-lg">{"读懂故事、理清脉络、续写篇章 —— AI 驱动的小说深度理解与创作平台"}</p>
            <div className="flex gap-3 mt-6">
              <Link href="/novels?action=import">
                <Button size="lg" className="bg-white text-novel-700 hover:bg-white/90 font-semibold">{"开始使用"}</Button>
              </Link>
              <Link href="/novels">
                <Button size="lg" variant="outline" className="border-white/30 text-white hover:bg-white/10 hover:text-white">{"浏览书架"}</Button>
              </Link>
            </div>
          </div>
        </div>
      </section>

      {/* 统计卡片行 */}
      <section className="mb-10">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          {stats.map((stat) => (
            <Card key={stat.label}>
              <CardContent className="flex items-center gap-4">
                <div className="text-3xl">{stat.icon}</div>
                <div>
                  <p className="text-2xl font-bold">{stat.value}</p>
                  <p className="text-xs text-muted-foreground">{stat.label}</p>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      </section>

      {/* 快捷操作卡片网格 */}
      <section className="mb-10">
        <h3 className="text-lg font-semibold mb-4">{"快捷操作"}</h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          {quickActions.map((action) => (
            <Link key={action.title} href={action.href}>
              <Card className="h-full cursor-pointer hover:ring-2 hover:ring-novel-400/50 hover:shadow-lg transition-all">
                <CardContent>
                  <div className={`w-12 h-12 rounded-xl bg-gradient-to-br ${action.color} flex items-center justify-center text-2xl mb-4`}>
                    {action.icon}
                  </div>
                  <h4 className="font-semibold mb-1">{action.title}</h4>
                  <p className="text-sm text-muted-foreground">{action.description}</p>
                </CardContent>
              </Card>
            </Link>
          ))}
        </div>
      </section>

      {/* 最近活动列表 */}
      <section>
        <h3 className="text-lg font-semibold mb-4">{"最近活动"}</h3>
        <Card>
          <CardContent>
            <div className="divide-y">
              {recentActivities.map((activity, idx) => (
                <div key={idx} className="flex items-start gap-3 py-4 first:pt-0 last:pb-0">
                  <div className="text-2xl mt-0.5">{activity.icon}</div>
                  <div className="flex-1">
                    <p className="text-sm font-medium">{activity.text}</p>
                    {activity.description && (
                      <p className="text-xs text-muted-foreground mt-0.5">{activity.description}</p>
                    )}
                  </div>
                  {activity.time && (
                    <span className="text-xs text-muted-foreground whitespace-nowrap">{activity.time}</span>
                  )}
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      </section>
    </div>
  );
}
