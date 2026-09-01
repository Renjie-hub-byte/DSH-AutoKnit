"""Pytest fixtures that build a sample task tree in a tmp dir."""

import pytest

from helpers import (
    build_final_task,
    build_sample_task,
    build_wiring_task,
)  # noqa: E402


@pytest.fixture
def sample_task(tmp_path):
    return build_sample_task(str(tmp_path / "task"))


@pytest.fixture
def final_task(tmp_path):
    return build_final_task(str(tmp_path / "final"))


@pytest.fixture
def wiring_task(tmp_path):
    return build_wiring_task(str(tmp_path / "wiring"))
