/**
 * 设置中心 - app/settings/page.tsx
 * AI 模型连接管理 / 用量概览。
 * 页面只负责组装，各区块实现见 components/settings/。
 */

import { ChapterOrnament } from "@/components/chapter-ornament";
import { PageContainer, PageHeader } from "@/components/page-header";
import { AgentSettingsSection } from "@/components/settings/agent-settings/agent-settings-section";
import { ModelsSection } from "@/components/settings/models-section";
import { ModelsBindingSection } from "@/components/settings/models-binding/models-binding-section";
import { SkillsToolsEntry } from "@/components/settings/skills-tools/skills-tools-entry";
import { UsageSection } from "@/components/settings/usage-section";

export default function SettingsPage() {
  return (
    <PageContainer className="space-y-8">
      <PageHeader
        eyebrow="Settings"
        title="设置中心"
        description="管理实际 AI 提供商连接、智能体能力与用量。"
      />

      <ModelsSection chapter="壹" />
      <ChapterOrnament />
      <UsageSection chapter="贰" />
      <ChapterOrnament />
      <AgentSettingsSection chapter="叁" />
      <ChapterOrnament />
      <ModelsBindingSection chapter="肆" />
      <ChapterOrnament />
      <SkillsToolsEntry chapter="伍" />
    </PageContainer>
  );
}
