# Shim connection budget

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

An established control, DERP or raw tunnel can hold its slot as long as it is
healthy. This setting does not add a stream lifetime or idle timeout. A lower
limit therefore uses fewer resources but can reject legitimate reconnects or
additional streams while all slots are occupied. Choose a value with the expected
number of simultaneous streams and the host's thread, socket and memory budgets
in mind. At most one handler and one forwarding worker are created for each
admitted connection, with one additional timer thread during a non-upgraded HTTPS
response setup stage. Certificate generation and buffers also consume resources;
this is a concurrency limit, not a complete memory or bandwidth quota.

The listener remains bound to `127.0.0.1`. This does **not** by itself make it
inaccessible to tailnet peers: Tailscale userspace networking can forward inbound
TCP for the node address to the same loopback port. Local processes and peers
whose tailnet policy allows that port can occupy slots. Restrict the node's
network policy to the services you intend to expose. The admission budget adds no
authentication and does not replace that policy, setup deadlines, or CONNECT
target validation. No actual resource-exhaustion incident is established by these
tests.

The regression suite uses isolated socket pairs and controlled startup failures
to verify rejection without new threads, capacity reuse, socket cleanup, joining
an upload worker when the response timer cannot start, and a raw stream that
remains usable after its setup deadline. These checks do not establish behavior
under a live Dots outage, a VM replacement, or a production load test.
