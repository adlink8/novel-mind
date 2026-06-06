export default function HomePage() {
  return (
    <div className="p-8">
      <header className="mb-8">
        <h2 className="text-2xl font-bold">欢迎使用 NovelMind</h2>
        <p className="text-muted-foreground mt-2">
          让 AI 成为你的小说伙伴 —— 读懂故事、理清脉络、续写篇章
        </p>
      </header>

      {/* 快捷操作卡片 */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
        <ActionCard
          title="导入小说"
          description="上传 TXT 文件，AI 自动分析"
          icon="📖"
          href="/novels?action=import"
        />
        <ActionCard
          title="剧情分析"
          description="深度理解剧情、人物、伏笔"
          icon="🔍"
          href="/novels"
        />
        <ActionCard
          title="时间线"
          description="可视化小说事件时间线"
          icon="⏱️"
          href="/novels"
        />
        <ActionCard
          title="续写同人文"
          description="基于原作风格续写新篇章"
          icon="✍️"
          href="/writing"
        />
      </div>

      {/* 最近活动 */}
      <section>
        <h3 className="text-lg font-semibold mb-4">最近活动</h3>
        <div className="rounded-lg border bg-card p-6 text-center text-muted-foreground">
          还没有活动记录。导入你的第一本小说开始吧！
        </div>
      </section>
    </div>
  );
}

function ActionCard({
  title,
  description,
  icon,
  href,
}: {
  title: string;
  description: string;
  icon: string;
  href: string;
}) {
  return (
    <a
      href={href}
      className="block rounded-lg border bg-card p-6 hover:border-primary hover:shadow-md transition-all"
    >
      <div className="text-3xl mb-3">{icon}</div>
      <h4 className="font-semibold">{title}</h4>
      <p className="text-sm text-muted-foreground mt-1">{description}</p>
    </a>
  );
}
