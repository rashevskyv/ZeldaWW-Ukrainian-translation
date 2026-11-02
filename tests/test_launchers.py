"""Run the actual Windows launchers against a harmless command recorder."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


@unittest.skipUnless(os.name == "nt", "CMD launchers require Windows")
class LauncherTests(unittest.TestCase):
    def test_all_launchers_forward_their_complete_command_from_another_directory(self):
        repo = Path(__file__).resolve().parents[1]
        commands = {
            "Play Ukrainian.bat": ["play", "bluewake"],
            "Play Dolphin.bat": ["play", "dolphin"],
            "Play Cemu.bat": ["play", "cemu"],
            "Build All.bat": ["build", "all"],
            "Build SwitchWaker.bat": ["build", "gc"],
            "Build Wii U and SwitchWakerHD.bat": ["build", "hd"],
        }
        with tempfile.TemporaryDirectory(prefix="windwaker launcher ") as temporary:
            recorder = Path(temporary)
            (recorder / "workflow.py").write_text(
                "import json, sys\nfrom pathlib import Path\n"
                "Path(__file__).with_name('argv.json').write_text(json.dumps(sys.argv[1:]))\n",
                encoding="utf-8",
            )
            env = dict(os.environ, WINDWAKER_TRANSLATION_REPO=str(recorder))
            for name, expected in commands.items():
                with self.subTest(launcher=name):
                    subprocess.run(
                        f'cmd.exe /d /s /c ""{repo / name}""',
                        cwd=recorder, env=env, check=True, capture_output=True, timeout=15,
                    )
                    self.assertEqual(json.loads((recorder / "argv.json").read_text()), expected)
