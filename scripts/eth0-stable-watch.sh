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
set -u
IFACE="eth0"

apply_fix() {
    ethtool -s "$IFACE" autoneg on advertise 0x008 2>/dev/null
    ethtool --set-eee "$IFACE" eee off 2>/dev/null
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
