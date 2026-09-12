# Changelog

## 1.7 - 2026-09-12 (untested, pending validation)

- 1.6 was tested on real hardware. `atl1c_reset_mac()` succeeded on the
  first attempt (no failure logged), so the 1.6 retry logic never had
  anything to trigger against - and yet the interfaces stayed down for
  much longer than a confirmed <60s RouterOS reboot time, even after
  RouterOS itself was fully up. Root cause, confirmed with two
  carefully-ordered `/proc/interrupts` checks (taken while genuinely
  stuck, not contaminated by an earlier manual reset): interrupt counts
  for all four ports went completely flat and stayed flat indefinitely
  - no further interrupt arrived at all while down, even minutes later.
- These four ports are PCIe-transport emulated by the CCR2004's own
  AL52400 SoC (confirmed via its block diagram - only two SFP28 ports
  and a management interface are real hardware; these four exist only
  as an emulation over the PCIe lanes). Link-change detection in this
  driver was purely interrupt-driven, with a `watchdog_timer` field
  declared in the adapter struct but never actually wired up anywhere
  - dead code. If the far end's interrupt generation for a virtual port
  doesn't resume after its own reboot for whatever reason, nothing on
  the Linux side would ever notice the link coming back, no matter how
  long it waited.
- Fix: wired up `watchdog_timer` as a real periodic poll (every 5s)
  that unconditionally re-checks link status via the existing
  LINK_CHANGE work-event path, independent of whether a hardware
  interrupt ever fires. Armed in `atl1c_up()`, cancelled in
  `atl1c_del_timer()` (already called from `atl1c_down()`/
  `atl1c_close()`), self-rearming via `mod_timer()` each time it runs.
- Needs the same reboot-cycle validation as every prior version -
  this time watching specifically for the interface coming up on its
  own within a few seconds of RouterOS actually finishing its boot,
  even with interrupts having gone silent.

## 1.6 - 2026-09-12 (untested, pending validation)

- 1.5's D3hot->D0 power-cycle was tested on real hardware and made
  things worse: `pci_set_power_state()` reported "device inaccessible"
  for both transitions, and a new failure appeared that had never shown
  up before - `Unable to allocate MSI interrupt Error: -22`. "Device
  inaccessible" means PCI config-space reads for this function were
  returning all-ones at that moment - the PCIe link itself was briefly
  unreachable, not just the Ethernet MAC/PHY. No in-band trick (soft
  reset, PHY reset, FLR, power-state change) can work at the exact
  moment the config space itself doesn't respond.
- Root cause (current best understanding): the CCR2004 is a whole
  separate router rebooting, which takes tens of seconds - every
  recovery attempt so far has tried to fix things within ~1-2 seconds
  of the failure, guaranteed to fail regardless of technique. The only
  thing that has ever reliably worked (manual PCI remove+rescan) likely
  works largely because enough real wall-clock time passes for a human
  to run several commands, not because of anything unique to that
  specific recovery mechanism.
- Fix: replaced the immediate D3/D0 attempt with a backing-off delayed
  retry. When `atl1c_reset_mac()` fails (via either `atl1c_down()`'s
  RESET path or `atl1c_check_link_status()`'s LINK_CHANGE path),
  instead of giving up immediately, schedule a delayed retry
  (`schedule_delayed_work`) that re-triggers the same recovery path
  after a real delay: 5s, 10s, 15s, ... capped at 30s, up to 6 attempts
  (~105s total) before finally giving up and logging.
- `atl1c_down()`+`atl1c_up()` are always still called as an immediate,
  paired cycle on every attempt (never skipped): `atl1c_free_irq()` has
  no guard against a double-free, so a retry must never call
  `atl1c_down()` again without an intervening `atl1c_up()` having
  re-requested the IRQ first.
- Needs the same reboot-cycle validation as every prior version -
  watch for the new "MAC reset failed (retry N/6), retrying in Ns"
  log line, and confirm the eventual recovery reports a real
  negotiated speed (not 0xffff) once it succeeds.

## 1.5 - 2026-09-12 (untested, pending validation)

- 1.4 was tested on `proxy` with full real-hardware logging. Result:
  the FLR escalation is a dead end. Twice observed:
  - `pci_reset_function()` returned `-25` (`-ENOTTY`, meaning the PCI
    core determined no reset method - FLR or secondary-bus - is usable
    on this AR8151 hardware) - no reset actually attempted.
  - A later occurrence: `pci_reset_function()` reported no error, but
    the retried `atl1c_reset_mac()` explicitly logged "still failed".
  In both cases the driver went on to report `NIC Link is Up<65535
  Mbps ...>` - `65535` is `0xffff`, the same garbage-read sentinel that
  caused the original soft lockup - not a real link. The TX watchdog
  fired again minutes later on the same port, confirming no real link
  had come up. Every prior software-only recovery (soft MAC reset, PHY
  reset, FLR) has produced this same fake recovery. Only the manual PCI
  `remove`+`rescan` has ever produced a genuinely working link (a real
  negotiated speed like 25000 Mbps, not the 0xffff sentinel).
- Root cause (best current understanding): `remove`+`rescan` power-cycles
  the PCI function (D3 -> D0) as a side effect of full re-enumeration.
  Nothing in-band - a register soft reset, a PHY reset, or a logical
  FLR - was ever equivalent to that actual power removal, so none of
  them could clear whatever hardware state genuinely needs a real power
  transition to clear.
- Fix: replaced the FLR escalation (in both `atl1c_common_task()`
  branches) with a `pci_set_power_state(PCI_D3hot)` -> `PCI_D0`
  power-cycle, moved directly into `atl1c_reset_mac()` itself - the one
  function every caller (`atl1c_probe()`, `atl1c_down()`,
  `atl1c_check_link_status()`, `atl1c_resume()`) already routes
  through, escalating automatically wherever a soft reset fails.
  `pci_set_power_state()` takes no lock the PCI core also needs (unlike
  `pci_reset_function()`), so unlike the FLR attempt this needs no
  workqueue-only restriction - it's safe from every caller, including
  `->probe()`/`->suspend()`/`->resume()`.
- Simplified `atl1c_common_task()`'s two branches back to their original
  shape now that the escalation logic lives in the shared function;
  reverted `atl1c_down()`/`atl1c_check_link_status()` back to `void`
  since nothing external needs their reset status anymore. Net result
  is a smaller diff than 1.1-1.4 combined despite fixing the real
  problem those didn't.
- Needs the same reboot-cycle validation as every prior version - this
  time specifically checking for a real negotiated link speed (not
  0xffff) after automatic recovery, and confirming traffic actually
  passes rather than just trusting the "Link is Up" log line.

## 1.4 - 2026-09-12 (untested, pending validation)

- Closes the gap flagged in 1.3: `atl1c_check_link_status()` (the
  LINK_CHANGE path, reached on every ordinary link-down/up interrupt -
  the most likely trigger in practice, since it fires long before any TX
  queue would ever time out) had the exact same issues as the RESET
  branch, and was completely unpatched by 1.1/1.2/1.3:
  - it only reset the MAC, never the PHY;
  - its one failure log (`reset mac failed`) was gated behind
    `netif_msg_hw(adapter)` and easy to miss;
  - it had no FLR escalation at all.
- `atl1c_check_link_status()` now: always logs a MAC reset failure, always
  resets the PHY when the link is down (matching `atl1c_probe()`/
  `atl1c_resume()`), and returns whether the MAC reset failed.
- `atl1c_common_task()`'s LINK_CHANGE branch now escalates to the same
  FLR-and-retry sequence as the RESET branch when that MAC reset fails -
  this is a safe call site (workqueue, no device_lock held), same
  reasoning as 1.1.
- Removed the RESET branch's now-redundant explicit `atl1c_phy_reset()`
  call added in 1.2: `atl1c_up()` calls `atl1c_check_link_status()` at
  the end of its own sequence, which now does the PHY reset itself if
  the link is still down at that point - no need for a second, unconditional
  call.
- `atl1c_check_link_status()` is also called directly from `atl1c_up()`,
  which is reachable from `atl1c_resume()` under `device_lock` during
  system sleep. The FLR escalation is deliberately NOT added inside
  `atl1c_check_link_status()` itself for that reason (same device_lock
  deadlock risk as the RESET branch's design in 1.1) - it only lives in
  `atl1c_common_task()`'s direct call, which is always workqueue-only.
  The PHY reset is safe everywhere and stays inside the shared function.
- Needs the same reboot-cycle validation as every prior version.

## 1.3 - 2026-09-12 (instrumentation only, no new recovery behavior)

- 1.2 was tested on `proxy`. Result was inconclusive on whether the FLR
  escalation from 1.1 does anything useful: `lspci -tv` + a dmesg grep for
  `pcieport`/`pciehp`/AER showed no PCIe-level hotplug/link event at all
  on the `05:00.x` functions' parent root port (`00:0d.0`) - ruling out a
  host-side PCIe hotplug/electrical-link theory. The interfaces sat at
  `NO-CARRIER` for 8+ hours with zero further driver log activity after
  the automatic recovery ran, and a later manual `modprobe -r`/`modprobe`
  (bypassing the automatic recovery path entirely) then failed probe
  with `-5` for all four functions.
- Since the AR8151 chip is old/cheap silicon with a spotty PCIe-compliance
  track record, `pci_reset_function()` (FLR, falling back to a secondary
  bus reset if unsupported) may be silently doing nothing useful on this
  hardware - we had no logging to tell either way.
- This release adds no new recovery behavior: it only logs
  `pci_reset_function()`'s actual return code and whether the retried
  `atl1c_reset_mac()` succeeded afterward, so the next real-hardware test
  tells us whether the FLR is doing anything at all before adding a
  further escalation (e.g. an explicit D3hot->D0 power-cycle, which
  actually removes power from the ASIC rather than issuing a logical
  reset).
- Caveat: this logging is only reachable via the RESET branch (a TX
  watchdog timeout). The LINK_CHANGE branch (`atl1c_check_link_status()`)
  - which real-hardware evidence suggests is the path actually exercised
  on a plain link flap with no pending traffic - is not instrumented and
  has none of 1.1/1.2's fixes either. May need to see nothing logged at
  all, which would itself be informative.

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
