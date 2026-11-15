#!/usr/bin/env python3
"""Opt-in, loopback-only application proxies for the installed tailscaled.

Configuration does not restart anything. Run apply from an independent platform
terminal: restarting tailscaled interrupts Tailscale SSH connectivity.
"""
import argparse
import contextlib
import fcntl
import json
import os
from pathlib import Path
import signal
import hashlib
import re
import socket
import stat
import subprocess
import sys
import threading
import time

SSH_PORT_CONFIG_SCHEMA = 1
SSH_PORT_FILE = "ssh-port.json"
CONFIG = "outbound-proxy.json"
DEFAULT = {"socks_port": None, "http_port": None}
STOP_TIMEOUT = 8
START_TIMEOUT = 20


class ProxyError(RuntimeError):
    pass


def _directory(path):
    path = Path(path)
    if not path.is_absolute():
        raise ProxyError("installation/runtime path must be absolute")
    for component in (path,) + tuple(path.parents):
        if component.is_symlink():
            raise ProxyError("symlink directory refused")
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ProxyError("installation/runtime directory must be owned by this user with mode 700")
    return path


def _read_private(path, maximum=4096, snapshot=False, allow_hardlink=False):
    try:
        fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as error:
        raise ProxyError("cannot safely open private configuration") from error
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise ProxyError("configuration must be a regular owned mode 600 file")
        if snapshot and not allow_hardlink and info.st_nlink != 1:
            raise ProxyError("configuration hardlink refused")
        data = stream.read(maximum + 1)
    if len(data) > maximum:
        raise ProxyError("configuration is too large")
    return (data, info) if snapshot else data


def _validate(value):
    if not isinstance(value, dict) or set(value) != set(DEFAULT):
        raise ProxyError("configuration requires only socks_port and http_port")
    for port in value.values():
        if port is not None and (type(port) is not int or not 1024 <= port <= 65535 or port == 18089):
            raise ProxyError("proxy ports must be integers 1024..65535, excluding transport port 18089")
    if value["socks_port"] is not None and value["socks_port"] == value["http_port"]:
        raise ProxyError("SOCKS5 and HTTP ports must differ")
    return dict(value)


def _unique_pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ProxyError("duplicate configuration key")
        result[key] = value
    return result


def load_config(d):
    d = _directory(d)
    path = d / CONFIG
    if not path.exists() and not path.is_symlink():
        return dict(DEFAULT)
    try:
        return _validate(json.loads(_read_private(path).decode("utf-8"), object_pairs_hook=_unique_pairs))
    except (UnicodeError, ValueError) as error:
        raise ProxyError("invalid proxy configuration JSON") from error


def ssh_port_value(value):
    if type(value) is int:
        port = value
    elif isinstance(value, str) and re.fullmatch(r"[1-9][0-9]{0,4}", value):
        port = int(value)
    else:
        raise ProxyError("SSH port must be a canonical integer 1..65535")
    if not 1 <= port <= 65535 or port == 18089:
        raise ProxyError("SSH port must be 1..65535 excluding shim port 18089")
    return port


def ssh_port_setting(d):
    path = Path(d) / SSH_PORT_FILE
    if not path.exists() and not path.is_symlink():
        return 22, None
    _directory(d)
    try:
        raw, info = _read_private(path, snapshot=True)
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs)
        if (not isinstance(value, dict) or set(value) != {"schema_version", "port"}
                or type(value["schema_version"]) is not int or value["schema_version"] != 1
                or type(value["port"]) is not int):
            raise ValueError()
        return ssh_port_value(value["port"]), _ssh_snapshot(raw, info)
    except (UnicodeError, ValueError, KeyError) as error:
        raise ProxyError("invalid SSH port configuration") from error


def _ssh_snapshot(raw, info):
    return {"sha256": hashlib.sha256(raw).hexdigest(),
            "identity": [info.st_dev, info.st_ino, info.st_uid, stat.S_IMODE(info.st_mode),
                         info.st_size, info.st_mtime_ns]}


def ssh_config_text(d, r, port):
    port = ssh_port_value(port)
    d, r = Path(d), Path(r)
    if any(not p.is_absolute() or any(c.isspace() or ord(c) < 32 or 127 <= ord(c) <= 159 for c in str(p)) for p in (d, r)):
        raise ProxyError("SSH paths must be absolute without whitespace")
    if any(c in {"\"", "'", "\\", "%", "#"} for p in (d, r) for c in str(p)):
        raise ProxyError("SSH paths contain unsupported configuration syntax")
    d, r = Path(os.path.normpath(d)), Path(os.path.normpath(r))
    return ("Port {port}\nListenAddress 127.0.0.1\nHostKey {d}/sshd/ssh_host_ed25519_key\n"
            "PidFile {r}/sshd.pid\nUsePAM no\nPasswordAuthentication no\n"
            "KbdInteractiveAuthentication no\nPubkeyAuthentication yes\n"
            "AuthorizedKeysFile {d}/sshd/authorized_keys\nStrictModes no\n"
            "Subsystem sftp internal-sftp\n").format(port=port, d=d, r=r)


def ssh_port_info(d, r, check_proxy=True):
    _directory(d)
    _directory(Path(d) / "sshd")
    port, setting = ssh_port_setting(d)
    raw, info = _read_private(Path(d) / "sshd/sshd_config", maximum=65536, snapshot=True)
    try:
        def rows(text):
            result = {}
            if any((ord(c) < 32 and c not in "\t\n") or 127 <= ord(c) <= 159
                   or (c.isspace() and c not in " \t\n") for c in text):
                raise ValueError()
            for line in text.split("\n"):
                line = line.strip(" \t")
                if not line or line.startswith("#"):
                    continue
                # Only ASCII separators/case are part of sshd's directive syntax.
                parts = re.split(r"[ \t]+", line)
                if not parts[0].isascii():
                    raise ValueError()
                key = parts[0].lower()
                if key in result:
                    raise ValueError()
                values = parts[1:]
                if key in ("hostkey", "pidfile", "authorizedkeysfile"):
                    if len(values) != 1 or not values[0].startswith("/"):
                        raise ValueError()
                    # Normalize only repeated Linux slash separators. Parent/dot
                    # traversal can resolve differently in the presence of links.
                    if any(part in (".", "..") for part in values[0].split("/")):
                        raise ValueError()
                    values = [re.sub(r"/+", "/", values[0])]
                result[key] = values
            return result
        if rows(raw.decode("utf-8")) != rows(ssh_config_text(d, r, port)):
            raise ValueError()
    except (UnicodeError, ValueError):
        raise ProxyError("SSH setting/config mismatch or unsupported sshd configuration") from None
    if check_proxy and port in load_config(d).values():
        raise ProxyError("SSH port conflicts with a configured application proxy")
    return {"port": port, "origin": "legacy" if setting is None else "persisted",
            "setting": setting, "sshd_config": _ssh_snapshot(raw, info)}


def ssh_install_port(d, r, requested=None):
    port, setting = ssh_port_setting(d)
    completed = (Path(d) / "installed").exists() or (Path(d) / "installed").is_symlink()
    if requested is not None:
        chosen = ssh_port_value(requested)
        if completed:
            raise ProxyError("SSH port selection is only for new installations")
        if setting is not None and chosen != port:
            raise ProxyError("SSH port selection differs from persisted installation intent")
        if setting is None and any((Path(d) / n).exists() or (Path(d) / n).is_symlink()
                                   for n in ("sshd/sshd_config", "sshd/ssh_host_ed25519_key", "sshd/ssh_host_ed25519_key.pub", "authenticated", "state/tailscaled.state")):
            raise ProxyError("existing identity/config cannot adopt a new SSH port")
        port = chosen
    if completed or (Path(d) / "sshd/sshd_config").exists() or (Path(d) / "sshd/sshd_config").is_symlink():
        if ssh_port_info(d, r)["port"] != port:
            raise ProxyError("SSH port selection/config mismatch")
    config_path = Path(d) / CONFIG
    if (config_path.exists() or config_path.is_symlink()) and port in load_config(d).values():
        raise ProxyError("SSH port conflicts with a configured application proxy")
    return port


def ssh_persist_port(d, r, port):
    d = _directory(d)
    port = ssh_port_value(port)
    if port in load_config(d).values():
        raise ProxyError("SSH port conflicts with a configured application proxy")
    current, setting = ssh_port_setting(d)
    if setting is not None:
        if current != port:
            raise ProxyError("SSH port intent changed")
        return
    # Legacy installations keep the absence of metadata; never migrate identity.
    if any((d / n).exists() or (d / n).is_symlink() for n in
           ("installed", "authenticated", "sshd/sshd_config", "sshd/ssh_host_ed25519_key", "sshd/ssh_host_ed25519_key.pub", "state/tailscaled.state")):
        if port != 22:
            raise ProxyError("legacy SSH port migration is unsupported")
        return
    import tempfile
    path = d / SSH_PORT_FILE
    fd, temporary = tempfile.mkstemp(prefix=".ssh-port-", dir=d)
    try:
        with os.fdopen(fd, "w") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump({"schema_version": 1, "port": port}, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        # Publication must not overwrite a concurrently created intent.
        os.link(temporary, path, follow_symlinks=False)
        os.unlink(temporary)
        parent = os.open(d, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        if os.path.lexists(temporary):
            os.unlink(temporary)


def _ssh_config_operand(argv):
    if not argv:
        return None
    if argv[0].startswith("sshd: "):
        title = argv[0][6:].split()
        if "[listener]" not in title:
            return None
        argv = title[:title.index("[listener]")]
    # Supported installer listeners have only -f CONFIG and optional -E LOG.
    if not argv or Path(argv[0]).name != "sshd":
        return None
    config = None
    args = list(argv[1:])
    while args:
        flag = args.pop(0)
        if flag not in ("-f", "-E") or not args:
            return None
        value = args.pop(0)
        if flag == "-f":
            if config is not None:
                return None
            config = value
    return config


def _ssh_listener(d, port):
    matches = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            if proc.stat().st_uid != os.getuid():
                continue
            executable = os.readlink(proc / "exe")
            if executable.endswith(" (deleted)"):
                executable = executable[:-10]
            if Path(executable).name != "sshd":
                continue
            args = (proc / "cmdline").read_bytes().rstrip(b"\0").decode("utf-8").split("\0")
            operand = _ssh_config_operand(args)
            expected = str(Path(d) / "sshd/sshd_config")
            if (operand is not None and not any(p in (".", "..") for p in operand.split("/"))
                    and re.sub(r"/+", "/", operand) == re.sub(r"/+", "/", expected)):
                matches.append({"pid": int(proc.name)})
        except (OSError, UnicodeError):
            continue
    return len(matches) == 1 and _listener_owned(matches[0], port)


def ssh_recover_unpublished_links(d):
    """Install-lock caller only: finish an interrupted publication, never migrate.

    Only sibling temporary names linking this valid final intent can be removed;
    unknown files/links and installations with identity remain untouched.
    """
    d = Path(d)
    if not d.exists():
        return
    for name in ("installed", "authenticated", "state/tailscaled.state", "sshd/sshd_config", "sshd/ssh_host_ed25519_key", "sshd/ssh_host_ed25519_key.pub"):
        if (d / name).exists() or (d / name).is_symlink():
            return
    final = d / SSH_PORT_FILE
    if not final.exists() or final.is_symlink():
        return
    _directory(d)
    raw, info = _read_private(final, snapshot=True, allow_hardlink=True)
    if info.st_nlink <= 1:
        return
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs)
        if (not isinstance(value, dict) or set(value) != {"schema_version", "port"}
                or type(value["schema_version"]) is not int or value["schema_version"] != 1
                or type(value["port"]) is not int):
            return
        ssh_port_value(value["port"])
    except (ValueError, UnicodeError, ProxyError):
        return
    links = []
    for candidate in d.glob(".ssh-port-*"):
        st = candidate.lstat()
        if (stat.S_ISREG(st.st_mode) and st.st_uid == os.getuid()
                and stat.S_IMODE(st.st_mode) == 0o600
                and (st.st_dev, st.st_ino) == (info.st_dev, info.st_ino)):
            links.append(candidate)
    if info.st_nlink != len(links) + 1:
        return  # Some link is outside this known publication; do not guess.
    for candidate in links:
        st = candidate.lstat()
        if not stat.S_ISREG(st.st_mode) or (st.st_dev, st.st_ino) != (info.st_dev, info.st_ino):
            raise ProxyError("SSH intent publication changed during recovery")
        candidate.unlink()
    fd = os.open(d, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def ssh_preflight(d, r, mode, requested=None, threshold=None):
    if mode != "install" and requested is not None:
        raise ProxyError("DOTS_TS_SSH_PORT is only accepted for a new install")
    ssh_config_text(d, r, 22)  # Validate path syntax before new intent/identity work.
    if mode == "install":
        ssh_recover_unpublished_links(d)
    port = ssh_install_port(d, r, requested) if mode == "install" else ssh_port_info(d, r)["port"]
    threshold = (Path("/proc/sys/net/ipv4/ip_unprivileged_port_start").read_text().strip()
                 if threshold is None else threshold)
    if not isinstance(threshold, str) or not re.fullmatch(r"0|[1-9][0-9]{0,4}", threshold) or int(threshold) > 65535:
        raise ProxyError("invalid kernel unprivileged port threshold")
    if port < int(threshold):
        raise ProxyError("selected SSH port is below the kernel unprivileged threshold")
    try:
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(("127.0.0.1", port))
    except OSError as error:
        if not _ssh_listener(d, port):
            raise ProxyError("selected SSH port is unavailable or not owned by this installation") from error
    return port


def _flags(config):
    result = []
    for key, flag in (("socks_port", "--socks5-server"), ("http_port", "--outbound-http-proxy-listen")):
        if config[key] is not None:
            result.append("{}=127.0.0.1:{}".format(flag, config[key]))
    return result


def _argv_config(argv):
    config = dict(DEFAULT)
    for index, arg in enumerate(argv):
        for key, flag in (("socks_port", "--socks5-server"), ("http_port", "--outbound-http-proxy-listen")):
            if arg == flag or arg.startswith(flag + "="):
                value = argv[index + 1] if arg == flag and index + 1 < len(argv) else arg.partition("=")[2]
                if not value.startswith("127.0.0.1:") or not value[10:].isdigit():
                    raise ProxyError("running daemon has an unsupported proxy bind address")
                if config[key] is not None:
                    raise ProxyError("running daemon repeats a proxy argument")
                config[key] = int(value[10:])
    return _validate(config)


def _ticks(pid):
    # comm may contain spaces and parentheses; fields after its closing ')' start at #3.
    fields = Path("/proc/{}/stat".format(pid)).read_text().rsplit(")", 1)[1].split()
    return None if fields[0] == "Z" else fields[19]


def _same_process(process):
    try:
        current = _ticks(process["pid"])
        return (current is not None and current == process["ticks"] and
                Path("/proc/{}".format(process["pid"])).stat().st_uid == os.getuid() and
                os.readlink("/proc/{}/exe".format(process["pid"])) == process["exe"])
    except (OSError, IndexError):
        return False


def _socket_owned(process, r):
    try:
        inodes = set()
        for line in Path("/proc/net/unix").read_text().splitlines()[1:]:
            fields = line.split()
            if len(fields) >= 8 and fields[7] == str(Path(r) / "tailscaled.sock"):
                inodes.add("socket:[{}]".format(fields[6]))
        fd_dir = Path("/proc/{}/fd".format(process["pid"]))
        return any(os.readlink(str(fd)) in inodes for fd in fd_dir.iterdir())
    except OSError:
        return False


def _daemon(d, r):
    if not Path("/proc").is_dir():
        raise ProxyError("daemon inspection requires Linux /proc")
    expected = str(Path(d) / "tailscaled")
    wanted_socket = str(Path(r) / "tailscaled.sock")
    matches = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if entry.stat().st_uid != os.getuid():
                continue
            try:
                argv = (entry / "cmdline").read_bytes().rstrip(b"\0").decode("utf-8", "surrogateescape").split("\0")
            except PermissionError:
                # Unrelated same-UID /proc entries may be unreadable in Dots.
                # Fail closed if the executable independently identifies ours.
                try:
                    executable = os.readlink(str(entry / "exe"))
                except PermissionError:
                    continue
                if executable == expected:
                    raise ProxyError("cannot establish daemon command line")
                continue
            owns_argument = any(arg == "--socket=" + wanted_socket or
                                (arg == "--socket" and i + 1 < len(argv) and argv[i + 1] == wanted_socket)
                                for i, arg in enumerate(argv))
            if owns_argument:
                if os.readlink(str(entry / "exe")) != expected:
                    continue
                ticks = _ticks(entry.name)
                if ticks is not None:
                    # argv[0] can be an exec -a title; only launch the verified
                    # executable when restoring this command line.
                    argv[0] = expected
                    matches.append({"pid": int(entry.name), "ticks": ticks, "argv": argv, "exe": expected})
        except (FileNotFoundError, ProcessLookupError):
            continue
        except (PermissionError, UnicodeError, IndexError) as error:
            raise ProxyError("cannot establish daemon identity") from error
    if len(matches) > 1:
        raise ProxyError("multiple daemons use the installation socket")
    return matches[0] if matches else None


def _listener_owned(process, port):
    try:
        inodes = set()
        for line in Path("/proc/net/tcp").read_text().splitlines()[1:]:
            fields = line.split()
            if fields[1] == "0100007F:{:04X}".format(port) and fields[3] == "0A":
                inodes.add("socket:[{}]".format(fields[9]))
        return any(os.readlink(str(fd)) in inodes for fd in Path("/proc/{}/fd".format(process["pid"])).iterdir())
    except OSError:
        return False


def _ports_available(config, process=None):
    held = []
    try:
        for port in config.values():
            if port is None or (process is not None and _listener_owned(process, port)):
                continue
            listener = socket.socket()
            held.append(listener)
            try:
                listener.bind(("127.0.0.1", port))
            except OSError as error:
                raise ProxyError("configured proxy port is unavailable") from error
    finally:
        for listener in held:
            listener.close()


def startup_args(d, r, check_ports=True):
    config = load_config(d)
    _directory(r)
    if check_ports:
        _ports_available(config, _daemon(d, r))
    return _flags(config)


def inspect_config(d, r):
    desired = load_config(d)
    _directory(r)
    process = _daemon(d, r)
    running = _argv_config(process["argv"]) if process else None
    healthy = process is not None and _socket_owned(process, r)
    if healthy:
        healthy = all(port is None or _listener_owned(process, port) for port in running.values())
    return {"desired": desired, "running": running, "restart_required": running != desired or not healthy,
            "daemon_running": healthy}


@contextlib.contextmanager
def _operation(d):
    d = _directory(d)
    fd = os.open(str(d / ".operation.lock"), os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise ProxyError("unsafe operation lock")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ProxyError("another operation is running") from error
        marker = d / ".runtime-update-block"
        try:
            os.lstat(marker)
        except FileNotFoundError:
            pass
        except OSError as error:
            raise ProxyError("cannot safely inspect runtime update fence") from error
        else:
            raise ProxyError("runtime update fence exists; inspect update status before proceeding")
        yield
    finally:
        os.close(fd)


def _write_config(d, config):
    import tempfile
    fd, name = tempfile.mkstemp(prefix=".outbound-proxy-", dir=str(d))
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(config, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, str(Path(d) / CONFIG))
    finally:
        if os.path.exists(name):
            os.unlink(name)


def configure(d, r, socks_port=None, http_port=None):
    config = _validate({"socks_port": socks_port, "http_port": http_port})
    _directory(r)
    with _operation(d):
        load_config(d)  # Refuse unsafe existing config rather than overwriting it.
        ssh_config = Path(d) / "sshd/sshd_config"
        ssh_port = (ssh_port_info(d, r, check_proxy=False)["port"]
                    if ssh_config.exists() or ssh_config.is_symlink() else ssh_port_setting(d)[0])
        if ssh_port in config.values():
            raise ProxyError("application proxy conflicts with SSH port")
        _ports_available(config, _daemon(d, r))
        _write_config(d, config)
    return inspect_config(d, r)


def disable(d, r):
    return configure(d, r)


def _stop(process):
    if not _same_process(process):
        return
    os.kill(process["pid"], signal.SIGTERM)
    deadline = time.monotonic() + STOP_TIMEOUT
    while _same_process(process) and time.monotonic() < deadline:
        time.sleep(0.1)
    if _same_process(process):
        raise ProxyError("daemon did not exit within deadline; no replacement was started")


def _socket_identity(r):
    path = Path(r) / "tailscaled.sock"
    if not path.exists() and not path.is_symlink():
        return None
    info = path.lstat()
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
        raise ProxyError("unsafe daemon socket")
    return info.st_dev, info.st_ino


def _remove_socket(d, r, process, expected_identity):
    path = Path(r) / "tailscaled.sock"
    if _same_process(process) or _daemon(d, r) is not None:
        raise ProxyError("refusing socket removal while daemon is alive")
    identity = _socket_identity(r)
    if identity is not None:
        if identity != expected_identity:
            raise ProxyError("daemon socket changed; refusing removal")
        path.unlink()


def _environment(process, d):
    try:
        raw = Path("/proc/{}/environ".format(process["pid"])).read_bytes()
        environment = dict(item.decode("utf-8").split("=", 1) for item in raw.split(b"\0") if item)
    except (OSError, UnicodeError, ValueError) as error:
        raise ProxyError("cannot preserve daemon environment") from error
    for key in ("HTTP_PROXY", "HTTPS_PROXY"):
        if environment.get(key) != "http://127.0.0.1:18089":
            raise ProxyError("unexpected daemon transport proxy; inspect before restarting")
    for key in ("http_proxy", "https_proxy"):
        if environment.get(key) not in (None, "", "http://127.0.0.1:18089"):
            raise ProxyError("unexpected daemon transport proxy; inspect before restarting")
    if environment.get("SSL_CERT_FILE") != str(Path(d) / "shim/bundle.crt"):
        raise ProxyError("unexpected daemon CA bundle; inspect before restarting")
    # Accept legacy absent/empty aliases, then make all subsequent launches explicit.
    environment["http_proxy"] = environment["HTTP_PROXY"]
    environment["https_proxy"] = environment["HTTPS_PROXY"]
    return environment


def _replace_flags(argv, config):
    result = []
    skip = False
    for arg in argv:
        if skip:
            skip = False
            continue
        if arg in ("--socks5-server", "--outbound-http-proxy-listen"):
            skip = True
        elif not arg.startswith(("--socks5-server=", "--outbound-http-proxy-listen=")):
            result.append(arg)
    return result + _flags(config)


@contextlib.contextmanager
def _apply_log(r):
    """Acquire one validated log before stopping a daemon, including rollback.

    Keep its descriptor open across both launches: a changed pathname must not
    invalidate recovery or redirect output into an unvalidated replacement.
    """
    path = Path(r) / "proxy-apply.log"
    try:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    except OSError as error:
        raise ProxyError("cannot safely open proxy apply log; no daemon was stopped") from error
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise ProxyError("unsafe proxy apply log")
        if info.st_size >= 1024 * 1024:
            os.ftruncate(fd, 0)
        yield fd
    finally:
        os.close(fd)


def _launch(argv, environment, log_fd):
    return subprocess.Popen(argv, env=environment, stdin=subprocess.DEVNULL, stdout=log_fd, stderr=log_fd,
                            close_fds=True, start_new_session=True)


def _backend_running(d, r):
    reply = subprocess.run([str(Path(d) / "tailscale"), "--socket=" + str(Path(r) / "tailscaled.sock"),
                            "status", "--json"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           timeout=2, check=True)
    return json.loads(reply.stdout.decode("utf-8")).get("BackendState") == "Running"


def _ready(d, r, config, child):
    deadline = time.monotonic() + START_TIMEOUT
    while time.monotonic() < deadline:
        if child.poll() is not None:
            raise ProxyError("replacement daemon exited")
        process = _daemon(d, r)
        if process and process["pid"] == child.pid and _socket_owned(process, r):
            child.proxy_socket_identity = _socket_identity(r)
            try:
                if _backend_running(d, r) and all(
                        port is None or _listener_owned(process, port) for port in config.values()):
                    return process
            except (OSError, subprocess.SubprocessError, ValueError):
                pass
        time.sleep(0.1)
    raise ProxyError("replacement daemon readiness timed out")


@contextlib.contextmanager
def _defer_signals():
    """Finish bounded recovery before reporting a caught interruption.

    Custom handlers (unlike SIG_IGN) reset to defaults when the daemon execs.
    SIGKILL and machine failure cannot be handled by this process.
    """
    if threading.current_thread() is not threading.main_thread():
        raise ProxyError("proxy apply restart must run on the main thread")
    pending = []
    previous = {}
    def defer(signum, frame):
        if not pending:
            pending.append(signum)
    try:
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            previous[signum] = signal.getsignal(signum)
            signal.signal(signum, defer)
        yield pending
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def _restart(d, r, desired, old, previous, environment, old_socket_identity, log_fd):
    _stop(old)
    child = None
    try:
        _remove_socket(d, r, old, old_socket_identity)
        child = _launch(_replace_flags(old["argv"], desired), environment, log_fd)
        _ready(d, r, desired, child)
    except Exception as failure:
        if child is not None:
            candidate = _daemon(d, r)
            if candidate and candidate["pid"] == child.pid:
                candidate_socket_identity = _socket_identity(r)
                if candidate_socket_identity is not None and not _socket_owned(candidate, r):
                    raise ProxyError("candidate socket owner uncertain; refusing rollback duplicate") from failure
                _stop(candidate)
                _remove_socket(d, r, candidate, candidate_socket_identity)
            elif child.poll() is None:
                raise ProxyError("candidate identity uncertain; refusing a second daemon") from failure
            else:
                # Its inherited exact argv and PID identify the dead child.
                _remove_socket(d, r, {"pid": child.pid, "ticks": "exited", "exe": old["exe"]},
                               getattr(child, "proxy_socket_identity", None))
        if _daemon(d, r) is not None:
            raise ProxyError("another daemon appeared; refusing rollback duplicate") from failure
        if _socket_identity(r) is not None:
            raise ProxyError("unknown socket remains; refusing rollback; use platform terminal recovery") from failure
        restored = _launch(old["argv"], environment, log_fd)
        try:
            _ready(d, r, previous, restored)
        except Exception as rollback:
            raise ProxyError("apply failed and rollback was not ready; use platform terminal recovery") from rollback
        raise ProxyError("apply failed; previous daemon restored; desired configuration remains pending") from failure


def apply(d, r):
    """Apply from an independent GUI terminal; preserve desired config on failure.

    This restarts only tailscaled. Authentication state, shim, and sshd are not
    rewritten. Readiness checks do not claim end-to-end application connectivity.
    """
    _directory(r)
    with _operation(d):
        desired = load_config(d)
        old = _daemon(d, r)
        if old is None or not _socket_owned(old, r):
            raise ProxyError("apply requires one identified running daemon owning its socket")
        previous = _argv_config(old["argv"])
        state_args = [arg for arg in old["argv"][1:] if arg == "--statedir" or arg.startswith("--statedir=")]
        if (state_args != ["--statedir=" + str(Path(d) / "state")] or
                any(arg == "--state" or arg.startswith("--state=") for arg in old["argv"][1:])):
            raise ProxyError("unexpected daemon state directory; inspect before restarting")
        tun_args = [arg for arg in old["argv"][1:] if arg == "--tun" or arg.startswith("--tun=")]
        if tun_args != ["--tun=userspace-networking"]:
            raise ProxyError("apply requires userspace networking mode")
        _ports_available(desired, old)
        environment = _environment(old, d)
        if previous == desired and all(port is None or _listener_owned(old, port) for port in desired.values()):
            return inspect_config(d, r)
        if os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_CLIENT"):
            raise ProxyError("run proxy apply from the independent platform terminal, not an SSH session")
        try:
            running = _backend_running(d, r)
        except (OSError, subprocess.SubprocessError, ValueError) as error:
            raise ProxyError("cannot confirm Running baseline; no daemon was stopped") from error
        if not running:
            raise ProxyError("daemon is not Running; no daemon was stopped")
        # Take the old process identity before any signal. Never kill by name.
        old_socket_identity = _socket_identity(r)
        with _apply_log(r) as log_fd, _defer_signals() as interrupted:
            _restart(d, r, desired, old, previous, environment, old_socket_identity, log_fd)
            result = inspect_config(d, r)
            if interrupted:
                result["interrupted_signal"] = interrupted[0]
                result["apply_result"] = "applied"
        return result


def main(argv=None, d=None, r=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("ssh-preflight", "ssh-persist", "ssh-config", "ssh-port"):
        if argv[0] == "ssh-preflight" and len(argv) == 4:
            print(ssh_preflight(argv[1], argv[2], argv[3], os.environ.get("DOTS_TS_SSH_PORT")))
        elif argv[0] == "ssh-persist" and len(argv) == 4:
            ssh_persist_port(argv[1], argv[2], argv[3])
        elif argv[0] == "ssh-config" and len(argv) == 4:
            print(ssh_config_text(argv[1], argv[2], argv[3]), end="")
        elif argv[0] == "ssh-port" and len(argv) == 3:
            print(ssh_port_info(argv[1], argv[2])["port"])
        else:
            raise ProxyError("invalid SSH configuration helper arguments")
        return 0
    if argv and argv[0] in ("startup", "validate"):
        if len(argv) != 3:
            raise ProxyError("startup/validate requires installation and runtime paths")
        flags = startup_args(argv[1], argv[2], check_ports=argv[0] == "startup")
        if argv[0] == "startup" and flags:
            print("\n".join(flags))
        return 0
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("show")
    configure_parser = commands.add_parser("configure")
    configure_parser.add_argument("--socks-port", type=int)
    configure_parser.add_argument("--http-port", type=int)
    commands.add_parser("disable")
    commands.add_parser("apply")
    args = parser.parse_args(argv)
    d = d if d is not None else os.environ.get("DOTS_TS_DIR", "/workspace/.dots-tailscale")
    r = r if r is not None else "/tmp/dots-tailscale-{}".format(os.getuid())
    if args.command == "configure":
        result = configure(d, r, args.socks_port, args.http_port)
    else:
        result = {"show": inspect_config, "disable": disable, "apply": apply}[args.command](d, r)
    print(json.dumps(result, sort_keys=True))
    return 128 + result["interrupted_signal"] if "interrupted_signal" in result else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ProxyError, OSError) as error:
        print("proxy: {}".format(error), file=sys.stderr)
        sys.exit(1)
