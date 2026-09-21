net: atl1: fix soft lockup on out-of-range cmb_tpd_next_to_clean read

Same issue as atl1c (see the first commit in this series, "net:
atl1c: fix soft lockup on out-of-range tpd_cons read"): the hardware
can report an out-of-range cmb_tpd_next_to_clean (seen as 0xffff)
while the PCIe link/MAC is resetting. An out-of-range value can
never be reached and the loop below would spin forever. Treat it as
"nothing new to clean" instead.

Fixes: f3cc28c797604f ("Add Attansic L1 ethernet driver.")
Cc: stable@vger.kernel.org
Signed-off-by: Gajdos Tamás <tamas@rimpianto.com>
