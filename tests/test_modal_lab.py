import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from modal_lab import prepare_workspace


class WorkspaceTests(unittest.TestCase):
    def test_reopen_preserves_browser_edits_and_refresh_backs_them_up(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'CA2_PROJECT_KEY': '1234567890abcdef'}):
            root = Path(folder)
            source = root / 'source'
            source.mkdir()
            (source / 'hello.ipynb').write_text('local original')
            options = {'workspace_root': root / 'work', 'source_root': source}
            workspace = prepare_workspace(**options)
            notebook = workspace / 'hello.ipynb'
            self.assertEqual(notebook.read_text(), 'local original')
            notebook.write_text('browser edits')
            (workspace / 'results' / 'checkpoint.pt').write_bytes(b'saved model')
            (source / 'hello.ipynb').write_text('local replacement')
            prepare_workspace(**options)
            self.assertEqual(notebook.read_text(), 'browser edits')
            prepare_workspace(refresh_local=True, **options)
            self.assertEqual(notebook.read_text(), 'local replacement')
            backups = list((workspace / 'backups').glob('*/hello.ipynb'))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(), 'browser edits')
            self.assertEqual((workspace / 'results' / 'checkpoint.pt').read_bytes(), b'saved model')

    def test_project_key_and_task_cannot_escape_workspace(self):
        for key in ('../other', '', 'x' * 16):
            with patch.dict(os.environ, {'CA2_PROJECT_KEY': key}):
                with self.assertRaises(ValueError):
                    prepare_workspace()
        with patch.dict(os.environ, {'CA2_PROJECT_KEY': '1234567890abcdef'}):
            with self.assertRaises(ValueError):
                prepare_workspace('../other')


if __name__ == '__main__':
    unittest.main()
