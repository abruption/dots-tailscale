"""The authenticated HTTP claim and the installed local parser share one contract."""
import contextlib
import importlib.machinery
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

from starlette.testclient import TestClient

from event_core import EventCore
from recovery_core import RecoveryCore
from server import create_app
import test_recovery_http as http_fixture

ORIGIN = http_fixture.ORIGIN
Provider = http_fixture.Provider


ROOT = Path(__file__).resolve().parents[2]
loader = importlib.machinery.SourceFileLoader('dots_ts_wire_contract_test', str(ROOT / 'dots-ts'))
spec = importlib.util.spec_from_loader(loader.name, loader)
local = importlib.util.module_from_spec(spec)
loader.exec_module(local)


class CoordinatedContractHTTP(unittest.TestCase):
    rpc = http_fixture.RecoveryHTTP.rpc
    observation = http_fixture.RecoveryHTTP.observation
    incident = http_fixture.RecoveryHTTP.incident
    call = http_fixture.RecoveryHTTP.call

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = float(int(time.time()))
        self.core = EventCore(self.tmp.name + '/events.sqlite', [])
        self.recovery = RecoveryCore(self.tmp.name + '/recovery.sqlite', [], 'alice',
                                     clock=lambda: self.now, local_coordination=True)
        self.client = TestClient(create_app(self.core, Provider(), ORIGIN, self.recovery), base_url=ORIGIN)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def test_wire_tools_discovery_and_claim_use_returned_argv_and_template_revision(self):
        tools = {item['name']: item for item in self.rpc('tools/list', {})['result']['tools']}
        description = tools['claim_recovery']['description']
        self.assertIn('command_argv', description)
        self.assertIn('execution_mode', description)
        self.assertNotIn('start.sh', description)
        instructions = self.rpc('server/discover', {})['result']['instructions']
        self.assertIn('execution_mode=coordinated', instructions)
        self.assertIn('instruction_template_revision=coordinated-recovery-r1', instructions)
        iid = self.incident()
        state = self.call('get_current_recovery', {'incident_id': iid})['result']['structuredContent']
        self.assertEqual(state['instruction_template_revision'], 'coordinated-recovery-r1')
        self.assertIn('error', self.call('claim_recovery', {'incident_id': iid}))
        claim = self.call('claim_recovery', {'incident_id': iid}, 'write')['result']['structuredContent']
        self.assertEqual(claim['execution_mode'], 'coordinated')
        self.assertEqual(claim['instruction_template_revision'], 'coordinated-recovery-r1')
        self.assertEqual(claim['local_contract']['request_id'], claim['claim_id'])
        self.assertEqual(claim['command_argv'][0], '/workspace/.dots-tailscale/dots-ts')
        self.assertEqual(claim['local_contract']['claim_expires_at'], int(claim['expires_at']))
        self.assertEqual(claim['local_contract']['observation_time'], int(self.now))

    def test_http_claim_argv_passes_real_local_dispatch_and_freshness_parser(self):
        iid = self.incident()
        claim = self.call('claim_recovery', {'incident_id': iid}, 'write')['result']['structuredContent']
        parsed = []
        def inspect(directory, runtime, options):
            local.recovery_external(options, int(self.now))
            parsed.append(options)
            return {'state': 'skipped_healthy', 'id': options.request_id, 'external_confirmation': 'pending'}
        for argv, success in ((claim['command_argv'], True),
                              ([value for value in claim['command_argv'] if value != '--ping-failed'], False)):
            with self.subTest(success=success):
                stdout = io.StringIO()
                with mock.patch.object(local, 'directories', return_value=(Path('/fixture'), Path('/fixture-runtime'))), \
                        mock.patch.object(local, 'recovery_run', side_effect=inspect), contextlib.redirect_stdout(stdout):
                    code = local.main(argv[1:])
                result = json.loads(stdout.getvalue())
                self.assertEqual(code, 0 if success else 1)
                self.assertEqual(result['state'], 'skipped_healthy' if success else 'rejected')
        self.assertEqual(len(parsed), 1)
        options = parsed[0]
        contract = claim['local_contract']
        self.assertEqual(options.actor, 'external')
        self.assertEqual(options.request_id, contract['request_id'])
        self.assertEqual(options.claim_expires_at, contract['claim_expires_at'])
        self.assertEqual(options.observation_time, contract['observation_time'])
        self.assertIs(options.ssh_failed, True)
        self.assertIs(options.ping_failed, True)


if __name__ == '__main__':
    unittest.main()
