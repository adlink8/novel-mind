/**
 * 设置中心 - app/settings/page.tsx
 * 账户（退出登录）+ AI 模型管理 / 预算 / 用量概览。
 * 页面只负责组装，各区块实现见 components/settings/。
 */

import { ChapterOrnament } from "@/components/chapter-ornament";
import { PageContainer, PageHeader } from "@/components/page-header";
import { AccountSection } from "@/components/settings/account-section";
import { AIBudgetSection } from "@/components/settings/ai-budget-section";
import { ModelsSection } from "@/components/settings/models-section";
import { UsageSection } from "@/components/settings/usage-section";

export default function SettingsPage() {
  return (
    <PageContainer className="space-y-8">
      <PageHeader
        eyebrow="Settings"
        title="设置中心"
        description="管理账户、AI 提供商与调用预算。退出登录请在下方账户区操作。"
      />

      <AccountSection chapter="壹" />
      <ChapterOrnament />
      <ModelsSection chapter="贰" />
      <ChapterOrnament />
      <AIBudgetSection chapter="叁" />
      <ChapterOrnament />
      <UsageSection chapter="肆" />
    </PageContainer>
  );
}
