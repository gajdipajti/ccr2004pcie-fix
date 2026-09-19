Subject: Re: [PATCH] net: atl1c: fix soft lockup on out-of-range tpd_cons read

Thanks for the review.

> Should this carry a Fixes: tag and a Cc: stable@vger.kernel.org?
>
> Fixes: 43250ddd75a35d ("atl1c: Atheros L1C Gigabit Ethernet driver")

Agreed on both, will add in v2.

> Could the changelog also say how the 0xffff value was observed - kernel
> version, hardware, and the log or reproducer? That makes the hardware
> claim easier to confirm.

Reproduced on two machines, same NIC (Qualcomm Atheros AR8151 v2.0,
4-port), triggered by rebooting a Mikrotik CCR2004 PCIe card that the
ports are directly linked to:

- Ubuntu 26.04.1 LTS, kernel 7.0.0-31-generic. The link-flap precursor,
  before the lockup was captured with a full trace elsewhere:

    atl1c 0000:05:00.0 enp5s0f0: NETDEV WATCHDOG: CPU: 4: transmit queue 2 timed out 489984 ms
    atl1c 0000:05:00.0: MAC state machine can't be idle since disabled for 10ms second
    atl1c 0000:05:00.0: atl1c: enp5s0f0 NIC Link is Up<65535 Mbps Full Duplex>

  65535 (0xffff) here is the same value the tpd_cons register reads
  back once the loop this patch fixes gets stuck.

- Proxmox VE, kernel 7.0.14-11-pve. Same NIC/trigger, this time caught
  by the soft lockup watchdog with a full stack trace:

    watchdog: BUG: soft lockup - CPU#12 stuck for 354s! [napi/eth%d-0:329]
    CPU: 12 UID: 0 PID: 329 Comm: napi/eth%d-0 Tainted: P O L 7.0.14-11-pve #1 PREEMPT(lazy)
    RIP: 0010:atl1c_clean_tx+0x142/0x2d0 [atl1c]
    Call Trace:
     <TASK>
     __napi_poll+0x32/0x1e0
     napi_threaded_poll_loop+0x286/0x2e0
     napi_threaded_poll+0xfd/0x140
     kthread+0xf7/0x130
     ret_from_fork+0x2da/0x3a0
     ret_from_fork_asm+0x1a/0x30
     </TASK>

Will fold both into the v2 changelog.

> This isn't a bug introduced by this patch, but the sibling Atheros
> drivers have the same loop and are left untouched here. Were they
> audited?

Yes - sending this as a 3-patch series (atl1c/atl1e/atl1), same guard
applied to atl1e_clean_tx_irq() and atl1_intr_tx(). One caveat worth
being upfront about: only the atl1c fix has been validated against
real hardware (repeated reboot cycles against the CCR2004 above,
confirmed fixed). The atl1e and atl1 patches apply the identical guard
against the identical loop shape by code inspection - I don't have
atl1e/atl1 hardware to reproduce and confirm the fix against directly.
Flagging this explicitly rather than implying equivalent test coverage
across all three.

> Would adding napi_disable()/napi_synchronize() around the reset in
> the link-change path be the right complement to this clamp?

Agreed this looks real - still confirming the right fix, will follow
up separately since it's an independent bug from the one this patch
addresses.
