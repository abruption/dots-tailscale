#!/usr/bin/env python3
"""Record filesystem continuity without reading user files or credentials."""
import argparse
import datetime
import fcntl
import json
import os
from pathlib import Path
import uuid


def read_json(path):
    if path.is_symlink():
        raise RuntimeError(f'Symlink refused: {path}')
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return None


def atomic_json(path, value):
    if path.is_symlink():
        raise RuntimeError(f'Symlink refused: {path}')
    tmp = path.with_name(path.name + '.tmp-' + uuid.uuid4().hex)
    try:
        with tmp.open('x') as f:
            json.dump(value, f, ensure_ascii=False)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def context():
    def text(path):
        try:
            return Path(path).read_text().strip()
        except OSError:
            return None
    # boot_id alone cannot distinguish container replacement from a host reboot.
    return {'hostname': os.uname().nodename,
            'boot_id': text('/proc/sys/kernel/random/boot_id'),
            'pid1_start_ticks': ((text('/proc/1/stat') or '').rpartition(')')[2].split()[19:20] or [None])[0]}


def observe(home, root):
    os.umask(0o077)
    home, root = Path(home).absolute(), Path(root).absolute()
    if not home.is_dir():
        raise RuntimeError('Home directory does not exist')
    for path in (home, root):
        if any(p.is_symlink() for p in (path, *path.parents)):
            raise RuntimeError(f'Symlink path refused: {path}')
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.stat().st_uid != os.getuid():
        raise RuntimeError('Probe directory must belong to current user')
    root.chmod(0o700)
    lock_path = root / '.lock'
    if lock_path.is_symlink():
        raise RuntimeError('Lock symlink refused')
    with lock_path.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if (root / 'history.jsonl').is_symlink():
            raise RuntimeError('History symlink refused')
        state_path = root / 'baseline.json'
        workspace_marker = root / 'workspace-marker.json'
        home_marker = home / '.dots-home-persistence-marker.json'
        baseline = read_json(state_path)
        first = baseline is None
        if baseline is not None and (baseline.get('version') != 1 or baseline.get('home') != str(home)):
            raise RuntimeError('Baseline version/home mismatch')
        old_home, old_workspace = read_json(home_marker), read_json(workspace_marker)
        for marker in (old_home, old_workspace):
            if marker is not None and (not isinstance(marker, dict) or not isinstance(marker.get('id'), str)):
                raise RuntimeError('Invalid probe marker; refusing overwrite')
        now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')
        current = context()
        home_id = old_home['id'] if old_home else uuid.uuid4().hex
        workspace_id = old_workspace['id'] if old_workspace else uuid.uuid4().hex
        if first:
            baseline = {'version': 1, 'home': str(home), 'home_id': home_id,
                        'workspace_id': workspace_id, 'context': current, 'created_at': now}
        home_missing = old_home is None and not first
        workspace_missing = old_workspace is None and not first
        home_changed = home_id != baseline['home_id']
        workspace_changed = workspace_id != baseline['workspace_id']
        result = {'checked_at': now, 'first_observation': first, 'home': str(home),
                  'probe_root': str(root), 'baseline_created_at': baseline['created_at'],
                  'home_marker_id': home_id, 'workspace_marker_id': workspace_id,
                  'home_marker_missing_before_check': home_missing,
                  'workspace_marker_missing_before_check': workspace_missing,
                  'home_marker_changed_since_baseline': home_changed,
                  'workspace_marker_changed_since_baseline': workspace_changed,
                  'context': current,
                  'context_changes_since_baseline': [key for key in current if current[key] != baseline['context'].get(key)]}
        if first:
            result['assessment'] = 'baseline_created'
        elif home_changed and not workspace_changed:
            result['assessment'] = 'home_marker_changed_workspace_intact'
        elif home_changed or workspace_changed:
            result['assessment'] = 'marker_continuity_changed'
        else:
            result['assessment'] = 'markers_intact'
        # A marker deletion proves marker loss, not a complete HOME reset.
        if old_home is None:
            atomic_json(home_marker, {'id': home_id, 'created_at': now})
        if old_workspace is None:
            atomic_json(workspace_marker, {'id': workspace_id, 'created_at': now})
        if first:
            atomic_json(state_path, baseline)
        atomic_json(root / 'latest.json', result)
        with (root / 'history.jsonl').open('a') as f:
            f.write(json.dumps(result, ensure_ascii=False) + '\n')
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', type=Path, default=Path.home())
    parser.add_argument('--probe-root', type=Path, default=Path('/workspace/.dots-persistence-probe'))
    args = parser.parse_args()
    print(json.dumps(observe(args.home, args.probe_root), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, AttributeError, KeyError) as exc:
        raise SystemExit(f'Probe failed: {exc}')
