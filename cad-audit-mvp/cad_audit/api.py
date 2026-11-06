"""Single-user prototype. Start on localhost; not an internet-facing production service."""
import os
import tempfile
from pathlib import Path
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from .engine import inspect_dxf, inspect_bom, inspect_step

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


@app.post("/api/review")
async def review(dxf: UploadFile | None = File(None),
                 step: UploadFile | None = File(None),
                 bom: UploadFile | None = File(None)):
    if not dxf and not step and not bom:
        raise HTTPException(400, "至少上传一个文件")
    report = {"title": "机械CAD智能审核报告（原型）", "results": {}, "findings": []}
    paths = []
    try:
        if dxf:
            raw, suffix = await bounded(dxf, {".dxf"})
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as f:
                f.write(raw); paths.append(f.name)
            result = inspect_dxf(f.name)
            result["filename"] = dxf.filename
            report["results"]["dxf"] = result
            report["findings"].extend(result["findings"])
        if bom:
            raw, _ = await bounded(bom, {".csv"})
            attrs = report["results"].get("dxf", {}).get("attributes", {})
            result = inspect_bom(raw, attrs)
            report["results"]["bom"] = result
            report["findings"].extend(result["findings"])
        if step:
            raw, suffix = await bounded(step, {".step", ".stp"})
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as f:
                f.write(raw); paths.append(f.name)
            result = inspect_step(f.name)
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
    report["summary"] = {"findings": len(report["findings"]),
                         "confirmed": sum(p["status"] == "confirmed" for p in report["findings"]),
                         "needs_review": sum(p["status"] == "needs_review" for p in report["findings"]),
                         "formats": list(report["results"])}
    return report
