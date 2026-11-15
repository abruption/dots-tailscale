"""New-install SSH settings and updater compatibility; no real listener or tailnet."""
import contextlib
import importlib.machinery
import importlib.util
import json
import os
import types
import re
import shlex
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import dots_proxy as proxy

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader('ssh_port_cli', str(ROOT/'dots-ts'))
spec = importlib.util.spec_from_loader(loader.name, loader)
tool = importlib.util.module_from_spec(spec)
loader.exec_module(tool)


class SshPortTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='codex-ssh63-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.d = self.root/'install'
        self.r = self.root/'runtime'
        for path in (self.d, self.r, self.d/'sshd'):
            path.mkdir(mode=0o700)

    def setting(self, port):
        path = self.d/proxy.SSH_PORT_FILE
        path.write_text(json.dumps({'schema_version': 1, 'port': port}))
        path.chmod(0o600)
        return path

    def config(self, port=22):
        path = self.d/'sshd/sshd_config'
        path.write_text(proxy.ssh_config_text(self.d, self.r, port))
        path.chmod(0o600)
        return path

    def test_default_persist_and_legacy(self):
        self.assertEqual(proxy.ssh_install_port(self.d, self.r), 22)
        self.config()
        self.assertEqual(proxy.ssh_port_info(self.d, self.r)['origin'], 'legacy')
        proxy.ssh_persist_port(self.d, self.r, 22)
        self.assertFalse((self.d/proxy.SSH_PORT_FILE).exists())

    def test_new_default_persisted_once(self):
        proxy.ssh_persist_port(self.d, self.r, 22)
        path = self.d/proxy.SSH_PORT_FILE
        before = (path.stat().st_ino, path.read_bytes())
        proxy.ssh_persist_port(self.d, self.r, 22)
        self.assertEqual((path.stat().st_ino, path.read_bytes()), before)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_new_2222_persisted_and_restart_has_one_endpoint(self):
        self.assertEqual(proxy.ssh_install_port(self.d, self.r, '2222'), 2222)
        proxy.ssh_persist_port(self.d, self.r, 2222)
        text = self.config(2222).read_text()
        self.assertIn('Port 2222\nListenAddress 127.0.0.1\n', text)
        self.assertIn('PasswordAuthentication no\n', text)
        self.assertIn('PubkeyAuthentication yes\n', text)
        self.assertEqual(proxy.ssh_port_info(self.d, self.r)['port'], 2222)
        with mock.patch.object(proxy.socket, 'socket'):
            self.assertEqual(proxy.ssh_preflight(self.d, self.r, 'start', threshold='1024'), 2222)
        with self.assertRaises(proxy.ProxyError):
            proxy.ssh_preflight(self.d, self.r, 'start', requested='2222', threshold='1024')

    def test_kernel_threshold_boundaries_before_binding(self):
        for port in (22, 2222):
            for threshold in (0, 1024, 2222, 2223):
                with self.subTest(port=port, threshold=threshold), mock.patch.object(proxy.socket, 'socket') as probe:
                    if port < threshold:
                        with self.assertRaisesRegex(proxy.ProxyError, 'threshold'):
                            proxy.ssh_preflight(self.d, self.r, 'install', str(port), str(threshold))
                        probe.assert_not_called()
                    else:
                        self.assertEqual(proxy.ssh_preflight(self.d, self.r, 'install', str(port), str(threshold)), port)
                        probe.return_value.__enter__.return_value.bind.assert_called_once_with(('127.0.0.1', port))

    def test_existing_start_keeps_the_published_kernel_threshold_gate(self):
        self.config(22)
        with mock.patch.object(proxy.socket,'socket') as probe:
            with self.assertRaisesRegex(proxy.ProxyError,'threshold'):
                proxy.ssh_preflight(self.d,self.r,'start',threshold='1024')
            probe.assert_not_called()

    def test_range_format_and_reserved_port(self):
        for bad in (0, -1, 65536, True, False, 1.5, '', '022', '2222 ', '+2222', '2e3', '２２２２', '22\n', '18089', 18089):
            with self.subTest(bad=bad), self.assertRaises(proxy.ProxyError):
                proxy.ssh_port_value(bad)
        for value in (1, 22, 1024, 2222, 65535):
            self.assertEqual(proxy.ssh_port_value(str(value)), value)

    def test_existing_and_partial_intent_do_not_migrate(self):
        self.setting(2222)
        self.assertEqual(proxy.ssh_install_port(self.d, self.r, '2222'), 2222)
        with self.assertRaises(proxy.ProxyError):
            proxy.ssh_install_port(self.d, self.r, '2223')
        (self.d/'installed').touch()
        with self.assertRaisesRegex(proxy.ProxyError, 'new installations'):
            proxy.ssh_install_port(self.d, self.r, '2222')

    def test_existing_identity_without_setting_cannot_adopt_port(self):
        key = self.d/'sshd/ssh_host_ed25519_key'
        key.write_bytes(b'inert retained key')
        before = (key.stat().st_ino, key.read_bytes())
        with self.assertRaises(proxy.ProxyError):
            proxy.ssh_install_port(self.d, self.r, '2222')
        self.assertEqual((key.stat().st_ino, key.read_bytes()), before)
        self.assertFalse((self.d/proxy.SSH_PORT_FILE).exists())

    def test_legacy_high_port_and_mismatches_are_not_inferred(self):
        self.config(2222)
        with self.assertRaisesRegex(proxy.ProxyError, 'mismatch'):
            proxy.ssh_port_info(self.d, self.r)
        self.setting(2222)
        self.assertEqual(proxy.ssh_port_info(self.d, self.r)['port'], 2222)
        self.config(22)
        with self.assertRaises(proxy.ProxyError):
            proxy.ssh_port_info(self.d, self.r)

    def test_ambiguous_or_nonloopback_sshd_config_refused(self):
        self.setting(2222)
        path = self.config(2222)
        good = path.read_text()
        for bad in (good+'Port 2222\n', good+'Include /tmp/other\n', good+'Match all\n',
                    good.replace('ListenAddress 127.0.0.1', 'ListenAddress 0.0.0.0'),
                    good.replace('ListenAddress 127.0.0.1', 'ListenAddress 127.0.0.1:2223'),
                    good.replace('PasswordAuthentication no', 'PasswordAuthentication yes')):
            with self.subTest(config=bad):
                path.write_text(bad)
                with self.assertRaises(proxy.ProxyError):proxy.ssh_port_info(self.d, self.r)

    def test_bad_settings_permissions_schema_and_symlinks_refused(self):
        path = self.setting(2222)
        for raw in ('{}', '{"schema_version":true,"port":2222}', '{"schema_version":1,"port":true}',
                    '{"schema_version":1,"port":2222,"port":2223}', '{"schema_version":2,"port":2222}',
                    '{"schema_version":1,"port":2222.0}'):
            path.write_text(raw)
            with self.assertRaises(proxy.ProxyError):proxy.ssh_port_setting(self.d)
        self.setting(2222).chmod(0o644)
        with self.assertRaises(proxy.ProxyError):proxy.ssh_port_setting(self.d)
        path.unlink();path.symlink_to(self.root/'absent')
        with self.assertRaises(proxy.ProxyError):proxy.ssh_port_setting(self.d)

    def test_proxy_conflict_is_checked_in_both_directions(self):
        self.setting(2222);self.config(2222)
        path = self.d/proxy.CONFIG
        path.write_text(json.dumps({'socks_port': 2222, 'http_port': None}));path.chmod(0o600)
        with self.assertRaisesRegex(proxy.ProxyError, 'conflicts'):proxy.ssh_port_info(self.d, self.r)
        path.unlink()
        with mock.patch.object(proxy, '_ports_available') as available:
            with self.assertRaisesRegex(proxy.ProxyError, 'conflicts'):
                proxy.configure(self.d, self.r, socks_port=2222)
            available.assert_not_called()
        self.assertFalse(path.exists())

    def test_unrelated_listener_refused_and_owned_listener_allowed(self):
        self.setting(2222);self.config(2222)
        with mock.patch.object(proxy.socket, 'socket') as sock, mock.patch.object(proxy, '_ssh_listener', return_value=False) as owner:
            sock.return_value.__enter__.return_value.bind.side_effect = OSError('occupied fixture')
            with self.assertRaisesRegex(proxy.ProxyError, 'unavailable'):
                proxy.ssh_preflight(self.d, self.r, 'start', threshold='1024')
            owner.assert_called_once_with(self.d, 2222)
            owner.return_value = True
            self.assertEqual(proxy.ssh_preflight(self.d, self.r, 'start', threshold='1024'), 2222)

    def test_setting_config_snapshots_detect_same_port_replacement(self):
        self.setting(2222);path = self.config(2222)
        baseline = {'_ssh': proxy.ssh_port_info(self.d, self.r)}
        self.assertEqual(tool.ssh_unchanged(self.d, self.r, proxy, baseline), 2222)
        path.write_text(path.read_text()+'# harmless but changed during transaction\n')
        with self.assertRaisesRegex(tool.ApplyError, 'ssh_configuration_changed'):
            tool.ssh_unchanged(self.d, self.r, proxy, baseline)

    def test_candidate_capabilities_legacy_default_and_mixed_versions(self):
        legacy = {'tailscale-setup.sh':b'#!/bin/bash\n', 'dots-ts':b'# old cli\n', 'dots_proxy.py':b'# old helper\n'}
        tool.ssh_candidate_contract(legacy, 22)
        with self.assertRaisesRegex(tool.ApplyError, 'candidate_ssh_port_unsupported'):
            tool.ssh_candidate_contract(legacy, 2222)
        new = {n:(ROOT/n).read_bytes() for n in legacy}
        tool.ssh_candidate_contract(new, 2222)
        for name in new:
            mixed = dict(new);mixed[name] = legacy[name]
            with self.subTest(name=name), self.assertRaises(tool.ApplyError):tool.ssh_candidate_contract(mixed, 2222)
        for raw in (b'SSH_PORT_CONFIG_SCHEMA=True\n', b'SSH_PORT_CONFIG_SCHEMA=2\n',
                    b'SSH_PORT_CONFIG_SCHEMA=1\nSSH_PORT_CONFIG_SCHEMA=1\n'):
            bad = dict(new);bad['dots-ts'] = raw
            with self.assertRaises(tool.ApplyError):tool.ssh_candidate_contract(bad, 2222)

    def test_completed_missing_config_does_not_reconstruct_or_migrate(self):
        (self.d/'installed').touch()
        with self.assertRaises((proxy.ProxyError, OSError)):
            proxy.ssh_install_port(self.d,self.r)
        self.assertFalse((self.d/proxy.SSH_PORT_FILE).exists())

    def test_legacy_generated_paths_normalize_redundant_linux_slashes(self):
        cfg=self.config(22)
        text=cfg.read_text().replace(str(self.d)+'/sshd/',str(self.d)+'//sshd/')
        text=text.replace(str(self.r)+'/sshd.pid','//'+str(self.r).lstrip('/')+'//sshd.pid')
        cfg.write_text(text)
        self.assertEqual(proxy.ssh_port_info(self.d,self.r)['port'],22)

    def test_shell_quotes_and_escapes_are_not_sshd_equivalence(self):
        self.setting(2222);cfg=self.config(2222);good=cfg.read_text()
        for bad in [good.replace('Port 2222', "Port '2222'"),good.replace('Port 2222','Port "2222"'),
                    good.replace('PasswordAuthentication no',r'PasswordAuthentication \n\o'),
                    good.replace('AuthorizedKeysFile '+str(self.d/'sshd/authorized_keys'),
                                 "AuthorizedKeysFile '"+str(self.d/'sshd/authorized_keys')+"'")]:
            cfg.write_text(bad)
            with self.assertRaises(proxy.ProxyError):proxy.ssh_port_info(self.d,self.r)

    def test_config_path_traversal_is_not_lexical_equivalence(self):
        self.setting(2222);cfg=self.config(2222);good=cfg.read_text()
        for path in [str(self.d/'untrusted')+'/../sshd/authorized_keys',str(self.d)+'/./sshd/authorized_keys',str(self.d/'sshd/authorized_keys')+'/']:
            cfg.write_text(good.replace(str(self.d/'sshd/authorized_keys'),path))
            with self.assertRaises(proxy.ProxyError):proxy.ssh_port_info(self.d,self.r)

    def test_ssh_helper_cli_dispatch_is_readonly_and_rejects_empty_selection(self):
        self.setting(2222);self.config(2222)
        before={str(p):(p.stat().st_ino,p.read_bytes()) for p in [self.d/proxy.SSH_PORT_FILE,self.d/'sshd/sshd_config']}
        r=subprocess.run([sys.executable,str(ROOT/'dots_proxy.py'),'ssh-port',str(self.d),str(self.r)],capture_output=True,text=True,timeout=10)
        self.assertEqual(r.returncode,0,r.stderr);self.assertEqual(r.stdout,'2222\n')
        self.assertEqual(before,{n:(Path(n).stat().st_ino,Path(n).read_bytes()) for n in before})
        (self.d/proxy.SSH_PORT_FILE).unlink();(self.d/'sshd/sshd_config').unlink()
        r=subprocess.run([sys.executable,str(ROOT/'dots_proxy.py'),'ssh-preflight',str(self.d),str(self.r),'install'],env=dict(os.environ,DOTS_TS_SSH_PORT=''),capture_output=True,text=True,timeout=10)
        self.assertNotEqual(r.returncode,0);self.assertIn('canonical integer',r.stderr)

    def test_unicode_and_control_separators_cannot_hide_directives(self):
        self.setting(2222);cfg=self.config(2222);good=cfg.read_text()
        for char in ['\x85','\u2028','\x0b','\x0c','\x1c','\x1d','\x1e','\u00a0','\r']:
            cfg.write_text(good.replace('ListenAddress 127.0.0.1', '#'+char+'ListenAddress 127.0.0.1'))
            with self.assertRaises(proxy.ProxyError):proxy.ssh_port_info(self.d,self.r)
        bad_keyword='Pub\u212AeyAuthentication'
        self.assertEqual(ord(bad_keyword[3]),0x212A)
        self.assertFalse(bad_keyword.isascii())
        cfg.write_text(good.replace('PubkeyAuthentication',bad_keyword))
        with self.assertRaises(proxy.ProxyError):proxy.ssh_port_info(self.d,self.r)
        cfg.write_text(good.replace('PubkeyAuthentication','PubKeyAuthentication'))
        self.assertEqual(proxy.ssh_port_info(self.d,self.r)['port'],2222)

    def test_interrupted_link_publication_can_retry_without_identity(self):
        with mock.patch.object(proxy.os,'unlink',side_effect=SystemExit('crash fixture')):
            with self.assertRaises(SystemExit):proxy.ssh_persist_port(self.d,self.r,2222)
        self.assertEqual((self.d/proxy.SSH_PORT_FILE).stat().st_nlink,2)
        with mock.patch.object(proxy.socket,'socket'):
            self.assertEqual(proxy.ssh_preflight(self.d,self.r,'install','2222','1024'),2222)
        self.assertEqual((self.d/proxy.SSH_PORT_FILE).stat().st_nlink,1)
        self.assertEqual(list(self.d.glob('.ssh-port-*')),[])
        self.assertFalse((self.d/'sshd/ssh_host_ed25519_key').exists())

    def test_link_recovery_never_deletes_unknown_or_completed_install_links(self):
        final=self.setting(2222);temporary=self.d/'.ssh-port-known'
        os.link(final,temporary);outside=self.root/'unknown-link';os.link(final,outside)
        with self.assertRaises(proxy.ProxyError):proxy.ssh_preflight(self.d,self.r,'install','2222','1024')
        self.assertTrue(temporary.exists());self.assertTrue(outside.exists())
        outside.unlink();(self.d/'installed').touch()
        with self.assertRaises(proxy.ProxyError):proxy.ssh_preflight(self.d,self.r,'install','2222','1024')
        self.assertTrue(temporary.exists())

    def test_root_paths_refuse_sshd_interpretation_characters_before_identity(self):
        for char in ['"',"'",'\\','%','#']:
            bad=self.d/('unsafe'+char)
            with mock.patch.object(proxy.socket,'socket') as probe:
                with self.assertRaisesRegex(proxy.ProxyError,'syntax'):
                    proxy.ssh_preflight(bad,self.r,'install','2222','1024')
                probe.assert_not_called()
            self.assertFalse(bad.exists())
        self.assertIn('한국어',proxy.ssh_config_text(self.d/'한국어',self.r,2222))

    def test_legacy_custom_configuration_and_file_metadata_fail_closed(self):
        cfg=self.config(22);good=cfg.read_text()
        cfg.write_text(good+'LogLevel VERBOSE\n')
        with self.assertRaises(proxy.ProxyError):proxy.ssh_port_info(self.d,self.r)
        cfg.write_text(good);cfg.chmod(0o644)
        with self.assertRaises(proxy.ProxyError):proxy.ssh_port_info(self.d,self.r)
        cfg.chmod(0o600);link=self.root/'backup-link';os.link(cfg,link)
        with self.assertRaises(proxy.ProxyError):proxy.ssh_port_info(self.d,self.r)
        link.unlink();self.assertEqual(proxy.ssh_port_info(self.d,self.r)['port'],22)

    def test_public_only_or_authenticated_evidence_cannot_choose_new_port(self):
        for name in ['sshd/ssh_host_ed25519_key.pub','authenticated']:
            path=self.d/name;path.write_bytes(b'inert retained identity evidence')
            with self.assertRaises(proxy.ProxyError):proxy.ssh_install_port(self.d,self.r,'2222')
            self.assertFalse((self.d/proxy.SSH_PORT_FILE).exists());path.unlink()

    def test_atomic_intent_write_failure_has_no_partial_final_file_and_can_retry(self):
        with mock.patch.object(proxy.os,'fsync',side_effect=OSError('inert disk failure')):
            with self.assertRaises(OSError):proxy.ssh_persist_port(self.d,self.r,2222)
        self.assertFalse((self.d/proxy.SSH_PORT_FILE).exists())
        self.assertEqual(list(self.d.glob('.ssh-port-*')),[])
        proxy.ssh_persist_port(self.d,self.r,2222)
        self.assertEqual(proxy.ssh_port_setting(self.d)[0],2222)
        self.assertEqual((self.d/proxy.SSH_PORT_FILE).stat().st_nlink,1)

    def test_atomic_intent_publish_never_overwrites_a_concurrent_file(self):
        def collision(source,target,**kwargs):
            Path(target).write_bytes(b'concurrent intent; not ours')
            raise FileExistsError('fixture competing publication')
        with mock.patch.object(proxy.os,'link',side_effect=collision):
            with self.assertRaises(FileExistsError):proxy.ssh_persist_port(self.d,self.r,2222)
        self.assertEqual((self.d/proxy.SSH_PORT_FILE).read_bytes(),b'concurrent intent; not ours')
        self.assertEqual(list(self.d.glob('.ssh-port-*')),[])

    def test_bind_probe_uses_reuseaddr_without_reusing_a_live_listener(self):
        with mock.patch.object(proxy.socket,'socket') as sock:
            self.assertEqual(proxy.ssh_preflight(self.d,self.r,'install','2222','1024'),2222)
        probe=sock.return_value.__enter__.return_value
        self.assertEqual(probe.method_calls[:2],[mock.call.setsockopt(proxy.socket.SOL_SOCKET,proxy.socket.SO_REUSEADDR,1),mock.call.bind(('127.0.0.1',2222))])

    def test_proxy_configure_refuses_lost_high_port_metadata(self):
        self.config(2222)
        with mock.patch.object(proxy,'_ports_available') as ports:
            with self.assertRaises(proxy.ProxyError):proxy.configure(self.d,self.r,socks_port=2222)
            ports.assert_not_called()
        self.assertFalse((self.d/proxy.CONFIG).exists())

    def test_sshd_listener_title_and_overrides(self):
        cfg = str(self.d/'sshd/sshd_config')
        self.assertEqual(proxy._ssh_config_operand(['/usr/sbin/sshd', '-f', cfg, '-E', '/tmp/log']), cfg)
        self.assertEqual(proxy._ssh_config_operand(['sshd: /usr/sbin/sshd -f '+cfg+' [listener] 0 of 10-100 startups']), cfg)
        for args in (['sshd','-t','-f',cfg],['sshd','-f',cfg,'-p','22'],['sshd','-f',cfg,'-o','Port=22']):
            self.assertIsNone(proxy._ssh_config_operand(args))


class EndpointIntegrationTests(unittest.TestCase):
    setUp = SshPortTests.setUp
    setting = SshPortTests.setting
    config = SshPortTests.config

    def endpoint(self):
        self.setting(2222)
        self.config(2222)
        return proxy.ssh_port_info(self.d, self.r)

    def component_context(self, cli=tool):
        info = self.endpoint()
        identities = {n:{'pid':n, 'ticks':str(n), 'uid':os.getuid(), 'exe':exe}
                      for n,exe in [(101,'/usr/bin/python3'),(102,'/usr/sbin/sshd'),(103,str(self.d/'tailscaled'))]}
        entries = []
        executables = {}
        for pid,args in [(101,['/usr/bin/python3',str(self.d/'shim/shim.py')]),
                         (102,['/usr/sbin/sshd','-f',str(self.d/'sshd/sshd_config')])]:
            entry = mock.MagicMock()
            entry.name = str(pid)
            entry.stat.return_value.st_uid = os.getuid()
            entry.__truediv__.return_value.read_bytes.return_value = ('\0'.join(args)+'\0').encode()
            entries.append(entry)
            executables[id(entry/'exe')] = identities[pid]['exe']
        double = mock.Mock()
        double.ssh_port_info.side_effect = lambda d,r:proxy.ssh_port_info(d,r)
        double._ssh_config_operand.side_effect = proxy._ssh_config_operand
        double._daemon.return_value = {'pid':103,'argv':[str(self.d/'tailscaled'),'--statedir='+str(self.d/'state')]}
        double._socket_owned.return_value = True
        double._socket_identity.return_value = [1,2]
        double._listener_owned.side_effect = lambda process,port: (process['pid'],port) in [(101,18089),(102,2222)]
        stack = contextlib.ExitStack()
        stack.enter_context(mock.patch.object(Path,'iterdir',return_value=entries))
        stack.enter_context(mock.patch.object(os,'readlink',side_effect=lambda p:executables[id(p)]))
        stack.enter_context(mock.patch.object(cli,'apply_identity',side_effect=identities.get))
        return stack,double,identities,info

    def test_component_ownership_uses_selected_2222(self):
        stack,double,_,info = self.component_context()
        with stack:
            baseline = tool.apply_components(self.d,self.r,double)
        self.assertEqual(baseline['_ssh'],info)
        self.assertEqual([call.args[1] for call in double._listener_owned.call_args_list],[18089,2222])

    def test_deleted_sshd_binary_still_identifies_its_owned_listener(self):
        stack,_,_,_=self.component_context()
        with stack,mock.patch.object(os,'readlink',return_value='/usr/sbin/sshd (deleted)'), \
             mock.patch.object(proxy,'_listener_owned',return_value=True) as owned:
            self.assertTrue(proxy._ssh_listener(self.d,2222))
        owned.assert_called_once_with({'pid':102},2222)

    def test_ssh_snapshot_survives_the_real_journal_json_roundtrip(self):
        self.config(22)
        info=proxy.ssh_port_info(self.d,self.r)
        self.assertIsNone(info['setting'])
        info['sshd_config']['identity'][1]=2**60
        journal=self.root/'transaction';journal.mkdir(mode=0o700)
        tool.apply_journal(journal,{'schema':1,'state':'prepared','ssh_configuration':info})
        self.assertEqual(tool.apply_read_journal(journal)['ssh_configuration'],info)

    def test_locked_main_restores_private_directories_and_uses_installed_helper(self):
        source=(ROOT/'tailscale-setup.sh').read_text()
        body=source[source.index('\npreflight\n')+1:source.index('AUTH_COMMAND_STATUS=0\n')]
        other=self.root/'outside';other.mkdir(mode=0o700)
        (other/'dots_proxy.py').write_text('raise SystemExit("must not execute outside helper")')
        trace=self.root/'locked'
        stub=('import pathlib,stat,sys;d=pathlib.Path(sys.argv[2]);'
              'assert stat.S_IMODE(d.stat().st_mode)==0o700;'
              'assert stat.S_IMODE((d/"sshd").stat().st_mode)==0o700;'
              'assert pathlib.Path('+repr(str(trace))+').exists();print(22)')
        (self.d/'dots_proxy.py').write_text(stub)
        self.d.chmod(0o755);(self.d/'sshd').chmod(0o755)
        script='set -euo pipefail\nD='+shlex.quote(str(self.d))+'\nR='+shlex.quote(str(self.r))+'\n'
        script+='S="$D/sshd"\nSOURCE='+shlex.quote(str(other))+'\nMODE=start\n'
        script+='preflight() { :; }; private_dir() { mkdir -p "$1"; chmod 700 "$1"; }; fail() { echo "$*" >&2; exit 1; };\n'
        script+='flock() { echo locked > '+shlex.quote(str(trace))+'; }; python3() { '+shlex.quote(sys.executable)+' "$@"; };\n'
        result=subprocess.run(['bash','-c',script+body+'\nprintf "%s" "$SSH_PORT"\n'],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout,'22')

    def test_update_readiness_connects_to_selected_port(self):
        info = self.endpoint()
        host = self.d/'sshd/ssh_host_ed25519_key.pub'
        host.write_bytes(b'fixture-public-key')
        baseline = {n:{'pid':pid,'ticks':str(pid),'uid':os.getuid(),'exe':'/python'}
                    for n,pid in [('shim',101),('sshd',102),('tailscaled',103)]}
        baseline['_ssh'] = info
        candidate = dict(baseline['shim'],pid=201)
        double = mock.Mock()
        double.ssh_port_info.side_effect = lambda d,r:proxy.ssh_port_info(d,r)
        double.inspect_config.return_value = {}
        double._listener_owned.return_value = True
        identity = {v['pid']:v for v in [baseline['shim'],baseline['sshd'],baseline['tailscaled'],candidate]}
        with mock.patch.object(tool,'apply_identity',side_effect=identity.get), \
             mock.patch.object(tool,'apply_public_identity',return_value='node'), \
             mock.patch.object(tool.socket,'create_connection') as connect:
            connect.return_value.__enter__.return_value.recv.return_value = b'SSH-2.0-fixture'
            tool.apply_ready(self.d,self.r,double,baseline,candidate,'node',{},b'fixture-public-key',timeout=1)
        self.assertEqual(connect.call_args.args[0],('127.0.0.1',2222))
        self.assertIn(mock.call(baseline['sshd'],2222),double._listener_owned.call_args_list)

    def test_doctor_reports_real_validated_endpoint_and_mismatch(self):
        self.endpoint()
        for name in ['installed','start.sh','shim/ca.crt','shim/ca.key','shim/bundle.crt','sshd/ssh_host_ed25519_key','sshd/authorized_keys','state/tailscaled.state','tailscale','tailscaled']:
            path = self.d/name;path.parent.mkdir(mode=0o700,parents=True,exist_ok=True);path.write_bytes(b'inert fixture')
        package = {'tailscale-setup.sh':b'TS_VERSION=1.104.4\n','dots-ts':b'#cli','dots_proxy.py':b'#proxy','shim.py':b'#shim'}
        import hashlib
        for source,data in package.items():
            (self.d/tool.CORE_FILES[source]).write_bytes(data)
        (self.d/tool.MANIFEST).write_text(json.dumps({'schema_version':1,'package_version':'0.9.1',
                    'files':{n:hashlib.sha256(b).hexdigest() for n,b in package.items()}}))
        with mock.patch.object(tool,'process_checks',return_value={n:'running' for n in ['shim','sshd','tailscaled']}), \
             mock.patch.object(tool,'socket_status',return_value='present'), \
             mock.patch.object(tool,'socket_connection',return_value='connectable'), \
             mock.patch.object(tool,'run_cli',return_value=types.SimpleNamespace(returncode=0,stdout=b'{"BackendState":"Running","Version":"1.104.4"}')), \
             mock.patch.object(tool,'local_binary_version',return_value={'status':'available','version':'1.104.4'}), \
             mock.patch.object(proxy,'inspect_config',return_value={'desired':{},'running':{},'daemon_running':True,'restart_required':False}):
            report,code = tool.doctor(self.d,self.r)
            self.assertEqual(code,0)
            self.assertEqual(report['checks']['ssh']['port'],2222)
            self.assertEqual(report['checks']['ssh']['status'],'configuration_verified')
            self.config(22)
            report,code = tool.doctor(self.d,self.r)
            self.assertEqual(code,1)
            self.assertEqual(report['checks']['ssh']['status'],'invalid_configuration')

    def test_local_recovery_probe_uses_the_same_selected_endpoint(self):
        self.endpoint()
        with mock.patch.object(tool,'process_checks',return_value={n:'running' for n in ['shim','sshd','tailscaled']}), \
             mock.patch.object(tool,'socket_status',return_value='present'), \
             mock.patch.object(tool,'recovery_capture',return_value={'BackendState':'Running'}), \
             mock.patch.object(tool.socket,'create_connection') as connect:
            connect.return_value.__enter__.return_value.recv.return_value=b'SSH-2.0-fixture'
            self.assertEqual(tool.recovery_probe(self.d,self.r)['state'],'healthy')
        connect.assert_called_once_with(('127.0.0.1',2222),timeout=2)

    def test_start_banner_probe_uses_preflight_port(self):
        source = (ROOT/'tailscale-setup.sh').read_text()
        function = re.search(r'^start_all\(\) \{\n.*?^}\n',source,re.M|re.S).group()
        function = function.replace('/usr/sbin/sshd -t -f "$S/sshd_config"', ':')
        for port in [22,2222]:
            with self.subTest(port=port):
                for name in ['installed','tailscale','tailscaled','dots-ts','dots_proxy.py','shim/shim.py','shim/ca.crt','shim/ca.key','shim/bundle.crt','sshd/sshd_config','sshd/ssh_host_ed25519_key','sshd/authorized_keys']:
                    path=self.d/name;path.parent.mkdir(mode=0o700,parents=True,exist_ok=True);path.write_bytes(b'inert fixture');path.chmod(0o700)
                record=self.root/'banner.json'
                fake = "import sys,socket,json,os;from unittest import mock;from pathlib import Path;code=sys.stdin.read();sys.argv=sys.argv[1:];cm=mock.MagicMock();cm.__enter__.return_value.recv.return_value=b'SSH-2.0-fixture';patch=mock.patch('socket.create_connection',return_value=cm);call=patch.start();exec(code);Path(os.environ['BANNER_RECORD']).write_text(json.dumps(call.call_args.args[0]));patch.stop()"
                script='set -euo pipefail\nD='+shlex.quote(str(self.d))+'\nR='+shlex.quote(str(self.r))+'\n'
                script+='S="$D/sshd"\nSOURCE="$D"\nSOCK="$R/tailscaled.sock"\nSSH_PORT='+str(port)+'\nUPSTREAM_PROXY=http://fixture.invalid:8889\n'
                script+='fail() { echo "$*" >&2; exit 1; }; private_dir() { :; }; running() { return 0; }; launch_needed() { return 1; };\n'
                script+='python3() { if [[ "$1" == "$D/dots_proxy.py" ]]; then if [[ "$2" == ssh-port ]]; then echo ssh-port >> "$HELPER_CALLS"; echo "$PERSISTED_PORT"; fi; return 0; fi; '
                script+='if [[ "$1" == - && "$2" == "$SSH_PORT" ]]; then '+shlex.quote(sys.executable)+' -c '+shlex.quote(fake)+' "$@"; else '+shlex.quote(sys.executable)+' "$@"; fi; }\n'
                (self.root/'helper-calls').unlink(missing_ok=True)
                result=subprocess.run(['bash','-c',script+function+'\nstart_all\n'],env=dict(os.environ,BANNER_RECORD=str(record),PERSISTED_PORT=str(port),HELPER_CALLS=str(self.root/'helper-calls')),capture_output=True,text=True,timeout=10)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(json.loads(record.read_text()),['127.0.0.1',port])
                self.assertEqual((self.root/'helper-calls').read_text().splitlines(),['ssh-port'])
                record.unlink()
                bad=subprocess.run(['bash','-c',script+function+'\nstart_all\n'],env=dict(os.environ,BANNER_RECORD=str(record),PERSISTED_PORT=str(port+1),HELPER_CALLS=str(self.root/'helper-calls')),capture_output=True,text=True,timeout=10)
                self.assertNotEqual(bad.returncode,0);self.assertIn('changed after preflight',bad.stderr)
                self.assertFalse(record.exists())
