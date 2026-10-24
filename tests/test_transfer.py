import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock

from modal.volume import FileEntryType
from studio_transfer import list_files, relative_path, save_files


class TransferTests(unittest.TestCase):
    def entry(self, path, size=0, directory=False):
        return MagicMock(path=path, size=size, mtime=0,
                         type=FileEntryType.DIRECTORY if directory else FileEntryType.FILE)

    def test_direct_file_save_keeps_binary_and_records_hash(self):
        payload = bytes(range(256)) * 100
        volume = MagicMock()
        volume.listdir.return_value = [self.entry('projects/demo/results/model.pt', len(payload))]
        volume.read_file.return_value = iter([payload[:300], payload[300:]])
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / 'new-download'
            result = save_files(volume, 'projects/demo', ['results/model.pt'], target)
            self.assertEqual((target / 'results/model.pt').read_bytes(), payload)
            self.assertEqual(result['files'], 1)
            manifest = json.loads((target / '_download_manifest.json').read_text())
            self.assertEqual(manifest[0]['sha256'], hashlib.sha256(payload).hexdigest())
            with self.assertRaises(FileExistsError):
                save_files(volume, 'projects/demo', ['results/model.pt'], target)

    def test_partial_or_changed_file_never_appears_as_complete(self):
        volume = MagicMock()
        volume.listdir.return_value = [self.entry('projects/demo/bad.csv', 30)]
        volume.read_file.return_value = iter([b'only 7'])
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / 'download'
            with self.assertRaises(ValueError):
                save_files(volume, 'projects/demo', ['bad.csv'], target)
            self.assertFalse((target / 'bad.csv').exists())
            self.assertFalse(list(target.rglob('*.partial')))

    def test_paths_and_returned_entries_cannot_escape_project(self):
        for path in ('../secret', '/root/file', 'C:/file', '..\\secret', 'file:stream'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                relative_path(path)
        volume = MagicMock()
        volume.listdir.return_value = [self.entry('other-project/file.csv', 1)]
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / 'download'
            with self.assertRaises(ValueError):
                save_files(volume, 'projects/demo', ['results'], target)
            self.assertFalse(target.exists())

    def test_listing_relative_paths_and_folder_sort(self):
        volume = MagicMock()
        volume.listdir.return_value = [self.entry('projects/demo/z.csv', 1),
                                       self.entry('projects/demo/results', directory=True)]
        result = list_files(volume, 'projects/demo')
        self.assertEqual([item['path'] for item in result], ['results', 'z.csv'])


if __name__ == '__main__':
    unittest.main()
