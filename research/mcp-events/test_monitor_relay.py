import datetime as dt
import json
import os
from pathlib import Path
import plistlib
import subprocess
import ssl
import socket
import time
import multiprocessing
import monitor_relay
from unittest.mock import patch
from bounded_network import bounded_call
import http.client
import io
from contextlib import redirect_stderr
import tempfile
import unittest
from types import SimpleNamespace

from monitor_relay import (sanitize, ssh_argv, transmit, step, save_private, read_private,
    https_target, https_transmit, update_health, emit_health_transition, ssh_failure, read_token, bounded_ssh_run, DeadlineSocket, capture_runtime_paths, runtime_diagnostics)

NOW = 1800000000.0

def sample(offset=0):
    return {'observed_at': dt.datetime.fromtimestamp(NOW+offset, dt.timezone.utc).isoformat(),
            'state': 'external_unreachable',
            'ssh': {'command_succeeded': False, 'probe_valid': False,
                    'exit_code': 255, 'error_class': 'timeout', 'stderr': 'SECRET',
                    'remote': {'boot_id': 'PRIVATE'}},
            'peer_ping': {'responded': False, 'exit_code': 1, 'route': 'PRIVATE'},
            'alerts': ['PRIVATE']}

class RelayTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/'observation.json'
        self.config = {'observation_file': str(self.path), 'ssh_host': 'operator@100.1.2.3',
                       'remote_python': '/app/.venv/bin/python', 'remote_server': '/app/server.py',
                       'remote_config': '/private/config.json'}
        self.path.write_text(json.dumps(sample()))

    def test_minimization(self):
        clean = sanitize(sample(), NOW)
        self.assertEqual(set(clean), {'observed_at', 'state', 'ssh', 'peer_ping', 'external_health'})
        self.assertNotIn('PRIVATE', json.dumps(clean))
        self.assertNotIn('SECRET', json.dumps(clean))

    def test_time_boundaries(self):
        for offset in (-151, 6):
            with self.assertRaises(ValueError): sanitize(sample(offset), NOW)
        for offset in (-150, 5): sanitize(sample(offset), NOW)

    def test_naive_timestamp_rejected(self):
        row = sample(); row['observed_at'] = '2026-10-22T05:00:00'
        with self.assertRaises(ValueError): sanitize(row, NOW)

    def test_type_confusion_rejected(self):
        for field, value in [('command_succeeded', 'true'), ('exit_code', True), ('error_class', 'secret text')]:
            row = sample(); row['ssh'][field] = value
            with self.assertRaises(ValueError): sanitize(row, NOW)

    def test_retry_same_observation_after_failure(self):
        sent = []
        def fail(c, row): sent.append(row); return False
        state, outcome = step(self.config, {}, NOW, fail)
        self.assertEqual((state, outcome), ({}, 'transport_failed'))
        def success(c, row): sent.append(row); return True
        state, outcome = step(self.config, state, NOW+15, success)
        self.assertEqual(outcome, 'delivered')
        self.assertEqual(sent[0], sent[1])
        self.assertEqual(state['delivered_at'], sample()['observed_at'])
        self.assertEqual(step(self.config, state, NOW+30, success)[1], 'already_delivered')
        self.assertEqual(len(sent), 2)

    def test_stale_never_sent(self):
        self.assertEqual(step(self.config, {}, NOW+151, lambda *_: self.fail('must not send'))[1], 'observation_unavailable')

    def test_old_sample_not_replayed(self):
        state = {'delivered_at': sample(1)['observed_at']}
        self.assertEqual(step(self.config, state, NOW, lambda *_: self.fail('must not send'))[1], 'already_delivered')

    def test_ssh_strict_and_payload_only_stdin(self):
        def run(argv, **kwargs):
            self.assertIn('StrictHostKeyChecking=yes', argv)
            self.assertIn('BatchMode=yes', argv)
            self.assertEqual(kwargs['timeout'], 20)
            self.assertEqual(kwargs['stderr'], subprocess.PIPE)
            self.assertEqual(json.loads(kwargs['input']), sanitize(sample(), NOW))
            self.assertIn('ingest-observation', argv[-1])
            return SimpleNamespace(returncode=0)
        self.assertTrue(transmit(self.config, sanitize(sample(), NOW), run))

    def test_host_argument_injection_rejected(self):
        self.config['ssh_host'] = '-oProxyCommand=evil'
        with self.assertRaises(ValueError): ssh_argv(self.config)

    def test_remote_path_shell_quoted(self):
        self.config['remote_config'] = '/private/a;echo secret'
        self.assertIn("'/private/a;echo secret'", ssh_argv(self.config)[-1])

    def test_timeout_does_not_succeed(self):
        def run(*a, **kw): raise subprocess.TimeoutExpired('ssh', 20)
        self.assertFalse(transmit(self.config, sample(), run))

    def test_private_atomic_state_and_symlink_rejection(self):
        p = Path(self.tmp.name)/'state.json'
        save_private(p, {'delivered_at': sample()['observed_at']})
        self.assertEqual(p.stat().st_mode & 0o777, 0o600)
        self.assertEqual(read_private(p)['delivered_at'], sample()['observed_at'])
        link = Path(self.tmp.name)/'link'; link.symlink_to(p)
        with self.assertRaises(OSError): read_private(link)

    def https_config(self):
        token = Path(self.tmp.name)/'monitor-token'
        token.write_text('a'*48 + '\n'); token.chmod(0o600)
        return dict(self.config, transport='https', ingest_url='https://recovery.example.com/monitor/observations', bearer_token_file=str(token))

    def connection(self, status=200, payload=None, error=None):
        row = sanitize(sample(), NOW)
        content = json.dumps({'accepted': True, 'observed_at': row['observed_at']}).encode() if payload is None else payload
        class Response:
            def __init__(self):
                self.status = status; self.stream = io.BytesIO(content)
            def read1(self, size): return self.stream.read(size)
            def close(self): self.stream.close()
        response = Response()
        class Connection:
            sock = SimpleNamespace(settimeout=lambda *a: None)
            closed = False
            def connect(self): pass
            def request(self, method, path, **kw):
                self.request_data = (method, path, kw)
                if error: raise error
            def getresponse(self): return response
            def close(self): self.closed = True
        conn = Connection()
        def factory(host, port, **kw):
            self.assertEqual((host, port), ('recovery.example.com', 443))
            self.assertTrue(kw['context'].check_hostname)
            self.assertEqual(kw['context'].verify_mode, ssl.CERT_REQUIRED)
            self.assertLessEqual(kw['timeout'], 15)
            return conn
        return conn, factory, row

    def test_https_verified_delivery_and_private_header(self):
        conn, factory, row = self.connection()
        result = https_transmit(self.https_config(), row, factory)
        self.assertTrue(result)
        method, path, kw = conn.request_data
        self.assertEqual((method, path), ('POST', '/monitor/observations'))
        self.assertEqual(kw['headers']['Authorization'], 'Bearer ' + 'a'*48)
        self.assertEqual(json.loads(kw['body']), row)
        self.assertTrue(conn.closed)

    def test_https_rejects_auth_redirect_invalid_ack_and_large_response(self):
        config = self.https_config()
        for status, body, reason in [(401,b'','authentication_failed'), (403,b'','authentication_failed'),
                (302,b'','redirect_rejected'), (503,b'','http_rejected'),
                (200,b'{"accepted":true,"observed_at":"other"}','invalid_acknowledgement'),
                (200,b'{"accepted":1}','invalid_acknowledgement'),
                (200,b'invalid json SECRET','invalid_configuration_or_response'),
                (200,b'x'*4097,'response_too_large')]:
            with self.subTest(status=status, reason=reason):
                conn, factory, row = self.connection(status, body)
                result = https_transmit(config,row,factory)
                self.assertFalse(result); self.assertEqual(result.failure_class,reason)
                self.assertNotIn('SECRET', repr(result)); self.assertTrue(conn.closed)

    def test_subscription_ack_optional_boolean_and_health_propagation(self):
        config=self.https_config()
        row=sanitize(sample(),NOW)
        for active in (True,False,None):
            payload={'accepted':True,'observed_at':row['observed_at']}
            if active is not None:payload['subscription_active']=active
            conn,factory,row=self.connection(payload=json.dumps(payload).encode())
            result=https_transmit(config,row,factory)
            self.assertTrue(result);self.assertIs(result.subscription_active,active)
            diagnostics={}
            state,outcome=step(self.config,{},NOW,lambda *_:result,diagnostics)
            health=update_health({},state,outcome,diagnostics,NOW)
            self.assertIs(health['subscription_active'],active)
        payload={'accepted':True,'observed_at':row['observed_at'],'subscription_active':'yes'}
        conn,factory,row=self.connection(payload=json.dumps(payload).encode())
        self.assertEqual(https_transmit(config,row,factory).failure_class,'invalid_acknowledgement')

    def test_https_tls_and_network_failures(self):
        config = self.https_config()
        for error, reason in [(ssl.SSLCertVerificationError('secret'),'tls_failed'),
                (TimeoutError(),'timeout'),(OSError('secret'),'network_unreachable')]:
            conn,factory,row = self.connection(error=error)
            result=https_transmit(config,row,factory)
            self.assertEqual(result.failure_class,reason); self.assertFalse(result)
            self.assertTrue(conn.closed)

    def test_https_url_and_token_constraints(self):
        config=self.https_config()
        for url in ('http://recovery.example.com/monitor/observations',
                'https://user:secret@recovery.example.com/monitor/observations',
                'https://recovery.example.com/monitor/observations?secret=value',
                'https://recovery.example.com/monitor/observations#secret',
                'https://recovery.example.com/other',
                'https://recovery.example.com/monitor/observations\n'):
            with self.assertRaises(ValueError): https_target(dict(config,ingest_url=url))
        token=Path(config['bearer_token_file']); token.chmod(0o644)
        with self.assertRaises(ValueError): read_token(token)
        token.chmod(0o600); token.write_text('a'*48+'\r\nInjection: secret')
        with self.assertRaises(ValueError): read_token(token)
        link=Path(self.tmp.name)/'token-link';link.symlink_to(token)
        with self.assertRaises(OSError): read_token(link)

    def test_safe_ssh_failure_classes(self):
        for text,reason in [('https://login.tailscale.com/a/SECRET','auth_required'),
                ('Host key verification failed','host_key_rejected'),
                ('Permission denied','authentication_failed'),('Connection refused','connection_refused')]:
            self.assertEqual(ssh_failure(text),reason)
        def run(*a,**kw):return SimpleNamespace(returncode=255,stderr='https://login.tailscale.com/a/SECRET')
        result=transmit(self.config,sample(),run)
        self.assertFalse(result);self.assertEqual(result.failure_class,'auth_required')
        self.assertNotIn('SECRET',repr(result))

    def test_stale_health_survives_restarts_and_restores(self):
        state={'delivered_at':sample(-149)['observed_at']}
        healthy=update_health({},state,'already_delivered',{},NOW)
        self.assertFalse(healthy['stale_delivery'])
        failed=update_health(healthy,state,'transport_failed',{'last_attempt_at':sample()['observed_at'],'failure_class':'auth_required'},NOW+2)
        self.assertTrue(failed['stale_delivery']);self.assertIsNotNone(failed['first_failure_at'])
        again=update_health(failed,state,'observation_unavailable',{},NOW+60)
        self.assertEqual(again['first_failure_at'],failed['first_failure_at'])
        self.assertEqual(again['last_attempt_at'],failed['last_attempt_at'])
        restored=update_health(again,{'delivered_at':sample(61)['observed_at']},'delivered',{'last_attempt_at':sample(61)['observed_at'],'failure_class':'ok'},NOW+61)
        self.assertFalse(restored['stale_delivery']);self.assertIsNone(restored['first_failure_at'])
        output=io.StringIO()
        with redirect_stderr(output):
            emit_health_transition(healthy,failed);emit_health_transition(failed,failed);emit_health_transition(again,restored)
        lines=output.getvalue().splitlines();self.assertEqual(len(lines),2)
        self.assertEqual(json.loads(lines[0])['failure_class'],'auth_required')
        self.assertEqual(json.loads(lines[1])['relay_health'],'restored')

    def test_runtime_paths_do_not_follow_a_repointed_installation_alias(self):
        root = Path(self.tmp.name)
        old, new = root / 'python-old', root / 'python-new'
        old.mkdir(); new.mkdir()
        (old / 'python3').write_text('old')
        (new / 'python3').write_text('new')
        alias = root / 'python-current'
        alias.symlink_to(old, target_is_directory=True)
        captured = capture_runtime_paths(str(alias), str(alias / 'python3'))
        self.assertEqual(captured, (str(old.resolve()), str((old / 'python3').resolve())))
        alias.unlink(); alias.symlink_to(new, target_is_directory=True)
        (old / 'python3').unlink(); old.rmdir()
        self.assertTrue(alias.is_dir())
        self.assertTrue((alias / 'python3').is_file())
        with patch.object(monitor_relay, '_RUNTIME_PATHS', captured):
            result = runtime_diagnostics()
        self.assertFalse(result['runtime_prefix_present'])
        self.assertFalse(result['runtime_executable_present'])
        self.assertRegex(result['runtime_version'], r'^\d+\.\d+\.\d+$')
        self.assertNotIn(str(root), json.dumps(result))

    def test_runtime_path_capture_failure_keeps_independent_executable(self):
        executable = Path(self.tmp.name) / 'python3'
        executable.write_text('python')
        for error in (OSError('PRIVATE'), RuntimeError('PRIVATE')):
            with patch.object(Path, 'resolve', side_effect=[error, executable]):
                captured = capture_runtime_paths('/unavailable', str(executable))
            self.assertEqual(captured, (None, str(executable)))
            with patch.object(monitor_relay, '_RUNTIME_PATHS', captured):
                result = runtime_diagnostics()
            self.assertFalse(result['runtime_prefix_present'])
            self.assertTrue(result['runtime_executable_present'])
            self.assertNotIn('PRIVATE', json.dumps(result))

    def test_runtime_diagnostics_probe_errors_are_false(self):
        with patch.object(monitor_relay, '_RUNTIME_PATHS', ('/prefix', '/python3')):
            with patch.object(Path, 'is_dir', side_effect=OSError('PRIVATE')):
                with patch.object(Path, 'is_file', return_value=True):
                    result = runtime_diagnostics()
            self.assertFalse(result['runtime_prefix_present'])
            self.assertTrue(result['runtime_executable_present'])
            with patch.object(Path, 'is_dir', return_value=True):
                with patch.object(Path, 'is_file', side_effect=OSError('PRIVATE')):
                    result = runtime_diagnostics()
            self.assertTrue(result['runtime_prefix_present'])
            self.assertFalse(result['runtime_executable_present'])
            self.assertNotIn('PRIVATE', json.dumps(result))

    def test_runtime_disappearance_emits_a_degraded_transition(self):
        present = {'runtime_prefix_present': True,
                   'runtime_executable_present': True, 'runtime_version': '3.13.17'}
        missing = dict(present, runtime_prefix_present=False,
                       runtime_executable_present=False)
        with patch.object(monitor_relay, 'runtime_diagnostics', return_value=present):
            healthy = update_health({}, {'delivered_at': sample()['observed_at']},
                                    'delivered', {}, NOW)
        failed = dict(healthy, **missing)
        output = io.StringIO()
        with redirect_stderr(output):
            with patch.object(monitor_relay, 'runtime_diagnostics', return_value=missing):
                emit_health_transition(healthy, failed)
                emit_health_transition(failed, failed)
            with patch.object(monitor_relay, 'runtime_diagnostics', return_value=present):
                emit_health_transition(failed, healthy)
        lines = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0]['relay_health'], 'degraded')
        self.assertEqual(lines[1]['relay_health'], 'restored')
        self.assertFalse(lines[0]['runtime_prefix_present'])
        self.assertFalse(lines[0]['runtime_executable_present'])
        self.assertEqual(lines[0]['failure_class'], 'ok')
        self.assertNotIn(str(self.tmp.name), output.getvalue())

    def test_diagnostics_preserve_bool_senders(self):
        diagnostics={}
        state,outcome=step(self.config,{},NOW,lambda *_:False,diagnostics)
        self.assertEqual((state,outcome),({},'transport_failed'))
        self.assertEqual(diagnostics['failure_class'],'transport_failed')
        self.assertEqual(diagnostics['last_attempt_at'],sample()['observed_at'])

    def test_ssh_diagnostics_bounded_even_when_remote_is_noisy(self):
        result=bounded_ssh_run(['/usr/bin/python3','-c','import sys;sys.stdin.read();sys.stderr.write("x"*1000000)'],input='{}',timeout=5)
        self.assertEqual(result.returncode,0);self.assertEqual(len(result.stderr),8192)

    def test_deadline_socket_preserves_http_close_body_and_expiry(self):
        left,right=socket.socketpair()
        self.addCleanup(left.close);self.addCleanup(right.close)
        wrapped=DeadlineSocket(left,time.monotonic()+1)
        response=http.client.HTTPResponse(wrapped)
        right.sendall(b'HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: 2\r\n\r\n{}')
        response.begin()
        wrapped.close()
        self.assertEqual(response.read(),b'{}')
        response.close()
        left2,right2=socket.socketpair()
        self.addCleanup(left2.close);self.addCleanup(right2.close)
        expired=DeadlineSocket(left2,time.monotonic()-1)
        with self.assertRaises(TimeoutError):expired.sendall(b'x')
        reader=expired.makefile('rb')
        self.addCleanup(reader.close)
        with self.assertRaises(TimeoutError):reader.read(1)

    def test_real_https_hung_dns_wall_deadline_and_child_cleanup(self):
        config=self.https_config()
        before={p.pid for p in multiprocessing.active_children()}
        with patch('socket.getaddrinfo',side_effect=lambda *a,**kw:time.sleep(60)), \
                patch('monitor_relay.bounded_call',side_effect=lambda operation:bounded_call(operation,timeout=0.1)):
            started=time.monotonic()
            result=https_transmit(config,sanitize(sample(),NOW))
        self.assertFalse(result)
        self.assertEqual(result.failure_class,'timeout')
        self.assertLess(time.monotonic()-started,2)
        self.assertEqual({p.pid for p in multiprocessing.active_children()},before)

    def test_launchd_templates_parse(self):
        for p in (Path(__file__).parent/'services').glob('*.plist'):
            value = plistlib.loads(p.read_bytes())
            self.assertTrue(value['KeepAlive'])
            self.assertTrue(value['RunAtLoad'])
            self.assertEqual(value['Umask'], 63)

if __name__ == '__main__': unittest.main()
