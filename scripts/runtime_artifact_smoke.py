#!/usr/bin/env python3
"""Explicit network smoke: inspect official artifacts, execute version only in scratch containers."""
import argparse
import hashlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--versions', nargs='+', default=['1.102.4', '1.104.4'])
    args = parser.parse_args()
    if len(args.versions) > 2:
        parser.error('at most two explicit versions')
    loader = importlib.machinery.SourceFileLoader('runtime_smoke_tool', str(ROOT/'dots-ts'))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    tool = importlib.util.module_from_spec(spec)
    loader.exec_module(tool)
    for version in args.versions:
        tool.runtime_version(version)
    docker = shutil.which('docker')
    if not docker:
        raise SystemExit('Docker is required; no host binary execution fallback')
    reports = []
    for version in args.versions:
        filename = 'tailscale_'+version+'_amd64.tgz'
        url = 'https://pkgs.tailscale.com/stable/'+filename
        deadline = time.monotonic()+180
        checksum = tool.runtime_checksum(tool.runtime_fetch(url+'.sha256', None, 4096, deadline), filename)
        raw = tool.runtime_fetch(url, None, tool.RUNTIME_ARCHIVE_LIMIT, deadline)
        binaries = tool.runtime_archive(raw, version, checksum)
        identity = uuid.uuid4().hex
        image = 'dots-runtime-smoke:'+identity
        created_image = False
        with tempfile.TemporaryDirectory(prefix='dots-runtime-artifact-') as folder:
            stage = Path(folder)
            for name, data in binaries.items():
                (stage/name).write_bytes(data)
                (stage/name).chmod(0o755)
            (stage/'Dockerfile').write_text('FROM scratch\nCOPY tailscale /tailscale\nCOPY tailscaled /tailscaled\nUSER 65534:65534\n')
            (stage/'.dockerignore').write_text('*\n!Dockerfile\n!tailscale\n!tailscaled\n')
            try:
                # No RUN, registry image, credentials, host mount, or daemon state.
                subprocess.run([docker,'build','--network=none','--quiet','--tag',image,str(stage)],
                               check=True, stdout=subprocess.DEVNULL, timeout=120)
                created_image = True
                output = {}
                for name, argument in (('tailscale','version'), ('tailscaled','--version')):
                    container = 'dots-runtime-smoke-'+identity+'-'+name
                    argv = [docker,'run','--rm','--name',container,'--network=none',
                            '--read-only','--cap-drop=ALL','--security-opt=no-new-privileges',
                            '--pids-limit=32','--memory=128m','--memory-swap=128m','--cpus=1',
                            '--entrypoint','/'+name,image]
                    wrapper = stage/(name+'-probe')
                    wrapper.write_text('#!/bin/sh\nexec '+shlex.join(argv)+' "$@"\n')
                    wrapper.chmod(0o700)
                    try:
                        observed = tool.runtime_probe(wrapper, argument)
                        if observed != version:
                            raise RuntimeError('artifact version mismatch')
                        output[name] = observed
                    finally:
                        # --rm normally removed it; kill only our named container if a timed probe left it.
                        check = subprocess.run([docker,'ps','-aq','--filter','name=^/'+container+'$'],
                                               capture_output=True, text=True, timeout=10, check=True)
                        if check.stdout.strip():
                            subprocess.run([docker,'rm','--force',container],check=True,stdout=subprocess.DEVNULL,timeout=10)
                reports.append({'version':version,'archive_sha256':checksum,
                                'binary_sha256':{n:hashlib.sha256(b).hexdigest() for n,b in binaries.items()},
                                'version_output':output,'network_during_execution':'none',
                                'state_or_host_mounts':False,'real_daemon_started':False})
            finally:
                if created_image:
                    subprocess.run([docker,'image','rm',image],check=True,stdout=subprocess.DEVNULL,timeout=30)
    print(json.dumps({'checks':reports},sort_keys=True))


if __name__ == '__main__':
    main()
