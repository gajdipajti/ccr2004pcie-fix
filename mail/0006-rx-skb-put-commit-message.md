net: atl1c: validate RX descriptor length before skb_put()

atl1c_clean_rx() takes the packet length straight from the hardware's
RX return-status descriptor and passes it to skb_put() with no check
against the size of the buffer actually allocated and DMA-mapped for
that receive slot (buffer_info->length). If the hardware ever reports
a length larger than that buffer, skb_put()'s own bounds check trips
skb_over_panic() and crashes the host.

First observed on a MikroTik CCR2004 PCIe card whose host-facing
interface RouterOS defaults to l2mtu=1600, while the Linux atl1c
interface was still at the default MTU 1500 (~1522-byte RX buffer):
a legitimate baby-jumbo frame from the RouterOS side produced a
1557-byte descriptor, and the resulting skb_put(skb, 1553) overran
the ~1522-byte buffer by 17 bytes.

    skb_over_panic ... len:1553 put:1553 ... tail:0x691 end:0x680
    kernel BUG at net/core/skbuff.c:211!

Not limited to that MTU-mismatch scenario, though: it's the same
blind trust in a hardware-reported value already fixed for the TX
consumer index in this driver ("net: atl1c: fix soft lockup on
out-of-range tpd_cons read"), on the same family of hardware, now on
the RX length field instead. Any garbage or unexpected descriptor -
from a genuine size mismatch or otherwise - can crash the host.

Validate the reported length against the actual buffer size and drop
the packet instead of crashing.

Fixes: 43250ddd75a35d ("atl1c: Atheros L1C Gigabit Ethernet driver")
Cc: stable@vger.kernel.org
Link: https://www.jayme.ca/home/proxmoxlinux-crash-skb_over_panic-wccr2004-1g-2xs-pcie
Signed-off-by: Gajdos Tamás <tamas@rimpianto.com>
