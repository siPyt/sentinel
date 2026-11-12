#!/bin/bash
# Deployed to: /usr/local/sbin/eth0-stable-watch.sh  (on the Pi)
# Run by: systemd/eth0-stable-watch.service
#
# NetworkManager's dispatcher.d/50-eth0-stable only reruns on NetworkManager's
# own recognized events (up/hostname/dhcp4-change/connectivity-change). Silent
# PHY-only link flaps on the Pi 5 <-> PC direct-cable link don't trigger any of
# those, so eth0 could drift back to unstable gigabit auto-negotiation between
# dispatcher runs. This watcher reacts to the kernel's own link-state events
# instead (via `ip monitor link`) and re-applies the fix every time eth0
# reports "state UP", regardless of whether NetworkManager noticed.
#
# NOTE: if you edit this on Windows, strip CRLF line endings before deploying
# (sudo sed -i 's/\r$//' <path>) -- a CRLF shebang silently breaks this script.
#
# Uses the absolute path to ethtool (/usr/sbin/ethtool) rather than relying on
# PATH: systemd's default service environment does not reliably include
# /sbin:/usr/sbin, and a bare `ethtool` call can silently fail with "command
# not found" there even though it works fine from an interactive sudo shell.
# Each application is also logged via `logger` so success/failure is visible
# in `journalctl -u eth0-stable-watch.service` instead of being assumed.
set -u
IFACE="eth0"
ETHTOOL="/usr/sbin/ethtool"

apply_fix() {
    local out1 out2 rc1 rc2
    out1=$("$ETHTOOL" -s "$IFACE" autoneg on advertise 0x008 2>&1); rc1=$?
    out2=$("$ETHTOOL" --set-eee "$IFACE" eee off 2>&1); rc2=$?
    [ -n "$out1" ] && logger -t eth0-stable-watch "$out1"
    [ -n "$out2" ] && logger -t eth0-stable-watch "$out2"
    if [ "$rc1" -eq 0 ] && [ "$rc2" -eq 0 ]; then
        logger -t eth0-stable-watch "applied autoneg/EEE fix to $IFACE"
    else
        logger -t eth0-stable-watch "FAILED to apply autoneg/EEE fix to $IFACE (rc1=$rc1 rc2=$rc2)"
    fi
}

# Apply once at service startup too, not just on subsequent flaps.
apply_fix

ip monitor link | while read -r line; do
    case "$line" in
        *"$IFACE"*"state UP"*)
            apply_fix
            ;;
    esac
done
