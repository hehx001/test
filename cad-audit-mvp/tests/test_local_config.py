"""Local secret storage tests; only synthetic values, never user credentials."""
from pathlib import Path

import pytest
from dotenv import dotenv_values

from scripts import configure_api_keys as cfg


def test_local_env_is_private_and_parses(tmp_path):
    path = tmp_path / ".env"
    values = {
        "QWEN_API_KEY": "sk-test-only-placeholder-one",
        "DEEPSEEK_API_KEY": "sk-test-only-placeholder-two",
        **cfg.DEFAULTS,
    }
    cfg.save_local_config(path, values)
    data = dotenv_values(path)
    assert data["QWEN_API_KEY"] == values["QWEN_API_KEY"]
    assert data["DEEPSEEK_API_KEY"] == values["DEEPSEEK_API_KEY"]
    assert (path.stat().st_mode & 0o777) == 0o600


def test_symlink_will_not_be_overwritten(tmp_path):
    backing = tmp_path / "do_not_touch.txt"
    backing.write_text("unmodified", encoding="utf-8")
    target = tmp_path / ".env"
    target.symlink_to(backing)
    with pytest.raises(ValueError, match="符号链接|其他位置"):
        cfg.save_local_config(target, cfg.DEFAULTS)
    assert backing.read_text(encoding="utf-8") == "unmodified"


def test_interactive_keys_never_echo(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cfg, "ROOT", tmp_path)
    secrets = iter(("sk-test-only-QWEN123456", "sk-test-only-DS123456"))
    monkeypatch.setattr(cfg.getpass, "getpass", lambda label: next(secrets))
    cfg.main()
    text = capsys.readouterr().out
    assert "sk-test-only-" not in text
    values = dotenv_values(tmp_path / ".env")
    assert values["QWEN_API_KEY"].startswith("sk-test-only-QWEN")
    assert values["DEEPSEEK_API_KEY"].startswith("sk-test-only-DS")


def test_rejects_newline_in_values():
    with pytest.raises(ValueError):
        cfg.env_text({"QWEN_API_KEY": "sk-a\nDEEPSEEK_API_KEY=trick"})
