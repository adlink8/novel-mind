"""Graph fold + degradation + provisional co-occurrence mixin for the query facade.

Extracted from ``query.py`` (refactor split): this mixin owns the deterministic
transition fold (``_fold_observations``), the D-22 degradation-mode decision
(``_degradation_mode``), and the provisional co-occurrence graph derived from
timeline participants (``_provisional_from_timeline`` + keyword heuristics +
stable synthetic ids). It never imports ``query.py`` — shared primitives come
from ``query_primitives`` (leaf) — so the query package dependency graph stays
acyclic. The composed class ``RelationshipGraphQueryService`` keeps the same
method surface.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.relationship import RelationshipObservation
from app.models.timeline import MachineTimelineEvent, TimelineParticipant
from app.schemas.relationship import (
    GraphDegradationMode,
    ProvenanceKind,
    RelationshipEdgeKind,
    RelationshipEdgeType,
    RelationshipGraphEdgeLabel,
    RelationshipIntakeKind,
)

from .query_primitives import (
    HARD_EDGE_CAP,
    HARD_NODE_CAP,
    NORMAL_EDGE_CAP,
    NORMAL_NODE_CAP,
    _FoldedEdge,
    logical_relationship_key,
)


class FoldQueryMixin:
    """Fold / degradation / provisional seams (see module docstring)."""

    @staticmethod
    def _mention_synthetic_id(mention: str) -> int:
        """Stable positive graph id for mention-only nodes (no characters row yet)."""
        import hashlib

        digest = hashlib.sha1(
            mention.strip().lower().encode("utf-8"), usedforsecurity=False
        ).hexdigest()
        # Keep in positive int32-ish range, avoid 0.
        return (int(digest[:8], 16) % 1_900_000_000) + 1

    @staticmethod
    def _pair_synthetic_id(
        source_id: int, target_id: int, relation_type: str = "ally"
    ) -> int:
        """Stable unique provisional observation id for a character pair + type."""
        import hashlib

        lo, hi = (
            (source_id, target_id) if source_id <= target_id else (target_id, source_id)
        )
        digest = hashlib.sha1(
            f"rel:{lo}:{hi}:{relation_type}".encode("utf-8"), usedforsecurity=False
        ).hexdigest()
        return (int(digest[:8], 16) % 1_900_000_000) + 1

    # Keyword heuristics for provisional typing (zh + common novel terms).
    # Priority: more specific types first when multiple match.
    _TYPE_KEYWORDS: dict[str, tuple[str, ...]] = {
        RelationshipEdgeType.ENEMY.value: (
            "战斗",
            "对决",
            "开战",
            "攻打",
            "攻击",
            "击败",
            "击杀",
            "杀死",
            "杀害",
            "仇敌",
            "敌人",
            "敌对",
            "对峙",
            "追杀",
            "讨伐",
            "交战",
            "开战",
            "冲突",
            "挑衅",
            "侮辱",
            "背叛",
            "反叛",
            "魔王战",
            "死斗",
            "斩杀",
            "围攻",
            "侵攻",
            "侵略",
            "battle",
            "fight",
            "enemy",
            "kill",
        ),
        RelationshipEdgeType.FAMILY.value: (
            "父亲",
            "母亲",
            "父女",
            "父子",
            "母女",
            "母子",
            "兄妹",
            "姐弟",
            "兄弟",
            "姐妹",
            "哥哥",
            "姐姐",
            "弟弟",
            "妹妹",
            "儿子",
            "女儿",
            "亲人",
            "血缘",
            "家族",
            "家人",
            "妻子",
            "丈夫",
            "夫妻",
            "父",
            "母",
            "family",
            "father",
            "mother",
            "sibling",
        ),
        RelationshipEdgeType.MENTOR.value: (
            "师父",
            "师傅",
            "徒弟",
            "弟子",
            "传授",
            "教导",
            "指导",
            "师从",
            "拜师",
            "收徒",
            "训练",
            "培养",
            "指点",
            "mentor",
            "master",
            "disciple",
            "apprentice",
        ),
        RelationshipEdgeType.ROMANTIC.value: (
            "恋爱",
            "恋人",
            "告白",
            "表白",
            "亲吻",
            "接吻",
            "结婚",
            "婚约",
            "爱慕",
            "喜欢",
            "倾心",
            "情人",
            "伴侣",
            "romance",
            "love",
            "kiss",
            "marry",
        ),
        RelationshipEdgeType.ALLY.value: (
            "同盟",
            "结盟",
            "盟友",
            "并肩",
            "合作",
            "帮助",
            "救援",
            "援护",
            "部下",
            "主从",
            "效忠",
            "誓约",
            "结成",
            "命名",
            "庇护",
            "守护",
            "好友",
            "伙伴",
            "友军",
            "ally",
            "friend",
            "allyship",
        ),
    }

    @classmethod
    def _infer_provisional_type(
        cls, *, title: str, description: str, event_type: str
    ) -> str:
        """Infer edge type from event text + timeline event_type (heuristic)."""
        blob = f"{title or ''}\n{description or ''}".lower()
        et = (event_type or "").lower().strip()

        # Event-type prior (timeline schema: conflict/plot/character/world).
        if et in {"conflict", "battle", "fight", "war"}:
            base = RelationshipEdgeType.ENEMY.value
        elif et in {"character", "dialogue", "social"}:
            base = RelationshipEdgeType.ALLY.value
        else:
            base = RelationshipEdgeType.ALLY.value

        scores: dict[str, int] = {t.value: 0 for t in RelationshipEdgeType}
        scores[base] += 1
        if et in {"conflict", "battle", "fight", "war"}:
            scores[RelationshipEdgeType.ENEMY.value] += 3

        for rel_type, keywords in cls._TYPE_KEYWORDS.items():
            for kw in keywords:
                if kw.lower() in blob:
                    scores[rel_type] += 2

        # Prefer non-ally when it has clear keyword evidence.
        ranked = sorted(
            scores.items(),
            key=lambda item: (
                item[1],
                1 if item[0] != RelationshipEdgeType.ALLY.value else 0,
            ),
            reverse=True,
        )
        best_type, best_score = ranked[0]
        if best_score <= 0:
            return RelationshipEdgeType.ALLY.value
        return best_type

    async def _provisional_from_timeline(
        self,
        session: AsyncSession,
        *,
        owner_id: int,
        novel_id: int,
        version_id: int,
        through_chapter: int,
        character_id: int | None,
        relation_type: str | None,
    ) -> tuple[list[_FoldedEdge], dict[int, str]]:
        """Provisional co-occurrence graph from timeline participants.

        Primary label is always ``cooccur`` (not a confirmed fiction type).
        Heuristic ally/enemy/… live only in ``suggested_type`` and preview text
        as non-assertive type clues, never as accepted observations.
        """
        events = list(
            (
                await session.scalars(
                    select(MachineTimelineEvent).where(
                        MachineTimelineEvent.owner_id == owner_id,
                        MachineTimelineEvent.novel_id == novel_id,
                        MachineTimelineEvent.version_id == version_id,
                        MachineTimelineEvent.narrative_chapter_number
                        <= through_chapter,
                    )
                )
            ).all()
        )
        if not events:
            return [], {}

        event_by_id = {e.id: e for e in events}
        event_ids = list(event_by_id.keys())
        parts = list(
            (
                await session.scalars(
                    select(TimelineParticipant).where(
                        TimelineParticipant.event_id.in_(event_ids)
                    )
                )
            ).all()
        )
        by_event: dict[int, list[TimelineParticipant]] = defaultdict(list)
        for p in parts:
            by_event[p.event_id].append(p)

        # pair -> aggregate co-occurrence + per-type votes
        pair_stats: dict[tuple[int, int], dict[str, Any]] = {}
        names: dict[int, str] = {}

        for event_id, plist in by_event.items():
            event = event_by_id.get(event_id)
            if event is None:
                continue
            inferred = self._infer_provisional_type(
                title=event.title or "",
                description=event.description or "",
                event_type=event.event_type or "",
            )
            seen: dict[int, str] = {}
            for p in plist:
                mention = (p.mention or "").strip()
                if not mention:
                    continue
                cid = (
                    p.entity_id
                    if p.entity_id is not None
                    else self._mention_synthetic_id(mention)
                )
                names[cid] = mention if p.entity_id is None else names.get(cid, mention)
                seen[cid] = mention
            ids = sorted(seen.keys())
            # 序章/未知章节（narrative_chapter_number=0）在可见性语义上归为第 1 章：
            # RelationshipGraphNode.first_visible_chapter 契约要求 gt=0。
            ch = max(1, event.narrative_chapter_number)
            for i in range(len(ids)):
                for j in range(i + 1, len(ids)):
                    a, b = ids[i], ids[j]
                    key = (a, b)
                    st = pair_stats.get(key)
                    if st is None:
                        st = {
                            "count": 0,
                            "first_chapter": ch,
                            "type_votes": defaultdict(int),
                            "type_samples": {},
                        }
                        pair_stats[key] = st
                    st["count"] += 1
                    st["type_votes"][inferred] += 1
                    if ch < st["first_chapter"]:
                        st["first_chapter"] = ch
                    # Keep a short sample title per type for preview.
                    if inferred not in st["type_samples"]:
                        st["type_samples"][inferred] = (event.title or "")[:80]

        # One co-occurrence edge per pair; optional suggested_type for UI tint.
        # Quotas still diversify by suggested heuristic so conflict arcs surface
        # without claiming accepted fiction types.
        min_cooccur = 2
        type_quota = {
            RelationshipEdgeType.ENEMY.value: 14,
            RelationshipEdgeType.ALLY.value: 14,
            RelationshipEdgeType.FAMILY.value: 8,
            RelationshipEdgeType.MENTOR.value: 8,
            RelationshipEdgeType.ROMANTIC.value: 6,
        }
        type_label = {
            "ally": "同盟/协作",
            "enemy": "敌对/冲突",
            "family": "亲属",
            "mentor": "师徒",
            "romantic": "爱慕",
        }
        cooccur_label = RelationshipGraphEdgeLabel.COOCCUR.value

        # (pair, suggested_t, vote_n, st)
        typed: list[tuple[tuple[int, int], str, int, dict[str, Any]]] = []
        for key, st in pair_stats.items():
            if st["count"] < min_cooccur:
                continue
            a, b = key
            if character_id is not None and character_id not in (a, b):
                continue
            votes: dict[str, int] = dict(st["type_votes"])
            if not votes:
                votes = {RelationshipEdgeType.ALLY.value: int(st["count"])}
            ordered = sorted(votes.items(), key=lambda kv: (-kv[1], kv[0]))
            selected: list[tuple[str, int]] = [ordered[0]]
            if len(ordered) > 1:
                top_t, top_v = ordered[0]
                second_t, second_v = ordered[1]
                if second_v >= max(2, int(top_v * 0.4)) and second_t != top_t:
                    selected.append((second_t, second_v))
            for suggested_t, vote_n in selected:
                if relation_type is not None and relation_type not in (
                    suggested_t,
                    cooccur_label,
                ):
                    continue
                if vote_n < 1:
                    continue
                typed.append((key, suggested_t, vote_n, st))

        # Prefer higher votes; enemy slightly boosted so conflict arcs surface.
        def sort_key(
            item: tuple[tuple[int, int], str, int, dict[str, Any]],
        ) -> tuple[int, int, int, tuple[int, int]]:
            key, suggested_t, vote_n, st = item
            boost = 3 if suggested_t == RelationshipEdgeType.ENEMY.value else 0
            if suggested_t in (
                RelationshipEdgeType.FAMILY.value,
                RelationshipEdgeType.MENTOR.value,
                RelationshipEdgeType.ROMANTIC.value,
            ):
                boost = 2
            return (-(vote_n + boost), -int(st["count"]), int(st["first_chapter"]), key)

        typed.sort(key=sort_key)

        used_quota: dict[str, int] = defaultdict(int)
        folded: list[_FoldedEdge] = []
        # Deduplicate undirected pair so multi-suggested does not double-claim.
        seen_pairs: set[tuple[int, int]] = set()
        for (a, b), suggested_t, vote_n, st in typed:
            pair_key = (a, b)
            if pair_key in seen_pairs:
                continue
            if used_quota[suggested_t] >= type_quota.get(suggested_t, 8):
                continue
            sample = (st.get("type_samples") or {}).get(suggested_t) or ""
            clue_label = type_label.get(suggested_t, suggested_t)
            preview = (
                f"时间线共现×{int(st['count'])}"
                f"（类型线索·{clue_label}×{int(vote_n)}，非已确认关系）"
                + (f"：{sample}" if sample else "")
                + " · 临时图"
            )
            folded.append(
                _FoldedEdge(
                    observation_id=self._pair_synthetic_id(a, b, cooccur_label),
                    source_character_id=a,
                    target_character_id=b,
                    relation_type=cooccur_label,
                    transition="establish",
                    confidence=min(
                        0.55, 0.22 + 0.03 * int(vote_n) + 0.02 * int(st["count"])
                    ),
                    valid_from_chapter=int(st["first_chapter"]),
                    valid_to_chapter=None,
                    logical_key=logical_relationship_key(a, b, cooccur_label),
                    provenance=ProvenanceKind.MACHINE,
                    evidence_preview=preview[:400],
                    evidence_count=int(st["count"]),
                    edge_kind=RelationshipEdgeKind.PROVISIONAL_COOCCURRENCE,
                    suggested_type=suggested_t,
                    intake_kind=RelationshipIntakeKind.COOCCURRENCE_CANDIDATE.value,
                )
            )
            used_quota[suggested_t] += 1
            seen_pairs.add(pair_key)

        return folded, names

    @staticmethod
    def _degradation_mode(node_count: int, edge_count: int) -> GraphDegradationMode:
        if node_count > HARD_NODE_CAP or edge_count > HARD_EDGE_CAP:
            return GraphDegradationMode.FILTERS_REQUIRED
        if node_count > NORMAL_NODE_CAP or edge_count > NORMAL_EDGE_CAP:
            return GraphDegradationMode.LARGE
        return GraphDegradationMode.NORMAL

    def _fold_observations(
        self,
        observations: Iterable[RelationshipObservation],
        *,
        identity_map: dict[int, int],
        override_fields: dict[str, dict[str, Any]],
    ) -> list[_FoldedEdge]:
        """Deterministic transition fold per logical relationship key (D-06)."""

        by_key: dict[str, list[RelationshipObservation]] = defaultdict(list)
        for obs in observations:
            src = identity_map.get(obs.source_character_id, obs.source_character_id)
            tgt = identity_map.get(obs.target_character_id, obs.target_character_id)
            if src == tgt:
                continue
            key = logical_relationship_key(src, tgt, obs.relation_type)
            by_key[key].append(obs)

        folded: list[_FoldedEdge] = []
        for key, chain in by_key.items():
            chain.sort(
                key=lambda o: (
                    o.valid_from_chapter,
                    o.valid_from_narrative_index,
                    o.id,
                )
            )
            current: _FoldedEdge | None = None
            for obs in chain:
                src = identity_map.get(obs.source_character_id, obs.source_character_id)
                tgt = identity_map.get(obs.target_character_id, obs.target_character_id)
                if obs.transition == "end":
                    current = None
                    continue
                current = _FoldedEdge(
                    observation_id=obs.id,
                    source_character_id=src,
                    target_character_id=tgt,
                    relation_type=obs.relation_type,
                    transition=obs.transition,
                    confidence=float(obs.confidence),
                    valid_from_chapter=obs.valid_from_chapter,
                    valid_to_chapter=obs.valid_to_chapter,
                    logical_key=key,
                    provenance=ProvenanceKind.MACHINE,
                    intake_kind=(
                        getattr(obs, "intake_kind", None)
                        or RelationshipIntakeKind.UNKNOWN.value
                    ),
                )

            if current is None:
                continue

            # Apply latest eligible overrides for this logical key (overlay only).
            patches = override_fields.get(current.logical_key, {})
            # Also accept overrides keyed without type if type was changed.
            if not patches:
                # Try all override keys that share endpoints.
                for okey, fields in override_fields.items():
                    parts = okey.split(":")
                    if (
                        len(parts) == 3
                        and parts[0] == str(current.source_character_id)
                        and parts[1] == str(current.target_character_id)
                    ):
                        patches = fields
                        break

            provenance = ProvenanceKind.MACHINE
            if patches:
                provenance = ProvenanceKind.MANUAL
                if "relation_type" in patches:
                    value = patches["relation_type"]
                    if isinstance(value, dict):
                        value = value.get("relation_type", current.relation_type)
                    current.relation_type = str(value)
                if "transition" in patches:
                    value = patches["transition"]
                    if isinstance(value, dict):
                        value = value.get("transition", current.transition)
                    current.transition = str(value)
                if "valid_from" in patches:
                    value = patches["valid_from"]
                    if isinstance(value, dict) and "valid_from_chapter" in value:
                        current.valid_from_chapter = int(value["valid_from_chapter"])
                if "valid_to" in patches:
                    value = patches["valid_to"]
                    if isinstance(value, dict):
                        if value.get("valid_to_chapter") is None:
                            current.valid_to_chapter = None
                        else:
                            current.valid_to_chapter = int(value["valid_to_chapter"])
                current.provenance = provenance

            if current.transition == "end":
                continue
            folded.append(current)

        folded.sort(
            key=lambda e: (
                e.valid_from_chapter,
                e.source_character_id,
                e.target_character_id,
                e.observation_id,
            )
        )
        return folded


__all__ = ["FoldQueryMixin"]
