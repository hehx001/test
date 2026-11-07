"""Bounded subprocess worker for untrusted DXF and STEP parsing.

This is a defense-in-depth measure, not a hardened OS sandbox. Keep the HTTP
service on localhost; production use still requires OS permissions/isolation.
"""
from __future__ import annotations

import multiprocessing as mp
import os
import sys


class GeometryWorkerError(Exception):
    pass


class GeometryWorkerTimeout(GeometryWorkerError):
    pass


def _child_task(connection, kind: str, path: str, max_cpu_seconds: int):
    try:
        # CPU-time limit limits runaway calculations. Memory limits can
        # reject legitimate OCCT process mappings on some OSes, so use
        # an external container/cgroup for robust production memory limits.
        if sys.platform.startswith("linux"):
            import resource
            hard = max_cpu_seconds + 5
            resource.setrlimit(resource.RLIMIT_CPU, (max_cpu_seconds, hard))
        if kind == "dxf":
            from .engine import inspect_dxf
            result = inspect_dxf(path)
        elif kind == "step":
            from .engine import inspect_step
            result = inspect_step(path)
        else:
            raise ValueError("不支持的CAD文件类型")
        connection.send(("ok", result))
    except (ValueError, OSError) as exc:
        connection.send(("invalid", str(exc)[:300]))
    except BaseException as exc:
        # Do not expose arbitrary source content or file paths.
        try:
            connection.send(("failed", type(exc).__name__))
        except OSError:
            pass
    finally:
        connection.close()


def run_geometry_worker(kind: str, path: str, timeout_seconds=45):
    if kind not in ("dxf", "step"):
        raise ValueError("不支持的CAD文件类型")
    if not 1 <= timeout_seconds <= 120:
        raise ValueError("几何计算时间限制不合理")
    ctx = mp.get_context("spawn")
    receiver, sender = ctx.Pipe(duplex=False)
    worker = ctx.Process(
        target=_child_task, args=(sender, kind, path, int(timeout_seconds)), daemon=True
    )
    try:
        worker.start()
        sender.close()
        if not receiver.poll(timeout_seconds):
            raise GeometryWorkerTimeout("CAD文件解析超过单次时间限制")
        try:
            status, data = receiver.recv()
        except EOFError as exc:
            raise GeometryWorkerError("CAD解析子进程异常退出") from exc
        if status == "ok":
            return data
        if status == "invalid":
            raise ValueError(data)
        raise GeometryWorkerError("CAD解析子进程执行失败（" + str(data) + "）")
    finally:
        receiver.close()
        if worker.pid is not None:
            if worker.is_alive():
                worker.terminate()
            worker.join(timeout=3)
            if worker.is_alive():
                worker.kill()
                worker.join(timeout=2)
