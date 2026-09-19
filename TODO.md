# TODO

## Upstream `atl1c` patch (the soft-lockup fix)

- [ ] Decide: send v2 now, or wait for a maintainer reply to v1 first (posted
      2026-09-11, no reply yet as of 2026-09-17 other than the AI-generated
      review).
- [ ] v2: add `Fixes: 43250ddd75a35d ("atl1c: Atheros L1C Gigabit Ethernet
      driver")` and `Cc: stable@vger.kernel.org` (confirmed real commit,
      review's claim checks out).
- [ ] v2: expand the changelog with actual reproduction details - kernel
      `7.0.0-31-generic`, Ubuntu 26.04, AR8151 v2.0 4-port card, Mikrotik
      CCR2004 link-partner reboot, the soft lockup trace
      (`RIP: atl1c_clean_tx+0x142/0x2d0`).

## Sibling driver patches (same bug, confirmed unfixed, no workaround exists)

- [x] `atl1e_clean_tx_irq()` in `atl1e_main.c` - patch drafted, checkpatch
      clean (0 errors/warnings). Guard: `if (unlikely(hw_next_to_clean >=
      tx_ring->count)) hw_next_to_clean = next_to_clean;`.
- [x] `atl1_intr_tx()` in `atlx/atl1.c` - patch drafted, checkpatch clean
      (0 errors/warnings). Guard: `if (unlikely(cmb_tpd_next_to_clean >=
      tpd_ring->count)) cmb_tpd_next_to_clean = sw_tpd_next_to_clean;`.
      Confirmed no existing workaround despite initially thinking otherwise.
- [x] Decided: send as a 3-patch series (atl1c + atl1e + atl1), not three
      standalone patches. Same maintainer/list (netdev, ATLX ETHERNET
      DRIVERS) covers all three.
- [ ] Write the cover letter for the series.
- [ ] `git commit -s` each patch on `raven`, `checkpatch.pl`,
      `get_maintainer.pl`, `send-email` as a series (not yet sent).

## Separate bug found by the AI review (real, independently confirmed)

- [ ] `atl1c_common_task()`'s LINK_CHANGE branch only masks IRQs
      (`atl1c_irq_disable()`), never calls `napi_disable()`/
      `napi_synchronize()`, unlike the RESET branch's `atl1c_down()`.
- [ ] `atl1c_check_link_status()` -> `atl1c_reset_dma_ring()` ->
      `atl1c_clean_tx_ring()` unconditionally walks every ring entry and
      zeroes `next_to_clean`, while NAPI (`atl1c_clean_tx()`) can run
      concurrently on the same entries.
- [ ] `atl1c_clean_buffer()`'s only guard is a bare, unsynchronized
      `buffer_info->flags & ATL1C_BUFFER_FREE` read/set - no lock, no CAS.
      Confirmed by direct source read: two contexts can both pass the check
      for the same buffer and double `dma_unmap_single()`/
      `napi_consume_skb()` it. The two `atomic_set()`s on `next_to_clean`
      can also race.
- [ ] Fix: add `napi_disable()`/`napi_synchronize()` around the reset in the
      LINK_CHANGE path, matching what `atl1c_down()` already does correctly.
      Needs its own patch/changelog, separate from the tpd_cons clamp.

## Mikrotik CCR2004 / RouterOS side (separate track, not an upstream bug)

- [ ] Local DKMS package (`ccr2004pcie-fix`, currently 1.8) has recovery
      improvements (PHY reset, backoff retry, watchdog poll) that are
      reasonable defensive fixes but were built while chasing what turned
      out to be RouterOS's own `al_pcie_ep` (out-of-tree, proprietary)
      endpoint reinitialization timing - not something fixable purely in
      `atl1c`. Decide whether any of this is worth upstreaming as general
      robustness, or if it should stay local-only.
- [ ] Last real-hardware test of 1.8 was interrupted before conclusively
      confirming it converges to a real, stable link within a bounded time
      after an actual Mikrotik reboot - re-test.
- [ ] Contact MikroTik support with the concrete evidence gathered:
      `al_pcie_ep.ko` has no upstream counterpart (host-mode `pcie-al.c`
      exists in mainline, no `pcie-al-ep.c`), and RouterOS's own
      `/interface print` doesn't show `R` on `ether-pcie1-4` even when the
      Linux-side driver believes the link is up. Not yet sent.
- [ ] Deploy the DKMS fix to the Proxmox host (`fribourg`) - instructions
      given, not yet confirmed done.
