# Changelog

## 1.0 - 2026-09-11

- Initial DKMS package wrapping upstream `atl1c` (Attansic/Atheros L1C 4-port
  NIC driver) with a fix for a soft lockup / hard host hang.
- Root cause: `atl1c_clean_tx()` reads a hardware `tpd_cons` register and
  loops `next_to_clean` towards it; if the register read returns a value
  outside the ring (seen as `0xffff` during a PCIe link/MAC reset — e.g.
  triggered by a neighboring Mikrotik CCR2004 card rebooting), the loop
  condition can never be satisfied and the NAPI thread spins forever,
  producing `watchdog: BUG: soft lockup ... atl1c_clean_tx` and a full
  host hang.
- Fix: guard the register read, treating an out-of-range value as
  "nothing new to clean" for that pass instead of looping forever.
- Fixed `dkms.conf` missing `BUILT_MODULE_LOCATION`, which caused DKMS to
  report a false build failure even though `atl1c.ko` compiled correctly.
- Validated on host `proxy` (Ubuntu 26.04.1 LTS, kernel `7.0.0-31-generic`,
  AR8151 v2.0 4-port card at PCI `05:00.0-.3`): multiple Mikrotik CCR2004
  reboot cycles with the interfaces up no longer hang the host; only the
  expected transient `NETDEV WATCHDOG` / `MAC state machine` recovery
  messages appear during the flap.
