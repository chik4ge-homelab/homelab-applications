#!/usr/bin/env python3
import json
import os
import shutil
import sys
from pathlib import Path

from huggingface_hub import hf_hub_download, try_to_load_from_cache


ROOT = Path(os.environ.get("MODEL_CACHE_ROOT", "/cache"))
HUB = ROOT / "hub"


def revision(asset: dict) -> str | None:
    configured = asset["revision"]
    if configured != "main":
        return configured
    repo = f"models--{asset['repo_id'].replace('/', '--')}"
    ref = HUB / repo / "refs" / "main"
    return ref.read_text(encoding="utf-8").strip() if ref.is_file() else None


def present(asset: dict) -> bool:
    if asset["kind"] == "hub":
        commit = revision(asset)
        if not commit:
            return False
        cached = try_to_load_from_cache(
            asset["repo_id"], asset["filename"], revision=commit, cache_dir=str(HUB)
        )
        return isinstance(cached, str) and Path(cached).is_file()

    target = ROOT / asset["filename"]
    metadata = ROOT / ".cache/huggingface/download" / f"{asset['filename']}.metadata"
    if not target.is_file() or target.stat().st_size == 0 or not metadata.is_file():
        return False
    lines = metadata.read_text(encoding="utf-8").splitlines()
    return bool(lines) and lines[0] == asset["revision"]


def clear_cache() -> None:
    if ROOT != Path("/cache"):
        raise ValueError(f"refusing to clear unexpected path: {ROOT}")
    ROOT.mkdir(parents=True, exist_ok=True)
    for entry in ROOT.iterdir():
        if entry.name == "lost+found":
            continue
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink()


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: cache.py /config/<model-set>.json")
    config_path = Path(sys.argv[1])
    assets = json.loads(config_path.read_text(encoding="utf-8"))["assets"]
    models = [asset for asset in assets if asset.get("role", "model") == "model"]
    if not models:
        raise ValueError("model set has no model assets")

    missing = [asset for asset in models if not present(asset)]
    if missing:
        names = ", ".join(asset["filename"] for asset in missing)
        print(f"model cache miss ({names}); clearing {ROOT}", flush=True)
        clear_cache()
    else:
        print("configured models are already cached", flush=True)

    for asset in assets:
        if asset["kind"] != "local" or not asset.get("download", True) or present(asset):
            continue
        path = hf_hub_download(
            repo_id=asset["repo_id"],
            filename=asset["filename"],
            revision=asset["revision"],
            local_dir=str(ROOT),
        )
        if Path(path) != ROOT / asset["filename"]:
            raise ValueError(f"unexpected download path: {path}")
        print(f"downloaded {asset['filename']}", flush=True)

    expected = {asset["filename"] for asset in assets if asset["kind"] == "local"}
    for path in ROOT.rglob("*"):
        relative = path.relative_to(ROOT)
        if path.is_file() and relative.parts[0] not in {"hub", ".cache", "lost+found"}:
            if relative.as_posix() not in expected:
                path.unlink()

    print(f"model cache ready: {config_path.name}", flush=True)


if __name__ == "__main__":
    main()
