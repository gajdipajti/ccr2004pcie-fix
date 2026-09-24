# atl1c-ccr2004fix

DKMS package fixing a soft-lockup in the `atl1c` driver (Attansic/Atheros
L1C 4-port NIC) triggered when a neighboring Mikrotik CCR2004 PCIe card
reboots and flaps the link.

## Root cause

`atl1c_clean_tx()` in atl1c_main.c reads a hardware "tx consumer" index
(`tpd_cons`) and loops `next_to_clean` up to it:

```c
AT_READ_REGW(&adapter->hw, atl1c_qregs[tpd_ring->num].tpd_cons, &hw_next_to_clean);
while (next_to_clean != hw_next_to_clean) { ... }
```

`next_to_clean` only ever takes values in `[0, tpd_ring->count)`. During a
PCIe link/MAC reset (seen when the Mikrotik card's own reboot flaps the
link) the register read can return an out-of-range value such as `0xffff`.
When that happens the loop condition can never become true, and the NAPI
thread spins forever — observed as:

```
watchdog: BUG: soft lockup - CPU#12 stuck for 354s! [napi/eth%d-0:329]
RIP: 0010:atl1c_clean_tx+0x142/0x2d0 [atl1c]
```

## Fix

Guard the register read: if it's out of range, treat it as "nothing new
to clean" this pass instead of looping forever. See `src/atl1c_main.c`.

## Build / install

```
sudo dkms add .
sudo dkms build atl1c-ccr2004fix/1.0
sudo dkms install atl1c-ccr2004fix/1.0
sudo modprobe -r atl1c && sudo modprobe atl1c
```

`dkms status` should show the module built and installed for the running
kernel; it rebuilds automatically on kernel upgrades.

## Test plan

1. Confirm the patched module is loaded: `modinfo atl1c | grep filename`
   should point under `/lib/modules/$(uname -r)/updates/`.
2. Reboot the Mikrotik CCR2004 card while the host's atl1c interfaces are
   up, repeatedly, and confirm the host no longer hard-hangs.
3. Watch `dmesg -w` during the test — the guard has no visible log output
   in the fixed path; a stuck host or a recurring soft lockup message
   means the fix didn't take (wrong module loaded, or a different bug).

## Recovering interfaces after a manual module reload

Do not run `modprobe -r atl1c` / `modprobe atl1c` while a port is actively
mid-flap (link down, "MAC state machine can't be idle" repeating). Pulling
the driver out from under a NIC that's stuck resetting leaves the hardware
wedged, and re-probing then fails for all four functions:

```
atl1c 0000:05:00.0: probe with driver atl1c failed with error -5
```

A plain `modprobe -r atl1c && modprobe atl1c` will **not** fix this once it
happens — the PCI core needs to fully re-enumerate the device, not just
re-attach the driver. Force that with a remove + rescan instead:

```
sudo modprobe -r atl1c
echo 1 | sudo tee /sys/bus/pci/devices/0000:05:00.0/remove
echo 1 | sudo tee /sys/bus/pci/devices/0000:05:00.1/remove
echo 1 | sudo tee /sys/bus/pci/devices/0000:05:00.2/remove
echo 1 | sudo tee /sys/bus/pci/devices/0000:05:00.3/remove
echo 1 | sudo tee /sys/bus/pci/rescan
```

Then verify the card came back and bring the interfaces up:

```
lspci -k -s 05:00.0            # should show "Kernel driver in use: atl1c"
ip link show | grep -A1 enp5s0f
modinfo atl1c | grep filename  # confirm still the patched module
sudo netplan apply             # or: ip link set enp5s0fX up, per interface
```

If the PCI rescan still fails to bring the card back, the MAC is wedged at
the hardware level and needs a full reboot to reset — don't chase it
further by hand.

## Second bug (1.10): RX skb_over_panic on unvalidated descriptor length

Unrelated to the TX soft lockup above - a full kernel crash, not a
lockup. Found via a third-party report on a different CCR2004 model
(1G-2XS-PCIe): https://www.jayme.ca/home/proxmoxlinux-crash-skb_over_panic-wccr2004-1g-2xs-pcie

`atl1c_clean_rx()` takes the packet length straight from the
hardware's RX-return-status descriptor and hands it to `skb_put()`
with no check against the actual allocated RX buffer size:

```c
length = le16_to_cpu((rrs->word3 >> RRS_PKT_SIZE_SHIFT) & RRS_PKT_SIZE_MASK);
...
skb_put(skb, length - ETH_FCS_LEN);
```

If the hardware ever reports a length larger than the buffer actually
DMA-mapped for that receive slot (`buffer_info->length`), `skb_put()`'s
own bounds check trips `skb_over_panic()` and crashes the host:

```
skb_over_panic ... len:1553 put:1553 ... tail:0x691 end:0x680 dev:enp1s0f1
kernel BUG at net/core/skbuff.c:211!
```

The originally reported trigger: RouterOS defaults a host-facing PCIe
interface to `l2mtu=1600` (baby-jumbo frames), while the Linux `atl1c`
interface was still at the default MTU 1500 (~1522-byte RX buffer) - a
legitimate 1557-byte frame from the RouterOS side overran the smaller
Linux-side buffer by 17 bytes. Same root-cause pattern as the TX bug
above (blind trust in a hardware-reported value, same hardware family)
- traces to the same original 2009 driver-introduction commit. Not
limited to the MTU-mismatch scenario either: a garbage/corrupted
descriptor during a link reset (what this whole package exists to
work around) could trigger it too, independent of any MTU setting.

Checked Intel's `e1000`/`e1000e` (the driver family `atl1c` is
modeled after) for an established fix to cite - neither has one.
`e1000` avoids the bug class architecturally (page-fragment RX, or
allocating the skb sized to the already-known length for its
copybreak path); `e1000e`'s directly comparable large-packet path has
the *exact same* unchecked gap `atl1c` does. Likely explanation:
Intel's silicon is well-behaved enough in practice that this
theoretical gap hasn't visibly bitten it, whereas this whole
investigation has repeatedly demonstrated the Attansic/Atheros/
Qualcomm chips `atl1c` targets *do* misbehave under real conditions.

### Fix

Validate the reported length against `buffer_info->length` before
`skb_put()`; drop the packet instead of crashing on mismatch:

```c
if (unlikely(length < ETH_FCS_LEN ||
	     length - ETH_FCS_LEN > buffer_info->length)) {
	dev_kfree_skb(skb);
	continue;
}
skb_put(skb, length - ETH_FCS_LEN);
```

### Triggering / reproducing it

This can be reproduced without a Mikrotik/RouterOS peer at all, since
the bug is purely local driver logic - any oversized raw Ethernet
frame at a genuine `atl1c` interface left at the default MTU 1500
will do it. A ready-to-run PoC is included: `scripts/atl1c_rx_panic_poc.py`.

**Warning: this crashes an unpatched kernel on purpose.** Only run it
against hardware you own/control and are prepared to reboot.

```
# on the SENDING machine (needs a real L2 path to the target - same
# switch/segment or a direct cable; this operates below IP, it won't
# route). Its own MTU may need raising so its kernel doesn't reject
# the oversized raw send on that side - doesn't affect the target:
sudo ip link set <sender_iface> mtu 1600

# confirm the TARGET atl1c interface is still at the default MTU:
ip link show <target_atl1c_iface>   # should show mtu 1500

# fire the oversized frame (needs scapy + root/CAP_NET_RAW):
sudo python3 scripts/atl1c_rx_panic_poc.py <sender_iface> <target_mac>
```

Expected: unpatched kernel crashes (`skb_over_panic` / `kernel BUG at
net/core/skbuff.c` in the target's console/serial log); patched
kernel (1.10+) silently drops the one frame and keeps running.

## Notes

Surveyed the sibling drivers in `drivers/net/ethernet/atheros/` for the
same two bug classes fixed here:

- **Unbounded tx-clean loop** (what 1.0 fixes in `atl1c_clean_tx()`): also
  present, unfixed, in:
  - `atl1e` — `atl1e_clean_tx_irq()` in atl1e_main.c: identical shape,
    `while (next_to_clean != hw_next_to_clean)` with the hardware value
    read straight from a register, no range check, no budget cap.
  - `atlx`/`atl1` — `atl1_intr_tx()` in atl1.c: same pattern, reading the
    consumer index from a DMA-shared "command block" instead of a
    register, still unbounded.
  - `alx` (the newer, actively maintained driver for later Atheros/
    Qualcomm chip revisions) already solved this class of bug
    independently, but differently: `alx_clean_tx_irq()` bounds the loop
    with a decrementing `budget` counter instead of range-checking the
    register value (`while (sw_read_idx != hw_read_idx && budget > 0)`).
    That's arguably stronger than our guard — it can't spin forever on
    *any* garbage value, not just an out-of-range one.
  - Worth submitting an equivalent fix upstream for `atl1e` and `atl1` -
    same bug, same driver family, same maintainer (netdev, ATLX ETHERNET
    DRIVERS).

- **Recovery path not resetting the PHY** (what 1.2 fixes): `alx` has the
  same gap. Its tx-timeout/reset-work path (`alx_reset()` ->
  `alx_reinit()` -> `alx_halt()`/`alx_activate()`) only calls
  `alx_reset_mac()`, never `alx_reset_phy()` - that's only called from
  `alx_probe()` and `alx_resume()`, exactly like `atl1c` before 1.2. `alx`
  also has no `pci_reset_function()`/FLR fallback anywhere (what 1.1
  adds). Neither of our two recovery-path fixes was already solved there.
