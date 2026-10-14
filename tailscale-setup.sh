#!/usr/bin/env bash
# Persistent files live in /workspace; runtime files are recreated per VM.
set -euo pipefail
umask 077
MODE="${1:-check}"
D="${DOTS_TS_DIR:-/workspace/.dots-tailscale}"
R="/tmp/dots-tailscale-$(id -u)"
SOCK="$R/tailscaled.sock"
S="$D/sshd"
SOURCE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
TS_VERSION=1.102.4
UPSTREAM_PROXY="${UPSTREAM_PROXY:-http://browser-proxy:8889}"
NODE_HOSTNAME="${NODE_HOSTNAME:-dots-sandbox}"
fail() { printf 'failed: %s\n' "$*" >&2; exit 1; }
TS() { timeout 20 "$D/tailscale" --socket="$SOCK" "$@"; }
require() { command -v "$1" >/dev/null || fail "missing command: $1"; }
private_dir() {
  local p="$1" q="$1"
  while [[ "$q" != / && "$q" != . ]]; do
    [[ ! -L "$q" ]] || fail "symlink path refused: $q"
    q="$(dirname -- "$q")"
  done
  mkdir -p -- "$p"
  [[ -O "$p" ]] || fail "not owned by current user: $p"
  chmod 700 -- "$p"
}
place() {
  local src="$1" dst="$2" mode="$3"
  [[ ! -L "$dst" ]] || fail "symlink refused: $dst"
  if [[ -e "$dst" ]]; then
    cmp -s "$src" "$dst" || fail "existing file differs: $dst (manual review required)"
  else
    install -m "$mode" "$src" "$dst"
  fi
}
# Match the actual configured path, including sshd's rewritten process title.
running() {
  python3 - "$1" <<'PY'
import os, pathlib, re, sys
needle = os.path.normpath(sys.argv[1].encode())
def same_path(path):
    return bool(path) and os.path.normpath(path) == needle

def basename(path):
    return path.rsplit(b'/', 1)[-1]

def matches(args, exe):
    if not args: args = [b'']
    if needle.endswith(b'/shim.py'):
        # Only the Python script operand counts, never arguments to -c/-m,
        # an editor, or a different script.
        interpreter = exe if exe is not None else args[0]
        if not re.fullmatch(rb'python(?:3(?:\.\d+)?)?', basename(interpreter)):
            return False
        i = 1
        while i < len(args):
            arg = args[i]
            if arg == b'--':
                i += 1
                break
            if arg in (b'-W', b'-X'):
                i += 2
            elif re.fullmatch(rb'-[uBEIsSO]+', arg):
                i += 1
            elif arg.startswith((b'-W', b'-X')):
                i += 1
            else:
                break
        return i < len(args) and same_path(args[i])
    if needle.endswith(b'/sshd_config'):
        # OpenSSH can collapse argv into a rewritten listener title.
        if args[0].startswith(b'sshd: '):
            title = args[0][6:].split()
            if b'[listener]' not in title:
                return False
            args = title[:title.index(b'[listener]')]
        if not args or basename(exe if exe is not None else args[0]) != b'sshd':
            return False
        # Parse all options: config checks (-t/-T) are not listeners, even
        # when combined with other flags or placed after the config path.
        # Consume option operands, so "-o -f CONFIG" is not misread.
        i = 1
        config = None
        while i < len(args):
            arg = args[i]
            if not arg.startswith(b'-') or len(arg) < 2:
                return False
            j = 1
            while j < len(arg):
                option = arg[j:j + 1]
                if option in (b't', b'T'):
                    return False
                if option in (b'c', b'E', b'f', b'h', b'o', b'p', b'u', b'C'):
                    value = arg[j + 1:]
                    if not value:
                        i += 1
                        if i >= len(args):
                            return False
                        value = args[i]
                    if option == b'f':
                        if config is not None:
                            return False
                        config = value
                    break
                if option not in (b'D', b'd', b'e', b'i', b'q', b'4', b'6'):
                    return False
                j += 1
            i += 1
        return config is not None and same_path(config)
    return same_path(exe) if exe is not None else same_path(args[0])

for p in pathlib.Path('/proc').iterdir():
    if not p.name.isdigit() or int(p.name) == os.getpid(): continue
    try:
        if p.stat().st_uid != os.getuid(): continue
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        continue
    try:
        args = (p/'cmdline').read_bytes().rstrip(b'\0').split(b'\0')
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        args = []
    try:
        exe = os.readlink(p/'exe').encode()
        if exe.endswith(b' (deleted)'):
            exe = exe[:-len(b' (deleted)')]
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        exe = None
    if matches(args, exe):
        sys.exit(0)
sys.exit(1)
PY
}
launch_needed() {
  local rc=0
  running "$1" || rc=$?
  case "$rc" in 0) return 1;; 1) return 0;; *) fail "process inspection failed";; esac
}
rotate() {
  local f="$1"
  [[ ! -L "$f" && ! -L "$f.1" ]] || fail "log symlink refused"
  if [[ -f "$f" ]] && [[ $(wc -c < "$f") -gt 1048576 ]]; then mv -f -- "$f" "$f.1"; fi
}
preflight() {
  [[ "$(uname -s)" = Linux && "$(uname -m)" = x86_64 ]] || fail 'requires Linux x86_64'
  [[ "$D" = /* && "$D" != *[[:space:]]* ]] || fail 'DOTS_TS_DIR must be an absolute path without whitespace'
  local c
  for c in python3 timeout flock openssl ssh-keygen /usr/sbin/sshd; do require "$c"; done
  local port_start
  port_start="$(cat /proc/sys/net/ipv4/ip_unprivileged_port_start)"
  [[ "$port_start" -le 22 ]] || fail 'unprivileged port 22 unavailable'
}
install_all() {
  require curl; require tar; require sha256sum
  local shim_source="$SOURCE/shim.py"
  [[ -f "$shim_source" ]] || shim_source="$SOURCE/shim/shim.py"
  [[ -f "$shim_source" ]] || fail "missing companion shim.py"
  local tmp tgz url want got
  tmp="$(mktemp -d "$R/install.XXXXXX")"
  trap 'rm -rf -- "$tmp"' RETURN
  if [[ -e "$D/tailscale" || -e "$D/tailscaled" ]]; then
    [[ -x "$D/tailscale" && -x "$D/tailscaled" ]] || fail 'partial binary installation'
  else
    tgz="tailscale_${TS_VERSION}_amd64.tgz"
    url="https://pkgs.tailscale.com/stable/$tgz"
    curl -fsSL --max-time 300 --proxy "$UPSTREAM_PROXY" "$url" -o "$tmp/$tgz"
    want="$(curl -fsSL --max-time 30 --proxy "$UPSTREAM_PROXY" "$url.sha256" | tr -d '[:space:]')"
    got="$(sha256sum "$tmp/$tgz" | cut -d' ' -f1)"
    [[ "$want" =~ ^[a-fA-F0-9]{64}$ && "$want" = "$got" ]] || fail 'download checksum mismatch'
    tar -xzf "$tmp/$tgz" -C "$tmp"
    install -m 700 "$tmp/tailscale_${TS_VERSION}_amd64/tailscale" "$D/tailscale"
    install -m 700 "$tmp/tailscale_${TS_VERSION}_amd64/tailscaled" "$D/tailscaled"
  fi
  private_dir "$D/shim"; private_dir "$D/state"; private_dir "$S"
  place "$shim_source" "$D/shim/shim.py" 700
  local ca="$D/shim/ca"
  if [[ -e "$ca.crt" || -e "$ca.key" || -e "$D/shim/bundle.crt" ]]; then
    [[ -s "$ca.crt" && -s "$ca.key" && -s "$D/shim/bundle.crt" ]] || fail 'partial CA installation; refusing regeneration'
  else
    openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes \
      -keyout "$ca.key" -out "$ca.crt" -days 3650 -subj '/CN=local-ts-alpn-shim'
    cat /etc/ssl/certs/ca-certificates.crt "$ca.crt" > "$D/shim/bundle.crt"
  fi
  if [[ ! -e "$S/ssh_host_ed25519_key" ]]; then
    [[ ! -e "$D/installed" && ! -e "$S/ssh_host_ed25519_key.pub" ]] || fail 'host key missing; refusing identity replacement'
    ssh-keygen -q -t ed25519 -N '' -f "$S/ssh_host_ed25519_key"
  fi
  if [[ ! -e "$S/authorized_keys" ]]; then
    if [[ -f "$HOME/.ssh/authorized_keys" ]]; then
      install -m 600 "$HOME/.ssh/authorized_keys" "$S/authorized_keys"
    else
      : > "$S/authorized_keys"
      printf 'notice: add your public key to %s/authorized_keys before SSH use\n' "$S"
    fi
  fi
  cat > "$tmp/sshd_config" <<CFG
Port 22
ListenAddress 127.0.0.1
HostKey $S/ssh_host_ed25519_key
PidFile $R/sshd.pid
UsePAM no
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
AuthorizedKeysFile $S/authorized_keys
StrictModes no
Subsystem sftp internal-sftp
CFG
  place "$tmp/sshd_config" "$S/sshd_config" 600
  place "${BASH_SOURCE[0]}" "$D/setup.sh" 700
  cat > "$tmp/start.sh" <<'START'
#!/usr/bin/env bash
set -euo pipefail
DOTS_TS_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export DOTS_TS_DIR
exec bash "$DOTS_TS_DIR/setup.sh" "${1:-start}"
START
  place "$tmp/start.sh" "$D/start.sh" 700
  printf 'persistent installation\n' > "$D/installed"
  printf 'installed: %s\n' "$D"
}
start_all() {
  [[ -f "$D/installed" && -x "$D/tailscaled" && -x "$D/tailscale" && -s "$S/ssh_host_ed25519_key" ]] || fail 'incomplete installation; run install first'
  if [[ -e "$D/authenticated" && ! -s "$D/state/tailscaled.state" ]]; then
    fail 'previously authenticated state is missing; restore it or review identity replacement manually'
  fi
  local f
  for f in "$D/tailscale" "$D/tailscaled" "$D/shim/shim.py" "$D/shim/ca.crt" "$D/shim/ca.key" "$D/shim/bundle.crt" "$S/sshd_config" "$S/ssh_host_ed25519_key" "$S/authorized_keys"; do
    [[ -f "$f" && ! -L "$f" ]] || fail "missing or symlink file: $f"
  done
  private_dir "$D/shim"; private_dir "$D/state"; private_dir "$S"
  chmod 600 "$S/authorized_keys" "$S/ssh_host_ed25519_key" "$D/shim/ca.key"
  /usr/sbin/sshd -t -f "$S/sshd_config"
  # The supplied shim uses UPSTREAM_HOST/PORT; derive them from the configured proxy.
  local proxy_parts
  proxy_parts="$(python3 - "$UPSTREAM_PROXY" <<'PY'
import sys, urllib.parse
u = urllib.parse.urlsplit(sys.argv[1])
if u.scheme != 'http' or not u.hostname or u.username or u.password: sys.exit('requires unauthenticated http proxy URL')
print(u.hostname, u.port or 80)
PY
)"
  read -r UPSTREAM_HOST UPSTREAM_PORT <<< "$proxy_parts"
  export UPSTREAM_HOST UPSTREAM_PORT
  if launch_needed "$D/shim/shim.py"; then
    rotate "$R/shim.log"
    nohup python3 "$D/shim/shim.py" >> "$R/shim.log" 2>&1 9>&- &
    sleep 1
  fi
  running "$D/shim/shim.py" || fail 'shim did not start'
  export HTTPS_PROXY=http://127.0.0.1:18089 HTTP_PROXY=http://127.0.0.1:18089 SSL_CERT_FILE="$D/shim/bundle.crt"
  if ! TS status --json >/dev/null 2>&1 && launch_needed "$D/tailscaled"; then
    [[ ! -L "$SOCK" ]] || fail 'socket symlink refused'
    if [[ -e "$SOCK" ]]; then
      fail "socket exists but daemon cannot be identified: $SOCK; inspect before removing it"
    fi
    rotate "$R/tailscaled.log"
    nohup "$D/tailscaled" --tun=userspace-networking --statedir="$D/state" --socket="$SOCK" --port=0 >> "$R/tailscaled.log" 2>&1 9>&- &
  fi
  local ready=0
  for _ in {1..20}; do
    if timeout 3 "$D/tailscale" --socket="$SOCK" status --json > "$R/status.json"; then ready=1; break; fi
    sleep 0.5
  done
  [[ "$ready" = 1 ]] || fail 'tailscaled CLI unavailable'
  if launch_needed "$S/sshd_config"; then
    rotate "$R/sshd.log"
    /usr/sbin/sshd -f "$S/sshd_config" -E "$R/sshd.log" 9>&-
  fi
  python3 - <<'PY'
import socket
with socket.create_connection(('127.0.0.1',22), timeout=5) as s:
    if not s.recv(256).startswith(b'SSH-'): raise SystemExit('invalid SSH banner')
PY
}
backend() {
  TS status --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["BackendState"])'
}
authenticate_with_key() {
  local state key_file
  state="$(backend)"
  [[ "$state" != Running ]] || return 0
  key_file="${DOTS_TS_AUTH_KEY_FILE:-}"
  [[ -n "$key_file" ]] || fail 'auth mode requires DOTS_TS_AUTH_KEY_FILE'
  python3 - "$key_file" <<'AUTH'
import os, pathlib, stat, sys
p = pathlib.Path(sys.argv[1])
if not p.is_absolute(): sys.exit('Auth key file must use an absolute path')
if any(q.is_symlink() for q in (p, *p.parents)): sys.exit('Auth key symlink refused')
st = p.stat()
if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid(): sys.exit('Auth key must be a regular file owned by current user')
if stat.S_IMODE(st.st_mode) not in (0o600, 0o400): sys.exit('Auth key permissions must be 600 or 400')
if not st.st_size: sys.exit('Auth key file is empty')
AUTH
  timeout 60 "$D/tailscale" --socket="$SOCK" up --auth-key="file:$key_file" \
    --hostname="$NODE_HOSTNAME" --ssh=false --timeout=45s
}
case "$MODE" in check|install|start|login|auth) ;; *) fail 'usage: tailscale-setup.sh [check|install|start|login|auth]';; esac
if [[ "$MODE" = check ]]; then
  printf 'persistent=%s\nruntime=%s\n' "$D" "$R"
  for f in tailscale tailscaled state/tailscaled.state sshd/ssh_host_ed25519_key installed; do
    if [[ -e "$D/$f" ]]; then printf 'present: %s\n' "$f"; else printf 'missing: %s\n' "$f"; fi
  done
  if [[ -S "$SOCK" && -x "$D/tailscale" ]]; then TS status --peers=false; fi
  exit 0
fi
preflight
private_dir "$D"; private_dir "$R"
[[ ! -L "$D/.operation.lock" ]] || fail 'lock symlink refused'
exec 9> "$D/.operation.lock"
flock -n 9 || fail 'another operation is running'
if [[ "$MODE" = install ]]; then install_all; exit 0; fi
start_all
if [[ "$MODE" = login ]]; then
  # Explicit interactive action only; no auth key, no forced reauthentication.
  timeout 180 "$D/tailscale" --socket="$SOCK" up --hostname="$NODE_HOSTNAME" --ssh=false
fi
if [[ "$MODE" = auth ]]; then authenticate_with_key; fi
state="$(backend)"
case "$state" in
  Running) : > "$D/authenticated"; printf 'running: Tailscale and local SSH ready\n';;
  NeedsLogin) printf 'needs_auth: run bash %s/setup.sh login\n' "$D"; exit 10;;
  *) printf 'unavailable: BackendState=%s\n' "$state"; exit 11;;
esac
