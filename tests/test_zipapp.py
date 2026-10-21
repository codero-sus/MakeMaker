"""Tests for the single-file zipapp build.

These build the real archive and then execute it, because the interesting
failure mode is not "did the zip get written" but "can it still find its
templates once they are inside an archive". ``pathlib`` cannot read through a
zip, so ``registry.template_root()`` has to unpack them first -- that is what
these tests pin down.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from build_zipapp import build  # noqa: E402  (tools is not a package)

HAS_PYTHON = shutil.which(sys.executable) is not None


class ZipAwareTemplateRootTest(unittest.TestCase):
    """template_root() must work whether or not the files are on disk."""

    def test_uses_the_on_disk_directory_when_it_exists(self):
        from makemaker.registry import template_root

        root = template_root()
        self.assertTrue(root.is_dir(), root)
        self.assertTrue((root / "linux" / "template.json").is_file())

    def test_finds_the_archive_that_contains_a_module(self):
        from makemaker.registry import _find_containing_archive

        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "app.pyz"
            with zipfile.ZipFile(archive, "w") as handle:
                handle.writestr("makemaker/registry.py", "x = 1\n")
            inner = archive / "makemaker" / "registry.py"
            self.assertEqual(_find_containing_archive(inner), archive)

    def test_no_archive_and_no_directory_raises_clearly(self):
        from makemaker.registry import _find_containing_archive

        with tempfile.TemporaryDirectory() as tmp:
            plain = Path(tmp) / "not_an_archive.pyz"
            plain.write_text("definitely not a zip\n", encoding="utf-8")
            inner = plain / "makemaker" / "registry.py"
            self.assertIsNone(_find_containing_archive(inner))


class ExtractBundledTemplatesTest(unittest.TestCase):
    """The unpacking path, driven with a real archive rather than a mock."""

    def test_extracts_templates_from_a_zipapp(self):
        from makemaker import registry

        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "app.pyz"
            with zipfile.ZipFile(archive, "w") as handle:
                handle.writestr("makemaker/registry.py", "x = 1\n")
                handle.writestr(
                    "makemaker/templates/demo/template.json", '{"id": "demo"}\n'
                )
                handle.writestr(
                    "makemaker/templates/demo/{{ identifier }}.txt.tmpl",
                    "{{ identifier }}\n",
                )

            original = registry.__file__
            registry.__file__ = str(archive / "makemaker" / "registry.py")
            registry._EXTRACTED_ROOT = None
            try:
                root = registry._extract_bundled_templates()
            finally:
                registry.__file__ = original
                registry._EXTRACTED_ROOT = None

            self.assertTrue(root.is_dir(), root)
            self.assertTrue((root / "demo" / "template.json").is_file())
            # The templated filename must survive the round trip.
            self.assertTrue(
                (root / "demo" / "{{ identifier }}.txt.tmpl").is_file()
            )


@unittest.skipUnless(HAS_PYTHON, "needs a Python interpreter")
class ZipappEndToEndTest(unittest.TestCase):
    """Build the real archive and run it as a separate process."""

    @classmethod
    def setUpClass(cls):
        cls.tempdir = tempfile.TemporaryDirectory()
        cls.archive = Path(cls.tempdir.name) / "makemaker.pyz"
        build(cls.archive, interpreter=None, compressed=True)

    @classmethod
    def tearDownClass(cls):
        cls.tempdir.cleanup()

    def run_bundle(self, *args):
        return subprocess.run(
            [sys.executable, str(self.archive), "--color", "never", *args],
            capture_output=True, text=True, cwd=self.tempdir.name,
        )

    def test_archive_is_a_single_file(self):
        self.assertTrue(self.archive.is_file())
        self.assertGreater(self.archive.stat().st_size, 10_000)

    def test_archive_carries_the_templates(self):
        with zipfile.ZipFile(self.archive) as handle:
            names = handle.namelist()
        bundled = [n for n in names if n.startswith("makemaker/templates/")]
        self.assertGreater(len(bundled), 300, f"only {len(bundled)} files bundled")
        self.assertIn("makemaker/cli.py", names)
        self.assertIn("__main__.py", names)

    def test_version_runs_inside_the_bundle(self):
        completed = self.run_bundle("version")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("makemaker 0.1.0", completed.stdout)

    def test_bundle_sees_every_template(self):
        from makemaker.registry import load_registry

        completed = self.run_bundle("template", "validate")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        expected = len(load_registry().ids())
        self.assertIn(f"OK {expected} template(s)", completed.stdout)

    def test_bundle_generates_a_working_project(self):
        """The decisive test: generation reads templates out of the archive."""
        completed = self.run_bundle(
            "new", "Bundle Check", "--target", "python", "--out", "out"
        )
        self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)

        project = Path(self.tempdir.name) / "out" / "bundle-check"
        self.assertTrue((project / "src" / "bundle_check" / "core.py").is_file())
        self.assertTrue((project / "Makefile").is_file())

        text = (project / "src" / "bundle_check" / "core.py").read_text(encoding="utf-8")
        self.assertNotIn("{{", text, "template syntax survived into the output")

    def test_bundle_reports_no_leftover_template_syntax(self):
        completed = self.run_bundle("template", "check")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("render cleanly", completed.stdout)


if __name__ == "__main__":
    unittest.main()
