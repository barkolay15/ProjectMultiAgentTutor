"""Общие настройки тестов: у каждого теста своя пустая база памяти во временной папке."""
import pytest


@pytest.fixture(autouse=True)
def _temp_memory_db(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB", str(tmp_path / "memory.db"))
