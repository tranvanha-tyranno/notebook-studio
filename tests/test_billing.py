from datetime import datetime, timezone
from decimal import Decimal
import json
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from studio_billing import Billing, summary_payload
from test_accounts import fixture


class BillingTests(unittest.TestCase):
    def test_sdk_capitalized_credits_and_missing_adjustment(self):
        summary = Mock(start=datetime.now(timezone.utc), metered_cost=Decimal('20.55'), billed_cost=Decimal('0'),
                       adjustments={'Credits': Decimal('-20.52'), 'Free Storage': Decimal('-0.03')})
        self.assertEqual(summary_payload('workspace', summary)['credits_used'], '20.52')
        summary.adjustments = {}
        self.assertIsNone(summary_payload('workspace', summary)['credits_used'])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.accounts = fixture(self.temp.name)
        self.billing = Billing(self.accounts)
        self.account = self.accounts.resolve()
        self.cycle = datetime.now(timezone.utc).strftime('%Y-%m')

    def result(self, used='20.52', cycle=None):
        return Mock(returncode=0, stdout=json.dumps({'workspace':'alpha-workspace', 'cycle':cycle or self.cycle,
            'metered':'20.54725492', 'billed':'0', 'credits_used':used}))

    def test_real_cost_is_not_mistaken_for_remaining_balance_and_auth_is_pinned(self):
        with patch('studio_billing.subprocess.run', return_value=self.result()) as run:
            self.billing.fetch(self.account['id'])
        env = run.call_args.kwargs['env']
        self.assertEqual(env['MODAL_PROFILE'], 'alpha')
        self.assertNotIn('MODAL_TOKEN_SECRET', env)
        data = self.billing.public(self.accounts.resolve())
        self.assertIsNone(data['balance'])
        self.assertEqual(data['metered'], '20.54725492')

    def test_balance_reference_tracks_credits_only_shares_workspace_and_survives_restart(self):
        with patch('studio_billing.subprocess.run', return_value=self.result()):
            self.billing.set_balance(self.account['id'], '9.48')
        with patch('studio_billing.subprocess.run', return_value=self.result('22.52')):
            self.billing.fetch(self.account['id'])
        self.assertEqual(self.billing.public(self.accounts.resolve())['balance'], '7.48')
        alias = dict(self.accounts.resolve(), id='alias')
        self.assertEqual(Billing(self.accounts).public(alias)['balance'], '7.48')
        with patch('studio_billing.subprocess.run', return_value=self.result('35')):
            self.billing.fetch(self.account['id'])
        self.assertEqual(self.billing.public(alias)['balance'], '0')

    def test_cycle_rollover_or_credit_recalculation_invalidates_balance(self):
        with patch('studio_billing.subprocess.run', return_value=self.result()):
            self.billing.set_balance(self.account['id'], '9.48')
        with patch('studio_billing.subprocess.run', return_value=self.result('19')):
            self.billing.fetch(self.account['id'])
        self.assertIsNone(self.billing.public(self.accounts.resolve())['balance'])
        with patch('studio_billing.subprocess.run', return_value=self.result('21', '2000-01')):
            self.billing.fetch(self.account['id'])
        self.assertIsNone(self.billing.public(self.accounts.resolve())['balance'])

    def test_missing_credit_field_failure_and_invalid_input_never_become_zero(self):
        with patch('studio_billing.subprocess.run', return_value=self.result(None)):
            with self.assertRaises(ValueError):
                self.billing.set_balance(self.account['id'], '5')
        for value in ('NaN', 'Infinity', '-1', 'invalid'):
            with self.assertRaises(ValueError):
                self.billing.set_balance(self.account['id'], value)
        with patch('studio_billing.subprocess.run', return_value=Mock(returncode=1, stdout='token_secret=private')):
            self.billing.refresh()
        public = self.billing.public(self.accounts.resolve())
        self.assertIsNotNone(public['error'])
        self.assertIsNone(public['balance'])
        self.assertNotIn('private', json.dumps(public))

    def test_refresh_queries_shared_workspace_once_and_retains_cache_on_timeout(self):
        for record in self.accounts.records.values():
            record['workspace'] = 'alpha-workspace'
        with patch('studio_billing.subprocess.run', return_value=self.result()) as run:
            self.billing.refresh()
        self.assertEqual(run.call_count, 1)
        with patch('studio_billing.subprocess.run', side_effect=subprocess.TimeoutExpired('billing', 45)):
            self.billing.refresh()
        data = self.billing.public(self.accounts.resolve())
        self.assertEqual(data['metered'], '20.54725492')
        self.assertTrue(data['error'])


if __name__ == '__main__':
    unittest.main()
