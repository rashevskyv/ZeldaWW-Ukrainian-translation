"""Ukrainian translation patcher: downloads the translation from the game's GitHub repository and applies it to the
user's own game dump. The mode in config.json picks the platform:

- ``gc_iso`` / ``disc``: GameCube or Wii image (.iso/.rvz/.gcz/.wia/.ciso) -> ``<name>_UA.iso`` (one file for Dolphin and
  for the console). A Wii image is detected by its game id in the manifest.
- ``wiiu_files``: a Wii U game folder -> ``<name>_UA`` with a Cemu graphic pack and an SDCafiine folder.
- ``nup``: a Switch 2 ``.nup`` package -> ``<name> (UA).nup`` (updated in place when it can) plus a ``<name> (UA) PC``
  romfs overlay folder that NX2navi and Hookshot read.

The original dump is only read. The translation is always downloaded from the repository; nothing is bundled.
"""
import filecmp
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

PATCHER_VERSION = "1.2.1"


def base_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


BASE = base_dir()
INPUT = os.path.join(BASE, "ISO")
CORE_DIR = os.path.join(BASE, "core")   # frozen .exe: a downloaded newer patcher.py (and nup.py) lives here
CLEAN_NAME = "Перепатчити з нуля.bat"   # written by scripts\make_zip.py next to the .exe


class Fail(Exception):
    """A clear error for the user (in Ukrainian)."""


def version_tuple(text):
    return tuple(int(part) for part in re.findall(r"\d+", text))[:4]


def sha256_file(path, algo="sha256"):
    digest = hashlib.new(algo)
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ask_yes(question):
    try:
        return input(f"{question} (y/N): ").strip().lower() in ("y", "yes", "т", "так")
    except EOFError:
        return False


def write_atomic(path, data):
    """Write bytes or text to ``path`` through a temporary file in the same folder."""
    tmp = path + ".tmp"
    if isinstance(data, str):
        with open(tmp, "w", encoding="utf-8", newline="\n") as out:
            out.write(data)
    else:
        with open(tmp, "wb") as out:
            out.write(data)
    os.replace(tmp, path)


def copy_if_changed(source, target):
    """Copy ``source`` over ``target`` only when the bytes differ; returns True when it copied."""
    if os.path.isfile(target) and os.path.getsize(target) == os.path.getsize(source) \
            and filecmp.cmp(source, target, shallow=False):
        return False
    os.makedirs(os.path.dirname(target), exist_ok=True)
    shutil.copyfile(source, target)
    return True


# -- translation source --------------------------------------------------------------------------------------------

def github_repo(config):
    owner, repo = config["github_repo"].rstrip("/").removesuffix(".git").split("/")[-2:]
    return owner, repo


def archive_url(config):
    if config.get("zip_url"):
        return config["zip_url"]
    owner, repo = github_repo(config)
    return f"https://github.com/{owner}/{repo}/archive/refs/heads/{config.get('branch', 'main')}.zip"


def api_json(config, path):
    """GitHub API answer (no token) or None when it cannot be read; never raises."""
    base = config.get("api_url", "https://api.github.com")
    request = urllib.request.Request(base + path, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                                                            "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError):
        return None


def head_commit(config):
    owner, repo = github_repo(config)
    data = api_json(config, f"/repos/{owner}/{repo}/commits/{config.get('branch', 'main')}")
    return data.get("sha") if isinstance(data, dict) else None


def print_news(config, previous, head):
    """Print the repository commits made since the version the user has (fails soft)."""
    if not previous or not head or previous == head:
        return
    owner, repo = github_repo(config)
    commits = api_json(config, f"/repos/{owner}/{repo}/commits?sha={config.get('branch', 'main')}&per_page=50")
    if not isinstance(commits, list):
        return
    news = []
    for commit in commits:
        if commit.get("sha") == previous:
            break
        news.append(commit.get("commit", {}).get("message", "").split("\n")[0][:120])
    if news:
        print("Що нового з вашої останньої збірки:")
        for line in news:
            print(f"  - {line}")
        print()


def download(url, target, what):
    print(f"Завантаження {what}: {url}")
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    with urllib.request.urlopen(request, timeout=60) as response, open(target, "wb") as out:
        total, done = int(response.headers.get("Content-Length") or 0), 0
        for chunk in iter(lambda: response.read(1 << 16), b""):
            out.write(chunk)
            done += len(chunk)
            shown = f"{done * 100 // total}%" if total else f"{done / 2**20:.1f} МБ"
            sys.stdout.write(f"\r  {shown}   ")
            sys.stdout.flush()
    print()


def fetch_translation(config, work):
    """Return the folder with ``files/`` and ``manifest.json`` from the repository (no other source exists)."""
    head = head_commit(config)
    cache = os.path.join(INPUT, ".ua_cache")
    cached = os.path.join(cache, f"translation-{head}.zip") if head else None
    if cached and os.path.isfile(cached):
        print("Переклад уже завантажено раніше (кеш тієї самої версії).")
        source = cached
    else:
        source = os.path.join(work, "translation.zip")
        try:
            download(archive_url(config), source, "перекладу")
        except OSError as error:
            raise Fail("Не вдалося завантажити переклад з GitHub "
                       f"({error}). Перевірте з'єднання з інтернетом і запустіть патчер ще раз.")
        if head:   # keep this one copy for the case when GitHub cannot be read next time
            os.makedirs(cache, exist_ok=True)
            shutil.copyfile(source, cached)
            for old in glob.glob(os.path.join(cache, "translation-*.zip")):
                if old != cached:
                    os.remove(old)
    extract = os.path.join(work, "translation")
    with zipfile.ZipFile(source) as archive:
        for name in archive.namelist():   # no absolute paths or ".." in a downloaded archive
            if name.startswith(("/", "\\")) or ".." in name.replace("\\", "/").split("/"):
                raise Fail(f"Архів перекладу пошкоджений: {name}")
        archive.extractall(extract)
    for root, dirs, files in os.walk(extract):
        if "manifest.json" in files and "files" in dirs:
            return root, head
    raise Fail("В архіві перекладу немає manifest.json і теки files.")


def tree_files(top):
    """``{relative path with /: absolute path}`` of every file under ``top``."""
    found = {}
    if not os.path.isdir(top):
        return found
    for root, _dirs, files in os.walk(top):
        for name in files:
            path = os.path.join(root, name)
            found[os.path.relpath(path, top).replace("\\", "/")] = path
    return found


def changed_files(repo):
    return tree_files(os.path.join(repo, "files"))


# -- state: what was built, from which translation version --------------------------------------------------------

class Store:
    """``ISO\\.ua_store.json``: per output file the translation commit, version and source it was built from."""

    def __init__(self):
        self.path = os.path.join(INPUT, ".ua_store.json")
        try:
            with open(self.path, encoding="utf-8") as stream:
                self.data = json.load(stream)
        except (OSError, ValueError):
            self.data = {}

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value):
        self.data[key] = value
        os.makedirs(INPUT, exist_ok=True)
        write_atomic(self.path, json.dumps(self.data, indent=2, ensure_ascii=False))


def facts_of(path):
    """What identifies the source file: name, size and time (the dump is never read to hash it)."""
    stat = os.stat(path)
    return {"name": os.path.basename(path), "size": stat.st_size, "mtime": stat.st_mtime_ns}


def remove_output(output):
    for path in (output, output + ".ua.json", output + ".source.json"):
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
        elif os.path.exists(path):
            os.remove(path)


def plan(output, ctx, facts):
    """'current' (nothing to do), 'update' (only the changed files) or 'build' (from the original dump)."""
    if ctx["clean"]:
        print("Режим --clean: старий результат і кеш видаляються, збірка з оригіналу.")
        remove_output(output)
        return "build"
    entry = ctx["store"].get(os.path.basename(output))
    if not entry or not os.path.exists(output) or entry.get("source") != facts:
        return "build"
    if ctx["commit"] and entry.get("commit") == ctx["commit"]:
        return "current"
    return "update"


def remember(output, ctx, facts, manifest):
    ctx["store"].set(os.path.basename(output), {"commit": ctx["commit"], "translation_version":
                                                manifest.get("translation_version", "?"),
                                                "patcher_version": PATCHER_VERSION, "source": facts})
    ctx["store"].set("_last_commit", ctx["commit"])


def check_free(path, needed):
    free = shutil.disk_usage(path).free
    if free < needed:
        raise Fail(f"Замало місця на диску: потрібно {needed // 2**20} МБ, вільно {free // 2**20} МБ.")
    print(f"Місця на диску вистачає: потрібно {needed // 2**20} МБ, вільно {free // 2**20} МБ.")


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


def prepare_iso(image, work):
    """A plain ISO path for ``image`` (converted with DolphinTool when it is not an .iso)."""
    if image.lower().endswith(".iso"):
        return image
    print("Розпакування образу в тимчасовий ISO (DolphinTool)...")
    iso = os.path.join(work, "source.iso")   # -u: Dolphin keeps its settings folder inside the work folder
    run(tool("DolphinTool.exe") + ["convert", "-u", os.path.join(work, "dolphin_user"),
                                   "-i", image, "-o", iso, "-f", "iso"])
    return iso


def read_game_id(iso):
    with open(iso, "rb") as stream:
        head = stream.read(8)
    return head[:6].decode("ascii", "replace"), head[7]


# -- GameCube and Wii images ---------------------------------------------------------------------------------------

GC_IMAGES = (".iso", ".rvz", ".gcz", ".wia", ".ciso")   # everything but .iso goes through DolphinTool


def apply_runs(data, runs):
    """Apply ``[[offset, hex old, hex new], ...]`` to ``data`` (bytearray); the old bytes must match."""
    for offset, old, new in runs:
        old, new = bytes.fromhex(old), bytes.fromhex(new)
        if data[offset:offset + len(old)] != old:
            raise Fail(f"main.dol не збігається з оригіналом (зсув {offset:#x}).")
        data[offset:offset + len(new)] = new


def patch_disc(manifest, repo, work, ctx):
    image = pick([p for ext in GC_IMAGES for p in glob.glob(os.path.join(INPUT, "*" + ext))],
                 "образу гри (" + ", ".join(GC_IMAGES) + ")")
    output = os.path.splitext(image)[0] + "_UA.iso"
    facts = facts_of(image)
    state = plan(output, ctx, facts)
    if state == "current":
        print(f"Вже актуальна версія перекладу ({ctx['commit'][:7]}): {output}")
        return output
    check_free(INPUT, 3 * os.path.getsize(image) + 2**30)
    iso = prepare_iso(image, work)
    game_id, revision = read_game_id(iso)
    wii = manifest.get("wii")
    if game_id == manifest["game_id"] and revision == manifest["revision"]:
        build_gc(manifest, repo, work, iso, output)
    elif wii and game_id == wii["game_id"] and revision == wii["revision"]:
        build_wii(manifest, repo, work, iso, output)
    else:
        mismatch(f"образ має ID {game_id} ревізія {revision}, а підтримуються {manifest['game_id']} "
                 f"та {wii['game_id'] if wii else '-'}.")
    remember(output, ctx, facts, manifest)
    return output


def build_into(output, build):
    """Build the image into a temporary file, then replace the old result. A failed build leaves no temporary file."""
    temporary = output + ".tmp.iso"
    try:
        build(temporary)
        os.replace(temporary, output)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def build_gc(manifest, repo, work, iso, output):
    print("Розпакування образу...")
    unpacked = os.path.join(work, "UNP")
    run(tool("gcr.exe") + [iso, "root", "e", unpacked])
    root = os.path.join(unpacked, "root")
    dol = os.path.join(root, "&&systemdata", "Start.dol")
    if "dol_sha1" in manifest:
        with open(dol, "rb") as stream:
            if hashlib.sha1(stream.read(manifest["dol_size"])).hexdigest() != manifest["dol_sha1"]:
                mismatch("main.dol у вашому образі відрізняється від оригінального образу.")
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
    print(f"Збирання образу: {os.path.basename(output)}...")
    build_into(output, lambda temporary: run(tool("gcr.exe") + [root, temporary]))


def build_wii(manifest, repo, work, iso, output):
    """Wii disc: only fontres.arc from the GameCube set plus the Wii's own Msgus files (repo folder ``wii/``)."""
    print("Розпакування Wii-образу (wit)...")
    unpacked = os.path.join(work, "UNP_WII")
    run(tool("wit.exe") + ["extract", iso, "-D", unpacked])
    data = os.path.join(unpacked, "DATA", "files")
    pairs = []
    for rel in manifest["wii"]["files"]:
        pairs.append((os.path.join(repo, "files", *rel.split("/")), rel))
    for rel, path in tree_files(os.path.join(repo, "wii")).items():
        pairs.append((path, rel))
    print("Заміна файлів перекладу...")
    for source, rel in pairs:
        target = os.path.join(data, *rel.split("/"))
        if not os.path.isfile(target):
            raise Fail(f"У Wii-образі немає файлу {rel}.")
        shutil.copyfile(source, target)
    print(f"Збирання образу: {os.path.basename(output)}...")
    build_into(output, lambda temporary: run(tool("wit.exe") + ["copy", unpacked, temporary]))


# -- Wii U ---------------------------------------------------------------------------------------------------------

def find_game_folders():
    found = []
    for meta in glob.glob(os.path.join(INPUT, "*", "meta", "meta.xml")) + glob.glob(os.path.join(INPUT, "*", "*", "meta", "meta.xml")):
        game = os.path.dirname(os.path.dirname(meta))
        if os.path.isdir(os.path.join(game, "content")):
            found.append(game)
    return sorted(set(found))


def patch_wiiu(manifest, repo, work, ctx):
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
    facts = {"name": os.path.basename(game), "meta_sha256": sha256_file(os.path.join(game, "meta", "meta.xml"))}
    state = plan(output, ctx, facts)
    if state == "current":
        print(f"Вже актуальна версія перекладу ({ctx['commit'][:7]}): {output}")
        return output
    files = changed_files(repo)
    pack = os.path.join(output, "graphicPacks", manifest["graphic_pack"])
    sds = [os.path.join(output, "sdcafiine", "wiiu", "sdcafiine", "00050000" + low, "Picoripi_UA")
           for low in manifest["title_lows"]]
    if state == "update":   # the folder is ours and matches the same dump: copy only the files that differ
        print("Оновлення: копіюються лише змінені файли.")
        for rel, path in files.items():
            targets = [os.path.join(pack, *rel.split("/"))] + \
                      ([os.path.join(sd, *rel.split("/")) for sd in sds] if rel.startswith("content/") else [])
            for target in targets:
                copy_if_changed(path, target)
    else:
        check_free(INPUT, 2 * sum(os.path.getsize(p) for p in files.values()) * (1 + len(sds)) + 2**28)
        temporary = output + ".tmp"
        shutil.rmtree(temporary, ignore_errors=True)
        pack = os.path.join(temporary, "graphicPacks", manifest["graphic_pack"])
        sds = [os.path.join(temporary, "sdcafiine", "wiiu", "sdcafiine", "00050000" + low, "Picoripi_UA")
               for low in manifest["title_lows"]]
        for rel, path in files.items():
            targets = [os.path.join(pack, *rel.split("/"))] + \
                      ([os.path.join(sd, *rel.split("/")) for sd in sds] if rel.startswith("content/") else [])
            for target in targets:
                os.makedirs(os.path.dirname(target), exist_ok=True)
                shutil.copyfile(path, target)
        if os.path.exists(output):
            shutil.rmtree(output)
        os.replace(temporary, output)
    remember(output, ctx, facts, manifest)
    return output


# -- Switch 2 .nup -------------------------------------------------------------------------------------------------

def nup_module():
    """Import the vendored nup.py: beside the frozen .exe's downloaded core if present, else beside this file."""
    folder = CORE_DIR if os.path.isfile(os.path.join(CORE_DIR, "nup.py")) else BASE
    sys.path.insert(0, folder)
    import nup
    return nup


def patch_nup(manifest, repo, work, ctx):
    nup = nup_module()
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
    stem = os.path.splitext(image)[0]
    output = stem + " (UA).nup"
    pc = stem + " (UA) PC"
    facts = facts_of(image)
    state = plan(output, ctx, facts)
    files = changed_files(repo)
    if state == "current":
        print(f"Вже актуальна версія перекладу ({ctx['commit'][:7]}): {output}")
        sync_overlay(files, pc)
        return output
    progress_shown = [-1]

    def progress(percent, _text):
        if percent != progress_shown[0]:
            progress_shown[0] = percent
            sys.stdout.write(f"\r  {percent}%   ")
            sys.stdout.flush()

    changes = {rel: open(path, "rb").read() for rel, path in files.items()}
    if state == "update" and nup.is_update_of(image, output):
        print(f"Оновлення {os.path.basename(output)} на місці (лише змінені файли)...")
        check_free(INPUT, sum(len(data) for data in changes.values()) + 2**28)
        nup.update(image, output, changes, progress)
    else:
        if state == "update":
            print("Немає запису про попередню збірку пакета: збираю його заново.")
        check_free(INPUT, os.path.getsize(image) + 2**30)
        print(f"Запис пакета {os.path.basename(output)} (це довго: копіюється весь пакет)...")
        nup.write(image, output, changes, progress, lambda: False)
    print()
    sync_overlay(files, pc)
    remember(output, ctx, facts, manifest)
    return output


def sync_overlay(files, pc):
    """``<name> (UA) PC\\romfs\\...``: the same romfs tree the NX2navi and Hookshot mods read. Only changed files."""
    for rel, path in files.items():
        copy_if_changed(path, os.path.join(pc, *rel.split("/")))
    print(f"Оверлей для ПК: {pc}")


def offer_nx2navi(pc_dir):
    """Set NX2navi's language and mods folder (y/N first). It reads the overlay folder; nothing is copied."""
    if not ask_yes("Налаштувати NX2navi на український переклад (вказати його теку)?"):
        return
    folder = input("Вкажіть теку NX2navi (там, де є game\\nx2navi.ini): ").strip().strip('"')
    ini = next((p for p in (os.path.join(folder, "game", "nx2navi.ini"), os.path.join(folder, "nx2navi.ini"))
                if os.path.isfile(p)), None)
    if not ini:
        print("Файл nx2navi.ini не знайдено. Налаштуйте NX2navi вручну (див. README).")
        return
    mods = os.path.dirname(os.path.abspath(pc_dir))
    with open(ini, encoding="utf-8", errors="replace") as stream:
        lines = stream.read().splitlines()
    wanted = {"lang": "ru", "mods_dir": mods}
    for key, value in wanted.items():
        for number, line in enumerate(lines):
            if re.match(rf"\s*{key}\s*=", line):
                lines[number] = f"{key} = {value}"
                break
        else:
            lines.append(f"{key} = {value}")
    shutil.copyfile(ini, ini + ".bak")
    write_atomic(ini, "\n".join(lines) + "\n")
    print(f"NX2navi: lang = ru, mods_dir = {mods}. Старий файл: {ini}.bak")


# -- the patcher itself: newer version from the repository ----------------------------------------------------------

def raw_url(config):
    owner, repo = github_repo(config)
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{config.get('branch', 'main')}/{config.get('patcher_path', 'patcher.py')}"


def fetch_text(url):
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    with urllib.request.urlopen(request, timeout=15) as response:
        return response.read().decode("utf-8")


def cached_core():
    """The downloaded patcher.py of a frozen .exe, when it is newer than the .exe's own."""
    path = os.path.join(CORE_DIR, "patcher.py")
    if not getattr(sys, "frozen", False) or not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as stream:
            found = re.search(r'^PATCHER_VERSION\s*=\s*"([^"]+)"', stream.read(4096), re.M)
    except OSError:
        return None
    return path if found and version_tuple(found.group(1)) > version_tuple(PATCHER_VERSION) else None


def check_self_update(config):
    """Download a newer patcher.py from the repository. Returns the new file's path, or None (fails soft)."""
    if os.environ.get("UA_NO_SELF_UPDATE"):
        return None
    url = config.get("patcher_url") or raw_url(config)
    try:
        text = fetch_text(url)
    except (OSError, ValueError):
        print("Перевірка оновлень патчера пропущена (немає з'єднання з GitHub).")
        return None
    found = re.search(r'^PATCHER_VERSION\s*=\s*"([^"]+)"', text, re.M)
    if not found:
        return None
    best = max(version_tuple(PATCHER_VERSION), version_tuple(cached_version() or "0"))
    if version_tuple(found.group(1)) <= best:
        return None
    try:
        compile(text, url, "exec")
    except SyntaxError:
        print("Новий патчер у репозиторії пошкоджений; використовую поточний.")
        return None
    print(f"Знайдено новішу версію патчера: {found.group(1)} (у вас {PATCHER_VERSION}). Оновлюю...")
    if getattr(sys, "frozen", False):
        target = os.path.join(CORE_DIR, "patcher.py")
    else:
        target = os.path.abspath(__file__)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    if os.path.exists(target):
        shutil.copyfile(target, target + ".bak")   # the old copy stays until the new one has started
    write_atomic(target, text)
    if config.get("mode") == "nup":
        try:
            nup_text = fetch_text(url.rsplit("/", 1)[0] + "/nup.py")
            write_atomic(os.path.join(os.path.dirname(target), "nup.py"), nup_text)
        except (OSError, ValueError):
            pass
    return target


def cached_version():
    path = cached_core()
    if not path:
        return None
    with open(path, encoding="utf-8") as stream:
        return re.search(r'^PATCHER_VERSION\s*=\s*"([^"]+)"', stream.read(4096), re.M).group(1)


def swap_script(exe, new, argv):
    """Batch file: after the old .exe exits, move it to ``.bak``, put the new .exe in its place and start it."""
    args = subprocess.list2cmdline(argv)
    lines = ["@echo off", "chcp 65001 >nul", f'set "EXE={exe}"', f'set "NEW={new}"', 'set "BAK=%EXE%.bak"',
             "set /a TRIES=0", ":wait", "set /a TRIES+=1", "if %TRIES% GEQ 60 exit /b 1",
             "ping -n 2 127.0.0.1 >nul", 'move /y "%EXE%" "%BAK%" >nul 2>&1', "if errorlevel 1 goto wait",
             'move /y "%NEW%" "%EXE%" >nul 2>&1',
             'if errorlevel 1 (move /y "%BAK%" "%EXE%" >nul & goto wait)',
             f'"%EXE%" {args}', 'del "%~f0" >nul 2>&1', ""]
    return "\r\n".join(lines)


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def check_exe_update(config, argv):
    """Frozen .exe only: when the repo's committed .exe differs from this one, download it next to this one.
    A small .bat swaps it in after this process exits. Returns True when the swap was started (the caller then exits).
    Off unless config has exe_url and exe_info_url. exe_info_url is JSON: {"version": "...", "sha256": "..."}."""
    if not getattr(sys, "frozen", False) or os.environ.get("UA_NO_SELF_UPDATE") or not config.get("exe_url"):
        return False
    exe = sys.executable
    try:
        info = json.loads(fetch_text(config["exe_info_url"]))
        remote_sha = str(info["sha256"]).lower()
    except (OSError, ValueError, KeyError):
        print("Перевірка нового .exe пропущена (немає з'єднання з GitHub).")
        return False
    if sha256_file(exe) == remote_sha:
        return False
    new = exe + ".new"
    print(f"Знайдено новіший патчер .exe: {info.get('version', '?')} (у вас {PATCHER_VERSION}). Завантажую...")
    try:
        request = urllib.request.Request(config["exe_url"], headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0)"})
        with urllib.request.urlopen(request, timeout=120) as response, open(new, "wb") as out:
            shutil.copyfileobj(response, out)
        if sha256_file(new) != remote_sha:
            raise ValueError("контрольна сума не збігається")
    except (OSError, ValueError) as error:
        print(f"Не вдалося завантажити новий .exe ({error}); працюю з поточним.")
        if os.path.exists(new):
            os.remove(new)
        return False
    bat = os.path.join(tempfile.gettempdir(), f"ua_patcher_swap_{os.getpid()}.bat")
    with open(bat, "w", encoding="utf-8", newline="") as out:
        out.write(swap_script(exe, new, argv))
    # tests (UA_PATCHER_NO_PAUSE) run the swap without a visible console window
    flags = subprocess.CREATE_NO_WINDOW if os.environ.get("UA_PATCHER_NO_PAUSE") else subprocess.CREATE_NEW_CONSOLE
    # The old .exe's PyInstaller bootloader leaves _PYI_* and _MEI* variables in the environment. The new .exe
    # inherits them and exits with -1 before it runs, so its .bak cleanup never happens. Drop them here.
    env = {key: value for key, value in os.environ.items() if not key.startswith(("_PYI_", "_MEI"))}
    subprocess.Popen(["cmd", "/c", bat], creationflags=flags, close_fds=True, env=env)
    print("Новий .exe встановлюється. Цей запуск завершується; патчер запуститься знову сам.")
    return True


def remove_swap_backup():
    """A new .exe that runs has replaced the old one: its ``.bak`` copy is no longer needed."""
    if getattr(sys, "frozen", False):
        try:
            os.remove(sys.executable + ".bak")
        except OSError:
            pass


def run_core(path, argv):
    """Run a newer patcher.py in this process (frozen .exe) or as a child process (plain Python)."""
    if getattr(sys, "frozen", False):
        os.environ["UA_CORE_LOADED"] = "1"
        with open(path, encoding="utf-8") as stream:
            source = stream.read()
        sys.path.insert(0, os.path.dirname(path))
        exec(compile(source, path, "exec"), {"__name__": "__main__", "__file__": path, "_ua_argv": argv})
        sys.exit(0)
    env = dict(os.environ, UA_CORE_LOADED="1")
    sys.exit(subprocess.call([sys.executable, path, *argv], env=env))


# -- main ----------------------------------------------------------------------------------------------------------

MODES = {"gc_iso": patch_disc, "disc": patch_disc, "wiiu_files": patch_wiiu, "nup": patch_nup}


def main(argv):
    clean = "--clean" in argv
    remove_swap_backup()  # first, so a failing start still clears the .bak from an exe swap
    config = json.load(open(os.path.join(BASE, "config.json"), encoding="utf-8"))
    print("=" * 60)
    print(f"  {config['title']} — патчер українського перекладу (версія {PATCHER_VERSION})")
    print("=" * 60 + "\n")
    os.makedirs(INPUT, exist_ok=True)
    if not os.environ.get("UA_CORE_LOADED"):
        if check_exe_update(config, argv):
            sys.exit(0)
        newer = check_self_update(config)
        if newer:
            run_core(newer, argv)
        cached = cached_core()
        if cached:
            run_core(cached, argv)
    store = Store()
    work = tempfile.mkdtemp(prefix="ua_patch_", dir=INPUT)
    try:
        repo, head = fetch_translation(config, work)
        manifest = json.load(open(os.path.join(repo, "manifest.json"), encoding="utf-8"))
        print(f"Версія перекладу: {manifest.get('translation_version', '?')}"
              + (f" ({head[:7]})" if head else " (версію репозиторію визначити не вдалося)"))
        print_news(config, store.get("_last_commit"), head)
        ctx = {"commit": head, "store": store, "clean": clean}
        output = MODES[config["mode"]](manifest, repo, work, ctx)
        if config["mode"] == "nup":
            offer_nx2navi(output.replace(" (UA).nup", " (UA) PC"))
        if head and store.get("_last_commit") != head:
            store.set("_last_commit", head)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print(f"\nГотово! Результат: {output}")
    print("Оригінальний дамп не змінено.")


if __name__ == "__main__":
    code = 0
    try:
        main(sys.argv[1:] if "_ua_argv" not in globals() else globals()["_ua_argv"])
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
