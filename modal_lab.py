"""One-command, token-protected JupyterLab on a Modal GPU."""

import os
from pathlib import Path

import modal

from modal_runtime import PROJECT, image

from studio_project import package_list

OUTPUT_VOLUME = os.environ.get("CA2_STUDIO_VOLUME", "notebook-studio")
outputs = modal.Volume.from_name(OUTPUT_VOLUME, create_if_missing=True)

app = modal.App("notebook-studio")
lab_image = (
    image.pip_install("jupyterlab==4.4.10")
    .add_local_dir(PROJECT / "ui/jupyter", "/opt/ca2/ui", copy=True)
    .run_commands(
        "mkdir -p /usr/local/share/jupyter/labextensions/@ca2/studio && "
        "cp -r /opt/ca2/ui/labextension/. /usr/local/share/jupyter/labextensions/@ca2/studio/"
    )
)
if modal.is_local() and os.environ.get("CA2_STAGED_PROJECT"):
    lab_image = lab_image.add_local_dir(os.environ["CA2_STAGED_PROJECT"], "/opt/studio/project", copy=True)
    extra = package_list(os.environ.get("CA2_EXTRA_PACKAGES", ""))
    if extra:
        lab_image = lab_image.pip_install(*extra)
lab_image = lab_image.env({key: os.environ[key] for key in (
    "CA2_PROJECT_KEY", "CA2_NOTEBOOK_RELATIVE", "CA2_STUDIO_VOLUME", "CA2_REFRESH_LOCAL",
    "CA2_PROJECT_NAME", "CA2_PREDICT_ONLY", "CA2_RESUME",
    "CA2_DATA_VOLUME",
    "CA2_CPU", "CA2_RAM_GIB",
    "CA2_ACCOUNT_LABEL", "CA2_ACCOUNT_WORKSPACE",
) if key in os.environ})
lab_image = lab_image.add_local_python_source("modal_runtime", "studio_project")
lab_volumes = {"/mnt/ca2-outputs": outputs}
if os.environ.get("CA2_DATA_VOLUME"):
    lab_volumes["/mnt/data"] = modal.Volume.from_name(os.environ["CA2_DATA_VOLUME"])


def prepare_workspace(task="CUSTOM", *, workspace_root=Path("/mnt/ca2-outputs/interactive"),
                      source_root=Path("/opt/studio/project"), refresh_local=False):
    """Seed once; preserve browser edits and back up any explicit replacement."""
    import re
    import shutil
    from datetime import datetime, timezone

    key = os.environ.get("CA2_PROJECT_KEY", "")
    if task != "CUSTOM" or not re.fullmatch(r"[0-9a-f]{16}", key):
        raise ValueError("Launch a notebook from Notebook Studio with a valid project key")
    workspace = workspace_root / "projects" / key
    workspace.mkdir(parents=True, exist_ok=True)
    backup = workspace / "backups" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    for original in source_root.rglob("*"):
        if not original.is_file():
            continue
        relative = original.relative_to(source_root)
        target = workspace / relative
        if target.exists():
            if not refresh_local:
                continue
            previous = backup / relative
            previous.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(target, previous)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, target)
    (workspace / "results").mkdir(exist_ok=True)
    return workspace


def configure_appearance(config_dir, *, assets=Path("/opt/ca2/ui")):
    """Use supported Jupyter settings and custom CSS, without browser extensions."""
    import json
    import shutil

    (config_dir / "custom").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(assets / "custom.css", config_dir / "custom/custom.css")
    (config_dir / "jupyter_server_config.json").write_text(json.dumps({
        "ServerApp": {"jpserver_extensions": {"ca2_session": True}},
    }), encoding="utf-8")
    settings = json.loads((assets / "settings.json").read_text(encoding="utf-8"))
    for plugin, values in settings.items():
        package, name = plugin.split(":", 1)
        folder = config_dir / "lab/user-settings" / package
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{name}.jupyterlab-settings").write_text(json.dumps(values), encoding="utf-8")


@app.function(
    image=lab_image, gpu=os.environ.get("CA2_GPU", "T4"),
    cpu=4, memory=16384, timeout=4 * 60 * 60 + 120, max_containers=1,
    volumes=lab_volumes,
)
def serve_lab(task: str, split: str, epochs: int, minutes: int, token: str, gpu: str = "",
              refresh_local: bool = False):
    import json
    import subprocess
    import sys
    import time
    import urllib.error
    import urllib.request
    import torch

    workspace = prepare_workspace(task, refresh_local=refresh_local)
    # Modal mounts are symlinks. Jupyter's static file handler compares resolved
    # file paths against root_dir, so the server root must be canonical too.
    server_root = workspace.resolve()
    jupyter_config = Path("/tmp/ca2-jupyter-config")
    configure_appearance(jupyter_config)
    gpu = gpu or os.environ.get("CA2_GPU", "T4")
    sys.path.insert(0, "/opt/ca2/ui")
    from ca2_session import validate_gpu
    validate_gpu(gpu)
    control = Path("/tmp/ca2-gpu-switch.json")
    control.unlink(missing_ok=True)
    session = {
        "gpu": gpu, "device": torch.cuda.get_device_name(0),
        "project": os.environ.get("CA2_PROJECT_NAME", task),
        "account_label": os.environ.get("CA2_ACCOUNT_LABEL", ""),
        "account_workspace": os.environ.get("CA2_ACCOUNT_WORKSPACE", ""),
        "workspace": str(workspace),
        "cpu_cores": int(os.environ.get("CA2_CPU", "4")),
        "memory_gib": int(os.environ.get("CA2_RAM_GIB", "16")),
        "started_at": time.time(), "monotonic_start": time.monotonic(),
        "duration_seconds": minutes * 60,
    }
    config = {
        **os.environ, "CA2_SPLIT": split,
        "CA2_OUTPUT_ROOT": str(workspace / "results"), "JUPYTER_TOKEN": token,
        "JUPYTER_RUNTIME_DIR": "/tmp/ca2-jupyter-runtime",
        "JUPYTER_CONFIG_DIR": str(jupyter_config),
        "JUPYTERLAB_SETTINGS_DIR": str(jupyter_config / "lab/user-settings"),
        "CA2_SESSION_INFO": json.dumps(session),
        "CA2_SESSION_CONTROL": str(control),
        "PYTHONPATH": "/opt/ca2/ui" + (":" + os.environ["PYTHONPATH"] if os.environ.get("PYTHONPATH") else ""),
    }
    # Each kernel inherits these settings. Do not inherit a previous epochs override.
    config.pop("CA2_EPOCHS", None)
    if epochs > 0:
        config["CA2_EPOCHS"] = str(epochs)
    outputs.commit()
    next_gpu = None
    with modal.forward(8888) as tunnel:
        process = subprocess.Popen([
            sys.executable, "-m", "jupyterlab", "--no-browser", "--allow-root",
            "--ip=0.0.0.0", "--port=8888", "--ServerApp.port_retries=0",
            "--ServerApp.allow_remote_access=True", "--ServerApp.log_level=WARN",
            "--LabApp.custom_css=True",
            f"--ServerApp.root_dir={server_root}",
        ], cwd=workspace, env=config)
        try:
            # Only hand the browser a URL after Jupyter is accepting requests.
            ready_by = time.monotonic() + 90
            while True:
                if process.poll() is not None:
                    raise RuntimeError(f"Jupyter exited with status {process.returncode}")
                try:
                    request = urllib.request.Request(
                        "http://127.0.0.1:8888/api/status",
                        headers={"Authorization": f"token {token}"},
                    )
                    with urllib.request.urlopen(request, timeout=2) as response:
                        if response.status == 200:
                            break
                except (urllib.error.URLError, TimeoutError):
                    pass
                if time.monotonic() >= ready_by:
                    raise TimeoutError("Jupyter did not become ready within 90 seconds")
                time.sleep(1)
            from urllib.parse import quote
            notebook_name = os.environ["CA2_NOTEBOOK_RELATIVE"]
            yield f"{tunnel.url}/lab/tree/{quote(notebook_name)}?token={token}"
            end = session["monotonic_start"] + minutes * 60
            next_commit = time.monotonic() + 15
            while process.poll() is None and time.monotonic() < end:
                time.sleep(min(1, max(0, end - time.monotonic())))
                if control.exists():
                    next_gpu = validate_gpu(json.loads(control.read_text(encoding="utf-8"))["gpu"])
                    break
                if time.monotonic() >= next_commit:
                    outputs.commit()
                    next_commit = time.monotonic() + 15
            if process.poll() not in (None, 0):
                raise RuntimeError(f"Jupyter exited with status {process.returncode}")
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            outputs.commit()
    # The previous GPU server is fully stopped and its files committed before switching.
    if next_gpu:
        yield {"switch_gpu": next_gpu}


@app.local_entrypoint()
def main(task: str = "CUSTOM", split: str = "public_test", epochs: int = 0,
         minutes: int = 60, browser: bool = True):
    import secrets
    import webbrowser

    task = "CUSTOM"
    if not os.environ.get("CA2_STAGED_PROJECT"):
        raise ValueError("Open python studio.py and choose a notebook to start a GPU session")
    if split not in ("public_test", "private_test"):
        raise ValueError("Invalid task or split")
    if not 1 <= minutes <= 240 or epochs < 0:
        raise ValueError("Minutes must be 1..240; epochs must be non-negative")
    gpu = os.environ.get("CA2_GPU", "T4")
    if gpu not in ("T4", "L4", "A10G", "A100", "H100"):
        raise ValueError("GPU must be T4, L4, A10G, A100 or H100")
    print("Starting JupyterLab on Modal. No training starts automatically.", flush=True)
    refresh_local = os.environ.get("CA2_REFRESH_LOCAL") == "1"
    while True:
        next_gpu = None
        for event in serve_lab.with_options(
            gpu=gpu, cpu=int(os.environ.get("CA2_CPU", "4")),
            memory=int(os.environ.get("CA2_RAM_GIB", "16")) * 1024,
            scaledown_window=2,
        ).remote_gen(
            task, split, epochs, minutes, secrets.token_urlsafe(32), gpu, refresh_local,
        ):
            if isinstance(event, dict):
                next_gpu = event["switch_gpu"]
                continue
            print(f"\nOPEN JUPYTERLAB: {event}", flush=True)
            print(f"GPU: {gpu} | Session: {minutes} minutes. Keep terminal open; Ctrl+C stops it.", flush=True)
            print("Save with Ctrl+S. Saved files sync to the project Volume every 15 seconds.", flush=True)
            print("Model/variables stay in RAM until the kernel or session stops.", flush=True)
            if browser:
                webbrowser.open(event)
        if next_gpu is None:
            break
        gpu = next_gpu
        refresh_local = False
        print(f"Saved session stopped. Starting new session on {gpu}...", flush=True)
    print("JupyterLab session ended.", flush=True)
