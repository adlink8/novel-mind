"""AIModelProvider schema enum must stay in sync with the provider catalog.

The /api/models request schema restricts `provider` to a Literal enum, and
/app/services/provider_catalog.py is the runtime authority that rejects
unknown providers. If the two drift apart, the API either accepts values the
catalog cannot serve (500 at request time) or rejects values the catalog
supports (422 for a valid provider).
"""

from __future__ import annotations

import typing

import pytest

from app.schemas.ai_model import AIModelProvider
from app.services.provider_catalog import provider_profiles

pytestmark = pytest.mark.unit


def test_provider_enum_matches_catalog_profiles():
    enum_values = set(typing.get_args(AIModelProvider))
    catalog_ids = {profile["id"] for profile in provider_profiles()}
    assert enum_values == catalog_ids
