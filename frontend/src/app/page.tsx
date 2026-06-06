"use client";

import React from "react";
import Link from "next/link";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";

// Placeholder statistics data
const stats = [
  { label: "\u5C0F\u8BF4\u603B\u6570", value: "0", icon: "\uD83D\uDCDA" },
  { label: "\u7AE0\u8282\u603B\u6570", value: "0", icon: "\uD83D\uDCDD" },
  { label: "AI \u5206\u6790\u6B21\u6570", value: "0", icon: "\uD83E\uDD16" },
  { label: "\u540C\u4EBA\u6587\u4F5C\u54C1", value: "0", icon: "\u270D\uFE0F" },
];

// Quick actions
const quickActions = [
  {
    title: "\u5BFC\u5165\u5C0F\u8BF4",
    description: "\u4E0A\u4F20 TXT \u6587\u4EF6\uFF0CAI \u81EA\u52A8\u5206\u6790",
    icon: "\uD83D\uDCD6",
    href: "/novels?action=import",
    color: "from-novel-400 to-novel-600",
  },
  {
    title: "\u6211\u7684\u4E66\u67B6",
    description: "\u6D4F\u89C8\u548C\u7BA1\u7406\u4F60\u7684\u5C0F\u8BF4\u5E93",
    icon: "\uD83D\uDCDA",
    href: "/novels",
    color: "from-blue-400 to-indigo-600",
  },
  {
    title: "\u521B\u4F5C\u4E2D\u5FC3",
    description: "\u57FA\u4E8E\u539F\u4F5C\u98CE\u683C\u7EED\u5199\u65B0\u7BC7\u7AE0",
    icon: "\u270D\uFE0F",
    href: "/writing",
    color: "from-pink-400 to-rose-600",
  },
  {
    title: "AI \u8BBE\u7F6E",
    description: "\u914D\u7F6E AI \u6A21\u578B\u548C\u8DEF\u7531\u7B56\u7565",
    icon: "\u2699\uFE0F",
    href: "/settings",
    color: "from-emerald-400 to-teal-600",
  },
];

// Placeholder recent activities
const recentActivities = [
  {
    icon: "\uD83D\uDCD6",
    text: "\u5C1A\u65E0\u6D3B\u52A8\u8BB0\u5F55",
    time: "",
    description: "\u5BFC\u5165\u4F60\u7684\u7B2C\u4E00\u672C\u5C0F\u8BF4\u5F00\u59CB\u4F53\u9A8C",
  },
];

export default function HomePage() {
  return (
    <div className="p-6 md:p-8 max-w-6xl mx-auto">
      {/* Hero Section */}
      <section className="mb-10">
        <div className="rounded-2xl bg-gradient-to-br from-novel-500 via-novel-600 to-purple-700 p-8 md:p-12 text-white relative overflow-hidden">
          {/* Decorative circles */}
          <div className="absolute -top-10 -right-10 w-40 h-40 rounded-full bg-white/10" />
          <div className="absolute -bottom-6 -left-6 w-28 h-28 rounded-full bg-white/10" />

          <div className="relative z-10">
            <h2 className="text-3xl md:text-4xl font-bold mb-3">
              NovelMind
            </h2>
            <p className="text-lg md:text-xl text-white/80 mb-2">
              {"\u8BA9 AI \u6210\u4E3A\u4F60\u7684\u5C0F\u8BF4\u4F19\u4F34"}
            </p>
            <p className="text-sm text-white/60 max-w-lg">
              {"\u8BFB\u61C2\u6545\u4E8B\u3001\u7406\u6E05\u8109\u7EDC\u3001\u7EED\u5199\u7BC7\u7AE0 \u2014\u2014 AI \u9A71\u52A8\u7684\u5C0F\u8BF4\u6DF1\u5EA6\u7406\u89E3\u4E0E\u521B\u4F5C\u5E73\u53F0"}
            </p>
            <div className="flex gap-3 mt-6">
              <Link href="/novels?action=import">
                <Button
                  size="lg"
                  className="bg-white text-novel-700 hover:bg-white/90 font-semibold"
                >
                  {"\u5F00\u59CB\u4F7F\u7528"}
                </Button>
              </Link>
              <Link href="/novels">
                <Button
                  size="lg"
                  variant="outline"
                  className="border-white/30 text-white hover:bg-white/10 hover:text-white"
                >
                  {"\u6D4F\u89C8\u4E66\u67B6"}
                </Button>
              </Link>
            </div>
          </div>
        </div>
      </section>

      {/* Statistics Row */}
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

      {/* Quick Action Cards */}
      <section className="mb-10">
        <h3 className="text-lg font-semibold mb-4">{"\u5FEB\u6377\u64CD\u4F5C"}</h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          {quickActions.map((action) => (
            <Link key={action.title} href={action.href}>
              <Card className="h-full cursor-pointer hover:ring-2 hover:ring-novel-400/50 hover:shadow-lg transition-all">
                <CardContent>
                  <div
                    className={`w-12 h-12 rounded-xl bg-gradient-to-br ${action.color} flex items-center justify-center text-2xl mb-4`}
                  >
                    {action.icon}
                  </div>
                  <h4 className="font-semibold mb-1">{action.title}</h4>
                  <p className="text-sm text-muted-foreground">
                    {action.description}
                  </p>
                </CardContent>
              </Card>
            </Link>
          ))}
        </div>
      </section>

      {/* Recent Activity */}
      <section>
        <h3 className="text-lg font-semibold mb-4">{"\u6700\u8FD1\u6D3B\u52A8"}</h3>
        <Card>
          <CardContent>
            <div className="divide-y">
              {recentActivities.map((activity, idx) => (
                <div
                  key={idx}
                  className="flex items-start gap-3 py-4 first:pt-0 last:pb-0"
                >
                  <div className="text-2xl mt-0.5">{activity.icon}</div>
                  <div className="flex-1">
                    <p className="text-sm font-medium">{activity.text}</p>
                    {activity.description && (
                      <p className="text-xs text-muted-foreground mt-0.5">
                        {activity.description}
                      </p>
                    )}
                  </div>
                  {activity.time && (
                    <span className="text-xs text-muted-foreground whitespace-nowrap">
                      {activity.time}
                    </span>
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
