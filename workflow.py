"""Build the saved Wind Waker translation for its supported runtimes."""

from __future__ import annotations

import argparse
import base64
import hashlib
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


WORKSPACE = Path(r"E:\Emulators\RomHacking\ZELDA\Wind Waker")
GC = WORKSPACE / "GC"
HD = WORKSPACE / "HD - Wii U"
SHARED = Path(r"E:\Emulators\RomHacking\_shared\scripts")
PICORIPI = Path(r"D:\git\dev\Picoripi")
FAMILY = WORKSPACE / "_family"
BLUEWAKE = GC / "port" / "bluewake-v0.10.0" / "app" / "BlueWake" / "BlueWake.exe"
GC_ISO = Path("Legend of Zelda, The - The Wind Waker (USA) (UA).iso")
LOCK_TIMEOUT = 2.0
LOCK_PATH = Path(tempfile.gettempdir()) / (
    "windwaker-ua-" + hashlib.sha256(str(WORKSPACE.resolve()).casefold().encode()).hexdigest()[:16] + ".lock"
)


class WorkflowBusy(RuntimeError):
    """Another build or launch already owns the workspace lock."""


@contextmanager
def _workflow_lock(path: Path = LOCK_PATH, timeout: float = LOCK_TIMEOUT):
    """Take one cross-process lock for builds and launches; fail instead of racing outputs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open("a+b")
    if stream.tell() == 0:
        stream.write(b"\0")
        stream.flush()
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                if os.name == "nt":
                    import msvcrt

                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise WorkflowBusy("Another Wind Waker build or launch is active. Try again later.")
                time.sleep(0.05)
        try:
            yield
        finally:
            if os.name == "nt":
                import msvcrt

                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    finally:
        stream.close()


def _link_copy(source: str, destination: str) -> str:
    """Copy immutable input files by hard link to keep the staging footprint small."""
    try:
        os.link(source, destination)
        return destination
    except OSError:
        return shutil.copy2(source, destination)


def _copy_detached(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".workflow-tmp")
    shutil.copy2(source, temporary)
    os.replace(temporary, destination)


def _use_newer_archive(gc_archive: Path, family_archive: Path, staged_archive: Path) -> None:
    available = [path for path in (gc_archive, family_archive) if path.is_file()]
    if not available:
        return
    if len(available) == 2:
        gc_time = gc_archive.stat().st_mtime_ns
        family_time = family_archive.stat().st_mtime_ns
        if gc_time == family_time and gc_archive.read_bytes() != family_archive.read_bytes():
            raise RuntimeError(f"Conflicting saved translations have the same timestamp: {gc_archive} and {family_archive}")
        newest = gc_archive if gc_time > family_time else family_archive
    else:
        newest = available[0]
    if newest != staged_archive:
        _copy_detached(newest, staged_archive)


def _copy_inputs(source: Path, target: Path, names: tuple[str, ...]) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for name in names:
        path = source / name
        if not path.exists():
            continue
        destination = target / name
        if destination.exists():
            continue
        if path.is_dir():
            shutil.copytree(path, destination, copy_function=_link_copy)
        else:
            _link_copy(str(path), str(destination))


def _run(command: list[str], *, cwd: Path) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def _family_export(stage: Path) -> None:
    staged_family = stage / "_family"
    shutil.copytree(FAMILY, staged_family, copy_function=shutil.copy2)
    # Keep the newest saved common-message archive; never assume which project was edited last.
    gc_messages = GC / "translation" / "files" / "res" / "Msg"
    family_translations = staged_family / "picoripi_translation"
    family_message_names = {path.name for path in family_translations.glob("bmgres*.arc")}
    gc_message_names = {path.name for path in gc_messages.glob("bmgres*.arc")}
    for name in sorted(family_message_names | gc_message_names):
        _use_newer_archive(gc_messages / name, family_translations / name, family_translations / name)
    for label, source, names in (
        ("GC", GC, ("source",)),
        ("HD - Wii U", HD, ("source",)),
    ):
        _copy_inputs(source, stage / label, names)
    _run([sys.executable, "-m", "plugins.zelda_ww.family", "push",
          str(staged_family / "family.json")], cwd=PICORIPI)


def _stage_workspace(source: Path, target: Path, *, hd: bool) -> None:
    common = ("source", "translation", "image", "zt_game.txt")
    _copy_inputs(source, target, common)
    if not hd:
        release = source / "port" / "switchwaker_release"
        if release.is_dir():
            _copy_inputs(source, target, ("port/switchwaker_release",))


def _remove_owned(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def _rename(source: Path, destination: Path) -> None:
    source.rename(destination)


def _publish_many(builds: list[tuple[Path, Path]]) -> None:
    """Prepare every replacement first, then swap all targets with rollback on interruption."""
    prepared: list[tuple[Path, Path, Path]] = []
    had_destination: dict[Path, bool] = {}
    try:
        for source, destination in builds:
            nonce = uuid.uuid4().hex
            replacement = destination.with_name(f".{destination.name}.workflow-new-{nonce}")
            backup = destination.with_name(f".{destination.name}.workflow-old-{nonce}")
            if replacement.exists() or backup.exists():
                raise FileExistsError(f"Refusing to use existing workflow transaction path: {replacement} / {backup}")
            prepared.append((replacement, destination, backup))
            shutil.copytree(source, replacement, copy_function=shutil.copy2)
        for replacement, destination, backup in prepared:
            if destination.exists():
                had_destination[destination] = True
                _rename(destination, backup)
            else:
                had_destination[destination] = False
            _rename(replacement, destination)
    except BaseException:
        for replacement, destination, backup in reversed(prepared):
            if backup.exists():
                if destination.exists():
                    _remove_owned(destination)
                backup.rename(destination)
            elif (had_destination.get(destination) is False and not replacement.exists()
                  and destination.exists()):
                _remove_owned(destination)
        for replacement, _destination, _backup in prepared:
            if replacement.exists():
                _remove_owned(replacement)
        raise
    for _replacement, _destination, backup in prepared:
        if backup.exists():
            # This backup is the old output moved by this successful transaction.
            _remove_owned(backup)


def _validate_bluewake_disc(path: Path) -> None:
    expected_dol_sha1 = "8d28bab68bb5078c38e43f29206f0bd01f7e7a67"
    with path.open("rb") as stream:
        header = stream.read(0x440)
        if len(header) < 0x440 or header[:6] != b"GZLE01":
            raise RuntimeError(f"Not a GameCube USA rev. 0 (GZLE01) disc: {path}")
        if struct.unpack_from(">I", header, 0x1C)[0] != 0xC2339F3D:
            raise RuntimeError(f"Invalid GameCube disc magic: {path}")
        dol_offset = struct.unpack_from(">I", header, 0x420)[0]
        stream.seek(dol_offset)
        dol_header = stream.read(0xE4)
        if len(dol_header) != 0xE4:
            raise RuntimeError(f"Truncated main.dol in disc: {path}")
        offsets = struct.unpack_from(">18I", dol_header, 0)
        sizes = struct.unpack_from(">18I", dol_header, 0x90)
        dol_size = max((offset + size for offset, size in zip(offsets, sizes)), default=0)
        stream.seek(dol_offset)
        digest = hashlib.sha1()
        remaining = dol_size
        while remaining:
            chunk = stream.read(min(1 << 20, remaining))
            if not chunk:
                raise RuntimeError(f"Truncated main.dol in disc: {path}")
            digest.update(chunk)
            remaining -= len(chunk)
    if digest.hexdigest() != expected_dol_sha1:
        raise RuntimeError(f"BlueWake requires the original GZLE01 main.dol; hash mismatch: {path}")


def _apply_family_archives(family_translations: Path, gc_translation: Path) -> None:
    for archive in family_translations.glob("bmgres*.arc"):
        _copy_detached(archive, gc_translation / archive.name)


def _close_bluewake() -> None:
    """Ask only BlueWake processes at the configured executable path to close cleanly."""
    if os.name != "nt":
        return
    encoded_path = base64.b64encode(str(BLUEWAKE.resolve()).encode("utf-16le")).decode("ascii")
    script = rf"""
$exe = [Text.Encoding]::Unicode.GetString([Convert]::FromBase64String('{encoded_path}'))
$name = [IO.Path]::GetFileNameWithoutExtension($exe)
$matches = @(Get-Process -Name $name -ErrorAction SilentlyContinue | Where-Object {{
    try {{ [IO.Path]::GetFullPath($_.Path) -ieq $exe }} catch {{ $false }}
}})
foreach ($process in $matches) {{
    if ($process.MainWindowHandle -eq 0 -or -not $process.CloseMainWindow()) {{
        Write-Error "Cannot request a graceful close from BlueWake process $($process.Id)."
        exit 2
    }}
    if (-not $process.WaitForExit(20000)) {{
        Write-Error "BlueWake process $($process.Id) is still open. Close it and try again."
        exit 3
    }}
}}
"""
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                            capture_output=True, text=True, timeout=25, check=False)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "BlueWake did not close cleanly; no build outputs were changed.")


def _build_locked(target: str) -> None:
    if not (SHARED / "zt.py").is_file() or not (FAMILY / "family.json").is_file():
        raise FileNotFoundError("Shared Zelda tools or _family/family.json were not found.")
    # Keep immutable inputs on the same volume so staging can hard-link large game files.
    with tempfile.TemporaryDirectory(prefix=".windwaker-build-", dir=WORKSPACE) as temporary:
        stage = Path(temporary)
        _family_export(stage)
        build_gc = target in ("all", "gc")
        build_hd = target in ("all", "hd")
        if build_gc:
            gc_stage = stage / "GC"
            _stage_workspace(GC, gc_stage, hd=False)
            _apply_family_archives(stage / "_family" / "picoripi_translation",
                                   gc_stage / "translation" / "files" / "res" / "Msg")
            _run([sys.executable, str(SHARED / "zt.py"), str(gc_stage), "build"], cwd=SHARED)
            _validate_bluewake_disc(gc_stage / "build" / "switchwaker" / "switch" / "switchwaker" / "GZLE01.iso")
        if build_hd:
            hd_stage = stage / "HD - Wii U"
            _stage_workspace(HD, hd_stage, hd=True)
            _run([sys.executable, str(SHARED / "zt.py"), str(hd_stage), "build"], cwd=SHARED)
        builds = []
        if build_gc:
            builds.append((gc_stage / "build", GC / "build"))
        if build_hd:
            builds.append((hd_stage / "build", HD / "build"))
        _publish_many(builds)
    print("Build completed. Translation and project folders were not modified.")


def build(target: str) -> None:
    if target not in ("all", "gc", "hd"):
        raise ValueError(f"Unsupported build target: {target}")
    with _workflow_lock():
        if target in ("all", "gc"):
            _close_bluewake()
        _build_locked(target)


def play(runtime: str) -> None:
    if runtime not in ("bluewake", "dolphin", "cemu"):
        raise ValueError(f"Unsupported runtime: {runtime}")
    target = "hd" if runtime == "cemu" else "gc"
    with _workflow_lock():
        if target == "gc":
            _close_bluewake()
        _build_locked(target)
        if runtime == "bluewake":
            iso = GC / "build" / "switchwaker" / "switch" / "switchwaker" / "GZLE01.iso"
            if not BLUEWAKE.is_file():
                raise FileNotFoundError(f"BlueWake was not found: {BLUEWAKE}")
            subprocess.Popen([str(BLUEWAKE), "--disc", str(iso)], cwd=BLUEWAKE.parent)
        else:
            workspace = GC if runtime == "dolphin" else HD
            _run([sys.executable, str(SHARED / "zt.py"), str(workspace), "run"], cwd=SHARED)


def _validate_command(action: str, target: str) -> None:
    valid = {"build": {"all", "gc", "hd"}, "play": {"bluewake", "dolphin", "cemu"}}
    if target not in valid[action]:
        raise ValueError(f"{action} does not support target {target!r}; choose from {', '.join(sorted(valid[action]))}.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("build", "play"))
    parser.add_argument("target", choices=("all", "gc", "hd", "bluewake", "dolphin", "cemu"))
    args = parser.parse_args()
    try:
        _validate_command(args.action, args.target)
        if args.action == "build":
            build(args.target)
        else:
            play(args.target)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        print(f"Build or launch failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
