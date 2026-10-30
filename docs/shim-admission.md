# Shim connection budget

This guide covers features included in **v0.8.0**. Check the release page for
published artifacts. Installing the package does not enable automatic updates or
register a recovery task.

The transport shim accepts at most **64 active connections** by default. A slot
is reserved before a handler thread is created and stays reserved until the
handler has closed its sockets and joined its bridge workers. Connections beyond
the limit are closed promptly, without allocating a handler or placing work in
an application queue. The kernel listen backlog remains separate from this limit.

For an operator-managed shim launch, `SHIM_MAX_CONNECTIONS` can select an integer
from **1 to 256**. Invalid values stop startup before the listener is bound. This
is a per-process setting: configure it in the environment that actually launches
the shim, rather than assuming a different shell's environment changes an already
running process. The startup message prints the effective limit. Changing the
value does not change a running process, install a scheduler, or restart services.
The package updater preserves and validates that existing shim environment value
when it launches either candidate or rollback code; an invalid value refuses the
transaction before the old shim is stopped.

An established control, DERP or raw tunnel can hold its slot as long as it is
healthy. This setting does not add a stream lifetime or idle timeout. A lower
limit therefore uses fewer resources but can reject legitimate reconnects or
additional streams while all slots are occupied. Choose a value with the expected
number of simultaneous streams and the host's thread, socket and memory budgets
in mind. At most one handler and one forwarding worker are created for each
admitted connection, with one additional timer thread during a non-upgraded HTTPS
response setup stage. Certificate generation and buffers also consume resources;
this is a concurrency limit, not a complete memory or bandwidth quota.

Every client shares this budget, including tailscaled itself. Saturation can
reject its control/DERP reconnects; even an idle connection can hold a setup slot
through its per-stage deadlines, including several consecutive setup stages.
Certificate generation still uses the existing serialized OpenSSL calls; this
connection budget is not a total setup-duration guarantee. A fixed warning reports saturation at most once every 30
seconds, after closing the excess socket. It does not identify clients or log
their CONNECT targets. Use the intended tailnet policy rather than arbitrary
stream lifetime limits to control who can reach this unauthenticated service.

The listener remains bound to `127.0.0.1`. This does **not** by itself make it
inaccessible to tailnet peers: Tailscale userspace networking can forward inbound
TCP for the node address to the same loopback port. Local processes and peers
whose tailnet policy allows that port can occupy slots. Restrict the node's
network policy to the services you intend to expose. The admission budget adds no
authentication and does not replace that policy, setup deadlines, or CONNECT
target validation. No actual resource-exhaustion incident is established by these
tests.

A permitted peer can also attempt to use the CONNECT proxy, subject to its target
validation and the upstream platform proxy's policy. Loopback binding is neither
proxy authentication nor proof that the peer is restricted to slot occupancy.

The regression suite uses isolated socket pairs and controlled startup failures
to verify rejection without new threads, capacity reuse, socket cleanup, joining
workers/timers even when startup raises after the thread began, and normal raw
and multiple WebSocket streams that remain usable after setup deadlines (with
TLS explicitly replaced by a fixture). These checks do not establish behavior
under a live Dots outage, a VM replacement, or a production load test.
