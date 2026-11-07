"""Provider-switching tests use mocked HTTPS responses, never real API credentials."""
import asyncio
import json

import ezdxf
import httpx
import pytest
from fastapi.testclient import TestClient

from cad_audit.api import app
from cad_audit.models import (
    ModelConfigurationError, ModelServiceError,
    generate_ai_review, public_provider_status,
)


REPORT = {
    "results": {
        "dxf": {
            "entity_counts": {"DIMENSION": 1, "LINE": 4},
            "dimension_text_stats": {"numeric_conflicts": 1},
        }
    },
    "findings": [{
        "rule": "DXF-001", "entity": "93", "title": "尺寸与测量值疑似冲突",
        "status": "needs_review", "source": "Model", "severity": "warning",
        "evidence": {"shown": "72", "raw_measurement": 70.0}
    }],
}


def _mock_transport(expected_key, expected_model, expected_host):
    def handler(request):
        assert request.url.host == expected_host
        assert request.method == "POST"
        assert request.url.path.endswith("/chat/completions")
        assert request.headers["authorization"] == "Bearer " + expected_key
        body = json.loads(request.content)
        assert body["model"] == expected_model
        if expected_host == "api.deepseek.com":
            assert body["thinking"] == {"type": "disabled"}
            assert body["response_format"] == {"type": "json_object"}
        assert body["messages"][1]["role"] == "user"
        user_data = json.loads(body["messages"][1]["content"].split("\n", 1)[1])
        assert user_data["findings"][0]["rule"] == "DXF-001"
        assert "preview" not in user_data  # no whole original CAD or geometry preview
        return httpx.Response(200, json={
            "choices": [{"message": {"content": json.dumps({
                "summary": "发现一处需要核查的尺寸疑点。",
                "checks": [
                    {"rule": "DXF-001", "entity": "93",
                     "analysis": "显示尺寸与原始几何测量值存在差异。",
                     "action": "核实公差、单位及人工指定尺寸。"},
                    {"rule": "MADE-UP", "entity": "FAKE",
                     "analysis": "编造的问题", "action": "编造的操作"},
                ],
                "scope_note": "没有开展受力与功能验证。"
            }, ensure_ascii=False)}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 80},
        })
    return httpx.MockTransport(handler)


@pytest.mark.parametrize("provider,key_name,model_name,host", [
    ("qwen", "QWEN_API_KEY", "qwen-plus", "dashscope.aliyuncs.com"),
    ("deepseek", "DEEPSEEK_API_KEY", "deepseek-flash", "api.deepseek.com"),
])
def test_two_providers_route_independently(monkeypatch, provider, key_name, model_name, host):
    monkeypatch.setenv(key_name, provider + "_local_fake_secret")
    # Explicitly configure both names; only selected service may be contacted.
    monkeypatch.setenv("QWEN_MODEL", "qwen-plus")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-flash")
    monkeypatch.delenv("QWEN_BASE_URL", raising=False)
    monkeypatch.delenv("DEEPSEEK_BASE_URL", raising=False)
    result = asyncio.run(generate_ai_review(
        REPORT, provider, transport=_mock_transport(
            provider + "_local_fake_secret", model_name, host
        )
    ))
    assert result["status"] == "ok"
    assert result["provider"] == provider and result["model"] == model_name
    assert len(result["checks"]) == 1
    assert result["omitted_unmatched"] == 1
    assert result["checks"][0]["rule"] == "DXF-001"
    assert result["usage"]["prompt_tokens"] == 100


def test_config_status_never_reveals_keys(monkeypatch):
    monkeypatch.setenv("QWEN_API_KEY", "private_key_for_test_one")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "private_key_for_test_two")
    info = TestClient(app).get("/api/model-providers")
    assert info.status_code == 200
    payload = info.json()
    assert payload["default"] == "off"
    assert {p["id"] for p in payload["providers"]} == {"qwen", "deepseek"}
    assert all(p["configured"] for p in payload["providers"])
    assert "private_key_for_test_" not in info.text


def test_missing_key_returns_sanitized_error(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(ModelConfigurationError, match="API Key"):
        asyncio.run(generate_ai_review(REPORT, "deepseek"))
    assert next(p for p in public_provider_status() if p["id"] == "deepseek")["configured"] is False


def test_rejects_untrusted_api_hosts(monkeypatch):
    monkeypatch.setenv("QWEN_API_KEY", "testing_only")
    monkeypatch.setenv("QWEN_BASE_URL", "https://some-random-host.example/compatible-mode/v1")
    with pytest.raises(ModelConfigurationError, match="官方接口"):
        asyncio.run(generate_ai_review(REPORT, "qwen"))


def test_model_does_not_call_cloud_without_rule_findings(monkeypatch):
    monkeypatch.setenv("QWEN_API_KEY", "testing_only")
    async def forbidden(request):
        raise AssertionError("No external call should occur")
    result = asyncio.run(generate_ai_review(
        {"findings": [], "results": {}}, "qwen",
        transport=httpx.MockTransport(forbidden),
    ))
    assert result["status"] == "skipped"


def test_bad_credentials_do_not_echo_key(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "confidential_for_test")
    with pytest.raises(ModelServiceError) as exc:
        asyncio.run(generate_ai_review(
            REPORT, "deepseek",
            transport=httpx.MockTransport(lambda req: httpx.Response(
                401, json={"error": "confidential_for_test"}
            )),
        ))
    assert "confidential_for_test" not in str(exc.value)


def _small_dxf():
    import io
    doc = ezdxf.new(setup=True)
    doc.modelspace().add_line((0, 0), (0, 0))
    stream = io.StringIO()
    doc.write(stream)
    return stream.getvalue().encode("utf-8")


def test_local_only_is_default_and_no_cloud_call(monkeypatch):
    calls = []
    async def fake_cloud(report, provider):
        calls.append(provider)
        raise AssertionError("cloud call not requested")
    monkeypatch.setattr("cad_audit.api.generate_ai_review", fake_cloud)
    client = TestClient(app)
    r = client.post("/api/review", files={"dxf": ("sample.dxf", _small_dxf())})
    assert r.status_code == 200, r.text
    assert r.json()["ai_review"]["status"] == "disabled"
    assert r.json()["summary"]["findings"] > 0
    assert calls == []


def test_selected_provider_failure_preserves_cad_results(monkeypatch):
    async def fail_cloud(report, provider):
        assert provider == "qwen"
        raise ModelServiceError("服务暂不可用")
    monkeypatch.setattr("cad_audit.api.generate_ai_review", fail_cloud)
    client = TestClient(app)
    r = client.post("/api/review", files={"dxf": ("part.dxf", _small_dxf())},
                    data={"provider": "qwen"})
    assert r.status_code == 200
    assert r.json()["ai_review"]["status"] == "error"
    assert r.json()["summary"]["findings"] > 0
    bad = client.post("/api/review", files={"dxf": ("part.dxf", _small_dxf())},
                      data={"provider": "unknown"})
    assert bad.status_code == 422


def test_qwen_coding_plan_official_endpoint(monkeypatch):
    monkeypatch.setenv("QWEN_API_KEY", "sk-synthetic-only-for-test")
    monkeypatch.setenv("QWEN_BASE_URL", "https://coding.dashscope.aliyuncs.com/v1")
    from cad_audit.models import _configuration
    conf = _configuration("qwen")
    assert conf["url"] == "https://coding.dashscope.aliyuncs.com/v1"


def test_rejects_mismatched_coding_plan_path(monkeypatch):
    monkeypatch.setenv("QWEN_API_KEY", "sk-synthetic-only-for-test")
    monkeypatch.setenv("QWEN_BASE_URL", "https://coding.dashscope.aliyuncs.com/compatible-mode/v1")
    from cad_audit.models import _configuration
    with pytest.raises(ModelConfigurationError):
        _configuration("qwen")
