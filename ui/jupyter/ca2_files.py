"""Create downloads from paths contained in one notebook workspace."""
import os
from pathlib import Path, PurePosixPath
import tempfile
import zipfile


def workspace_path(root, name):
    root = Path(root).resolve(strict=True)
    relative = PurePosixPath(name)
    if relative.is_absolute() or ".." in relative.parts or "\\" in name:
        raise ValueError("Đường dẫn phải nằm trong workspace")
    target = (root / str(relative)).resolve(strict=True)
    target.relative_to(root)
    return target


def build_archive(root, names):
    if not isinstance(names, list) or not names or any(not isinstance(name, str) for name in names):
        raise ValueError("Chọn ít nhất một file hoặc thư mục")
    root = Path(root).resolve(strict=True)
    targets = [workspace_path(root, name) for name in names]
    stream = tempfile.TemporaryFile()
    seen = set()
    try:
        with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
            for target in targets:
                if target.is_file():
                    entries = [target]
                else:
                    entries = []
                    for parent, directories, files in os.walk(target, followlinks=False):
                        directories[:] = [name for name in directories if not (Path(parent) / name).is_symlink()]
                        for name in files:
                            entry = Path(parent) / name
                            if not entry.is_symlink():
                                entries.append(entry)
                    # Include an empty directory, too.
                    if not entries and target != root:
                        archive.writestr(target.relative_to(root).as_posix() + "/", "")
                for entry in entries:
                    # Check again immediately before opening a file.
                    checked = workspace_path(root, entry.relative_to(root).as_posix())
                    name = entry.relative_to(root).as_posix()
                    if name not in seen:
                        archive.write(checked, name)
                        seen.add(name)
        stream.seek(0)
        return stream
    except BaseException:
        stream.close()
        raise
