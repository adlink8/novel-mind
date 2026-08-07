"""Adversarial scope gates for Canon Fork creation (Phase 35-02, D-35-03).

REQ-FORK-01 / REQ-CRE-01: the client can never widen the fork scope. These
deterministic gates (contract + AST source checks, no PostgreSQL) prove that:

- identical input replays the identical frozen manifest hash, and every scope
  dimension (owner/novel/version/snapshot/cutoff/lineage) changes it;
- an unauthorized full-book cutoff can never be elevated (403) and a future
  cutoff can never expand the scope (400);
- a stale or beyond-cutoff citation leaf fails closed;
- the wire request is ``extra="forbid"``: the client cannot inject
  ``owner_id``/``novel_id``/``active``/``full_book_authorized``;
- no source path creates or switches a production ``active`` pointer, and the
  ORM binds ``active`` to ``false`` at the database level.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.models.user import User
from app.services.canon_fork.contracts import (
    CanonAuthority,
    CanonCitationPolicy,
    CanonScope,
    CanonSpace,
)
from app.services.canon_fork.lineage import (
    build_leaf_lineage,
    validate_leaf_lineage,
)
from app.services.canon_fork.snapshot import (
    CanonForkManifest,
    CanonForkScopeError,
    build_canon_fork_manifest,
    chapter_content_hash,
    compute_cutoff_snapshot_hash,
    resolve_cutoff,
)
from app.api.canon_fork import CanonForkCreateRequest

pytestmark = [pytest.mark.unit, pytest.mark.adversarial]

HEX64 = "a" * 64
HEX64_B = "b" * 64

BACKEND_ROOT = Path(__file__).resolve().parents[2]
API_SOURCE = (BACKEND_ROOT / "app" / "api" / "canon_fork.py").read_text(
    encoding="utf-8"
)
SNAPSHOT_SOURCE = (
    BACKEND_ROOT / "app" / "services" / "canon_fork" / "snapshot.py"
).read_text(encoding="utf-8")
MODEL_SOURCE = (BACKEND_ROOT / "app" / "models" / "canon_fork.py").read_text(
    encoding="utf-8"
)

CONTENTS = ("chapter 1 body", "chapter 2 body", "chapter 3 body")
CONTENT_HASHES = {i + 1: chapter_content_hash(text) for i, text in enumerate(CONTENTS)}


def _manifest(
    *, fork_key: str = "ff-main", through_chapter: int = 3, **overrides
) -> CanonForkManifest:
    payload = dict(
        owner_id=1,
        novel_id=2,
        fork_key=fork_key,
        source_version_key="original:0123456789abcdef",
        source_snapshot_id="novel:2:0123456789abcdef",
        source_snapshot_hash=HEX64,
        through_chapter=through_chapter,
        full_book_authorized=False,
        citation_lineage=[],
        authorization={
            "source": "server_chapter_limit",
            "requested_cutoff_chapter": through_chapter,
            "full_book_requested": False,
            "novel_chapter_count": 3,
            "authorized_cutoff_chapter": through_chapter,
            "granted_full_book": False,
        },
    )
    payload.update(overrides)
    lineage = [
        leaf.to_payload()
        for leaf in build_leaf_lineage(
            source_snapshot_hash=payload["source_snapshot_hash"],
            chapter_numbers=[1, 2, 3],
            content_hashes=CONTENT_HASHES,
            through_chapter=payload["through_chapter"],
        )
    ]
    payload["citation_lineage"] = lineage
    payload["cutoff_snapshot_hash"] = compute_cutoff_snapshot_hash(
        source_snapshot_hash=payload["source_snapshot_hash"],
        through_chapter=payload["through_chapter"],
        lineage=lineage,
    )
    return build_canon_fork_manifest(**payload)


def _user(*, superuser: bool = False) -> User:
    return User(
        id=7,
        username="adversarial",
        email="adv@example.com",
        hashed_password="!test-hash",
        is_superuser=superuser,
    )


# ---------------------------------------------------------------------------
# Deterministic manifest hash: same input -> same hash, any drift -> new hash
# ---------------------------------------------------------------------------


def test_same_input_replays_same_manifest_hash():
    assert _manifest().manifest_hash == _manifest().manifest_hash
    assert len(_manifest().manifest_hash) == 64
    assert _manifest().recompute_manifest_hash() == _manifest().manifest_hash


def test_manifest_hash_is_sensitive_to_every_scope_dimension():
    base = _manifest()
    for different in (
        _manifest(owner_id=9),
        _manifest(novel_id=9),
        _manifest(fork_key="ff-other"),
        _manifest(source_version_key="original:ffffffffffffffff"),
        _manifest(source_snapshot_hash=HEX64_B),
        _manifest(through_chapter=2),
        _manifest(full_book_authorized=True),
    ):
        assert different.manifest_hash != base.manifest_hash, (
            "scope drift must change the manifest hash"
        )


def test_manifest_binds_space_and_cutoff_into_scope_hash():
    a = _manifest(through_chapter=2)
    b = _manifest(through_chapter=3)
    assert a.scope_hash != b.scope_hash
    scope = CanonScope.model_validate(
        {
            "owner_id": 1,
            "novel_id": 2,
            "space": CanonSpace.FANFICTION_CANON,
            "namespace": "fork:ff-main",
            "version_key": "ff-main",
            "authority": CanonAuthority.CREATIVE_DRAFT,
            "citation_policy": CanonCitationPolicy.FANFICTION_ONLY,
            "source_snapshot_hash": HEX64,
            "cutoff": {
                "through_chapter": 3,
                "full_book_authorized": False,
                "snapshot_hash": a.cutoff_snapshot_hash,
            },
        }
    )
    assert len(scope.scope_hash()) == 64


# ---------------------------------------------------------------------------
# Server-derived cutoff: no elevation, no expansion
# ---------------------------------------------------------------------------


def test_unauthorized_full_book_cannot_be_elevated():
    with pytest.raises(CanonForkScopeError, match="full_book_requires_authorization"):
        resolve_cutoff(
            user=_user(superuser=False),
            requested_cutoff_chapter=3,
            full_book_requested=True,
            novel_chapter_count=3,
        )


def test_superuser_full_book_is_sealed_with_audit():
    cutoff = resolve_cutoff(
        user=_user(superuser=True),
        requested_cutoff_chapter=3,
        full_book_requested=True,
        novel_chapter_count=3,
    )
    assert cutoff.through_chapter == 3
    assert cutoff.full_book_authorized is True
    assert cutoff.authorization["source"] == "server_superuser"
    assert cutoff.authorization["granted_full_book"] is True


def test_future_cutoff_cannot_expand_scope():
    with pytest.raises(CanonForkScopeError, match="cutoff_exceeds_scope"):
        resolve_cutoff(
            user=_user(),
            requested_cutoff_chapter=4,
            full_book_requested=False,
            novel_chapter_count=3,
        )


def test_invalid_cutoff_fails_closed():
    with pytest.raises(CanonForkScopeError, match="invalid_cutoff"):
        resolve_cutoff(
            user=_user(),
            requested_cutoff_chapter=0,
            full_book_requested=False,
            novel_chapter_count=3,
        )


def test_empty_novel_source_fails_closed():
    with pytest.raises(CanonForkScopeError, match="empty_source_snapshot"):
        resolve_cutoff(
            user=_user(),
            requested_cutoff_chapter=None,
            full_book_requested=False,
            novel_chapter_count=0,
        )


# ---------------------------------------------------------------------------
# Citation lineage: stale and beyond-cutoff leaves fail closed
# ---------------------------------------------------------------------------


def test_stale_citation_leaf_fails_closed():
    lineage = [
        {
            "leaf_key": "chapter:1",
            "chapter_number": 1,
            "content_hash": CONTENT_HASHES[1],
            "source_snapshot_hash": HEX64_B,  # not the sealed snapshot
        }
    ]
    with pytest.raises(ValueError, match="stale_citation_leaf"):
        validate_leaf_lineage(lineage, source_snapshot_hash=HEX64, through_chapter=3)


def test_beyond_cutoff_leaf_fails_closed():
    lineage = [
        {
            "leaf_key": "chapter:2",
            "chapter_number": 2,
            "content_hash": CONTENT_HASHES[2],
            "source_snapshot_hash": HEX64,
        }
    ]
    with pytest.raises(ValueError, match="beyond_cutoff_leaf"):
        validate_leaf_lineage(lineage, source_snapshot_hash=HEX64, through_chapter=1)


def test_empty_lineage_fails_closed():
    with pytest.raises(ValueError, match="empty citation lineage"):
        validate_leaf_lineage([], source_snapshot_hash=HEX64, through_chapter=3)


def test_build_leaf_lineage_respects_cutoff():
    leaves = build_leaf_lineage(
        source_snapshot_hash=HEX64,
        chapter_numbers=[1, 2, 3],
        content_hashes=CONTENT_HASHES,
        through_chapter=2,
    )
    assert [leaf.chapter_number for leaf in leaves] == [1, 2]


# ---------------------------------------------------------------------------
# Wire contract: extra="forbid" prevents client scope injection
# ---------------------------------------------------------------------------


def test_client_cannot_inject_owner_or_novel():
    with pytest.raises(ValidationError, match="extra_forbidden"):
        CanonForkCreateRequest(
            fork_key="ff-main",
            owner_id=1,
            novel_id=2,
        )
    with pytest.raises(ValidationError, match="extra_forbidden"):
        CanonForkCreateRequest(
            fork_key="ff-main",
            requested_cutoff_chapter=3,
            active=True,
        )


def test_client_cannot_inject_full_book_authorized():
    with pytest.raises(ValidationError, match="extra_forbidden"):
        CanonForkCreateRequest(
            fork_key="ff-main",
            full_book_requested=True,
            full_book_authorized=True,
        )


def test_blank_or_oversized_fork_key_fails_closed():
    with pytest.raises((ValidationError, ValueError)):
        CanonForkCreateRequest(fork_key="   ")
    with pytest.raises(ValidationError):
        CanonForkCreateRequest(fork_key="x" * 200)


def test_invalid_expected_snapshot_hash_fails_closed():
    with pytest.raises(ValidationError):
        CanonForkCreateRequest(fork_key="ff", expected_source_snapshot_hash="short")


# ---------------------------------------------------------------------------
# No production active pointer write path exists
# ---------------------------------------------------------------------------


def test_api_and_snapshot_sources_never_set_active_true():
    for source in (API_SOURCE, SNAPSHOT_SOURCE):
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "active":
                        # The only literal assignment is the immutable default False.
                        if (
                            isinstance(node.value, ast.Constant)
                            and node.value.value is True
                        ):
                            raise AssertionError(
                                f"active=True must never be assigned in {source}"
                            )
                    if isinstance(target, ast.Attribute) and target.attr == "active":
                        if (
                            isinstance(node.value, ast.Constant)
                            and node.value.value is True
                        ):
                            raise AssertionError(
                                f"active=True must never be assigned in {source}"
                            )
    assert "active_pointer" not in API_SOURCE
    assert "create_active" not in API_SOURCE


def test_orm_binds_active_to_false():
    assert "active = false" in MODEL_SOURCE
    assert "ck_canon_forks_no_active_pointer" in MODEL_SOURCE


def test_contracts_source_is_write_free():
    # The service may persist candidate rows, but it must never write an active
    # pointer and never touch the Original Canon mutation path.
    assert "NarrativeActivePointer" not in SNAPSHOT_SOURCE
    assert "OriginalCanon" not in SNAPSHOT_SOURCE
    for token in ("active=True", "active = True"):
        assert token not in SNAPSHOT_SOURCE
