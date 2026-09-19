# TODO

## Upstream `atl1c` patch (the soft-lockup fix)

- [x] Decided: send v2 now, as patch 1 of the 3-patch series (see below),
      alongside a reply to the AI review rather than waiting further.
- [x] v2 commit message written: `mail/0002-patch1-commit-message.md` -
      `Fixes: 43250ddd75a35d`, `Cc: stable@vger.kernel.org`, and both
      dmesg excerpts folded in. Same code diff as v1, unchanged. Not yet
      actually committed on `raven`.

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
- [x] Cover letter drafted (0/3, summarizes the shared bug across all
      three drivers).
- [x] Reply draft to the AI review written: `mail/0001-reply.md`. Answers
      all three points - confirms `Fixes:`/`Cc: stable` for v2, gives the
      real reproduction details requested (kernel versions, hardware,
      both dmesg excerpts), and states plainly that only atl1c has been
      validated on real hardware, atl1e/atl1 by code inspection only.
      Also settles the "fold v2 details in" question: yes, per the reply.
- [ ] `git commit -s` each patch on `raven` (patch 1 with the v2 details
      folded in per the reply draft), `checkpatch.pl`, `get_maintainer.pl`,
      `send-email` as a series (not yet sent), and send the reply itself.

## Separate bug found by the AI review (real, independently confirmed)

- [x] `atl1c_common_task()`'s LINK_CHANGE branch only masks IRQs
      (`atl1c_irq_disable()`), never calls `napi_disable()`/
      `napi_synchronize()`, unlike the RESET branch's `atl1c_down()`.
- [x] `atl1c_check_link_status()` -> `atl1c_reset_dma_ring()` ->
      `atl1c_clean_tx_ring()` unconditionally walks every ring entry and
      zeroes `next_to_clean`, while NAPI (`atl1c_clean_tx()`) can run
      concurrently on the same entries.
- [x] `atl1c_clean_buffer()`'s only guard is a bare, unsynchronized
      `buffer_info->flags & ATL1C_BUFFER_FREE` read/set - no lock, no CAS.
      Confirmed by direct source read: two contexts can both pass the check
      for the same buffer and double `dma_unmap_single()`/
      `napi_consume_skb()` it. The two `atomic_set()`s on `next_to_clean`
      can also race.
- [x] Fix drafted: `napi_disable()`/`napi_enable()` for all tx/rx queues
      wrapped around the LINK_CHANGE branch's call to
      `atl1c_check_link_status()`, matching what `atl1c_down()`/
      `atl1c_up()` already do. checkpatch clean (0 errors/warnings).
      Verified safe against `atl1c_check_link_status()`'s *other* caller
      (`atl1c_up()`): putting the disable/enable inside the shared
      function instead would have double-enabled NAPI there and hit
      `BUG_ON(!test_bit(NAPI_STATE_SCHED, ...))` in `napi_enable_locked()`
      - confirmed against the real `net/core/dev.c` implementation before
      settling on wrapping the one call site instead.
- [x] Audited the sibling drivers for the same gap:
    - `atl1e` - not vulnerable. `atl1e_check_link()` never resets rings on
      ordinary link-change (just an RX-enable bit + carrier state); ring
      resets only happen via the separate, already NAPI-guarded
      `atl1e_reinit_locked()` -> `atl1e_down()` path.
    - `atl1`/`atlx` - not vulnerable, same reason: `atl1_check_link()`
      only touches carrier state.
    - `alx` (newest, actively-maintained sibling) - not vulnerable, but
      for a real reason, not architectural avoidance: `alx_check_link()`
      *does* reset rings on link-down (`alx_reinit_rings()`), but calls
      `alx_netif_stop()` first, which does call `napi_disable()` for
      every queue before any ring is touched. Gets it right.
    - Conclusion: `atl1c` is uniquely affected - the only driver in the
      family that resets rings on an ordinary link-change *and* forgets
      to disable NAPI first. No additional sibling patches needed for
      this bug.
- [x] Commit message written: `mail/0003-napi-sync-commit-message.md`.
      Includes `Fixes: 5e5c0964d9b93d ("atl1c: do MAC-reset when PHY link
      down")` (the actual commit that introduced the unguarded reset,
      found via `git blame`) and the sibling-audit conclusion as context.
- [ ] `git commit -s`, `checkpatch.pl`, `get_maintainer.pl`, `send-email`
      on `raven` - send as its own standalone patch, not part of the
      3-patch series (different bug, `atl1c`-only).

## Mikrotik CCR2004 / RouterOS side (separate track, not an upstream bug)

- [ ] Local DKMS package (`ccr2004pcie-fix`, currently 1.8) has recovery
      improvements (PHY reset, backoff retry, watchdog poll) that are
      reasonable defensive fixes but were built while chasing what turned
      out to be RouterOS's own `al_pcie_ep` (out-of-tree, proprietary)
      endpoint reinitialization timing - not something fixable purely in
      `atl1c`. Decide whether any of this is worth upstreaming as general
      robustness, or if it should stay local-only.
- [x] Re-tested DKMS 1.8 on real hardware - done (2026-09-19); result not
      yet reported back in this conversation.
- [x] Contacted MikroTik support with the gathered evidence - done
      (2026-09-19); response not yet received/reported.
- [ ] Deploy the DKMS fix to the Proxmox host (`fribourg`) - in progress /
      under testing as of 2026-09-19.
