#!/usr/bin/env python3
"""
候选测试题生成器 — 基于文本块内容生成 RAG 评测测试题

使用方式:
  cd backend
  python scripts/generate_eval_candidates.py --novel-id 1 --output evals/novel_eval_candidates.json

流程:
  1. 从 text_chunks 表读取指定小说的已索引块内容
  2. 使用 ai_service.chat() 按 5 种题型生成候选测试题
  3. 输出到 JSON 文件，标记 status=candidate

题型:
  - original_text      : 原文定位（从块内容直接改写为问句）
  - character_relation : 人物关系（跨 2-3 个块的关系型问题）
  - event_causality    : 事件因果（跨章节的因果链问题）
  - timeline           : 时间线（事件顺序和时序问题）
  - foreshadowing      : 伏笔/回收（长距离关联问题）
"""

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from pathlib import Path

# 将 backend 目录加入 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.core.database import async_session_factory
from app.models.text_chunk import TextChunk
from app.models.novel import Novel

# Ollama 配置
OLLAMA_URL = "http://localhost:11434"
OLLAMA_MODEL = "qwen3.5:9b"

# 题型定义
QUESTION_TYPES = [
    "original_text",
    "character_relation",
    "event_causality",
    "timeline",
    "foreshadowing",
]

# 每类型最少生成数
MIN_PER_TYPE = 3
# 每类型目标数
TARGET_PER_TYPE = 20

# ── Prompt 模板 ──────────────────────────────────────────────────────

SYSTEM_PROMPT = """你是一个小说测试题生成专家。给定小说片段，你需要生成高质量的 RAG 检索评测题目。

你必须严格按照 JSON 格式输出，不要输出任何其他内容。格式如下：
{
  "questions": [
    {
      "question": "测试问题文本",
      "question_type": "original_text",
      "difficulty": "medium",
      "gold_chunks": [1, 3, 5],
      "expected_points": ["要点1", "要点2"],
      "must_not_say": ["不应包含的内容1"]
    }
  ]
}

规则：
1. question: 自然语言问题，1-3句话，明确可回答
2. question_type: 必须是指定的题型
3. difficulty: easy/medium/hard
4. gold_chunks: 从提供的片段 ID 列表中选择，表示回答该问题需要参考的片段
5. expected_points: 答案应包含的关键信息点（2-5个）
6. must_not_say: 答案不应包含的错误信息（1-3个）
7. 只生成 {count} 道题
"""

TYPE_INSTRUCTIONS = {
    "original_text": "从片段内容直接改写为问句。问题应能从对应片段中找到明确答案。难度选 easy。",
    "character_relation": "基于跨片段的人物关系设计问题。需要综合 2-3 个片段才能完整回答。难度选 medium。",
    "event_causality": "设计事件的因果关系问题。需要理解跨片段的因果链。难度选 medium/hard。",
    "timeline": "设计事件发生的先后顺序或时间点问题。需要跨片段的时间线索。难度选 medium。",
    "foreshadowing": "设计前期伏笔与后期回收的关联问题。需要跨越较大距离的片段。难度选 hard。",
}


def build_prompt(chunks: list[dict], question_type: str, count: int) -> str:
    """构建生成 prompt"""
    # 格式化片段
    chunks_text = "\n\n".join(
        f"[片段 {c['chunk_index']} | ID={c['id']} | 类型={c['chunk_type']}]\n{c['content']}"
        for c in chunks
    )

    instruction = TYPE_INSTRUCTIONS.get(question_type, "")
    prompt = f"""以下是一部小说的文本片段：

{chunks_text}

请根据以上片段生成 {count} 道 "{question_type}" 类型的测试题。
{instruction}

注意：gold_chunks 字段只使用上面出现的片段 ID。
"""
    return prompt


async def parse_ai_response(response_text: str) -> list[dict]:
    """解析 AI 返回的 JSON"""
    # 去掉可能的 markdown 代码块标记
    text = response_text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:]) if len(lines) > 1 else text
        if text.endswith("```"):
            text = text[:-3]

    try:
        data = json.loads(text)
        if isinstance(data, dict):
            data = data.get("questions", [data])
        if isinstance(data, list):
            return data
    except json.JSONDecodeError:
        # 尝试提取 JSON 数组
        import re
        match = re.search(r"\[[\s\S]*\]", text)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
    return []


async def generate_candidates(
    novel_id: int,
    output_path: str,
    max_chunks_per_prompt: int = 15,
) -> dict:
    """主生成函数"""
    async with async_session_factory() as db:
        # 1. 验证小说存在
        novel = await db.get(Novel, novel_id)
        if not novel:
            raise ValueError(f"小说 ID={novel_id} 不存在")

        # 2. 查询文本块
        result = await db.execute(
            select(TextChunk)
            .where(TextChunk.novel_id == novel_id)
            .order_by(TextChunk.chunk_index)
        )
        chunks = result.scalars().all()

        if not chunks:
            raise ValueError(f"小说 '{novel.title}' (ID={novel_id}) 没有已索引的文本块")

        print(f"[OK] 找到 {len(chunks)} 个文本块 (小说: {novel.title})")

        # 3. 格式化块数据
        chunk_data = [
            {
                "id": c.id,
                "chunk_index": c.chunk_index,
                "chunk_type": c.chunk_type,
                "content": c.content[:800],  # 每块取前 800 字符
            }
            for c in chunks
        ]

    # 4. 按类型生成
    all_candidates = []
    stats = defaultdict(int)

    for qtype in QUESTION_TYPES:
        print(f"\n--- 生成 {qtype} 类型测试题 ---")

        # 采样：均匀分布
        step = max(1, len(chunk_data) // max_chunks_per_prompt)
        sampled = chunk_data[::step][:max_chunks_per_prompt]

        # 确定生成数量
        count = min(TARGET_PER_TYPE, max(MIN_PER_TYPE, len(chunk_data) // 3))

        # 构建 prompt 并调用 AI（直接 HTTP 方式调用 Ollama）
        user_prompt = build_prompt(sampled, qtype, count)
        try:
            import httpx
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(
                    f"{OLLAMA_URL}/api/chat",
                    json={
                        "model": OLLAMA_MODEL,
                        "messages": [
                            {"role": "system", "content": SYSTEM_PROMPT.replace("{count}", str(count))},
                            {"role": "user", "content": user_prompt},
                        ],
                        "stream": False,
                        "options": {"temperature": 0.7},
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                response_text = data["message"]["content"]
            questions = await parse_ai_response(response_text)
        except Exception as e:
            import traceback
            print(f"  [WARN] AI 调用失败: {e}")
            print(f"  [DEBUG] {traceback.format_exc()[:300]}")
            questions = []

        # 过滤无效结果
        valid_count = 0
        for q in questions:
            if not isinstance(q, dict):
                continue
            if "question" not in q or not q["question"]:
                continue

            candidate = {
                "novel_id": novel_id,
                "question": q.get("question", ""),
                "question_type": qtype,
                "difficulty": q.get("difficulty", "medium"),
                "gold_chunks": q.get("gold_chunks", []),
                "expected_points": q.get("expected_points", []),
                "must_not_say": q.get("must_not_say", []),
                "status": "candidate",
                "created_by": "auto",
            }
            all_candidates.append(candidate)
            valid_count += 1

        stats[qtype] = valid_count
        print(f"  [OK] 生成 {valid_count} 条有效候选 (目标 {count})")

    # 5. 输出
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    # 如果指定的是目录，自动加文件名
    if output.is_dir():
        output = output / "novel_eval_candidates.json"

    with open(output, "w", encoding="utf-8") as f:
        json.dump(all_candidates, f, ensure_ascii=False, indent=2)

    # 6. 打印统计
    print(f"\n{'='*60}")
    print(f"生成完成: {output}")
    print(f"总候选数: {len(all_candidates)}")
    for qtype, count in sorted(stats.items()):
        print(f"  {qtype}: {count}")
    print(f"{'='*60}")

    return {"total": len(all_candidates), "by_type": dict(stats), "output": str(output)}


async def main():
    parser = argparse.ArgumentParser(
        description="生成 RAG 评测候选测试题",
    )
    parser.add_argument(
        "--novel-id", type=int, required=True, help="小说 ID"
    )
    parser.add_argument(
        "--output", type=str, default="evals/novel_eval_candidates.json",
        help="输出 JSON 文件路径 (默认: evals/novel_eval_candidates.json)",
    )
    parser.add_argument(
        "--max-chunks", type=int, default=15,
        help="每个 prompt 最多使用的文本块数 (默认: 15)",
    )
    args = parser.parse_args()

    try:
        result = await generate_candidates(
            novel_id=args.novel_id,
            output_path=args.output,
            max_chunks_per_prompt=args.max_chunks,
        )
        print(f"\n[DONE] 候选测试题已生成到 {result['output']}")
    except Exception as e:
        print(f"\n[ERROR] {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
