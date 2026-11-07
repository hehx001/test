"""Single-user prototype. Start on localhost; not an internet-facing production service."""
import os
import hashlib
import tempfile
from pathlib import Path
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from .engine import inspect_dxf, inspect_bom, inspect_step, issue
from .geometry_worker import run_geometry_worker, GeometryWorkerError
from .models import (ModelConfigurationError, ModelServiceError,
                     generate_ai_review, public_provider_status)

app = FastAPI(title="机械CAD智能审核原型", version="0.1.0", docs_url="/api/docs")
ROOT = Path(__file__).parent
MAX_BYTES = 20 * 1024 * 1024


@app.get("/")
def home():
    return FileResponse(ROOT / "static" / "index.html")


async def bounded(file, extensions):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in extensions:
        raise HTTPException(400, f"只支持 {', '.join(sorted(extensions))} 文件")
    data = await file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "单个文件最大20MB")
    return data, suffix


@app.get("/api/model-providers")
def model_providers():
    return {"default": "off", "providers": public_provider_status()}


@app.post("/api/review")
async def review(dxf: UploadFile | None = File(None),
                 step: UploadFile | None = File(None),
                 bom: UploadFile | None = File(None),
                 provider: str = Form("off")):
    if provider not in ("off", "qwen", "deepseek"):
        raise HTTPException(422, "请选择有效的模型服务商")
    if not dxf and not step and not bom:
        raise HTTPException(400, "至少上传一个文件")
    report = {"title": "机械CAD智能审核报告（原型）", "results": {}, "findings": []}
    paths = []
    try:
        if dxf:
            raw, suffix = await bounded(dxf, {".dxf"})
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as f:
                f.write(raw); paths.append(f.name)
            try:
                result = run_geometry_worker("dxf", f.name, timeout_seconds=45)
            except GeometryWorkerError as exc:
                result = {"findings": [issue(
                    "SYS-001", "二维图纸解析未完成", "warning", "failed",
                    "DXF", "PARSER", {"reason": str(exc)},
                    "核实文件完整性，并使用原生CAD软件重新检查。")],
                    "inspection_scope": {"file_parsing": "failed"}}
            result["filename"] = dxf.filename
            result["sha256"] = hashlib.sha256(raw).hexdigest()
            report["results"]["dxf"] = result
            report["findings"].extend(result["findings"])
        if bom:
            raw, _ = await bounded(bom, {".csv"})
            attrs = report["results"].get("dxf", {}).get("attributes", {})
            result = inspect_bom(raw, attrs)
            result["sha256"] = hashlib.sha256(raw).hexdigest()
            report["results"]["bom"] = result
            report["findings"].extend(result["findings"])
        if step:
            raw, suffix = await bounded(step, {".step", ".stp"})
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as f:
                f.write(raw); paths.append(f.name)
            try:
                result = run_geometry_worker("step", f.name, timeout_seconds=60)
            except GeometryWorkerError as exc:
                result = {"findings": [issue(
                    "SYS-002", "三维模型解析或计算未完成", "warning", "failed",
                    "STEP", "PARSER", {"reason": str(exc)},
                    "复核原始模型、拓扑状态和计算限制后重新执行检查。")],
                    "pair_counts": {"calculated": 0, "failed": 1,
                                    "not_supported": 0, "bbox_excluded": 0},
                    "part_count": 0, "candidate_pairs": 0}
            result["sha256"] = hashlib.sha256(raw).hexdigest()
            report["results"]["step"] = result
            report["findings"].extend(result["findings"])
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    finally:
        for path in paths:
            try:
                os.unlink(path)
            except OSError:
                pass
    counts = {}
    for item in report["findings"]:
        status = item["status"]
        counts[status] = counts.get(status, 0) + 1
    report["summary"] = {
        "findings": len(report["findings"]),
        "confirmed": counts.get("confirmed", 0),
        "needs_review": counts.get("needs_review", 0),
        "not_supported": counts.get("not_supported", 0),
        "failed": counts.get("failed", 0),
        "by_status": counts,
        "formats": list(report["results"]),
    }
    report["coverage"] = {
        "dxf": report["results"].get("dxf", {}).get("inspection_scope"),
        "step_pairs": report["results"].get("step", {}).get("pair_counts"),
        "bom_crossfile_complete": report["results"].get("bom", {}).get("crossfile_check_complete"),
        "note": "未支持或失败的部分不能视为审核通过。",
    }
    # Cloud calls are explicit. Original CAD files remain local: only a
    # bounded summary of the engineering checks is sent to the chosen API.
    report["ai_review"] = {"status": "disabled", "message": "仅执行本地工程规则检查"}
    if provider != "off":
        try:
            report["ai_review"] = await generate_ai_review(report, provider)
        except (ModelConfigurationError, ModelServiceError) as exc:
            report["ai_review"] = {
                "status": "error", "provider": provider,
                "message": str(exc),
            }
    return report
