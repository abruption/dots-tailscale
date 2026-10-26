"""Offline runtime-updater security fixtures; no real daemon or download.

Synthetic ELF headers are validation inputs, not runnable Tailscale binaries.
Lifecycle cases must explicitly mock daemon behavior; they do not establish a
live pidfd shutdown, network recovery, or compatibility with real node state.
"""
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import struct
import tarfile
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader('dots_ts_runtime_test', str(ROOT/'dots-ts'))
spec = importlib.util.spec_from_loader(loader.name, loader)
tool = importlib.util.module_from_spec(spec)
loader.exec_module(tool)
VERSION = '1.104.0'
ARCHIVE_NAME = 'tailscale_'+VERSION+'_amd64.tgz'


def elf(machine=62, elf_class=2):
    header = bytearray(64)
    header[:7] = b'\x7fELF'+bytes((elf_class, 1, 1))
    struct.pack_into('<HHI', header, 16, 2, machine, 1)
    struct.pack_into('<H', header, 52, 64)
    return bytes(header)+b'fixture-not-executable'


def archive(extra=(), replacements=None, root_version=VERSION):
    bodies = {'tailscale': elf(), 'tailscaled': elf()}
    bodies.update(replacements or {})
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w:gz') as package:
        for name, body in bodies.items():
            entry = tarfile.TarInfo('tailscale_'+root_version+'_amd64/'+name)
            entry.mode = 0o755
            entry.size = len(body)
            package.addfile(entry, io.BytesIO(body))
        for entry, body in extra:
            package.addfile(entry, io.BytesIO(body) if body is not None else None)
    return output.getvalue()


def parse(raw, version=VERSION, checksum=None):
    return tool.runtime_archive(raw, version, checksum or hashlib.sha256(raw).hexdigest())


class RuntimeArchiveTests(unittest.TestCase):
    def test_exact_pair_and_checksum(self):
        raw = archive()
        result = parse(raw)
        self.assertEqual(set(result), {'tailscale', 'tailscaled'})
        self.assertEqual(result['tailscale'], elf())
        self.assertEqual(result['tailscaled'], elf())
        with self.assertRaises(tool.ApplyError):
            parse(raw, checksum='0'*64)

    def test_links_special_files_and_duplicate_binary_rejected(self):
        prefix='tailscale_'+VERSION+'_amd64/'
        for kind, name in ((tarfile.SYMTYPE, prefix+'link'), (tarfile.LNKTYPE, prefix+'hardlink'),
                           (tarfile.FIFOTYPE, prefix+'pipe'), (tarfile.CHRTYPE, prefix+'device')):
            with self.subTest(kind=kind):
                entry=tarfile.TarInfo(name);entry.type=kind;entry.linkname='tailscale'
                with self.assertRaises(tool.ApplyError):parse(archive([(entry,None)]))
        duplicate=tarfile.TarInfo(prefix+'tailscale');duplicate.size=len(elf())
        with self.assertRaises(tool.ApplyError):parse(archive([(duplicate,elf())]))

    def test_traversal_absolute_and_wrong_version_tree_rejected(self):
        for path in ('../outside','/tmp/outside','tailscale_'+VERSION+'_amd64/../../outside'):
            entry=tarfile.TarInfo(path);entry.size=1
            with self.subTest(path=path), self.assertRaises(tool.ApplyError):
                parse(archive([(entry,b'x')]))
        with self.assertRaises(tool.ApplyError):parse(archive(root_version='1.102.4'))

    def test_wrong_architecture_nonelf_and_incomplete_pair_rejected(self):
        for body in (b'#!/bin/sh\necho malicious\n', elf(machine=183), elf(elf_class=1), b'\x7fELF'):
            with self.subTest(body=body[:8]), self.assertRaises(tool.ApplyError):
                parse(archive(replacements={'tailscaled':body}))
        raw=io.BytesIO()
        with tarfile.open(fileobj=raw,mode='w:gz') as package:
            entry=tarfile.TarInfo('tailscale_'+VERSION+'_amd64/tailscale');entry.size=len(elf())
            package.addfile(entry,io.BytesIO(elf()))
        with self.assertRaises(tool.ApplyError):parse(raw.getvalue())

    def test_corruption_and_truncation_are_not_validated_by_matching_digest(self):
        raw=archive()
        for damaged in (raw[:20], raw[:len(raw)//2], raw[:-8], b'not gzip'):
            with self.subTest(length=len(damaged)), self.assertRaises(tool.ApplyError):
                parse(damaged)

    def test_checksum_cannot_select_another_archive(self):
        digest=hashlib.sha256(archive()).hexdigest()
        self.assertEqual(tool.runtime_checksum(digest.encode(), ARCHIVE_NAME),digest)
        self.assertEqual(tool.runtime_checksum((digest+'  '+ARCHIVE_NAME+'\n').encode(),ARCHIVE_NAME),digest)
        for body in ((digest+'  another.tgz\n').encode(), (digest+'\n'+digest+'\n').encode(), b'x'*64):
            with self.subTest(body=body[:12]), self.assertRaises(tool.ApplyError):
                tool.runtime_checksum(body,ARCHIVE_NAME)

    def test_stable_numeric_version_input(self):
        self.assertGreater(tool.runtime_version('1.104.0'),tool.runtime_version('1.102.4'))
        for value in ('v1.104.0','1.104','1.0104.0','1.104.0-rc1','../1.104.0',True,None):
            with self.subTest(value=value), self.assertRaises(tool.ApplyError):
                tool.runtime_version(value)

    def test_known_upstream_systemd_extras_are_bounded_and_not_installed(self):
        entries=[]
        for name in ('tailscaled.service','tailscaled.defaults','tailscale-online.target','tailscale-wait-online.service'):
            entry=tarfile.TarInfo('tailscale_'+VERSION+'_amd64/systemd/'+name)
            body=b'# fixture unit, not executable';entry.size=len(body)
            entries.append((entry,body))
        self.assertEqual(set(parse(archive(entries))),{'tailscale','tailscaled'})
        for name in ('systemd/unknown.service','systemd/extra-executable','bin/unexpected'):
            unknown=tarfile.TarInfo('tailscale_'+VERSION+'_amd64/'+name)
            body=b'#!/bin/sh\nexit 0\n';unknown.size=len(body);unknown.mode=0o755
            with self.subTest(extra=name),self.assertRaises(tool.ApplyError):
                parse(archive([(unknown,body)]))
        entry=tarfile.TarInfo('tailscale_'+VERSION+'_amd64/systemd/tailscaled.service')
        entry.size=65537
        with self.assertRaises(tool.ApplyError):parse(archive([(entry,b'x'*entry.size)]))

    def test_declared_oversized_binary_rejected(self):
        import gzip
        entry=tarfile.TarInfo('tailscale_'+VERSION+'_amd64/tailscale')
        entry.size=128*1024*1024+1
        raw=gzip.compress(entry.tobuf()+b'\0'*1024)
        with self.assertRaises(tool.ApplyError):parse(raw)

    def test_invalid_deflate_block_is_sanitized_archive_error(self):
        # Gzip header followed by a reserved DEFLATE block type, not a tarfile
        # mock. Reader must turn zlib's parser error into the public error type.
        raw=b'\x1f\x8b\x08\x00'+b'\x00'*6+b'\x07'+b'\x00'*8
        with self.assertRaises(tool.ApplyError):parse(raw)


class RuntimeFenceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='runtime-fence-')
        self.addCleanup(self.temp.cleanup)
        self.directory=Path(self.temp.name).resolve()
        loader=importlib.machinery.SourceFileLoader('runtime_fixture_proxy',str(ROOT/'dots_proxy.py'))
        spec=importlib.util.spec_from_loader(loader.name,loader)
        self.proxy=importlib.util.module_from_spec(spec);loader.exec_module(self.proxy)

    def test_proxy_mutation_rejects_regular_and_dangling_fence(self):
        marker=self.directory/'.runtime-update-block'
        for dangling in (False,True):
            if dangling:marker.symlink_to(self.directory/'missing')
            else:marker.write_text('fixture transaction id')
            try:
                with self.assertRaisesRegex(self.proxy.ProxyError,'runtime update fence'):
                    with self.proxy._operation(self.directory):
                        self.fail('mutation lock yielded despite runtime fence')
            finally:marker.unlink()
        with self.proxy._operation(self.directory):pass

    def test_setup_fence_fragment_blocks_without_touching_live_runtime(self):
        import re
        import subprocess
        source=(ROOT/'tailscale-setup.sh').read_text()
        fragment=re.search(r'if \[\[ -e "\$D/\.runtime-update-block".*?\nfi',source,re.S).group(0)
        self.assertLess(source.index('flock -n 9'),source.index(fragment))
        self.assertLess(source.index(fragment),source.index('if [[ "$MODE" = install ]]; then install_all'))
        self.assertLess(source.index('if [[ "$MODE" = check ]]'),source.index(fragment))
        # Execute only the guard fragment. Full setup would use /tmp/... UID
        # runtime and is deliberately never invoked by this fixture.
        command='D="$1"\nfail() { exit 19; }\n'+fragment+'\nprintf allowed'
        marker=self.directory/'.runtime-update-block'
        for dangling in (False,True):
            if dangling:marker.symlink_to(self.directory/'missing')
            else:marker.write_text('fixture')
            result=subprocess.run(['bash','-c',command,'fixture',str(self.directory)],capture_output=True,timeout=3)
            self.assertEqual(result.returncode,19)
            self.assertNotIn(b'allowed',result.stdout)
            marker.unlink()
        result=subprocess.run(['bash','-c',command,'fixture',str(self.directory)],capture_output=True,timeout=3)
        self.assertEqual(result.stdout,b'allowed')


class RuntimeMetadataTests(unittest.TestCase):
    def test_official_stable_metadata_and_reject_wrong_tarball_or_prerelease(self):
        import json
        valid={'Version':VERSION,'Tarballs':{'amd64':ARCHIVE_NAME}}
        with mock.patch.object(tool,'runtime_fetch',return_value=json.dumps(valid).encode()) as fetch:
            self.assertEqual(tool.runtime_latest(None,123),VERSION)
            self.assertEqual(fetch.call_args.args[0],'https://pkgs.tailscale.com/stable/?mode=json')
        for data in ({'Version':'1.105.0','Tarballs':{'amd64':'tailscale_1.105.0_amd64.tgz'}},
                     {'Version':VERSION,'Tarballs':{'amd64':'https://evil.example/payload'}},
                     {'Version':VERSION,'Tarballs':{'arm64':ARCHIVE_NAME}}, [],
                     {'Version':'1.104.0-rc1','Tarballs':{'amd64':ARCHIVE_NAME}}):
            with self.subTest(data=data), mock.patch.object(tool,'runtime_fetch',return_value=json.dumps(data).encode()):
                with self.assertRaises(tool.RuntimeUpdateError):tool.runtime_latest(None,123)

    def test_explicit_check_is_readonly_and_distinguishes_current_newer_downgrade(self):
        with tempfile.TemporaryDirectory() as root:
            directory=Path(root);sentinel=directory/'untouched';sentinel.write_bytes(b'private fixture')
            before=sentinel.stat().st_ino
            for version,available in ((VERSION,True),('1.102.4',False),('1.100.0',False)):
                with mock.patch.object(tool,'tailscale_version',return_value={'version':'1.102.4'}), mock.patch.object(tool,'runtime_latest') as network:
                    result=tool.runtime_check(directory,directory,version)
                self.assertEqual(result['upgrade_available'],available)
                self.assertFalse(result['automatic_updates']);network.assert_not_called()
            self.assertEqual(list(directory.iterdir()),[sentinel]);self.assertEqual(sentinel.stat().st_ino,before)
            self.assertEqual(sentinel.read_bytes(),b'private fixture')

    def test_fetch_rejects_unapproved_url_expired_deadline_and_worker_failures(self):
        import subprocess,time,json,base64
        for url in ('http://pkgs.tailscale.com/stable/'+ARCHIVE_NAME,
                    'https://evil.example/'+ARCHIVE_NAME,'https://pkgs.tailscale.com/stable/../secret'):
            with mock.patch.object(tool.subprocess,'run') as run:
                with self.assertRaises(tool.RuntimeUpdateError):tool.runtime_fetch(url,None,100,time.monotonic()+1)
                run.assert_not_called()
        url='https://pkgs.tailscale.com/stable/'+ARCHIVE_NAME
        with mock.patch.object(tool.subprocess,'run') as run:
            with self.assertRaisesRegex(tool.RuntimeUpdateError,'network_timeout'):
                tool.runtime_fetch(url,None,100,time.monotonic()-1)
            run.assert_not_called()
        with mock.patch.object(tool.subprocess,'run',side_effect=subprocess.TimeoutExpired('fixture',1)):
            with self.assertRaisesRegex(tool.RuntimeUpdateError,'network_timeout'):tool.runtime_fetch(url,None,100,time.monotonic()+1)
        for result in (mock.Mock(returncode=1,stdout=b'{}'),mock.Mock(returncode=0,stdout=b'not json'),
                       mock.Mock(returncode=0,stdout=json.dumps({'data':base64.b64encode(b'x'*101).decode()}).encode())):
            with mock.patch.object(tool.subprocess,'run',return_value=result):
                with self.assertRaisesRegex(tool.RuntimeUpdateError,'network_failed'):tool.runtime_fetch(url,None,100,time.monotonic()+1)

    def test_pair_version_probe_mismatch_is_not_accepted(self):
        with mock.patch.object(tool,'runtime_probe',side_effect=[VERSION,'1.102.4']):
            with self.assertRaisesRegex(tool.RuntimeUpdateError,'binary_version_mismatch'):tool.runtime_verify_pair(Path('/fixture'),VERSION)

    def test_unknown_installed_version_does_not_report_no_upgrade(self):
        with mock.patch.object(tool,'tailscale_version',return_value={'version':None,'status':'unavailable'}):
            report=tool.runtime_check(Path('/fixture'),Path('/fixture'),VERSION)
        self.assertIsNone(report['upgrade_available'])
        self.assertNotEqual(report['installed_version_status'],'available')


class RuntimeTransactionTests(unittest.TestCase):
    def setUp(self):
        import contextlib
        self.temp=tempfile.TemporaryDirectory(prefix='runtime-transaction-');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.directory=self.root/'install';self.directory.mkdir(mode=0o700)
        self.runtime=self.root/'runtime';self.runtime.mkdir(mode=0o700)
        self.tx=self.root/'transaction';self.tx.mkdir(mode=0o700);(self.tx/'stage').mkdir(mode=0o700)
        self.old={name:elf()+b'OLD' for name in ('tailscale','tailscaled')}
        self.new={name:elf()+b'NEW' for name in ('tailscale','tailscaled')}
        for name in self.old:
            (self.directory/name).write_bytes(self.old[name]);(self.directory/name).chmod(0o700)
            (self.tx/'stage'/name).write_bytes(self.new[name]);(self.tx/'stage'/name).chmod(0o700)
        self.protected={}
        for name in ('state/tailscaled.state','sshd/ssh_host_ed25519_key','sshd/ssh_host_ed25519_key.pub',
                     'shim/ca.key','outbound-proxy.json'):
            p=self.directory/name;p.parent.mkdir(mode=0o700,exist_ok=True);p.write_bytes(b'fixture only '+name.encode());p.chmod(0o600)
            self.protected[name]=(p.stat().st_ino,p.read_bytes())
        package={name:('fixture package '+name).encode() for name in tool.CORE_FILES}
        manifest={'schema_version':1,'package_version':'0.5.0','files':{n:hashlib.sha256(b).hexdigest() for n,b in package.items()}}
        package[tool.MANIFEST]=json.dumps(manifest).encode()
        for name,body in package.items():
            path=self.directory/tool.CORE_FILES.get(name,name);path.parent.mkdir(mode=0o700,parents=True,exist_ok=True);path.write_bytes(body)
        self.package=package
        self.journal={'schema':1,'transaction_id':'a'*32,'boot_id':'11111111-1111-4111-8111-111111111111','state':'prepared','version':VERSION,
                      'binary_hashes':{n:hashlib.sha256(v).hexdigest() for n,v in self.new.items()},
                      'package_hashes':{n:hashlib.sha256(v).hexdigest() for n,v in package.items()}}
        self.baseline={name:{'pid':i,'ticks':str(i),'uid':os.getuid(),'exe':'/fixture'} for name,i in (('shim',101),('sshd',102),('tailscaled',103))}
        self.baseline['_socket']={'inode':1}
        self.config={'restart_required':False,'daemon_running':True,'desired':{'socks_port':1080,'http_port':None}}
        self.argv=[str(self.directory/'tailscaled'),'--tun=userspace-networking','--statedir='+str(self.directory/'state'),
                   '--socket='+str(self.runtime/'tailscaled.sock'),'--port=0','--socks5-server=127.0.0.1:1080']
        self.proxy=mock.Mock();self.proxy._daemon.return_value={'argv':self.argv};self.proxy._socket_owned.return_value=True
        self.proxy._environment.return_value={'FIXTURE':'private environment not exposed'}
        self.proxy.inspect_config.return_value=self.config;self.proxy._flags.return_value=self.argv[-1:]
        self.child=mock.Mock(pid=201);self.proxy._launch.return_value=self.child
        self.stack=contextlib.ExitStack();self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.object(tool,'apply_components',return_value=self.baseline))
        self.stack.enter_context(mock.patch.object(tool,'apply_identity',side_effect=lambda pid:next((v for v in self.baseline.values() if v.get('pid')==pid),None)))
        self.public=self.stack.enter_context(mock.patch.object(tool,'apply_public_identity',return_value='same-public-node'))
        self.stop=self.stack.enter_context(mock.patch.object(tool,'apply_stop',side_effect=lambda process:setattr(self.proxy._daemon,'return_value',None)))
        self.proxy._socket_identity.return_value=None
        self.stop_child=self.stack.enter_context(mock.patch.object(tool,'apply_stop_child'))
        self.probe=self.stack.enter_context(mock.patch.object(tool,'runtime_probe',side_effect=lambda path,arg:VERSION if Path(path).read_bytes().endswith(b'NEW') else '1.102.4'))

    def run_transaction(self):tool.runtime_transaction(self.directory,self.runtime,self.tx,self.journal,self.proxy)
    def assert_preserved(self):
        for name,expected in self.protected.items():
            p=self.directory/name;self.assertEqual((p.stat().st_ino,p.read_bytes()),expected,name)
    def pair(self):return {n:(self.directory/n).read_bytes() for n in self.old}

    def test_success_updates_pair_only_retains_node_ssh_proxy_and_no_external_success(self):
        self.run_transaction();self.assertEqual(self.journal['state'],'applied');self.assertEqual(self.pair(),self.new)
        self.assertFalse((self.directory/tool.RUNTIME_FENCE).exists());self.assert_preserved()
        self.stop.assert_called_once_with(self.baseline['tailscaled']);self.proxy._launch.assert_called_once()
        self.assertEqual(self.proxy._launch.call_args.args[0],self.argv)
        self.assertEqual(self.journal['state_compatibility'],'current_version_only')

    def test_preexec_failure_rolls_back_exact_pair_and_preserves_state(self):
        original=tool.apply_write;fired=[]
        def fail(path,data,mode=0o600):
            original(path,data,mode)
            if path==self.directory/'tailscale' and not fired:fired.append(True);raise OSError('fixture write fault')
        with mock.patch.object(tool,'apply_write',side_effect=fail):self.run_transaction()
        self.assertEqual(self.journal['state'],'failed_rolled_back');self.assertEqual(self.pair(),self.old)
        self.assertFalse((self.directory/tool.RUNTIME_FENCE).exists());self.assert_preserved()
        self.proxy._launch.assert_called_once()  # Only restored old daemon, mocked.

    def test_postexec_readiness_failure_never_relaunches_old_daemon(self):
        self.proxy._ready.side_effect=RuntimeError('fixture readiness failure')
        self.run_transaction();self.assertEqual(self.journal['state'],'manual_recovery_required')
        self.assertEqual(self.pair(),self.new);self.assertTrue((self.directory/tool.RUNTIME_FENCE).exists())
        self.assertEqual(self.proxy._launch.call_count,1);self.stop_child.assert_called_once_with(self.child);self.assert_preserved()

    def test_launch_exception_is_uncertain_execution_and_never_rolls_back(self):
        self.proxy._launch.side_effect=OSError('fixture launch uncertainty')
        self.run_transaction();self.assertEqual(self.journal['state'],'manual_recovery_required')
        self.assertEqual(self.pair(),self.new);self.assertTrue((self.directory/tool.RUNTIME_FENCE).exists());self.assert_preserved()

    def test_stop_uncertainty_keeps_fence_and_original_pair(self):
        self.stop.side_effect=tool.ApplyError('fixture stop uncertainty')
        self.run_transaction();self.assertEqual(self.journal['state'],'manual_recovery_required')
        self.proxy._launch.assert_not_called();self.assertEqual(self.pair(),self.old)
        self.assertTrue((self.directory/tool.RUNTIME_FENCE).exists());self.assert_preserved()

    def test_candidate_source_tamper_and_unsafe_installed_symlink_stop_before_signal(self):
        (self.tx/'stage'/'tailscaled').write_bytes(elf()+b'changed')
        with self.assertRaisesRegex(tool.RuntimeUpdateError,'source_changed'):self.run_transaction()
        self.stop.assert_not_called();self.assert_preserved()
        (self.tx/'stage'/'tailscaled').write_bytes(self.new['tailscaled'])
        (self.directory/'tailscale').unlink();(self.directory/'tailscale').symlink_to(self.tx/'stage'/'tailscale')
        with self.assertRaises(tool.ApplyError):self.run_transaction()
        self.stop.assert_not_called();self.assert_preserved()

    def test_same_version_and_downgrade_rejected_before_signal(self):
        for version in ('1.102.4','1.100.0'):
            self.journal['version']=version
            with self.assertRaisesRegex(tool.RuntimeUpdateError,'downgrade_or_same_version'):self.run_transaction()
        self.stop.assert_not_called();self.assert_preserved()

    def test_changed_public_node_after_candidate_start_requires_manual(self):
        self.public.side_effect=['old-node','different-node']
        self.run_transaction();self.assertEqual(self.journal['state'],'manual_recovery_required')
        self.proxy._launch.assert_called_once();self.assertTrue((self.directory/tool.RUNTIME_FENCE).exists());self.assert_preserved()

    def test_candidate_journal_failure_keeps_fence(self):
        original=tool.apply_journal
        def failing(transaction,journal,**changes):
            if changes.get('phase')=='candidate_readiness':raise OSError('fixture journal failure')
            return original(transaction,journal,**changes)
        with mock.patch.object(tool,'apply_journal',side_effect=failing):self.run_transaction()
        self.assertEqual(self.journal['state'],'manual_recovery_required');self.proxy._launch.assert_called_once()
        self.assertTrue((self.directory/tool.RUNTIME_FENCE).exists());self.assert_preserved()

    def test_unknown_socket_after_stop_blocks_old_binary_restart(self):
        self.proxy._remove_socket.side_effect=RuntimeError('socket changed owner')
        self.proxy._socket_identity.return_value={'inode':999}
        self.run_transaction()
        self.assertEqual(self.journal['state'],'manual_recovery_required')
        self.assertEqual(self.journal['error'],'rollback_failed')
        self.proxy._launch.assert_not_called()
        self.assertEqual(self.pair(),self.old);self.assert_preserved()
        self.assertTrue((self.directory/tool.RUNTIME_FENCE).exists())

    def test_proxy_config_drift_after_candidate_start_requires_manual(self):
        self.proxy.inspect_config.side_effect=[self.config,{**self.config,'restart_required':True}]
        self.run_transaction()
        self.assertEqual(self.journal['state'],'manual_recovery_required')
        self.proxy._launch.assert_called_once();self.assert_preserved()

    def test_preserved_ssh_process_identity_change_requires_manual(self):
        with mock.patch.object(tool,'apply_identity',return_value=None):self.run_transaction()
        self.assertEqual(self.journal['state'],'manual_recovery_required')
        self.proxy._launch.assert_called_once();self.assert_preserved()

    def fence_fsync_fault(self):
        import stat
        original=tool.os.fsync;fired=[]
        def sync(fd):
            info=os.fstat(fd)
            if stat.S_ISDIR(info.st_mode) and info.st_ino==self.directory.stat().st_ino and not (self.directory/tool.RUNTIME_FENCE).exists() and not fired:
                fired.append(True)
                raise OSError('fixture directory fsync after fence unlink')
            return original(fd)
        return sync,fired

    def test_fence_unlink_then_fsync_failure_cannot_leave_stopped_unfenced(self):
        sync,fired=self.fence_fsync_fault()
        with mock.patch.object(tool.os,'fsync',side_effect=sync):self.run_transaction()
        self.assertTrue(fired)
        self.assertIn(self.journal['state'],('applied','manual_recovery_required'))
        self.assertTrue((self.directory/tool.RUNTIME_FENCE).exists())
        self.stop_child.assert_not_called()  # Verified durable commit remains running.
        self.assertEqual(self.pair(),self.new);self.assert_preserved()
        self.proxy._launch.assert_called_once()

    def test_fence_cleanup_and_cleanup_warning_write_failure_still_project_manual(self):
        sync,fired=self.fence_fsync_fault();original=tool.apply_journal;warning_failures=[]
        def journal(transaction,value,**changes):
            if changes.get('phase')=='cleanup_review':
                warning_failures.append(True);raise OSError('fixture cleanup warning journal unavailable')
            return original(transaction,value,**changes)
        with mock.patch.object(tool.os,'fsync',side_effect=sync),mock.patch.object(tool,'apply_journal',side_effect=journal):
            self.run_transaction()
        self.assertTrue(fired);self.assertTrue(warning_failures);self.stop_child.assert_not_called()
        self.assertTrue((self.directory/tool.RUNTIME_FENCE).exists())
        canonical=self.directory/tool.RUNTIME_DIRECTORY/self.journal['transaction_id'];canonical.mkdir(mode=0o700,parents=True)
        tool.apply_write(canonical/'journal.json',(self.tx/'journal.json').read_bytes())
        report=tool.runtime_status(self.directory,self.journal['transaction_id'])
        self.assertEqual(report['state'],'manual_recovery_required');self.assert_preserved()

    def test_candidate_exec_intent_journal_failure_rolls_back_before_candidate_launch(self):
        original=tool.apply_journal
        def journal(transaction,value,**changes):
            if changes.get('phase')=='candidate_exec_intent':raise OSError('fixture intent journal failure')
            return original(transaction,value,**changes)
        with mock.patch.object(tool,'apply_journal',side_effect=journal):self.run_transaction()
        self.assertEqual(self.journal['state'],'failed_rolled_back');self.assertEqual(self.pair(),self.old)
        self.proxy._launch.assert_called_once();self.stop_child.assert_not_called();self.assert_preserved()

    def test_candidate_stop_timeout_preserves_manual_fence(self):
        self.proxy._ready.side_effect=RuntimeError('fixture readiness failure')
        self.stop_child.side_effect=tool.ApplyError('fixture child stop timeout')
        self.run_transaction()
        self.assertEqual(self.journal['state'],'manual_recovery_required')
        self.assertTrue((self.directory/tool.RUNTIME_FENCE).exists());self.proxy._launch.assert_called_once();self.assert_preserved()

    def test_stale_atomic_temporary_is_rejected_before_stopping_daemon(self):
        stale=self.directory/'tailscale.new';stale.write_bytes(b'preexisting unrelated data')
        try:self.run_transaction()
        except (tool.ApplyError,OSError):pass
        self.stop.assert_not_called();self.proxy._launch.assert_not_called()
        self.assertEqual(stale.read_bytes(),b'preexisting unrelated data');self.assertEqual(self.pair(),self.old);self.assert_preserved()

    def test_baseline_changed_after_backup_refused_before_signal(self):
        original=tool.runtime_verify_pair
        def verify(path,version):
            original(path,version)
            if Path(path)==self.tx/'stage':(self.directory/'tailscale').write_bytes(elf()+b'externally changed')
        with mock.patch.object(tool,'runtime_verify_pair',side_effect=verify):
            with self.assertRaisesRegex(tool.ApplyError,'source_changed'):self.run_transaction()
        self.stop.assert_not_called();self.proxy._launch.assert_not_called();self.assert_preserved()

    def test_terminal_journal_uncertainty_and_failed_manual_report_remain_fenced(self):
        original=tool.apply_journal;fired=[]
        def journal(transaction,value,**changes):
            if changes.get('state')=='manual_recovery_required':raise OSError('manual report unavailable')
            original(transaction,value,**changes)
            if changes.get('state')=='applied' and not fired:
                fired.append(True);raise OSError('terminal acknowledgement uncertain')
        with mock.patch.object(tool,'apply_journal',side_effect=journal):self.run_transaction()
        self.assertTrue(fired);self.stop_child.assert_called_once_with(self.child)
        self.assertTrue((self.directory/tool.RUNTIME_FENCE).exists());self.assert_preserved()
        canonical=self.directory/tool.RUNTIME_DIRECTORY/self.journal['transaction_id'];canonical.mkdir(mode=0o700,parents=True)
        tool.apply_write(canonical/'journal.json',(self.tx/'journal.json').read_bytes())
        self.assertEqual(tool.runtime_status(self.directory,self.journal['transaction_id'])['state'],'manual_recovery_required')

    def test_rollback_same_version_but_changed_binary_hash_is_not_success(self):
        original=tool.apply_write;fired=[]
        def write(path,data,mode=0o600):
            original(path,data,mode)
            if path==self.directory/'tailscale' and not fired:
                fired.append(True);raise OSError('candidate replacement failed')
        self.proxy._ready.side_effect=lambda *a:(self.directory/'tailscale').write_bytes(elf()+b'changed-but-old-version')
        with mock.patch.object(tool,'apply_write',side_effect=write):self.run_transaction()
        self.assertEqual(self.journal['state'],'manual_recovery_required')
        self.assertTrue((self.directory/tool.RUNTIME_FENCE).exists());self.assert_preserved()

    def test_committed_healthy_daemon_survives_failed_fence_restore(self):
        sync,fired=self.fence_fsync_fault();original_open=os.open
        def opening(path,flags,*args,**kwargs):
            if path==tool.RUNTIME_FENCE and fired and flags & os.O_EXCL:
                raise PermissionError('fixture fence recreation denied')
            return original_open(path,flags,*args,**kwargs)
        with mock.patch.object(tool.os,'fsync',side_effect=sync),mock.patch.object(tool.os,'open',side_effect=opening):
            self.run_transaction()
        self.assertTrue(fired);self.stop_child.assert_not_called();self.proxy._launch.assert_called_once()
        self.assertFalse((self.directory/tool.RUNTIME_FENCE).exists())
        durable=json.loads((self.tx/'journal.json').read_text())
        self.assertEqual(durable['state'],'applied');self.assertEqual(durable['cleanup_warning'],'fence_cleanup_failed')
        self.assertEqual(self.pair(),self.new);self.assert_preserved()

    def test_rollback_journal_records_restore_and_readiness_before_terminal(self):
        original_write=tool.apply_write;original_journal=tool.apply_journal;fired=[];phases=[]
        def write(path,data,mode=0o600):
            original_write(path,data,mode)
            if path==self.directory/'tailscale' and not fired:
                fired.append(True);raise OSError('fixture pre-exec replacement failure')
        def journal(transaction,value,**changes):
            original_journal(transaction,value,**changes)
            if 'phase' in changes:phases.append(changes['phase'])
        with mock.patch.object(tool,'apply_write',side_effect=write),mock.patch.object(tool,'apply_journal',side_effect=journal):
            self.run_transaction()
        self.assertEqual(self.journal['state'],'failed_rolled_back')
        self.assertLess(phases.index('rollback_restore'),phases.index('rollback_readiness'))
        self.assertLess(phases.index('rollback_readiness'),phases.index('complete'))
        self.assertEqual(self.journal['restored_pid'],self.child.pid)
        self.assertEqual(self.pair(),self.old);self.assert_preserved()


class RuntimeStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='runtime-status-');self.addCleanup(self.temp.cleanup)
        self.directory=Path(self.temp.name).resolve();self.identifier='b'*32
        self.tx=self.directory/tool.RUNTIME_DIRECTORY/self.identifier;self.tx.mkdir(mode=0o700,parents=True)
        self.worker={'pid':123,'ticks':'45','uid':os.getuid(),'exe':'/fixture'}
        self.journal={'schema':1,'transaction_id':self.identifier,'state':'running','boot_id':'22222222-2222-4222-8222-222222222222',
                      'version':VERSION,'phase':'candidate_readiness','worker':self.worker,
                      'environment':'SECRET_ENV','argv':['SECRET_ARGUMENT'],'error':'SECRET_ERROR'}
        tool.apply_journal(self.tx,self.journal)

    def test_dead_worker_or_boot_change_requires_manual_and_status_is_readonly(self):
        before=(self.tx/'journal.json').read_bytes()
        for boot,identity in (('33333333-3333-4333-8333-333333333333',self.worker),('22222222-2222-4222-8222-222222222222',None)):
            with mock.patch.object(tool,'apply_boot',return_value=boot),mock.patch.object(tool,'apply_identity',return_value=identity):
                result=tool.runtime_status(self.directory,self.identifier)
            self.assertEqual(result['state'],'manual_recovery_required');self.assertEqual(result['external_ssh'],'pending')
            self.assertNotIn('SECRET',json.dumps(result));self.assertEqual((self.tx/'journal.json').read_bytes(),before)

    def test_terminal_success_with_stale_fence_is_not_reported_applied(self):
        tool.apply_journal(self.tx,self.journal,state='applied',phase='complete')
        marker=self.directory/tool.RUNTIME_FENCE;marker.symlink_to(self.directory/'missing')
        result=tool.runtime_status(self.directory,self.identifier)
        self.assertEqual(result['state'],'manual_recovery_required');self.assertEqual(result['external_ssh'],'pending')

    def test_status_rejects_path_traversal_symlink_and_wrong_journal_id(self):
        for identifier in ('../outside','a'*31,'A'*32,'b'*32+'/extra'):
            with self.assertRaises(tool.ApplyError):tool.runtime_status(self.directory,identifier)
        tool.apply_journal(self.tx,self.journal,transaction_id='c'*32)
        with self.assertRaises(tool.ApplyError):tool.runtime_status(self.directory,self.identifier)
        journal=self.tx/'journal.json';journal.unlink();journal.symlink_to(self.directory/'missing')
        with self.assertRaises((tool.ApplyError,ValueError,OSError)):tool.runtime_status(self.directory,self.identifier)

    def test_fence_is_only_cleared_for_own_exact_transaction(self):
        tool.runtime_fence(self.directory,self.journal)
        other=dict(self.journal,transaction_id='c'*32)
        with self.assertRaises(tool.ApplyError):tool.runtime_clear_fence(self.directory,other)
        self.assertTrue((self.directory/tool.RUNTIME_FENCE).exists())
        tool.runtime_clear_fence(self.directory,self.journal)
        self.assertFalse((self.directory/tool.RUNTIME_FENCE).exists())

    def test_malformed_worker_metadata_is_manual_not_success(self):
        for worker in ({'pid':True}, {'pid':'123'}, {'pid':123,'uid':-1}, []):
            tool.apply_write(self.tx/'journal.json',json.dumps({**self.journal,'worker':worker}).encode())
            with mock.patch.object(tool,'apply_boot',return_value='22222222-2222-4222-8222-222222222222'):
                report=tool.runtime_status(self.directory,self.identifier)
            self.assertEqual(report['state'],'manual_recovery_required')


    def test_another_transactions_valid_fence_does_not_relabel_historical_success(self):
        tool.apply_journal(self.tx,self.journal,state='applied',phase='complete')
        tool.runtime_fence(self.directory,dict(self.journal,transaction_id='c'*32))
        report=tool.runtime_status(self.directory,self.identifier)
        self.assertEqual(report['state'],'applied');self.assertEqual(report['external_ssh'],'pending')

    def test_invalid_other_fence_schema_never_allows_historical_success(self):
        tool.apply_journal(self.tx,self.journal,state='applied',phase='complete')
        valid={'schema':1,'transaction_id':'c'*32,'boot_id':'11111111-1111-4111-8111-111111111111'}
        for changes in ({'schema':True},{'schema':2},{'boot_id':'invalid'},
                        {'transaction_id':'not-an-id'},{'unexpected':True}):
            tool.apply_write(self.directory/tool.RUNTIME_FENCE,json.dumps({**valid,**changes}).encode())
            self.assertEqual(tool.runtime_status(self.directory,self.identifier)['state'],'manual_recovery_required')



class RuntimeApplyWorkerTests(unittest.TestCase):
    def setUp(self):
        import contextlib,sys
        RuntimeTransactionTests.setUp(self)
        self.bundle=self.root/'bundle';self.bundle.mkdir(mode=0o700)
        raw=archive();(self.bundle/ARCHIVE_NAME).write_bytes(raw);(self.bundle/ARCHIVE_NAME).chmod(0o600)
        (self.bundle/(ARCHIVE_NAME+'.sha256')).write_text(hashlib.sha256(raw).hexdigest());(self.bundle/(ARCHIVE_NAME+'.sha256')).chmod(0o600)
        self.stack.enter_context(mock.patch.object(tool,'runtime_platform'))
        self.stack.enter_context(mock.patch.object(tool,'apply_bundle',return_value=self.package))
        self.stack.enter_context(mock.patch.object(tool,'runtime_verify_pair'))
        self.stack.enter_context(mock.patch.object(tool,'runtime_probe',return_value='1.102.4'))
        self.stack.enter_context(mock.patch.object(tool,'apply_boot',return_value='11111111-1111-4111-8111-111111111111'))
        self.proxy._operation.side_effect=lambda d:contextlib.nullcontext()
        self.stack.enter_context(mock.patch.dict(sys.modules,{'dots_proxy':self.proxy}))
        self.spawn=self.stack.enter_context(mock.patch.object(tool.subprocess,'Popen',return_value=mock.Mock(pid=301)))

    def apply(self,**overrides):
        return tool.runtime_apply(self.directory,self.runtime,VERSION,bundle=self.bundle,confirm=True,**overrides)

    def test_detached_worker_is_frozen_and_waits_for_id_ack(self):
        result=self.apply();identifier=result['transaction_id'];tx=self.directory/tool.RUNTIME_DIRECTORY/identifier
        self.assertEqual(result['state'],'prepared');self.assertEqual(result['external_ssh'],'pending')
        self.assertFalse((tx/'ack').exists())
        argv=self.spawn.call_args.args[0];options=self.spawn.call_args.kwargs
        self.assertIn(str(tx/'supervisor'/'dots-ts'),argv)
        self.assertTrue(options['start_new_session']);self.assertTrue(options['close_fds'])
        self.assertEqual((tx/'supervisor'/'dots-ts').read_bytes(),self.package['dots-ts'])
        self.assertEqual({n:(self.directory/n).read_bytes() for n in self.old},self.old)
        tool.runtime_ack(self.directory,identifier);self.assertEqual((tx/'ack').read_bytes(),b'1\n')
        RuntimeTransactionTests.assert_preserved(self)

    def test_confirmation_unsafe_bundle_and_bundle_symlink_fail_before_spawn(self):
        with self.assertRaisesRegex(tool.ApplyError,'confirmation_required'):
            tool.runtime_apply(self.directory,self.runtime,VERSION,bundle=self.bundle,confirm=False)
        self.bundle.chmod(0o755)
        with self.assertRaisesRegex(tool.ApplyError,'unsafe_bundle'):self.apply()
        self.bundle.chmod(0o700)
        archive_path=self.bundle/ARCHIVE_NAME;archive_path.unlink();archive_path.symlink_to(self.directory/'tailscale')
        with self.assertRaisesRegex(tool.ApplyError,'unsafe_bundle'):self.apply()
        self.spawn.assert_not_called();RuntimeTransactionTests.assert_preserved(self)

    def test_prior_unresolved_runtime_transaction_blocks_second_spawn(self):
        self.apply();self.spawn.reset_mock()
        with self.assertRaisesRegex(tool.ApplyError,'prior_transaction_requires_manual_review'):self.apply()
        self.spawn.assert_not_called()

    def worker_fixture(self,ack=True):
        result=self.apply();identifier=result['transaction_id'];tx=self.directory/tool.RUNTIME_DIRECTORY/identifier
        if ack:tool.runtime_ack(self.directory,identifier)
        self.proxy.__file__=str(tx/'supervisor'/'dots_proxy.py')
        self.stack.enter_context(mock.patch.object(tool,'__file__',str(tx/'supervisor'/'dots-ts')))
        return tx

    def test_worker_ack_timeout_never_enters_transaction(self):
        tx=self.worker_fixture(ack=False)
        with mock.patch.object(tool.time,'monotonic',side_effect=[0,31]),mock.patch.object(tool,'runtime_transaction') as execute:
            self.assertEqual(tool.runtime_worker(self.directory,self.runtime,tx),1)
            execute.assert_not_called()
        self.assertEqual(tool.apply_read_journal(tx)['error'],'worker_not_acknowledged')

    def test_worker_frozen_code_tamper_or_boot_change_is_rejected_before_transaction(self):
        tx=self.worker_fixture()
        with mock.patch.object(tool,'apply_boot',return_value='different-boot'),mock.patch.object(tool,'runtime_transaction') as execute:
            with self.assertRaises(tool.ApplyError):tool.runtime_worker(self.directory,self.runtime,tx)
            execute.assert_not_called()
        (tx/'supervisor'/'dots-ts').write_bytes(b'changed frozen worker')
        with mock.patch.object(tool,'runtime_transaction') as execute:
            with self.assertRaisesRegex(tool.ApplyError,'source_changed'):tool.runtime_worker(self.directory,self.runtime,tx)
            execute.assert_not_called()

    def test_worker_interruption_preserves_fence_and_reports_manual(self):
        tx=self.worker_fixture()
        def interrupted(d,r,t,j,p,commit_outcome=None):
            tool.runtime_fence(d,j)
            raise KeyboardInterrupt()
        with mock.patch.object(tool,'runtime_transaction',side_effect=interrupted):
            tool.runtime_worker(self.directory,self.runtime,tx)
        self.assertEqual(json.loads((tx/'journal.json').read_text())['state'],'manual_recovery_required')
        self.assertTrue((self.directory/tool.RUNTIME_FENCE).exists());RuntimeTransactionTests.assert_preserved(self)

    def test_bundle_writable_file_is_rejected_before_worker(self):
        (self.bundle/ARCHIVE_NAME).chmod(0o666)
        with self.assertRaisesRegex(tool.ApplyError,'unsafe_bundle'):self.apply()
        self.spawn.assert_not_called()

    def test_bundle_foreign_owner_is_rejected_before_worker(self):
        from types import SimpleNamespace
        original=Path.stat
        original_fstat=os.fstat
        target=self.bundle/ARCHIVE_NAME
        target_info=target.stat()
        def stat_file(path,*args,**kwargs):
            info=original(path,*args,**kwargs)
            if path==target:
                values={name:getattr(info,name) for name in dir(info) if name.startswith('st_')}
                values['st_uid']=os.getuid()+1
                return SimpleNamespace(**values)
            return info
        def fstat_file(fd):
            info=original_fstat(fd)
            if (info.st_dev,info.st_ino)==(target_info.st_dev,target_info.st_ino):
                values={name:getattr(info,name) for name in dir(info) if name.startswith('st_')}
                values['st_uid']=os.getuid()+1
                return SimpleNamespace(**values)
            return info
        with mock.patch.object(Path,'stat',stat_file),mock.patch.object(os,'fstat',fstat_file):
            with self.assertRaisesRegex(tool.ApplyError,'unsafe_bundle'):self.apply()
        self.spawn.assert_not_called()

    def test_main_output_failure_does_not_acknowledge_worker(self):
        import io
        report={'state':'prepared','transaction_id':'a'*32,'version':VERSION,'external_ssh':'pending'}
        class BrokenOutput(io.StringIO):
            def write(self,text):raise BrokenPipeError('fixture closed SSH output')
        with mock.patch.object(tool,'directories',return_value=(self.directory,self.runtime)), \
             mock.patch.object(tool,'runtime_apply',return_value=report) as apply, \
             mock.patch.object(tool,'runtime_ack') as ack,mock.patch.object(tool.sys,'stdout',BrokenOutput()),mock.patch.object(tool.sys,'stderr',io.StringIO()):
            try:tool.main(['runtime','apply','--version',VERSION,'--confirm-recovery-terminal','--json'])
            except (BrokenPipeError,SystemExit):pass
        apply.assert_called_once()
        ack.assert_not_called()

    def test_cli_status_read_failure_preserves_id_and_is_not_terminal_rejection(self):
        import io
        output=io.StringIO()
        with mock.patch.object(tool,'directories',return_value=(self.directory,self.runtime)), \
             mock.patch.object(tool,'runtime_status',side_effect=tool.RuntimeUpdateError('invalid_transaction')), \
             mock.patch.object(tool.sys,'stdout',output),mock.patch.object(tool.sys,'stderr',io.StringIO()):
            result=tool.main(['runtime','status','--id','a'*32,'--json'])
        report=json.loads(output.getvalue())
        self.assertEqual(report['state'],'status_unavailable')
        self.assertEqual(report['transaction_id'],'a'*32);self.assertNotEqual(result,0)

    def test_cli_ack_visible_then_directory_fsync_failure_preserves_uncertainty_and_id(self):
        import io,stat
        prepared=self.apply();identifier=prepared['transaction_id'];tx=self.directory/tool.RUNTIME_DIRECTORY/identifier
        original=tool.os.fsync;fired=[]
        def sync(fd):
            info=os.fstat(fd)
            if stat.S_ISDIR(info.st_mode) and info.st_ino==tx.stat().st_ino and (tx/'ack').exists():
                fired.append(True);raise OSError('fixture ack is visible but durability uncertain')
            return original(fd)
        output=io.StringIO()
        with mock.patch.object(tool,'directories',return_value=(self.directory,self.runtime)), \
             mock.patch.object(tool,'runtime_apply',return_value=prepared),mock.patch.object(tool.os,'fsync',side_effect=sync), \
             mock.patch.object(tool.sys,'stdout',output),mock.patch.object(tool.sys,'stderr',io.StringIO()):
            result=tool.main(['runtime','apply','--version',VERSION,'--confirm-recovery-terminal','--json'])
        reports=[json.loads(line) for line in output.getvalue().splitlines()]
        self.assertTrue(fired);self.assertTrue((tx/'ack').exists())
        self.assertEqual(reports[0]['state'],'prepared');self.assertEqual(reports[-1]['state'],'ack_uncertain')
        self.assertEqual(reports[-1]['transaction_id'],identifier);self.assertNotEqual(result,0)
        self.assertNotIn('rejected',[r['state'] for r in reports])

    def test_staging_keyboard_interrupt_records_rejected_without_spawning(self):
        with mock.patch.object(tool,'runtime_verify_pair',side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):self.apply()
        transactions=list((self.directory/tool.RUNTIME_DIRECTORY).iterdir())
        self.assertEqual(len(transactions),1)
        journal=json.loads((transactions[0]/'journal.json').read_text())
        self.assertEqual(journal['state'],'rejected');self.spawn.assert_not_called()
        RuntimeTransactionTests.assert_preserved(self)

    def test_other_workers_fence_does_not_claim_this_worker_mutated_runtime(self):
        tx=self.worker_fixture()
        other={'schema':1,'transaction_id':'f'*32,'boot_id':'11111111-1111-4111-8111-111111111111'}
        tool.apply_write(self.directory/tool.RUNTIME_FENCE,json.dumps(other).encode())
        self.proxy._operation.side_effect=RuntimeError('other operation fenced')
        with mock.patch.object(tool,'runtime_transaction') as transaction:
            tool.runtime_worker(self.directory,self.runtime,tx)
        transaction.assert_not_called()
        self.assertEqual(json.loads((tx/'journal.json').read_text())['state'],'rejected')
        self.assertEqual(json.loads((self.directory/tool.RUNTIME_FENCE).read_text()),other)
        RuntimeTransactionTests.assert_preserved(self)

    def test_cli_status_cleanup_warning_preserves_original_error_and_nonzero_exit(self):
        import io
        status={'state':'failed_rolled_back','transaction_id':'a'*32,'version':VERSION,
                'error':'candidate_failed','cleanup_warning':'fence_cleanup_failed','external_ssh':'pending'}
        output=io.StringIO()
        with mock.patch.object(tool,'directories',return_value=(self.directory,self.runtime)), \
             mock.patch.object(tool,'runtime_status',return_value=status),mock.patch.object(tool.sys,'stdout',output):
            code=tool.main(['runtime','status','--id','a'*32,'--json'])
        report=json.loads(output.getvalue())
        self.assertNotEqual(code,0);self.assertEqual(report['error'],'candidate_failed')
        self.assertEqual(report['cleanup_warning'],'fence_cleanup_failed')

    def test_staging_error_transaction_id_survives_exception_constructor_and_cli(self):
        import io
        identifier='d'*32
        error=tool.RuntimeUpdateError('binary_probe_failed',identifier)
        self.assertEqual(error.transaction_id,identifier)
        self.assertEqual(tool.runtime_error(error)['transaction_id'],identifier)
        output=io.StringIO()
        with mock.patch.object(tool,'directories',return_value=(self.directory,self.runtime)), \
             mock.patch.object(tool,'runtime_apply',side_effect=error),mock.patch.object(tool.sys,'stdout',output):
            result=tool.main(['runtime','apply','--version',VERSION,'--confirm-recovery-terminal','--json'])
        report=json.loads(output.getvalue())
        self.assertEqual(report['transaction_id'],identifier);self.assertEqual(report['error'],'binary_probe_failed')
        self.assertNotEqual(result,0)

    def test_malformed_status_identifier_is_never_echoed(self):
        import io
        secret='SECRET_TOKEN_INVALID_IDENTIFIER'
        for explicit,error in ((secret,tool.RuntimeUpdateError('invalid_transaction')),
                               (None,tool.RuntimeUpdateError('invalid_transaction',secret))):
            report=tool.runtime_error(error,explicit)
            self.assertIsNone(report.get('transaction_id'));self.assertNotIn(secret,json.dumps(report))
        output=io.StringIO();diagnostic=io.StringIO()
        with mock.patch.object(tool,'directories',return_value=(self.directory,self.runtime)), \
             mock.patch.object(tool.sys,'stdout',output),mock.patch.object(tool.sys,'stderr',diagnostic):
            result=tool.main(['runtime','status','--id',secret,'--json'])
        self.assertNotEqual(result,0)
        self.assertNotIn(secret,output.getvalue()+diagnostic.getvalue())
        report=json.loads(output.getvalue())
        self.assertEqual(report['error'],'invalid_transaction')
        self.assertIsNone(report.get('transaction_id'))

    def worker_lock_close_fault(self, rollback=False):
        import sys
        tx=self.worker_fixture()
        loader=importlib.machinery.SourceFileLoader('runtime_real_proxy_close_fixture',str(ROOT/'dots_proxy.py'))
        spec=importlib.util.spec_from_loader(loader.name,loader)
        proxy=importlib.util.module_from_spec(spec);loader.exec_module(proxy)
        proxy.__file__=str(tx/'supervisor'/'dots_proxy.py')
        # Real _operation owns and releases a real flock/file descriptor. Only
        # daemon/network operations below are mocked, never a live process.
        for name in ('_daemon','_socket_owned','_environment','inspect_config','_flags','_remove_socket','_launch','_ready','_socket_identity'):
            setattr(proxy,name,getattr(self.proxy,name))
        actual_open=os.open;actual_close=os.close;actual_write=tool.apply_write
        lock_fds=set();fault=[];replacement_fault=[]
        def opening(path,flags,*args,**kwargs):
            fd=actual_open(path,flags,*args,**kwargs)
            if str(path)==str(self.directory/'.operation.lock'):lock_fds.add(fd)
            return fd
        def closing(fd):
            actual_close(fd)
            if fd in lock_fds and not fault:
                fault.append(True)
                raise OSError('fixture lock close result uncertain after real close')
        def writing(path,data,mode=0o600):
            actual_write(path,data,mode)
            if rollback and path==self.directory/'tailscale' and not replacement_fault:
                replacement_fault.append(True);raise OSError('fixture pre-exec replacement fault')
        with mock.patch.dict(sys.modules,{'dots_proxy':proxy}),mock.patch.object(os,'open',side_effect=opening), \
             mock.patch.object(os,'close',side_effect=closing),mock.patch.object(tool,'apply_write',side_effect=writing):
            tool.runtime_worker(self.directory,self.runtime,tx)
        self.assertTrue(fault)
        journal=json.loads((tx/'journal.json').read_text())
        self.assertEqual(journal['state'],'failed_rolled_back' if rollback else 'applied')
        self.assertEqual(journal['cleanup_warning'],'worker_cleanup_failed')
        self.assertFalse((self.directory/tool.RUNTIME_FENCE).exists())
        self.stop_child.assert_not_called();self.proxy._launch.assert_called_once()
        RuntimeTransactionTests.assert_preserved(self)
        return journal

    def test_real_operation_close_failure_preserves_committed_applied_outcome(self):
        self.worker_lock_close_fault()

    def test_real_operation_close_failure_preserves_committed_rollback_outcome(self):
        self.worker_lock_close_fault(rollback=True)

    def test_staging_io_error_is_not_reported_as_binary_probe_failure(self):
        original=tool.apply_write
        def write(path,data,mode=0o600):
            if path.parent.name=='stage':raise OSError('fixture staging storage failure')
            return original(path,data,mode)
        with mock.patch.object(tool,'apply_write',side_effect=write):
            with self.assertRaisesRegex(tool.RuntimeUpdateError,'staging_failed'):self.apply()
        transactions=list((self.directory/tool.RUNTIME_DIRECTORY).iterdir())
        self.assertEqual(len(transactions),1)
        journal=json.loads((transactions[0]/'journal.json').read_text())
        self.assertEqual(journal['state'],'rejected');self.assertEqual(journal['error'],'staging_failed')
        self.spawn.assert_not_called()

    def test_prior_terminal_cleanup_warning_blocks_next_runtime_apply(self):
        result=self.apply();tx=self.directory/tool.RUNTIME_DIRECTORY/result['transaction_id']
        journal=json.loads((tx/'journal.json').read_text())
        tool.apply_journal(tx,journal,state='applied',phase='cleanup_review',cleanup_warning='worker_cleanup_failed')
        self.spawn.reset_mock()
        with self.assertRaisesRegex(tool.RuntimeUpdateError,'prior_transaction_requires_manual_review'):self.apply()
        self.spawn.assert_not_called()

    def test_mutated_terminal_journal_without_commit_proof_still_requires_manual(self):
        tx=self.worker_fixture()
        def uncertain(d,r,t,j,proxy,commit_outcome=None):
            tool.runtime_fence(d,j)
            tool.apply_journal(t,j,state='applied',phase='complete')
            # Durable-write acknowledgement failed: no successful return from
            # the commit operation, so the independent proof remains empty.
            raise OSError('fixture terminal journal acknowledgement uncertain')
        with mock.patch.object(tool,'runtime_transaction',side_effect=uncertain):
            tool.runtime_worker(self.directory,self.runtime,tx)
        self.assertEqual(json.loads((tx/'journal.json').read_text())['state'],'manual_recovery_required')
        self.assertTrue((self.directory/tool.RUNTIME_FENCE).exists())
        RuntimeTransactionTests.assert_preserved(self)
