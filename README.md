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
