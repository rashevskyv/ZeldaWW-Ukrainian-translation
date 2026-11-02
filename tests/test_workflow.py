"""Focused checks for isolated workflow staging."""

import os
import tempfile
import unittest
import subprocess
import sys
from pathlib import Path
from unittest import mock

import workflow


class WorkflowStagingTests(unittest.TestCase):
    def test_stage_copies_only_declared_inputs_and_leaves_source_readable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, target = root / "workspace", root / "stage"
            (source / "source").mkdir(parents=True)
            (source / "source" / "game.dat").write_bytes(b"source")
            (source / "translation").mkdir()
            (source / "translation" / "game.dat").write_bytes(b"translation")
            (source / "build").mkdir()
            (source / "build" / "old.iso").write_bytes(b"stale")

            workflow._copy_inputs(source, target, ("source", "translation"))

            self.assertEqual((target / "source" / "game.dat").read_bytes(), b"source")
            self.assertEqual((target / "translation" / "game.dat").read_bytes(), b"translation")
            self.assertFalse((target / "build").exists())
            self.assertEqual((source / "build" / "old.iso").read_bytes(), b"stale")

    def test_detached_copy_replaces_staged_hard_link_without_touching_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original, staged = root / "saved.arc", root / "staged.arc"
            newer = root / "newer.arc"
            original.write_bytes(b"saved")
            os.link(original, staged)
            newer.write_bytes(b"newer")

            workflow._copy_detached(newer, staged)

            self.assertEqual(original.read_bytes(), b"saved")
            self.assertEqual(staged.read_bytes(), b"newer")

    def test_newer_family_archive_wins(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gc, family = root / "gc.arc", root / "family.arc"
            staged = root / "staged.arc"
            gc.write_bytes(b"gc")
            family.write_bytes(b"family")
            os.utime(gc, ns=(1_000_000_000, 1_000_000_000))
            os.utime(family, ns=(2_000_000_000, 2_000_000_000))

            workflow._use_newer_archive(gc, family, staged)

            self.assertEqual(staged.read_bytes(), b"family")

    def test_newer_gc_archive_wins(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gc, family = root / "gc.arc", root / "family.arc"
            staged = root / "staged.arc"
            gc.write_bytes(b"gc")
            family.write_bytes(b"family")
            os.utime(gc, ns=(2_000_000_000, 2_000_000_000))
            os.utime(family, ns=(1_000_000_000, 1_000_000_000))

            workflow._use_newer_archive(gc, family, staged)

            self.assertEqual(staged.read_bytes(), b"gc")

    def test_equal_time_conflict_stops_before_build(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gc, family = root / "gc.arc", root / "family.arc"
            staged = root / "staged.arc"
            gc.write_bytes(b"gc")
            family.write_bytes(b"family")
            timestamp = 1_000_000_000
            os.utime(gc, ns=(timestamp, timestamp))
            os.utime(family, ns=(timestamp, timestamp))

            with self.assertRaises(RuntimeError):
                workflow._use_newer_archive(gc, family, staged)

            self.assertFalse(staged.exists())

    def test_all_archives_are_applied_to_gc_staging(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            family, translation = root / "family", root / "gc-translation"
            family.mkdir()
            family.joinpath("bmgres.arc").write_bytes(b"main")
            family.joinpath("bmgresh.arc").write_bytes(b"extra")

            workflow._apply_family_archives(family, translation)

            self.assertEqual((translation / "bmgres.arc").read_bytes(), b"main")
            self.assertEqual((translation / "bmgresh.arc").read_bytes(), b"extra")

    def test_publish_interrupted_copy_keeps_old_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, destination = root / "new", root / "build"
            source.mkdir()
            source.joinpath("new.iso").write_bytes(b"new")
            destination.mkdir()
            destination.joinpath("old.iso").write_bytes(b"old")

            def interrupted_copy(_source, target, **_kwargs):
                target.mkdir()
                target.joinpath("partial.iso").write_bytes(b"partial")
                raise KeyboardInterrupt

            with mock.patch.object(workflow.shutil, "copytree", side_effect=interrupted_copy):
                with self.assertRaises(KeyboardInterrupt):
                    workflow._publish_many([(source, destination)])

            self.assertEqual((destination / "old.iso").read_bytes(), b"old")
            self.assertFalse(list(root.glob(".*.workflow-new-*")))

    def test_second_publish_failure_rolls_back_both_outputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sources, destinations = [], []
            for name in ("gc", "hd"):
                source, destination = root / f"new-{name}", root / name
                source.mkdir()
                source.joinpath("new.txt").write_text(name)
                destination.mkdir()
                destination.joinpath("old.txt").write_text(name)
                sources.append(source)
                destinations.append(destination)

            original_rename = workflow._rename
            calls = 0

            def fail_second_install(source, destination):
                nonlocal calls
                calls += 1
                if calls == 4:
                    raise OSError("simulated second target failure")
                original_rename(source, destination)

            with mock.patch.object(workflow, "_rename", side_effect=fail_second_install):
                with self.assertRaisesRegex(OSError, "second target"):
                    workflow._publish_many(list(zip(sources, destinations)))

            for destination in destinations:
                self.assertEqual((destination / "old.txt").read_text(), destination.name)
                self.assertFalse((destination / "new.txt").exists())
            self.assertFalse(list(root.glob(".*.workflow-new-*")))
            self.assertFalse(list(root.glob(".*.workflow-old-*")))

    def test_workflow_lock_rejects_parallel_owner(self):
        with tempfile.TemporaryDirectory() as temporary:
            lock = Path(temporary) / "workflow.lock"
            code = ("import pathlib, sys, time, workflow\n"
                    "with workflow._workflow_lock(pathlib.Path(sys.argv[1]), timeout=2):\n"
                    " print('locked', flush=True)\n time.sleep(1)\n")
            process = subprocess.Popen([sys.executable, "-c", code, str(lock)],
                                       cwd=Path(__file__).resolve().parents[1],
                                       stdout=subprocess.PIPE, text=True)
            try:
                with process.stdout:
                    self.assertEqual(process.stdout.readline().strip(), "locked")
                    with self.assertRaises(workflow.WorkflowBusy):
                        with workflow._workflow_lock(lock, timeout=0.05):
                            pass
            finally:
                process.wait(timeout=3)

    def test_play_does_not_launch_if_build_fails(self):
        with mock.patch.object(workflow, "_workflow_lock"), \
                mock.patch.object(workflow, "_close_bluewake"), \
                mock.patch.object(workflow, "_build_locked", side_effect=RuntimeError("build failed")), \
                mock.patch.object(workflow.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(RuntimeError, "build failed"):
                workflow.play("bluewake")
        popen.assert_not_called()

    def test_bluewake_disc_rejects_wrong_main_dol(self):
        with tempfile.TemporaryDirectory() as temporary:
            disc = Path(temporary) / "wrong.iso"
            data = bytearray(0x500)
            data[:6] = b"GZLE01"
            data[0x1C:0x20] = (0xC2339F3D).to_bytes(4, "big")
            data[0x420:0x424] = (0x440).to_bytes(4, "big")
            disc.write_bytes(data)

            with self.assertRaisesRegex(RuntimeError, "main.dol"):
                workflow._validate_bluewake_disc(disc)

    def test_build_rejects_play_only_target(self):
        with self.assertRaisesRegex(ValueError, "does not support target"):
            workflow._validate_command("build", "bluewake")


if __name__ == "__main__":
    unittest.main()
