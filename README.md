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
