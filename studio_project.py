"""Project staging shared by the local launcher and Modal notebook server."""

import hashlib
import json
import os
from pathlib import Path
import re
import shutil

SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "data", "outputs", ".ipynb_checkpoints"}


def project_key(folder):
    return hashlib.sha256(str(Path(folder).resolve()).casefold().encode()).hexdigest()[:16]


def validate_notebook(folder, notebook):
    folder = Path(folder).resolve(strict=True)
    notebook = Path(notebook).resolve(strict=True)
    notebook.relative_to(folder)
    if notebook.suffix.lower() != ".ipynb" or notebook.stat().st_size > 100 * 1024 ** 2:
        raise ValueError("Chọn file .ipynb dưới 100 MiB trong thư mục dự án")
    payload = json.loads(notebook.read_text(encoding="utf-8"))
    if not isinstance(payload.get("cells"), list):
        raise ValueError("Notebook không hợp lệ")
    return folder, notebook


def stage_project(folder, notebook, destination, include_code=True):
    folder, notebook = validate_notebook(folder, notebook)
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    selected = {notebook}
    if include_code:
        for current, dirs, files in os.walk(folder):
            dirs[:] = [name for name in dirs if name not in SKIP_DIRS and not name.startswith(".")
                       and not (Path(current) / name).is_symlink()]
            for name in files:
                source = Path(current) / name
                if source.suffix == ".py" and not name.startswith(".") and not source.is_symlink() and source.stat().st_size <= 10 * 1024 ** 2:
                    selected.add(source)
                    if len(selected) > 1000:
                        raise ValueError("Dự án vượt 1.000 file mã; hãy chọn thư mục nhỏ hơn")
    for source in selected:
        target = destination / source.relative_to(folder)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    return {"key": project_key(folder), "notebook": notebook.relative_to(folder).as_posix(),
            "files": len(selected)}


def package_list(text):
    values = text.split()
    for value in values:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.\[\],<>=!~+\-]*", value):
            raise ValueError("Thư viện phải có dạng pandas==2.2.3, không dùng URL hay cờ pip")
    return values
