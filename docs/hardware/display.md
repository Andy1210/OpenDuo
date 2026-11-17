# Displays

## The panels

Both panels work (DSI-1 and DSI-2, DSC command mode) at 90 Hz, which is the
default. The panel driver's probe does not pull the
reset line (see [boot.md](boot.md)). The GPU is the Adreno 660 (`freedreno`).
After a GMU out-of-band timeout (six `Timeout waiting for GMU OOB set
GPU_SET`, a CP fault at iova 0) the GPU used to deadlock in recovery; a
stress test across display wake-ups (kept in the development repository)
shows whether it recovers.

## External display over USB-C (DisplayPort alt mode)

Wired in, tried by the kernel side only as far as the notes say: the code
paths exist (kernel patches, `compositor/duo-wc` desktop mode on an external
output), but treat it as not fully tested on a real monitor.

- **Hardware.** The stock device tree puts the DisplayPort controller
  (`sde_dp`, INTF0) next to the two DSI outputs and uses the USB-C port's QMP
  USB3/DP combo PHY. The ADSP handles the alt mode through `pmic_glink`
  (PD, pin assignment and HPD belong to the firmware). The plug orientation
  is on GPIO 81 (the HDK's `orientation-gpios`). There is no SBU mux in the
  device tree: only an AUX enable pin (GPIO 26, driven low while DP is
  active). If the picture appears in only one plug orientation, the AUX
  routing is the likely cause.
- **Mainline path.** msm DP (`qcom,sm8350-dp`) -> the combo PHY's DRM bridge
  (`aux-bridge`) -> the connector's HPD bridge (`aux-hpd-bridge`, registered
  by `pmic_glink_altmode`). The PHY stays in USB3+DP mode, so DisplayPort
  runs on two lanes, up to HBR2 (1080p60, 1440p60, 4K30); 4K60 would need
  four lanes and `mode-switch`.
- **DPU resource fix.** The two DSC panels take two DSPP-equipped mixer pairs
  (LM0/1 = DSI-1, LM2/3 = DSI-2; only LM4/5 have no DSPP). A DP output asks
  for two mixers from 1920 px of width and the resource manager hands out
  the lowest free pair, so with a panel off the DP took that panel's pair and
  the panel could not come back. The patch makes a topology that needs
  neither DSPP nor DSC pick from the DSPP-less mixers first. A second fix
  handles the two panels handing a DPU CTL to each other.
- **Module loading.** With DP enabled, msm binds (both panels!) only when
  `phy_qcom_qmp_combo`, `aux_bridge`, `pmic_glink(_altmode)` and
  `aux_hpd_bridge` are present. If a DP probe fails without deferring, msm
  does not bind: the panels stay dark, but WiFi and ssh work; reboot into
  the known-good kernel.
- **Not done:** DisplayPort audio, four lanes / 4K60, USB3 data through the
  adapter (the dwc3 is high-speed only, peripheral mode).
- **Checking.** `compositor/duo-wc/tools/dp-check.sh` reads what the kernel
  and the compositor see (`snap`), cycles the connector (`cycle`, which
  writes) and runs pose changes with the display plugged (`poses`: single
  panel, display off/on, dual; DSI-2 must come back). Healthy runs show no
  `frame done timeout`, `dsi_err_worker` or `ctl start interrupt wait
  failed`, and the panels stay at 90 Hz.
