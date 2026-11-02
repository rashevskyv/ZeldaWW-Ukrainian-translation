"""Safe, local checks for patch packaging and Git publication preflight."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import publish_translation as publisher


class PublishTranslationTests(unittest.TestCase):
    def test_remote_match_requires_exact_github_host_and_repository(self):
        self.assertTrue(publisher._matches_expected_remote(
            "https://github.com/rashevskyv/ZeldaWW-Ukrainian-translation.git"))
        self.assertTrue(publisher._matches_expected_remote(
            "git@github.com:rashevskyv/ZeldaWW-Ukrainian-translation"))
        self.assertFalse(publisher._matches_expected_remote(
            "https://evilgithub.com/rashevskyv/ZeldaWW-Ukrainian-translation.git"))
        self.assertFalse(publisher._matches_expected_remote(
            "https://github.com/other-user/ZeldaWW-Ukrainian-translation.git"))

    def test_unchanged_package_still_retries_push(self):
        remote = subprocess.CompletedProcess([], 0,
            "https://github.com/rashevskyv/ZeldaWW-Ukrainian-translation.git", "")
        with mock.patch.object(publisher, "_git", return_value=remote) as git:
            publisher._commit_and_push(Path("."), has_patch_changes=False)

        commands = [call.args[1:] for call in git.call_args_list]
        self.assertIn(("push", "origin", "HEAD"), commands)
        self.assertNotIn(("commit", "-m", "Update Wind Waker translation patches"), commands)

    def test_xdelta_patch_round_trips_synthetic_files(self):
        try:
            xdelta = publisher._configured_file("xdelta")
        except publisher.PublishError as error:
            self.skipTest(str(error))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, target, patch, restored = (root / name for name in ("source", "target", "change.xdelta", "restored"))
            source.write_bytes((b"The Wind Waker source text. " * 4096) + b"old value")
            target.write_bytes((b"The Wind Waker source text. " * 4096) + b"new value")

            publisher._xdelta(xdelta, source, target, patch)
            subprocess.run([str(xdelta), "-d", "-s", str(source), str(patch), str(restored)],
                           check=True, capture_output=True)

            self.assertEqual(restored.read_bytes(), target.read_bytes())
            self.assertLess(patch.stat().st_size, target.stat().st_size)

    def test_different_push_url_is_rejected_despite_correct_fetch_origin(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            def run(*args):
                subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)

            run("init", "-q")
            run("remote", "add", "origin", "https://github.com/rashevskyv/ZeldaWW-Ukrainian-translation.git")
            run("config", "remote.origin.pushurl", "https://github.com/rashevskyv/another-repository.git")
            with self.assertRaisesRegex(publisher.PublishError, "push URL"):
                publisher._verify_remote_and_clean(repo)
            with self.assertRaisesRegex(publisher.PublishError, "push URL"):
                publisher._commit_and_push(repo, has_patch_changes=False)

    def test_dirty_unrelated_file_fails_before_git_staging(self):
        git = shutil.which("git")
        if not git:
            self.skipTest("git is unavailable")
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            def run(*args):
                return subprocess.run([git, *args], cwd=repo, check=True, capture_output=True, text=True)

            run("init", "-q")
            run("config", "user.name", "Patch Test")
            run("config", "user.email", "patch-test@example.invalid")
            (repo / "README.md").write_text("base\n", encoding="utf-8")
            run("add", "README.md")
            run("commit", "-qm", "base")
            run("remote", "add", "origin", "https://github.com/rashevskyv/ZeldaWW-Ukrainian-translation.git")
            (repo / "unrelated.txt").write_text("keep me\n", encoding="utf-8")

            with self.assertRaisesRegex(publisher.PublishError, "unrelated.txt"):
                publisher._verify_remote_and_clean(repo)

            self.assertEqual(run("diff", "--cached", "--name-only").stdout, "")
            self.assertTrue((repo / "unrelated.txt").is_file())

    def test_package_replacement_refuses_unlisted_user_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package, stage = root / "patches", root / "stage"
            package.mkdir()
            (package / "manifest.json").write_text(json.dumps({
                "generated_by": "publish_translation.py", "files": []}), encoding="utf-8")
            (package / "README.txt").write_text("owned", encoding="utf-8")
            (package / "notes.txt").write_text("user data", encoding="utf-8")
            stage.mkdir()

            with self.assertRaisesRegex(publisher.PublishError, "outside the generated package"):
                publisher._replace_package(stage, package)

            self.assertEqual((package / "notes.txt").read_text(encoding="utf-8"), "user data")

    @unittest.skipUnless(os.name == "nt", "CMD launchers require Windows")
    def test_publish_batch_launches_prepare_script_from_workspace(self):
        repo = Path(__file__).resolve().parents[1]
        launcher = repo / "Publish Translation.bat"
        workspace_launcher = Path(r"E:\Emulators\RomHacking\ZELDA\Wind Waker\Publish Translation.bat")
        with tempfile.TemporaryDirectory(prefix="translation publisher ") as temporary:
            recorder = Path(temporary)
            (recorder / "publish_translation.py").write_text(
                "import json, sys\nfrom pathlib import Path\n"
                "Path(__file__).with_name('argv.json').write_text(json.dumps(sys.argv[1:]))\n",
                encoding="utf-8",
            )
            env = dict(os.environ, WINDWAKER_TRANSLATION_REPO=str(recorder))
            subprocess.run(f'cmd.exe /d /s /c ""{launcher}""', cwd=recorder, env=env,
                           check=True, capture_output=True, timeout=15)
            self.assertEqual(json.loads((recorder / "argv.json").read_text()), [])
            self.assertEqual(launcher.read_bytes(), workspace_launcher.read_bytes())


if __name__ == "__main__":
    unittest.main()
