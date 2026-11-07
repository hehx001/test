"""Minimal paid API check using the same adapter as the CAD app.

After local key setup, run: python scripts/check_model_connection.py
Only reports statuses and provider model IDs, never the credentials.
"""
from __future__ import annotations

import asyncio

from cad_audit.models import (
    ModelConfigurationError, ModelServiceError,
    generate_ai_review, public_provider_status,
)

TEST_REPORT = {
    "results": {"dxf": {"entity_counts": {"DIMENSION": 1}}},
    "findings": [
        {"rule": "TEST-001", "entity": "SIMULATED-DIM",
         "title": "模拟尺寸差异，测试API连通性（非真实工程问题）",
         "severity": "info", "status": "needs_review", "source": "SIMULATED",
         "evidence": {"shown": "12", "raw_measurement": 10}},
    ],
}


async def main():
    for item in public_provider_status():
        provider = item["id"]
        if not item["configured"]:
            print(f"{provider}：未配置可用密钥或服务地址，跳过")
            continue
        try:
            result = await generate_ai_review(TEST_REPORT, provider)
        except (ModelServiceError, ModelConfigurationError) as exc:
            print(f"{provider}：连接或解析失败：{exc}")
            continue
        if result.get("status") == "ok":
            print(f"{provider}：API调用成功，模型={result['model']}；"
                  f"规则解释数={len(result['checks'])}；"
                  f"tokens={result.get('usage', {}).get('prompt_tokens', '未知')}/"
                  f"{result.get('usage', {}).get('completion_tokens', '未知')}")
        else:
            print(f"{provider}：调用未完成（{result.get('status', '未知')}）")


if __name__ == "__main__":
    asyncio.run(main())
