import Link from "next/link";
import { BookOpenText } from "lucide-react";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/** 404 — 书卷语气，纸面卡片样式。 */
export default function NotFound() {
  return (
    <div className="mx-auto grid w-full max-w-[1480px] place-items-center px-4 py-16 sm:px-6 xl:px-10">
      <div className="paper-surface flex w-full max-w-xl flex-col items-center rounded-3xl p-10 text-center sm:p-14">
        <div className="mb-5 grid size-14 place-items-center rounded-2xl bg-secondary text-primary">
          <BookOpenText className="size-6" />
        </div>
        <h1 className="font-serif text-xl font-semibold">这一页还没有写下</h1>
        <p className="mt-2 mb-6 max-w-md text-sm text-muted-foreground">
          你访问的页面不存在，或已被移动到别的章节。
        </p>
        <Link href="/" className={cn(buttonVariants({ size: "lg" }))}>
          回到书架
        </Link>
      </div>
    </div>
  );
}
