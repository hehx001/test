"""Safely configure local model keys without putting secrets in Git or shell history.

Run once: python scripts/configure_api_keys.py
New keys should be generated after accidental exposure in chat or logs.
"""
from __future__ import annotations

import getpass
import os
import re
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
ALLOWED = (
    "QWEN_API_KEY", "DEEPSEEK_API_KEY",
    "QWEN_MODEL", "DEEPSEEK_MODEL",
    "QWEN_BASE_URL", "DEEPSEEK_BASE_URL",
)
DEFAULTS = {
    "QWEN_MODEL": "qwen-plus",
    "DEEPSEEK_MODEL": "deepseek-flash",
    "QWEN_BASE_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "DEEPSEEK_BASE_URL": "https://api.deepseek.com",
}


def env_text(values: dict[str, str]) -> str:
    """Render only expected variables; do not accept newlines or key injections."""
    lines = ["# Private credentials for this computer only. Do not commit this file."]
    for name in ALLOWED:
        value = str(values.get(name, ""))
        if any(ch in value for ch in "\r\n\0"):
            raise ValueError("密钥或模型配置中包含非法字符")
        value = value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$")
        lines.append(f'{name}="{value}"')
    return "\n".join(lines) + "\n"


def save_local_config(path: Path, values: dict[str, str]) -> None:
    if path.is_symlink():
        raise ValueError("拒绝写入指向其他位置的密钥文件")
    data = env_text(values)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fd = -1
            fh.write(data)
    finally:
        if fd >= 0:
            os.close(fd)


def main() -> None:
    env_path = ROOT / ".env"
    if env_path.is_symlink():
        raise SystemExit("拒绝读取符号链接形式的密钥文件")
    existing = dict(dotenv_values(env_path)) if env_path.exists() else {}
    values = {k: (existing.get(k) or DEFAULTS.get(k, "")) for k in ALLOWED}
    print("仅保存于本机 cad-audit-mvp/.env，Git 已忽略此文件。")
    print("请使用新生成的密钥，不要继续使用已在聊天中发送的密钥。")
    for provider, name in (("Qwen", "QWEN_API_KEY"), ("DeepSeek", "DEEPSEEK_API_KEY")):
        received = getpass.getpass(f"{provider} 新API Key（不回显，回车保留旧值）: ").strip()
        if received:
            if not re.fullmatch(r"sk-[A-Za-z0-9._-]{9,}", received):
                raise SystemExit(f"{provider}密钥格式看起来不完整，未保存任何改动")
            values[name] = received
    print("Qwen默认采用阿里云百炼北京公共地址；若密钥属于其他地域或订阅方案，需在.env中修改QWEN_BASE_URL。")
    save_local_config(env_path, values)
    print("本机密钥文件保存完成（权限仅限当前用户）。")
    print("请执行 python scripts/check_model_connection.py 测试真实API连通性。")


if __name__ == "__main__":
    main()
