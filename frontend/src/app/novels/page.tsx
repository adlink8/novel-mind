export default function NovelsPage() {
  return (
    <div className="p-8">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h2 className="text-2xl font-bold">我的书架</h2>
          <p className="text-muted-foreground mt-1">管理你的小说库</p>
        </div>
        <button className="px-4 py-2 bg-primary text-primary-foreground rounded-lg hover:opacity-90 transition-opacity">
          + 导入小说
        </button>
      </div>

      {/* 搜索栏 */}
      <div className="mb-6">
        <input
          type="text"
          placeholder="搜索小说标题、作者..."
          className="w-full px-4 py-2 rounded-lg border bg-background focus:outline-none focus:ring-2 focus:ring-primary/50"
        />
      </div>

      {/* 小说列表 - 空状态 */}
      <div className="rounded-lg border bg-card p-12 text-center">
        <div className="text-5xl mb-4">📚</div>
        <h3 className="text-lg font-semibold mb-2">书架是空的</h3>
        <p className="text-muted-foreground mb-4">
          导入你的第一本小说，AI 将帮你深度理解和分析
        </p>
        <button className="px-6 py-2 bg-primary text-primary-foreground rounded-lg hover:opacity-90 transition-opacity">
          导入 TXT 文件
        </button>
        <p className="text-xs text-muted-foreground mt-2">
          支持 .txt 格式，EPUB 和 PDF 即将支持
        </p>
      </div>
    </div>
  );
}
