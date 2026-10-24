"""Read committed Modal Volume files using the local SDK, without a GPU/browser download."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import sys


def relative_path(value):
    if not isinstance(value, str) or "\\" in value or ":" in value:
        raise ValueError("Đường dẫn file không hợp lệ")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or any(part.endswith((".", " ")) for part in path.parts):
        raise ValueError("Đường dẫn phải nằm trong dự án")
    return path


def list_files(volume, root, relative=""):
    from modal.volume import FileEntryType
    relative = relative_path(relative)
    parent = PurePosixPath(root) / relative
    entries = volume.listdir(parent.as_posix())
    result = []
    for entry in entries:
        path = PurePosixPath(entry.path.lstrip("/"))
        path.relative_to(PurePosixPath(root.lstrip("/")))
        if entry.type not in (FileEntryType.FILE, FileEntryType.DIRECTORY):
            continue
        rel = path.relative_to(PurePosixPath(root.lstrip("/"))).as_posix()
        relative_path(rel)
        result.append({"name": path.name, "path": rel, "folder": entry.type == FileEntryType.DIRECTORY,
                       "size": entry.size, "modified": entry.mtime})
    return sorted(result, key=lambda item: (not item["folder"], item["name"].casefold()))


def save_files(volume, root, names, destination):
    from modal.volume import FileEntryType
    if not isinstance(names, list) or not names:
        raise ValueError("Chọn file hoặc thư mục để lưu")
    root = PurePosixPath(root.lstrip("/"))
    files = {}
    directories = set()
    # Resolve every selection and validate returned paths before writing anything.
    for name in names:
        rel = relative_path(name)
        target = root / rel
        entries = volume.listdir(target.as_posix(), recursive=True)
        # listdir(file) returns that file; listdir(dir) returns descendants.
        for entry in entries:
            path = PurePosixPath(entry.path.lstrip("/"))
            path.relative_to(target) if path != target else None
            key = path.relative_to(root).as_posix()
            relative_path(key)
            if entry.type == FileEntryType.FILE:
                files[key] = entry.size
            elif entry.type == FileEntryType.DIRECTORY:
                directories.add(key)
            else:
                raise ValueError(f"Không tải symlink hoặc file đặc biệt: {key}")
        if not entries:
            directories.add(rel.as_posix())
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    for name in directories:
        (destination / name).mkdir(parents=True, exist_ok=True)
    manifest = []
    for name, expected_size in files.items():
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".partial")
        digest = hashlib.sha256()
        count = 0
        try:
            with temporary.open("xb") as output:
                for chunk in volume.read_file((root / name).as_posix()):
                    output.write(chunk)
                    digest.update(chunk)
                    count += len(chunk)
            if count != expected_size:
                raise ValueError(f"File đã thay đổi lúc tải: {name}; hãy lưu notebook rồi tải lại")
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        manifest.append({"path": name, "bytes": count, "sha256": digest.hexdigest()})
        print(json.dumps({"progress": len(manifest), "file": name, "bytes": count}), flush=True)
    (destination / "_download_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"destination": str(destination.resolve()), "files": len(manifest),
            "bytes": sum(item["bytes"] for item in manifest)}


def main():
    options = json.load(sys.stdin)
    if options.get("profile"):
        os.environ["MODAL_PROFILE"] = options["profile"]
    import modal
    volume = modal.Volume.from_name("notebook-studio")
    root = "interactive/projects/" + options["project_key"]
    if options["action"] == "list":
        result = {"items": list_files(volume, root, options.get("path", ""))}
    else:
        result = save_files(volume, root, options["paths"], options["destination"])
    print(json.dumps({"result": result}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        main()
    except Exception as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), flush=True)
        sys.exit(1)
