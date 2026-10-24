import json
import os
import subprocess
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from studio_accounts import Accounts, clean_modal_env
from studio import Manager, PYTHON


def fixture(folder):
    path = Path(folder) / 'modal.toml'
    path.write_text('[alpha]\ntoken_id="id-a"\ntoken_secret="secret-a"\nactive=true\n'
                    '[beta]\ntoken_id="id-b"\ntoken_secret="secret-b"\n', encoding='utf-8')
    return Accounts(Path(folder) / 'studio', PYTHON, config_path=path)


class AccountsTests(unittest.TestCase):
    def test_discovery_exposes_metadata_without_credentials(self):
        with tempfile.TemporaryDirectory() as temp:
            accounts = fixture(temp)
            state = accounts.status()
            self.assertEqual(len(state['items']), 2)
            self.assertNotIn('secret-a', json.dumps(state))
            self.assertNotIn('token_id', json.dumps(state))
            self.assertNotIn('config', state['items'][0])
            self.assertEqual(accounts.resolve()['profile'], 'alpha')

    def test_default_change_is_local_and_does_not_modify_cli_or_environment(self):
        with tempfile.TemporaryDirectory() as temp:
            accounts = fixture(temp)
            original = accounts.external_config.read_bytes()
            beta = next(r for r in accounts.records.values() if r['profile'] == 'beta')
            with patch.dict(os.environ, {'MODAL_PROFILE': 'do-not-change', 'MODAL_TOKEN_SECRET': 'inherited-secret'}):
                accounts.set_default(beta['id'])
                env = accounts.environment(beta)
                self.assertEqual(env['MODAL_PROFILE'], 'beta')
                self.assertNotIn('MODAL_TOKEN_SECRET', env)
                self.assertEqual(os.environ['MODAL_PROFILE'], 'do-not-change')
                self.assertEqual(os.environ['MODAL_TOKEN_SECRET'], 'inherited-secret')
            self.assertEqual(accounts.external_config.read_bytes(), original)
            self.assertEqual(Accounts(accounts.store, PYTHON, config_path=accounts.external_config).resolve()['profile'], 'beta')

    def test_cli_logout_disconnects_only_studio_and_can_reconnect(self):
        with tempfile.TemporaryDirectory() as temp:
            accounts = fixture(temp)
            alpha = accounts.resolve()
            original = accounts.external_config.read_bytes()
            accounts.logout(alpha['id'])
            with self.assertRaises(ValueError):
                accounts.resolve(alpha['id'])
            self.assertEqual(accounts.external_config.read_bytes(), original)
            accounts.discover()  # Polling/discovery must not silently log the user back in.
            self.assertFalse(accounts.status()['items'][0]['connected'])
            accounts.reconnect(alpha['id'])
            self.assertEqual(accounts.resolve(alpha['id'])['profile'], 'alpha')

    def test_managed_logout_deletes_only_its_own_credentials(self):
        with tempfile.TemporaryDirectory() as temp:
            accounts = fixture(temp)
            path = accounts.folder / 'managed.toml'
            path.write_text('[studio]\ntoken_secret="managed-secret"\n')
            accounts.records['managed'] = {'id': 'managed', 'label': 'new account', 'source': 'Studio',
                                          'config': str(path), 'profile': 'studio', 'enabled': True}
            original = accounts.external_config.read_bytes()
            accounts.logout('managed')
            self.assertFalse(path.exists())
            self.assertEqual(accounts.external_config.read_bytes(), original)

    def test_login_uses_separate_config_and_cancel_stops_waiting_process(self):
        with tempfile.TemporaryDirectory() as temp:
            accounts = fixture(temp)
            process = MagicMock()
            process.poll.return_value = None
            with patch('studio_accounts.subprocess.Popen', return_value=process) as start, patch('studio_accounts.threading.Thread'):
                state = accounts.start_login('Second account')
                env = start.call_args.kwargs['env']
                self.assertNotEqual(env['MODAL_CONFIG_PATH'], str(accounts.external_config))
                self.assertEqual(env['MODAL_PROFILE'], 'studio')
                self.assertNotIn('MODAL_TOKEN_SECRET', env)
                self.assertIn('--no-activate', start.call_args.args[0])
                self.assertNotIn('process', state['login'])
                with self.assertRaises(ValueError):
                    accounts.start_login('Another')
                accounts.cancel_login()
                process.terminate.assert_called_once()
                self.assertEqual(accounts.status()['login']['state'], 'cancelled')

    def test_login_parser_never_returns_raw_output_or_secrets(self):
        with tempfile.TemporaryDirectory() as temp:
            accounts = fixture(temp)
            config = accounts.folder / 'new.toml'
            config.write_text('[studio]\ntoken_secret="not-for-ui"\n')
            process = MagicMock()
            process.stdout = ['token_secret=not-for-ui\n', 'https://modal.com/token-flow/test\n', 'Enter this code: ABC-123\n']
            process.wait.return_value = 0
            login = {'id': 'new', 'label': 'New', 'state': 'waiting', 'process': process, 'config': str(config)}
            record = {'id':'new','label':'New','config':str(config),'profile':'studio','source':'Studio','enabled':True}
            accounts.login = login
            with patch.object(accounts, 'verify'):
                accounts._login_worker(login, record)
            public = accounts.status()
            self.assertEqual(public['login']['state'], 'complete')
            self.assertNotIn('not-for-ui', json.dumps(public))
            self.assertIsNone(public['login']['url'])


class MultiAccountTests(unittest.TestCase):
    def test_launch_command_is_accepted_by_actual_modal_entrypoint_parser(self):
        with tempfile.TemporaryDirectory() as temp:
            accounts = fixture(temp)
            manager = Manager(Path(temp) / 'manager', accounts=accounts)
            notebook = Path(temp) / 'test.ipynb'
            notebook.write_text('{"cells": []}')
            with patch('studio.subprocess.Popen') as start, patch('studio.threading.Thread'):
                manager.start({'folder': temp, 'notebook': str(notebook), 'account_id': accounts.default})
                command = start.call_args.args[0]
                environment = start.call_args.kwargs['env']
            self.assertLess(command.index('--profile'), command.index('modal_lab.py'))
            # --help imports the real app and exercises Modal's two-level parser
            # without authenticating or allocating a GPU.
            result = subprocess.run([*command, '--help'], cwd=Path(__file__).resolve().parents[1],
                env=environment, capture_output=True, text=True, encoding='utf-8', timeout=40)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('--minutes', result.stdout)

    def test_same_project_can_run_on_two_accounts_and_all_operations_remain_bound(self):
        with tempfile.TemporaryDirectory() as temp:
            accounts = fixture(temp)
            manager = Manager(Path(temp) / 'manager', accounts=accounts)
            notebook = Path(temp) / 'test.ipynb'
            notebook.write_text('{"cells": []}')
            alpha, beta = list(accounts.records)
            options = {'folder': temp, 'notebook': str(notebook)}
            process = MagicMock()
            with patch('studio.subprocess.Popen', return_value=process) as start, patch('studio.threading.Thread'):
                first = manager.start({**options, 'account_id': alpha})
                first_env = dict(start.call_args.kwargs['env'])
                second = manager.start({**options, 'account_id': beta})
                second_env = dict(start.call_args.kwargs['env'])
                self.assertEqual(first_env['MODAL_PROFILE'], 'alpha')
                self.assertEqual(second_env['MODAL_PROFILE'], 'beta')
                self.assertEqual(first['project_key'], second['project_key'])
                accounts.set_default(beta)
                self.assertEqual(manager._transfer_options(first['id'], 'list')['account_id'], alpha)
                self.assertEqual(manager._transfer_options(second['id'], 'list')['account_id'], beta)
                with self.assertRaises(ValueError):
                    manager.logout_account(alpha)
                with self.assertRaises(ValueError):
                    manager.start({**options, 'account_id': alpha})
                # Aliases verified as one workspace cannot write the same project at once.
                accounts.records[alpha]['workspace'] = 'shared'
                accounts.records[beta]['workspace'] = 'shared'
                manager.sessions[second['id']]['state'] = 'stopped'
                with self.assertRaises(ValueError):
                    manager.start({**options, 'account_id': beta})

    def test_logout_is_blocked_during_transfer_and_legacy_sessions_bind_once(self):
        with tempfile.TemporaryDirectory() as temp:
            accounts = fixture(temp)
            manager = Manager(Path(temp) / 'manager', accounts=accounts)
            alpha = accounts.resolve()
            manager.sessions['legacy'] = {'id':'legacy','profile':'alpha','state':'stopped',
                'started':0,'logs':[],'project_key':'1234567890abcdef','account_id':alpha['id']}
            manager.transfers['saving'] = {'session_id':'legacy','state':'saving'}
            with self.assertRaises(ValueError):
                manager.logout_account(alpha['id'])
            manager.transfers.clear()
            manager.sessions['legacy'].pop('account_id')
            manager.persist()
            restored = Manager(manager.store, accounts=accounts)
            self.assertEqual(restored.sessions['legacy']['account_id'],alpha['id'])


if __name__ == '__main__':
    unittest.main()
