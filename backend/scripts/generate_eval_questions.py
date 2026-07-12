#!/usr/bin/env python3
"""
基于规则的测试题快速生成器（不需要 AI）

直接从 text_chunks 表中提取内容，按规则生成 5 类测试题。
用于 AI 服务不可用时的降级方案。

使用方式:
  cd backend
  source venv/Scripts/activate
  python scripts/generate_eval_questions.py --novel-id 6 --output evals/novel_eval_candidates.json
"""

import argparse
import asyncio
import json
import random
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.core.database import async_session_factory
from app.models.text_chunk import TextChunk
from app.models.novel import Novel

# 中文标点分割
CHINESE_SENTENCE_RE = re.compile(r"([^。！？\n]+[。！？\n]*)")


def extract_sentences(text: str, min_len: int = 10) -> list[str]:
    """从文本中提取句子"""
    sentences = []
    for match in CHINESE_SENTENCE_RE.finditer(text):
        s = match.group(1).strip()
        if len(s) >= min_len and not s.isspace():
            sentences.append(s)
    return sentences


def contains_person_name(text: str) -> bool:
    """简单检测是否含有人名（含常见姓氏）"""
    pattern = re.compile(
        r"(路明非|楚子航|诺诺|恺撒|夏弥|昂热|曼斯|叶胜|亚纪"
        r"|芬格尔|零|源稚生|源稚女|上杉绘梨衣|酒德麻衣|苏恩曦"
        r"|[李王张刘陈杨黄赵周吴徐孙马胡朱郭何罗高林]"
        r"[一二三四五六七八九十]|[李王张刘陈杨]..)"
    )
    return bool(pattern.search(text))


def is_event_sentence(text: str) -> bool:
    """检测是否描述事件"""
    event_keywords = ["发生", "出现", "出发", "到达", "离开", "进入",
                      "打开", "关闭", "发现", "找到", "失去", "得到",
                      "战斗", "攻击", "逃跑", "死亡", "受伤"]
    return any(kw in text for kw in event_keywords)


def is_temporal_sentence(text: str) -> bool:
    """检测是否含时间表达"""
    time_keywords = ["早上", "中午", "下午", "晚上", "第二天", "次日",
                     "三天后", "一周后", "一个月后", "那年", "那年夏天",
                     "那年冬天", "十年前", "二十年前", "数年后"]
    return any(kw in text for kw in time_keywords)


def make_question(sentence: str) -> str:
    """从句子生成一个简单的问句"""
    # 提取关键词
    words = sentence[:50]
    # 简单替换：把陈述句变成疑问句
    if "是" in words:
        return f"文中提到{words[:15]}...这具体指什么？"
    elif "在" in words:
        return f"{words[:20]}...是在什么情况下发生的？"
    elif contains_person_name(words):
        return f"关于{words[:20]}...具体情况是怎样的？"
    else:
        return f"文中描述\"{words[:30]}...\"是在讲述什么？"


async def generate(novel_id: int, output_path: str, count_per_type: int = 20):
    """主生成函数"""
    async with async_session_factory() as db:
        novel = await db.get(Novel, novel_id)
        if not novel:
            print(f"[ERROR] 小说 ID={novel_id} 不存在")
            return

        result = await db.execute(
            select(TextChunk)
            .where(TextChunk.novel_id == novel_id)
            .order_by(TextChunk.chunk_index)
        )
        chunks = result.scalars().all()

    print(f"[OK] 小说: {novel.title}, 文本块: {len(chunks)}")

    # 按类型分类句子
    type_pool = defaultdict(list)
    chunk_id_map = {}  # sentence → chunk_id

    for c in chunks:
        sentences = extract_sentences(c.content, min_len=15)
        for s in sentences:
            chunk_id_map[s] = c.id

            # 简单分类
            if contains_person_name(s) and len(s) > 20:
                type_pool["character_relation"].append(s)
            if is_event_sentence(s):
                type_pool["event_causality"].append(s)
            if is_temporal_sentence(s):
                type_pool["timeline"].append(s)
            # 所有句子都可以做原文定位题
            type_pool["original_text"].append(s)

    # 伏笔类：取跨度大的句子（相距远的 chunk 中）
    if len(chunks) > 10:
        for i in [0, len(chunks) // 3, len(chunks) // 2, len(chunks) - 1]:
            sentences = extract_sentences(chunks[i].content, min_len=20)
            for s in sentences[:5]:
                type_pool["foreshadowing"].append(s)

    # 每类洗牌取前 N 条
    all_candidates = []
    stats = {}

    for qtype in ["original_text", "character_relation", "event_causality", "timeline", "foreshadowing"]:
        pool = type_pool.get(qtype, [])
        random.shuffle(pool)
        selected = pool[:count_per_type]

        for s in selected:
            cid = chunk_id_map.get(s)
            if not cid:
                continue

            # 从原始 chunk 中找 gold_chunks
            gold = [cid]

            candidate = {
                "novel_id": novel_id,
                "question": make_question(s),
                "question_type": qtype,
                "difficulty": "medium" if qtype != "foreshadowing" else "hard",
                "gold_chunks": gold,
                "expected_points": ["见对应文本块"],
                "must_not_say": [],
                "status": "candidate",
                "created_by": "auto",
            }
            all_candidates.append(candidate)

        stats[qtype] = len(selected)
        print(f"  {qtype}: {len(selected)} 条")

    # 输出
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.is_dir():
        output = output / "novel_eval_candidates.json"

    with open(output, "w", encoding="utf-8") as f:
        json.dump(all_candidates, f, ensure_ascii=False, indent=2)

    print(f"\n[DONE] 共生成 {len(all_candidates)} 条候选测试题 → {output}")
    return all_candidates


async def main():
    parser = argparse.ArgumentParser(description="规则生成 RAG 评测测试题（无需 AI）")
    parser.add_argument("--novel-id", type=int, required=True)
    parser.add_argument("--output", default="evals/novel_eval_candidates.json")
    parser.add_argument("--count", type=int, default=20, help="每类题数 (默认 20)")
    args = parser.parse_args()

    await generate(args.novel_id, args.output, args.count)


if __name__ == "__main__":
    asyncio.run(main())
