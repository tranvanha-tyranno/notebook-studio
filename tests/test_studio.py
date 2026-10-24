from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch, MagicMock
from collections import deque
import urllib.error
import urllib.request

from studio import Handler, LocalServer, Manager
from studio_pricing import SNAPSHOT, hourly
from studio_project import package_list, stage_project, validate_notebook
from test_accounts import fixture


class ProjectTests(unittest.TestCase):
    def test_stage_only_chosen_notebook_and_code(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            project = root / "project"
            project.mkdir()
            notebook = project / "my.ipynb"
            notebook.write_text(json.dumps({"cells": []}))
            (project / "model.py").write_text("x = 1")
            (project / ".env").write_text("secret")
            (project / ".private.py").write_text("secret")
            (project / "data").mkdir()
            (project / "data/private.py").write_text("private")
            (project / "photo.png").write_bytes(b"data")
            stage_project(project, notebook, root / "staged")
            names = {p.relative_to(root / "staged").as_posix() for p in (root / "staged").rglob("*") if p.is_file()}
            self.assertEqual(names, {"my.ipynb", "model.py"})
            with self.assertRaises(ValueError):
                validate_notebook(project, root / "staged/my.ipynb")

    def test_packages_are_specs_not_commands_or_urls(self):
        self.assertEqual(package_list("matplotlib==3.10.8 numpy"), ["matplotlib==3.10.8", "numpy"])
        for value in ("--index-url", "https://example.org/code.whl", "foo;bar", "$(secret)"):
            with self.assertRaises(ValueError):
                package_list(value)

    def test_estimate_includes_cpu_and_ram(self):
        self.assertAlmostEqual(hourly(SNAPSHOT, "T4", 4, 16), 0.906912)
        self.assertGreater(hourly(SNAPSHOT, "L4", 4, 16), hourly(SNAPSHOT, "T4", 4, 16))

    def test_start_builds_independent_project_environment_and_blocks_duplicate(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            notebook = root / "hello.ipynb"
            notebook.write_text(json.dumps({"cells": []}))
            manager = Manager(root / "store", accounts=fixture(folder))
            process = MagicMock()
            with patch("studio.subprocess.Popen", return_value=process) as start, patch("studio.threading.Thread"):
                result = manager.start({"folder": str(root), "notebook": str(notebook), "gpu": "L4", "cpu": 2, "ram": 8})
                self.assertEqual(result["gpu"], "L4")
                environment = start.call_args.kwargs["env"]
                self.assertEqual(environment["CA2_RAM_GIB"], "8")
                self.assertEqual(environment["CA2_STUDIO_VOLUME"], "notebook-studio")
                self.assertEqual(environment["CA2_NOTEBOOK_RELATIVE"], "hello.ipynb")
                self.assertNotIn("shell", start.call_args.kwargs)
                with self.assertRaises(ValueError):
                    manager.start({"folder": str(root), "notebook": str(notebook)})
                other = root / "another-project"
                other.mkdir()
                other_notebook = other / "analysis.ipynb"
                other_notebook.write_text(json.dumps({"cells": []}))
                second = manager.start({"folder": str(other), "notebook": str(other_notebook), "gpu": "T4"})
                self.assertNotEqual(second["project_key"], result["project_key"])
                self.assertEqual(len(manager.status()["sessions"]), 2)


class LocalHttpTests(unittest.TestCase):
    def test_local_host_and_csrf_guards_and_price_read(self):
        with tempfile.TemporaryDirectory() as folder:
            server = LocalServer(("127.0.0.1", 0), Handler)
            server.manager = Manager(Path(folder), accounts=fixture(folder))
            server.token = "test-token"
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            try:
                with self.assertRaises(OSError):
                    LocalServer(("127.0.0.1", server.server_port), Handler)
                with urllib.request.urlopen(base + "/api/status") as response:
                    self.assertIn("prices", json.load(response))
                for headers in ({"Host": "attacker.example"}, {"Sec-Fetch-Site": "cross-site"}):
                    with self.assertRaises(urllib.error.HTTPError) as rejected:
                        urllib.request.urlopen(urllib.request.Request(base + "/api/status", headers=headers))
                    self.assertEqual(rejected.exception.code, 403)
                request = urllib.request.Request(base + "/api/start", data=b"{}", headers={"Origin": base})
                with self.assertRaises(urllib.error.HTTPError) as rejected:
                    urllib.request.urlopen(request)
                self.assertEqual(rejected.exception.code, 403)
                # The new file-save endpoint must also reject requests without the local token.
                with self.assertRaises(urllib.error.HTTPError) as rejected:
                    urllib.request.urlopen(urllib.request.Request(base + "/api/save-local", data=b'{}'))
                self.assertEqual(rejected.exception.code, 403)
                for endpoint in ('login', 'logout', 'default', 'reconnect', 'cancel'):
                    with self.assertRaises(urllib.error.HTTPError) as rejected:
                        urllib.request.urlopen(urllib.request.Request(base + '/api/accounts/' + endpoint, data=b'{}'))
                    self.assertEqual(rejected.exception.code, 403)
                for endpoint in ('refresh', 'balance'):
                    with self.assertRaises(urllib.error.HTTPError) as rejected:
                        urllib.request.urlopen(urllib.request.Request(base + '/api/billing/' + endpoint, data=b'{}'))
                    self.assertEqual(rejected.exception.code, 403)
                with urllib.request.urlopen(base + "/app.js") as response:
                    self.assertEqual(response.headers["Cross-Origin-Resource-Policy"], "same-origin")
                    self.assertIn(b"test-token", response.read())
            finally:
                server.shutdown()
                server.server_close()
                thread.join()


class LocalSaveTests(unittest.TestCase):
    def test_worker_preserves_completed_transfer_across_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            manager = Manager(temp, accounts=fixture(temp))
            manager.transfers['job'] = {'id': 'job', 'session_id': 's', 'state': 'saving',
                'destination': temp, 'files': 0, 'bytes': 0, 'message': ''}
            process = MagicMock()
            process.stdout = [json.dumps({'progress': 1, 'file': 'report.csv', 'bytes': 21}),
                              json.dumps({'result': {'destination': temp, 'files': 1, 'bytes': 21}})]
            process.wait.return_value = 0
            with patch.object(manager, '_transfer_process', return_value=process):
                manager._save_worker('job', {'action': 'save'})
            reloaded = Manager(temp, accounts=fixture(temp))
            self.assertEqual(reloaded.transfers['job']['state'], 'complete')
            self.assertEqual(reloaded.transfers['job']['files'], 1)

    def test_reject_traversal_before_starting_transfer(self):
        with tempfile.TemporaryDirectory() as temp:
            manager = Manager(temp, accounts=fixture(temp))
            with patch.object(manager, '_transfer_process') as process:
                with self.assertRaises(ValueError):
                    manager.save_local('unused', ['../private'])
                process.assert_not_called()


if __name__ == "__main__":
    unittest.main()
