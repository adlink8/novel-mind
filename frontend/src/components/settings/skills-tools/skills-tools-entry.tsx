import { SettingsSection } from "../settings-section";
import Link from "next/link";

export function SkillsToolsEntry({ chapter }: { chapter: string }) {
  return (
    <SettingsSection chapter={chapter} title="Skills/Tools">
      <div className="paper-surface rounded-3xl p-5 sm:p-6">
        <div className="space-y-2 text-sm leading-6 text-muted-foreground">
          <p>第一阶段支持声明式 Skill、现有 Tool Catalog 与受限 HTTP Tool。</p>
          <p>不支持任意代码/Shell。</p>
        </div>
        <Link
          href="/settings/extensions"
          className="mt-5 inline-flex h-8 items-center rounded-lg border border-border bg-background px-3 text-sm font-medium transition-colors hover:bg-muted"
        >
          进入 Skills/Tools 管理
        </Link>
      </div>
    </SettingsSection>
  );
}
