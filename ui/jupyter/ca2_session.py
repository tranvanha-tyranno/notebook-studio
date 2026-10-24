"""Authenticated session metadata and GPU switch requests for JupyterLab."""

import json
import os
from pathlib import Path
import time
import asyncio
from urllib.parse import quote
from ca2_metrics import ResourceSampler
from ca2_files import build_archive

from jupyter_server.base.handlers import APIHandler
from jupyter_server.utils import url_path_join
from tornado import web


GPU_CHOICES = ("T4", "L4", "A10G", "A100", "H100")


def validate_gpu(value):
    if value not in GPU_CHOICES:
        raise ValueError(f"GPU must be one of {', '.join(GPU_CHOICES)}")
    return value


class SessionHandler(APIHandler):
    @web.authenticated
    def get(self):
        state = self.settings["ca2_session"]
        self.set_header("Cache-Control", "no-store")
        self.finish({
            **state,
            "elapsed_seconds": max(0, time.monotonic() - state["monotonic_start"]),
            "gpu_choices": list(GPU_CHOICES),
            "resources": self.settings["ca2_resources"].read(),
        })

    @web.authenticated
    def post(self):
        payload = self.get_json_body() or {}
        try:
            gpu = validate_gpu(payload.get("gpu"))
        except ValueError as error:
            raise web.HTTPError(400, str(error)) from error
        if gpu == self.settings["ca2_session"]["gpu"]:
            self.finish({"status": "unchanged"})
            return
        control = Path(os.environ["CA2_SESSION_CONTROL"])
        if control.exists():
            raise web.HTTPError(409, "A GPU switch is already pending")
        temporary = control.with_suffix(".tmp")
        temporary.write_text(json.dumps({"gpu": gpu}), encoding="utf-8")
        temporary.replace(control)
        self.finish({"status": "switching", "gpu": gpu})


class DownloadHandler(APIHandler):
    @web.authenticated
    async def get(self):
        try:
            names = json.loads(self.get_query_argument("paths"))
            archive = await asyncio.to_thread(build_archive, self.settings["ca2_session"]["workspace"], names)
        except (ValueError, OSError, KeyError) as error:
            raise web.HTTPError(400, str(error)) from error
        filename = Path(names[0]).name + ".zip" if len(names) == 1 else "selected-files.zip"
        self.set_header("Content-Type", "application/zip")
        self.set_header("Content-Disposition", f"attachment; filename=download.zip; filename*=UTF-8''{quote(filename)}")
        self.set_header("Cache-Control", "no-store")
        self.set_header("X-Content-Type-Options", "nosniff")
        try:
            while chunk := await asyncio.to_thread(archive.read, 1024 * 1024):
                self.write(chunk)
                await self.flush()
            self.finish()
        finally:
            archive.close()


def _jupyter_server_extension_points():
    return [{"module": "ca2_session"}]


def _load_jupyter_server_extension(serverapp):
    state = json.loads(os.environ["CA2_SESSION_INFO"])
    serverapp.web_app.settings["ca2_session"] = state
    serverapp.web_app.settings["ca2_resources"] = ResourceSampler(
        state.get("cpu_cores", 4), state.get("memory_gib", 16))
    serverapp.web_app.add_handlers(".*$", [
        (url_path_join(serverapp.web_app.settings["base_url"], "ca2/session"), SessionHandler),
        (url_path_join(serverapp.web_app.settings["base_url"], "ca2/download"), DownloadHandler),
    ])
