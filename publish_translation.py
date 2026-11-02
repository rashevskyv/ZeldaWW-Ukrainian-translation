"""Prepare and publish translation-only patches to the dedicated Wind Waker repository."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import workflow


REPO = Path(__file__).resolve().parent
PATCHES = REPO / "patches"
SHARED_ENV = workflow.SHARED / "zelda_env.ini"
EXPECTED_REMOTE = "rashevskyv/ZeldaWW-Ukrainian-translation"


class PublishError(RuntimeError):
    """A safe precondition for patch generation or publication was not met."""


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _paths_from_ini(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        raise PublishError(f"Missing shared path configuration: {path}")
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line and not line.startswith((";", "#", "[")) and "=" in line:
            key, _, value = line.partition("=")
            values[key.strip().casefold()] = value.strip().strip('"')
    return values


def _configured_file(key: str) -> Path:
    value = os.path.expandvars(_paths_from_ini(SHARED_ENV).get(key, ""))
    candidate = Path(value) if value else Path()
    if not value or not candidate.is_file():
        raise PublishError(f"Configure an existing {key} executable in {SHARED_ENV}.")
    return candidate.resolve()


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=False)
    if check and result.returncode:
        raise PublishError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result


def _reject_unrelated_changes(repo: Path, allowed_roots: tuple[str, ...] = ()) -> None:
    status = _git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all").stdout
    paths = [record[3:] for record in status.split("\0") if record]
    unrelated = [path for path in paths if not any(
        path == root or path.startswith(root + "/") for root in allowed_roots)]
    if unrelated:
        raise PublishError("Commit or move unrelated changes before publishing: " + ", ".join(unrelated))


def _matches_expected_remote(remote: str) -> bool:
    repo = EXPECTED_REMOTE.casefold()
    allowed = {
        f"https://github.com/{repo}", f"https://github.com/{repo}.git",
        f"git@github.com:{repo}", f"git@github.com:{repo}.git",
        f"ssh://git@github.com/{repo}", f"ssh://git@github.com/{repo}.git",
    }
    return remote.strip().rstrip("/").casefold() in allowed


def _verify_origin(repo: Path) -> None:
    remote = _git(repo, "remote", "get-url", "origin").stdout.strip()
    if not _matches_expected_remote(remote):
        raise PublishError(f"origin must point to {EXPECTED_REMOTE}; found {remote!r}.")
    push_urls = _git(repo, "remote", "get-url", "--push", "--all", "origin").stdout.splitlines()
    if not push_urls or any(not _matches_expected_remote(url) for url in push_urls):
        raise PublishError(f"Every origin push URL must point to {EXPECTED_REMOTE}.")


def _verify_remote_and_clean(repo: Path) -> None:
    _verify_origin(repo)
    _reject_unrelated_changes(repo)
    for key in ("user.name", "user.email"):
        if not _git(repo, "config", key, check=False).stdout.strip():
            raise PublishError(f"Set git config {key} before publishing.")


def _commit_and_push(repo: Path, has_patch_changes: bool) -> None:
    if has_patch_changes:
        _git(repo, "commit", "-m", "Update Wind Waker translation patches")
    _verify_origin(repo)
    _git(repo, "push", "origin", "HEAD")


def _xdelta(xdelta: Path, source: Path, target: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([str(xdelta), "-e", "-9", "-S", "djw", "-s", str(source), str(target), str(output)],
                   check=True)


def _add_delta(xdelta: Path, stage: Path, *, source: Path, target: Path,
               source_name: str, target_name: str, patch_name: str) -> dict:
    patch = stage / patch_name
    _xdelta(xdelta, source, target, patch)
    return {
        "operation": "xdelta3",
        "source_path": source_name,
        "source_sha256": _hash_file(source),
        "target_path": target_name,
        "target_sha256": _hash_file(target),
        "patch_path": patch_name,
        "patch_sha256": _hash_file(patch),
    }


def _make_package(stage: Path, xdelta: Path) -> dict:
    gc_source = workflow.GC / "image" / "original (USA).iso"
    gc_target = workflow.GC / "build" / "switchwaker" / "switch" / "switchwaker" / "GZLE01.iso"
    if not gc_source.is_file() or not gc_target.is_file():
        raise PublishError("The original GC USA ISO and the built SwitchWaker disc are required.")
    workflow._validate_bluewake_disc(gc_target)
    files = [_add_delta(xdelta, stage, source=gc_source, target=gc_target,
                        source_name="GC/image/original (USA).iso", target_name="GZLE01.iso",
                        patch_name="gc/SwitchWaker/GZLE01.iso.xdelta")]

    paths = _paths_from_ini(SHARED_ENV)
    game_value = os.path.expandvars(paths.get("wwhd_game", "").strip())
    if not game_value:
        raise PublishError(f"Configure wwhd_game in {SHARED_ENV}.")
    game_content = Path(game_value) / "content"
    graphic_pack = workflow.HD / "build" / "graphicPacks" / "Picoripi_UA_WWHD"
    content = graphic_pack / "content"
    if not game_content.is_dir() or not content.is_dir():
        raise PublishError("Wind Waker HD source content and the built Cemu graphic pack are required.")
    for target in sorted(path for path in content.rglob("*") if path.is_file()):
        relative = target.relative_to(content)
        source = game_content / relative
        if not source.is_file():
            raise PublishError(f"No source game file for built HD content: {relative}")
        relative_text = relative.as_posix()
        files.append(_add_delta(
            xdelta, stage, source=source, target=target,
            source_name=f"HD/content/{relative_text}", target_name=f"content/{relative_text}",
            patch_name=f"hd/content/{relative_text}.xdelta"))

    rules = graphic_pack / "rules.txt"
    if not rules.is_file():
        raise PublishError(f"Missing Cemu graphic-pack rules: {rules}")
    rules_name = "hd/graphicPacks/Picoripi_UA_WWHD/rules.txt"
    rules_copy = stage / rules_name
    rules_copy.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(rules, rules_copy)
    files.append({"operation": "copy", "source_path": None, "source_sha256": None,
                  "target_path": "graphicPacks/Picoripi_UA_WWHD/rules.txt",
                  "target_sha256": _hash_file(rules), "patch_path": rules_name,
                  "patch_sha256": _hash_file(rules_copy)})

    manifest = {"format": 1, "game": "The Legend of Zelda: The Wind Waker",
                "generated_by": "publish_translation.py", "files": files}
    (stage / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                          encoding="utf-8")
    (stage / "README.txt").write_text(
        "Translation-only patch package; use the exact supported game versions listed in manifest.json.\n"
        "For each xdelta3 entry, run: xdelta3 -d -s <source_path> <patch_path> <output>.\n"
        "GC: place the decoded GZLE01.iso in your BlueWake disc location or Switch SD switch/switchwaker/.\n"
        "HD: decode the content/ paths into a separate overlay folder, never over the original game.\n"
        "Cemu: place that content/ folder inside graphicPacks/Picoripi_UA_WWHD/, beside rules.txt copied\n"
        "from hd/graphicPacks/Picoripi_UA_WWHD/rules.txt. Enable the Picoripi UA graphic pack in Cemu.\n"
        "Wii U: place content/ under SD wiiu/sdcafiine/0005000010143500/Picoripi_UA/.\n"
        "SwitchWakerHD: copy content/ over SD switch/wwhd/game/content/ (keep an original backup).\n"
        "See manifest.json for SHA256 values and relative paths.\n"
        "This package does not include game images, source files, executables, keys, or saves.\n",
        encoding="utf-8")
    return manifest


def _replace_package(stage: Path, patches: Path) -> None:
    if patches.exists():
        manifest_path = patches / "manifest.json"
        if not manifest_path.is_file():
            raise PublishError(f"Refusing to replace a patches folder without our manifest: {patches}")
        try:
            old_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise PublishError(f"Cannot verify existing generated patch package: {error}") from error
        if old_manifest.get("generated_by") != "publish_translation.py":
            raise PublishError(f"Refusing to replace an unowned patches folder: {patches}")
        expected = {"README.txt", "manifest.json"}
        expected.update(entry["patch_path"] for entry in old_manifest.get("files", []) if entry.get("patch_path"))
        actual = {path.relative_to(patches).as_posix() for path in patches.rglob("*") if path.is_file()}
        expected_dirs = {parent.as_posix() for name in expected for parent in Path(name).parents
                         if parent != Path(".")}
        actual_dirs = {path.relative_to(patches).as_posix() for path in patches.rglob("*") if path.is_dir()}
        if actual != expected or actual_dirs != expected_dirs:
            raise PublishError(f"Refusing to remove files outside the generated package: {patches}")
    backup = patches.with_name(f".patches-old-{uuid.uuid4().hex}")
    had_package = patches.exists()
    try:
        if had_package:
            patches.rename(backup)
        stage.rename(patches)
    except BaseException:
        if backup.exists():
            if patches.exists():
                shutil.rmtree(patches)
            backup.rename(patches)
        elif not had_package and not stage.exists() and patches.exists():
            shutil.rmtree(patches)
        raise
    if backup.exists():
        shutil.rmtree(backup)


def prepare(repo: Path = REPO, patches: Path = PATCHES, *, publish: bool = False) -> None:
    stage = repo / f".patches-stage-{uuid.uuid4().hex}"
    try:
        with workflow._workflow_lock():
            if publish:
                _verify_remote_and_clean(repo)
            xdelta = _configured_file("xdelta")
            workflow._close_bluewake()
            workflow._build_locked("all")
            _make_package(stage, xdelta)
            _replace_package(stage, patches)
            print(f"Translation patches prepared: {patches}")
            if not publish:
                print("Prepare-only mode: inspect patches/ before committing or publishing.")
                return
            _reject_unrelated_changes(repo, ("patches",))
            _git(repo, "add", "--", "patches")
            staged = _git(repo, "diff", "--cached", "--name-only", "-z").stdout
            changed = [path for path in staged.split("\0") if path]
            if any(path != "patches" and not path.startswith("patches/") for path in changed):
                raise PublishError("Refusing to commit staged files outside patches/.")
            _commit_and_push(repo, bool(changed))
            print("Translation patch commit pushed to origin.")
    except BaseException:
        if stage.exists():
            shutil.rmtree(stage)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare-only", action="store_true",
                        help="build and write patches/ without staging, committing, or pushing")
    args = parser.parse_args()
    try:
        prepare(publish=not args.prepare_only)
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"Translation patch preparation failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
