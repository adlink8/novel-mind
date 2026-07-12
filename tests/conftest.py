"""Root test configuration for repo-level CI policy tests."""

from __future__ import annotations

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "contract: Schema/API/policy contract tests (timeout 15s)"
    )
    config.addinivalue_line("markers", "unit: Isolated unit tests")
    config.addinivalue_line("markers", "integration: Multi-component tests")
    config.addinivalue_line("markers", "live: Requires external live dependencies")
