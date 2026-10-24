from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ui/jupyter"))
from ca2_files import build_archive, workspace_path


class FileDownloadTests(unittest.TestCase):
    def test_folder_download_preserves_binary_nested_paths_and_avoids_duplicates(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "results/nested").mkdir(parents=True)
            (root / "results/model.pt").write_bytes(bytes(range(256)))
            (root / "results/nested/report.csv").write_text("name,value\nsample,42\n")
            with build_archive(root, ["results", "results/model.pt"]) as stream, zipfile.ZipFile(stream) as archive:
                self.assertEqual(sorted(archive.namelist()), ["results/model.pt", "results/nested/report.csv"])
                self.assertEqual(archive.read("results/model.pt"), bytes(range(256)))
                self.assertEqual(archive.testzip(), None)

    def test_empty_directory_can_be_downloaded(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "empty").mkdir()
            with build_archive(root, ["empty"]) as stream, zipfile.ZipFile(stream) as archive:
                self.assertEqual(archive.namelist(), ["empty/"])

    def test_paths_cannot_escape_the_workspace(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "workspace"
            root.mkdir()
            for name in ("../secret", "/etc/passwd", "sub/../../secret", "..\\secret"):
                with self.subTest(name=name), self.assertRaises(ValueError):
                    workspace_path(root, name)
            with self.assertRaises(ValueError):
                build_archive(root, [])


if __name__ == "__main__":
    unittest.main()
