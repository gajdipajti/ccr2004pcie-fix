net: atl1c: synchronize NAPI around the link-change reset path

atl1c_common_task()'s LINK_CHANGE branch only masks IRQs
(atl1c_irq_disable()) before calling atl1c_check_link_status(), which
on link-down resets the MAC and, via atl1c_reset_dma_ring() ->
atl1c_clean_tx_ring()/atl1c_clean_rx_ring(), unconditionally walks
every ring entry and zeroes next_to_clean/next_to_use - while TX/RX
NAPI are still enabled and can run concurrently on the same entries.

atl1c_clean_buffer()'s only guard against handling a buffer twice is
an unsynchronized read of buffer_info->flags & ATL1C_BUFFER_FREE, no
lock, no compare-and-swap. Two contexts can both pass that check for
the same buffer before either has set the FREE flag, and both go on
to dma_unmap_single()/napi_consume_skb() it. The two plain
atomic_set()s on next_to_clean (one from the NAPI poll, one from the
reset) can also race and leave the software index out of sync with
the freshly reset ring.

The RESET branch already disables NAPI around atl1c_down()/atl1c_up()
before touching the rings, for the same reason; do the same around
the LINK_CHANGE branch's call to atl1c_check_link_status().

Audited the sibling drivers (atl1e, atl1, alx) for the same gap: none
share it. atl1e/atl1's link-change handling only ever touches carrier
state and an RX-enable bit, never resets rings on an ordinary
link-change - their full ring reset is confined to a separate,
already NAPI-guarded reset path. alx's link-change handling does
reset rings on link-down, but calls alx_netif_stop(), which disables
NAPI first. atl1c is the only driver in the family that resets rings
on an ordinary link-change without disabling NAPI first.

Fixes: 5e5c0964d9b93d ("atl1c: do MAC-reset when PHY link down")
Signed-off-by: Gajdos Tamás <tamas@rimpianto.com>
