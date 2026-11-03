"""Проверка выбора проверяющего из .env без ключей и сети."""
import runpy
from pathlib import Path
from unittest.mock import Mock

import dotenv
import pytest

from core import adapters, llm_client


@pytest.mark.parametrize("setting, expected", [("1", "real"), ("0", "stub"), (None, "stub")])
def test_checker_from_dotenv(tmp_path, monkeypatch, setting, expected):
    project = Path(__file__).resolve().parents[1]
    env_file = tmp_path / ".env"
    env_file.write_text("" if setting is None else f"USE_REAL_CHECKER={setting}\n", encoding="utf-8")
    load_dotenv = dotenv.load_dotenv

    def load_test_env(path):
        assert path == project / ".env"
        return load_dotenv(env_file)

    monkeypatch.delenv("USE_REAL_CHECKER", raising=False)
    monkeypatch.setattr(dotenv, "load_dotenv", load_test_env)
    model = Mock()
    monkeypatch.setattr(llm_client, "get_llm", lambda: model)
    connect = Mock(side_effect=lambda agents, llm: agents)
    monkeypatch.setattr(adapters, "with_real_checker", connect)
    monkeypatch.chdir(tmp_path)

    namespace = runpy.run_path(str(project / "api" / "routes.py"))

    assert namespace["info"]()["checker"] == expected
    assert connect.call_count == (expected == "real")
    if expected == "real":
        assert connect.call_args.args[1] is model
