"""Ukrainian translation patcher: downloads the current translation files from GitHub and applies them to the
user's own game dump. The mode in config.json picks the platform:

- ``gc_iso``: GameCube .iso/.rvz -> a new ``<name>_UA.iso`` (gcr extract, replace files, gcr rebuild; as Twilight Princess).
- ``wiiu_files``: a Wii U game folder (code/content/meta) -> ``<name>_UA`` with a Cemu graphic pack and an SDCafiine folder.
- ``nup``: a Switch 2 ``.nup`` package -> ``<name> (UA).nup`` plus a ``romfs`` overlay for the PC ports.

The original dump is only read. Repository layout: ``files/`` (changed files), ``manifest.json`` (checks), this script.
"""
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile


def base_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


BASE = base_dir()
INPUT = os.path.join(BASE, "ISO")


class Fail(Exception):
    """A clear error for the user (in Ukrainian)."""


def sha256_file(path, algo="sha256"):
    digest = hashlib.new(algo)
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ask_yes(question):
    return input(f"{question} (y/N): ").strip().lower() in ("y", "yes", "т", "так")


# -- download ------------------------------------------------------------------------------------------------------

def archive_url(config):
    if config.get("zip_url"):
        return config["zip_url"]
    owner, repo = config["github_repo"].rstrip("/").removesuffix(".git").split("/")[-2:]
    return f"https://github.com/{owner}/{repo}/archive/refs/heads/{config.get('branch', 'main')}.zip"


def fetch_translation(config, work):
    """Download the repository archive and return the folder that holds ``files/`` and ``manifest.json``."""
    url = archive_url(config)
    print(f"Завантаження перекладу: {url}")
    target = os.path.join(work, "translation.zip")
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response, open(target, "wb") as out:
            total, done = int(response.headers.get("Content-Length") or 0), 0
            for chunk in iter(lambda: response.read(1 << 16), b""):
                out.write(chunk)
                done += len(chunk)
                shown = f"{done * 100 // total}%" if total else f"{done / 2**20:.1f} МБ"
                sys.stdout.write(f"\r  {shown}   ")
                sys.stdout.flush()
        print()
    except OSError as error:
        raise Fail(f"Не вдалося завантажити файли перекладу ({error}). Перевірте з'єднання з інтернетом.")
    extract = os.path.join(work, "translation")
    with zipfile.ZipFile(target) as archive:
        for name in archive.namelist():   # no absolute paths or ".." in a downloaded archive
            if name.startswith(("/", "\\")) or ".." in name.replace("\\", "/").split("/"):
                raise Fail(f"Архів перекладу пошкоджений: {name}")
        archive.extractall(extract)
    for root, dirs, files in os.walk(extract):
        if "manifest.json" in files and "files" in dirs:
            return root
    raise Fail("В архіві перекладу немає manifest.json і теки files.")


def changed_files(repo):
    """``{relative path with /: absolute path}`` of every file under ``files/``."""
    top = os.path.join(repo, "files")
    found = {}
    for root, _dirs, files in os.walk(top):
        for name in files:
            path = os.path.join(root, name)
            found[os.path.relpath(path, top).replace("\\", "/")] = path
    return found


# -- input ---------------------------------------------------------------------------------------------------------

def pick(paths, what):
    paths = [p for p in paths if not re.search(r"(_UA|\(UA\)|_temp)(\.\w+)?$", os.path.basename(p), re.I)]
    if not paths:
        raise Fail(f"У теці ISO немає {what}. Покладіть туди свій дамп гри і запустіть патчер знову.")
    if len(paths) == 1:
        print(f"Знайдено: {os.path.basename(paths[0])}")
        return paths[0]
    for number, path in enumerate(paths, 1):
        print(f"  [{number}] {os.path.basename(path)}")
    choice = input("Оберіть номер файлу: ").strip()
    if not choice.isdigit() or not 1 <= int(choice) <= len(paths):
        raise Fail("Неправильний номер.")
    return paths[int(choice) - 1]


def check_free(path, needed):
    free = shutil.disk_usage(path).free
    if free < needed:
        raise Fail(f"Замало місця на диску: потрібно {needed // 2**20} МБ, вільно {free // 2**20} МБ.")


def mismatch(message):
    print(f"\nУВАГА: {message}")
    print("Переклад зроблено для іншої версії гри; з цим дампом гра може працювати неправильно.")
    if not ask_yes("Продовжити все одно?"):
        raise Fail("Зупинено. Потрібен чистий дамп підтримуваної версії (див. README).")


def run(command):
    print("  > " + " ".join(os.path.basename(c) if i == 0 else c for i, c in enumerate(command)))
    result = subprocess.run(command, capture_output=True, text=True, errors="replace")
    if result.returncode:
        raise Fail(f"Помилка {os.path.basename(command[0])} (код {result.returncode}):\n{result.stdout}\n{result.stderr}")
    return result.stdout


def tool(name):
    path = os.path.join(BASE, "soft", name)
    if not os.path.isfile(path):
        raise Fail(f"Немає програми soft\\{name}. Розпакуйте архів патчера повністю.")
    return [path] if sys.platform == "win32" else ["wine", path]


# -- GameCube ------------------------------------------------------------------------------------------------------

def apply_runs(data, runs):
    """Apply ``[[offset, hex old, hex new], ...]`` to ``data`` (bytearray); the old bytes must match."""
    for offset, old, new in runs:
        old, new = bytes.fromhex(old), bytes.fromhex(new)
        if data[offset:offset + len(old)] != old:
            raise Fail(f"main.dol не збігається з оригіналом (зсув {offset:#x}).")
        data[offset:offset + len(new)] = new


def patch_gc(manifest, repo, work):
    image = pick(glob.glob(os.path.join(INPUT, "*.iso")) + glob.glob(os.path.join(INPUT, "*.rvz")),
                 "образу гри (.iso або .rvz)")
    check_free(INPUT, 3 * 1500 * 2**20)
    iso = image
    if image.lower().endswith(".rvz"):
        print("Розпакування RVZ у тимчасовий ISO...")
        iso = os.path.join(work, "source.iso")
        run(tool("rom-converto-cli-windows-x64.exe") + ["dol", "decompress", image, iso])
    with open(iso, "rb") as stream:
        game_id = stream.read(8)
    if game_id[:6].decode("ascii", "replace") != manifest["game_id"] or game_id[7] != manifest["revision"]:
        mismatch(f"образ має ID {game_id[:6].decode('ascii', 'replace')} ревізія {game_id[7]}, "
                 f"а потрібен {manifest['game_id']} ревізія {manifest['revision']}.")
    print("Розпакування образу...")
    unpacked = os.path.join(work, "UNP")
    run(tool("gcr.exe") + [iso, "root", "e", unpacked])
    root = os.path.join(unpacked, "root")
    dol = os.path.join(root, "&&systemdata", "Start.dol")
    if hashlib.sha1(open(dol, "rb").read()[:manifest["dol_size"]]).hexdigest() != manifest["dol_sha1"]:
        mismatch("main.dol у вашому образі відрізняється від оригінального GZLE01.")
    files = changed_files(repo)
    print("Заміна файлів перекладу...")
    for rel, path in files.items():
        if rel.endswith(".dolpatch.json"):
            continue
        target = os.path.join(root, *rel.split("/"))
        if not os.path.isfile(target):
            raise Fail(f"У розпакованому образі немає файлу {rel}.")
        shutil.copyfile(path, target)
    dol_patch = files.get("sys/main.dol.dolpatch.json")
    if dol_patch:
        data = bytearray(open(dol, "rb").read())
        apply_runs(data, json.load(open(dol_patch, encoding="utf-8")))
        with open(dol, "wb") as out:
            out.write(data)
    output = os.path.splitext(image)[0] + "_UA.iso"
    print(f"Збирання образу: {os.path.basename(output)}...")
    temporary = output + ".tmp.iso"
    run(tool("gcr.exe") + [root, temporary])
    os.replace(temporary, output)
    return output


# -- Wii U ---------------------------------------------------------------------------------------------------------

def find_game_folders():
    found = []
    for meta in glob.glob(os.path.join(INPUT, "*", "meta", "meta.xml")) + glob.glob(os.path.join(INPUT, "*", "*", "meta", "meta.xml")):
        game = os.path.dirname(os.path.dirname(meta))
        if os.path.isdir(os.path.join(game, "content")):
            found.append(game)
    return sorted(set(found))


def patch_wiiu(manifest, repo, _work):
    game = pick(find_game_folders(), "теки гри Wii U (з підтеками code, content, meta)")
    xml = open(os.path.join(game, "meta", "meta.xml"), encoding="utf-8", errors="replace").read()
    title = re.search(r"<title_id[^>]*>([0-9A-Fa-f]{16})<", xml)
    title = title.group(1).upper() if title else "?"
    if title[8:] not in manifest["title_lows"]:
        mismatch(f"title id гри {title}, а підтримуються ...{', ...'.join(manifest['title_lows'])}.")
    for rel, expected in manifest.get("originals", {}).items():
        path = os.path.join(game, *rel.split("/"))
        if not os.path.isfile(path):
            mismatch(f"у грі немає файлу {rel}.")
        elif sha256_file(path) != expected:
            mismatch(f"файл {rel} не збігається з оригіналом (можливо, гра вже змінена іншим патчем).")
    output = game.rstrip("\\/") + "_UA"
    temporary = output + ".tmp"
    shutil.rmtree(temporary, ignore_errors=True)
    files = changed_files(repo)
    pack = os.path.join(temporary, "graphicPacks", manifest["graphic_pack"])
    sds = [os.path.join(temporary, "sdcafiine", "wiiu", "sdcafiine", "00050000" + low, "Picoripi_UA")
           for low in manifest["title_lows"]]
    for rel, path in files.items():
        targets = [os.path.join(pack, rel)] + ([os.path.join(sd, rel) for sd in sds] if rel.startswith("content/") else [])
        for target in targets:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copyfile(path, target)
    if os.path.exists(output):
        shutil.rmtree(output)
    os.replace(temporary, output)
    return output


# -- Switch 2 .nup -------------------------------------------------------------------------------------------------

def patch_nup(manifest, repo, _work):
    sys.path.insert(0, BASE)
    import nup
    image = pick(glob.glob(os.path.join(INPUT, "*.nup")), "пакета гри .nup")
    with open(image, "rb") as stream:
        package = nup.Nup(stream)
    if package.title_id != manifest["title_id"] or package.version != manifest["version"]:
        mismatch(f"пакет {package.title_id} v{package.version}, а потрібен {manifest['title_id']} v{manifest['version']}.")
    by_path = {entry.path: entry for entry in package.entries}
    for rel, expected in manifest.get("originals", {}).items():
        entry = by_path.get(rel)
        if entry is None or entry.sha256.hex() != expected:
            mismatch(f"файл {rel} у пакеті не збігається з оригіналом.")
    files = changed_files(repo)
    changes = {rel: open(path, "rb").read() for rel, path in files.items()}
    stem = os.path.splitext(image)[0]
    pc = stem + " (UA) PC"
    if os.path.exists(pc):
        shutil.rmtree(pc)
    for rel, path in files.items():
        target = os.path.join(pc, *rel.split("/"))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copyfile(path, target)
    output = stem + " (UA).nup"
    check_free(INPUT, os.path.getsize(image) + 2**30)
    print(f"Запис пакета {os.path.basename(output)} (це довго: копіюється весь пакет)...")
    shown = [-1]

    def progress(percent, _text):
        if percent != shown[0]:
            shown[0] = percent
            sys.stdout.write(f"\r  {percent}%   ")
            sys.stdout.flush()

    nup.write(image, output, changes, progress, lambda: False)
    print()
    return output


MODES = {"gc_iso": patch_gc, "wiiu_files": patch_wiiu, "nup": patch_nup}


def main():
    config = json.load(open(os.path.join(BASE, "config.json"), encoding="utf-8"))
    print("=" * 60)
    print(f"  {config['title']} — патчер українського перекладу")
    print("=" * 60 + "\n")
    os.makedirs(INPUT, exist_ok=True)
    work = tempfile.mkdtemp(prefix="ua_patch_", dir=INPUT)
    try:
        repo = fetch_translation(config, work)
        manifest = json.load(open(os.path.join(repo, "manifest.json"), encoding="utf-8"))
        print(f"Версія перекладу: {manifest.get('translation_version', '?')}")
        output = MODES[config["mode"]](manifest, repo, work)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print(f"\nГотово! Результат: {output}")
    print("Оригінальний дамп не змінено.")


if __name__ == "__main__":
    code = 0
    try:
        main()
    except Fail as error:
        print(f"\nПОМИЛКА: {error}")
        code = 1
    except Exception:
        import traceback
        traceback.print_exc()
        code = 1
    if not os.environ.get("UA_PATCHER_NO_PAUSE"):
        input("\nНатисніть Enter, щоб закрити вікно...")
    sys.exit(code)
