# Changelog

## 1.2 - 2026-09-11 (untested, pending validation)

- 1.1 was tested on `proxy`: the PCIe FLR escalation never fired (the MAC
  soft reset succeeded on its own), yet after the Mikrotik CCR2004 reboot
  all four interfaces stayed at `NO-CARRIER` indefinitely - administratively
  `UP`, `ip link set up` a no-op, no further recovery. Only a full PCI
  `remove`+`rescan` (which also resets the PHY) brought the link back.
- Root cause: `atl1c_down()`'s reset only ever touches the MAC
  (`atl1c_reset_mac()`). It never calls `atl1c_phy_reset()`, which
  `atl1c_probe()` and `atl1c_resume()` both do as part of their own
  recovery. A PCIe link event can leave the PHY itself stuck even when
  the MAC reset succeeds cleanly - nothing in the automatic recovery path
  ever touches the PHY, so the link can never renegotiate on its own.
- Fix: `atl1c_common_task()`'s reset path now also calls
  `atl1c_phy_reset()` unconditionally (regardless of whether the FLR
  escalation from 1.1 fired), matching what `probe()`/`resume()` already
  do, before bringing the interface back up.
- Needs the same reboot-cycle validation as 1.0/1.1 before being trusted.

## 1.1 - 2026-09-11 (superseded by 1.2, PHY was still not being reset)

- Second, separate issue found in production use: even with 1.0 installed
  (no more crashes), the four interfaces did not come back on their own
  after every single Mikrotik CCR2004 reboot. Recovery required manually
  `modprobe -r`ing the driver, removing all four PCI functions, and
  rescanning the bus.
- Root cause: `atl1c`'s own tx-timeout recovery path
  (`atl1c_tx_timeout()` -> `atl1c_common_task()` -> `atl1c_down()` /
  `atl1c_up()`) only issues a register-level MAC soft reset
  (`atl1c_reset_mac()`). After the kind of PCIe link event a neighboring
  device's reboot causes, that soft reset can time out and fail
  (`MAC state machine can't be idle since disabled for 10ms second`) -
  and `atl1c_down()` previously ignored that failure and proceeded to
  bring the interface back up on top of a MAC that was never actually
  reset. Even a full `modprobe -r`/`modprobe` (which re-runs
  `atl1c_probe()`, itself already doing a fuller reset sequence) hit the
  same soft-reset timeout and failed to probe with `-EIO` (-5). Only an
  actual PCI-level `remove`+`rescan`, which forces real hardware
  re-enumeration, reliably cleared it.
- Fix: `atl1c_down()` now reports whether its MAC reset succeeded.
  `atl1c_common_task()`'s reset path escalates to a PCIe function-level
  reset (`pci_reset_function()`, i.e. FLR / secondary-bus reset) and
  retries the MAC reset once before bringing the interface back up.
- Deliberately NOT added inside `atl1c_reset_mac()` itself or in
  `atl1c_down()` directly: `pci_reset_function()` takes the device's
  `device_lock`, which is already held by the driver core while
  `atl1c_probe()` and `atl1c_suspend()`/`atl1c_resume()` run - calling it
  from those paths would self-deadlock. The escalation only happens in
  `atl1c_common_task()`'s workqueue context, which holds no such lock.
- Needs the same reboot-cycle validation as 1.0 before being trusted,
  since this touches the actual reset/recovery path rather than just
  guarding a read.

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
