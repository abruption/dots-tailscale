# Continuous trial service templates

Replace every `/ABSOLUTE/...` placeholder before installation. Keep configuration
and relay state outside the repository in operator-owned mode-0700 directories,
with mode-0600 files. The default example sends only documented observation fields over verified HTTPS
with a private machine credential. See [transport provisioning](../relay-transport.md).
The optional SSH transport requires an independently verified host key and a
noninteractive authentication contract; Tailscale SSH check renewal is unsuitable
for an unattended dependency. Neither transport prints raw remote output.

The Mac observer already owns its own singleton lock. Confirm its current PID and
command, stop only that identified instance, then adopt it under launchd using
the observer template. Do not run two observers or edit the existing script.
Load the relay only after a fresh observer sample and a successful receiver
smoke check. LaunchAgents survive process exits, but require the user's login
session after a Mac restart. Verify startup after login separately.

The observer template explicitly sets `TERM=xterm-256color`. During the Mac
deployment trial, the installed Tailscale app binary attempted to launch its GUI
under launchd's minimal environment without `TERM`. It printed `The Tailscale
GUI failed to start` to stdout and returned exit code 0, without a ping response.
Adding only `TERM` to that minimal environment produced a normal `pong` response.
The live observer LaunchAgent received the same setting; the observer script was
unchanged. Check the ping response itself after adoption, not just its exit code.

The KR user systemd service needs lingering enabled to survive logout and start
at boot. Inspect `loginctl show-user <operator> -p Linger`; enabling it may require
root. Adopt the existing server after checking the exact old process and port;
do not start a second listener. These templates contain no runtime deadline.

The relay processes a fresh latest sample (maximum age 150 seconds; future skew
maximum 5 seconds), polling every 15 or 30 seconds. Transport failures back off to at
most 60 seconds and retain the original observation time for receiver deduplication.
A newer sample may supersede a failed older sample. A stale observation never
becomes a fresh outage event. The health file reports unavailable observations
or failed transports; neither condition itself asserts a Dots outage.

Stop/unload the two adopted launchd jobs and stop/disable the KR user service to
end the trial. Subscription lifetime and renewal are separately controlled by
the event service and Dots; process persistence does not make a subscription
permanent. Verify an active authorized recovery subscription before expecting
an outage event to cause a Dots action.
