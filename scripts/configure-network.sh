#!/usr/bin/env bash
# Configure a static IP on the Raspberry Pi's wired Ethernet for sentinel.
#
# Run this ON the Pi itself (console, or over SSH using whatever DHCP address
# it already picked up on first boot -- see README "Finding the Pi" section).
#
# Defaults match config.example.yaml's PLC/OT network (192.168.1.0/24) so the
# Pi can reach the example PLCs with zero changes. Override via environment
# variables when you know your real site network, e.g.:
#
#   SENTINEL_IP=10.20.30.50/24 SENTINEL_GATEWAY=10.20.30.1 sudo -E ./configure-network.sh
#
# Safe to re-run (idempotent).

set -euo pipefail

SENTINEL_IP="${SENTINEL_IP:-192.168.1.40/24}"
SENTINEL_GATEWAY="${SENTINEL_GATEWAY:-}"   # leave unset on an isolated OT segment with no router
SENTINEL_DNS="${SENTINEL_DNS:-}"           # e.g. "1.1.1.1 8.8.8.8"; leave unset if not needed
SENTINEL_CONN_NAME="${SENTINEL_CONN_NAME:-sentinel-static}"

if [[ $EUID -ne 0 ]]; then
  echo "Run this with sudo (it edits NetworkManager connections)." >&2
  exit 1
fi

# Make sure SSH is on so you're not locked out if you're on console right now.
systemctl enable --now ssh

# Find the onboard wired Ethernet device. Raspberry Pi 5 on current kernels
# names it "end0"; older Pi boards/kernels use "eth0" -- auto-detect instead
# of hardcoding either, since current Raspberry Pi OS uses NetworkManager
# (dhcpcd is masked/unused), and interface names vary by board/kernel.
IFACE="$(nmcli -t -f DEVICE,TYPE device status | awk -F: '$2=="ethernet"{print $1; exit}')"
if [[ -z "$IFACE" ]]; then
  echo "No Ethernet device found via nmcli. Is a cable plugged in?" >&2
  exit 1
fi
echo "Using interface: $IFACE"

# Reuse an existing connection profile for this interface if one exists,
# otherwise create a dedicated one so other profiles are left alone.
EXISTING_CONN="$(nmcli -t -f NAME,DEVICE connection show | awk -F: -v i="$IFACE" '$2==i{print $1; exit}')"
CONN="${EXISTING_CONN:-$SENTINEL_CONN_NAME}"

if [[ -z "$EXISTING_CONN" ]]; then
  nmcli connection add type ethernet ifname "$IFACE" con-name "$CONN"
fi

nmcli connection modify "$CONN" ipv4.method manual ipv4.addresses "$SENTINEL_IP"
nmcli connection modify "$CONN" ipv4.gateway "${SENTINEL_GATEWAY:-}"
if [[ -n "$SENTINEL_DNS" ]]; then
  nmcli connection modify "$CONN" ipv4.dns "$SENTINEL_DNS"
fi

nmcli connection up "$CONN"

echo
echo "Done. Current address(es) on $IFACE:"
ip -4 addr show "$IFACE" | grep inet
