# The kernel patch series

**License:** everything under `kernel/` (these patches, `kernel/config/`,
`kernel/drafts/`) is GPL-2.0, like the Linux kernel it changes
([kernel/LICENSE](../LICENSE)); where a patched file carries another
kernel-compatible SPDX identifier, that one applies. Patches by other
authors (class 3 below) keep their authors' terms.

OpenDuo's kernel is Linux **v7.3.4** (stable) plus the 137 patches in this
directory, in order. They are exported from the `duo2` branch of the kernel
tree:

```sh
git -C src/linux format-patch -q --no-signature v7.3.4..duo2 -o kernel/patches
git -C <linux v7.3.4 checkout> am kernel/patches/*.patch      # to apply them
```

`duo2` is `duo2-clean` plus the SM8350 Inline Crypto Engine (`0135`, `0136`,
2026-11-16, promoted as `7.3.4-duo2-g9787c2186`) and one vendor-driver
fix (`0137`, 2026-11-17, not yet built into a promoted kernel). `duo2-clean` is the
bring-up series of the kernel promoted on 2026-11-15 (`7.3.4-duo2-g3ba7db140`)
with the same code: its commit messages rewritten in upstream style and
checkpatch's style findings fixed (whitespace, alignment, comments; the vendor
HID-over-SPI driver formatted with the kernel's `.clang-format`).
`git diff 3ba7db140 duo2-clean` holds only those changes; the third-party
patches (class 3) are byte for byte as taken. `0135` and `0136` still carry
`Co-Authored-By:` (the others: `Assisted-by: Claude:claude-opus-5-5`): to be
changed before they are sent, not in the tree (the release string names the
commit).

## Classes

| Class | Meaning | Patches |
| --- | --- | --- |
| 1 | Upstreamable driver, binding or DT change (as it is, or after the work in the notes) | 82 |
| 2 | `BRING-UP:` device-local workaround, diagnostic or vendor code; not for upstream as it is (every such subject starts with `BRING-UP:`) | 33 |
| 3 | Backport or cherry-pick of someone else's posted or out-of-tree work | 21 |

The **Upstream** column says what to do with it:

- **first**: self-contained, against upstream code; send on its own (most are
  bug fixes, measured on the Surface Duo 2).
- **series**: upstreamable, but goes with the patches it depends on (a new
  driver and its follow-ups are folded together before sending).
- **rework**: needs real changes first (a binding, a different design).
- **track**: someone else's; follow its upstream fate, drop it when merged.
- **no**: bring-up only.

## Send first

The smallest, clearest fixes to upstream code, each standing alone:

1. `0114` rtc: interface: Release the wakeup source when reading the time fails
2. `0126` scsi: ufs: qcom: Don't enable the clocks again while they are enabled
3. `0009` iommu/arm-smmu-qcom: Add SM8350 MDSS compatible to the identity-domain list
4. `0036` arm64: dts: qcom: sm8350: Fix the IPA interrupt
5. `0130`, `0007` the upstream Surface Duo 2 DT: L5B's name, S11B's maximum
6. `0021` clk: qcom: gcc-sm8350: Use retention for the PCIe power domains
7. `0119` power: supply: qcom_battmgr: Return the firmware's answer from the charge thresholds
8. `0035` net: qrtr: ns: Retry the announcement to a node that cannot receive it yet
9. `0124`, `0125` drm/msm/dpu: the command-mode CTL fixes
10. `0085` ASoC: qcom: qdsp6: q6asm-dai: Offer 24-bit PCM as S32_LE, not S24_LE
11. `0031`, `0032`, `0107` q6afe and wsa883x fixes
12. `0131`, `0132` wifi: ath11k regulatory fixes
13. `0134` leds: flash: leds-qcom-flash torch resolution
14. `0127` drm/msm/dsi/phy: Unprepare the interface clock across system sleep

Then the RFCs that change behaviour for other boards too: `0020` and `0117`
(PCI: qcom), `0112` (drm/msm command-mode enable), and the qrtr wakeup set
(`0115`, `0123`, `0128`). The GPU recovery fixes `0039`-`0041` follow
`0038` (Nico Angelo Dela Cruz's posted fix they build on).

## Where the backports come from

| # | Source |
| --- | --- |
| 0012 | The OnePlus 9 Pro port (author bluelightsaber; no Signed-off-by), after Dmitry Baryshkov's "wifi: ath11k: enable support for WCN6851" (lkml, June 2026): replace it with that series once merged |
| 0038 | Nico Angelo Dela Cruz, freedreno, 2026-11-02, 1/4 of "drm/msm: fix GPU recovery deadlocks, add a8xx crash dumps" (https://lore.kernel.org/all/20261101175553.53746-2-nicoangelodelacruz@gmail.com/), backported to v7.3 |
| 0045 | Fenglin Wu, linux-input, "input: misc: Add Qualcomm SPMI PMIC haptics driver" RESEND v7 2/2 (https://patchwork.kernel.org/project/linux-input/patch/20261103-qcom-spmi-haptics-v7-2-418de67ef84a@oss.qualcomm.com/), as posted |
| 0086-0103 | The out-of-tree q6voice series as collected in sc7280-mainline/linux PR #35 (postmarketOS): Stephan Gerhold's msm8916-mainline commits (`0086`-`0093`, cherry-pick ids in the messages), Otto Pflüger's VoiceMMode1 (`0094`, msm8953-mainline), Richard Acayan's CVD 2.3 and API updates (`0095`-`0101`), Bhushan Shah's (`0102`) and Val Packett's (`0103`) |

Unapplied work in progress lives in `kernel/drafts/` (GPU, video codec, pen
charger).

## Before sending anything

- **Signed-off-by (DCO).** None of the author's own patches carries one: only
  the human author can certify the Developer Certificate of Origin
  (Documentation/process/submitting-patches.rst, coding-assistants.rst). The
  author adds it himself, for example on a copy of the branch:
  `git rebase v7.3.4 --exec 'git commit --amend --no-edit -s'`.
- **Assisted-by.** The patches written with an AI assistant carry
  `Assisted-by: Claude:claude-opus-5-5`, the tag the kernel asks for
  (Documentation/process/coding-assistants.rst).
- **Fixes: tags** for `0007`, `0036` and `0130` (and wherever a fix names a
  regression): this tree's history starts at v7.3, so the original commits
  have to be looked up in mainline.
- **Bindings** missing for compatibles the series uses:
  `microsoft,elgin-panel`, `sony,imx481`, `sony,imx563`,
  `qcom,smp2p-sleepstate`, `qcom,sm8350-camss`, `qcom,sm8350-camcc`,
  `qcom,sm8350-cci`, `qcom,sm8350-sndcard`, `qcom,sm8350-lpass-wsa-macro`,
  `qcom,sm8350-lpass-va-macro`, `qcom,sm8350-lpassaudiocc`,
  `qcom,pm8350b-haptics` (on top of the haptics series), the q6voice ones
  (`qcom,q6mvm`, `qcom,q6cvs`, `qcom,q6cvp`, `qcom,q6voice-dais`) and the
  vendor `hid-over-spi`. Binding changes made inside DT patches (`0060`,
  `0062`, `0068`, ...) go into patches of their own.
- **MAINTAINERS** entries for the new drivers (elgin panel, imx48x,
  camcc-sm8350, smp2p-sleepstate).

## checkpatch

`scripts/checkpatch.pl --strict` over the whole series (run in the kernel
tree at the series' head):

| | errors | warnings | checks |
| --- | ---: | ---: | ---: |
| `duo2` (before) | 263 | 364 | 146 |
| `duo2-clean` | 120 | 97 | 56 |

What is left, and why:

- 114 errors are the missing Signed-off-by lines (above), on purpose.
- The third-party patches (class 3) are kept as taken: their findings
  (`MODULE_LICENSE("GPL v2")`, missing commit descriptions, long lines) are
  their authors'.
- Missing bindings and MAINTAINERS entries (above); "DT binding docs ...
  should be a separate patch" where a DT patch extends a binding.
- False positives: the list-building macros of q6voice-dai (`0104`, `0106`:
  "Macros with complex values"), constant macro arguments (`0037`), the
  spi-hid trace header's `TRACE_EVENT` layout, an upstream commit id quoted
  from a stable tree (`0031`), a firmware log line quoted with its own
  spelling (`0036`).
- Kept to match the files around them: the long `/bits/ 8` port lines of
  the SoundWire node (`0029`) and the `qcom,protection-domain` lines (`0108`),
  which sm8250.dtsi / sm8450.dtsi and sm8350.dtsi write the same way;
  upstream's own double assignment in leds-qcom-flash (`0134`).
- Not style, so left for a later change: `kzalloc` to `kzalloc_obj` in
  q6voice (`0105`), the vendor driver's CamelCase descriptor fields (the HID
  over SPI specification's names), its lock comments and its ftrace-like
  `dev_info`.

## The patches

| # | Class | Subsystem | Purpose | Upstream | Notes |
| --- | --- | --- | --- | --- | --- |
| 0001 | 1 | arm64: dts: qcom: sm8350-microsoft-surface-duo2 | Add the charger, USB-C, WiFi, BT and keys | series | board DT: fold 0006 in; split per function for review |
| 0002 | 2 | arm64: dts: qcom: sm8350-microsoft-surface-duo2 | Put the USB controller in peripheral mode | no | bring-up DTB variant / USB console workaround |
| 0003 | 2 | arm64: dts: qcom: sm8350-microsoft-surface-duo2 | Add variants without the USB-C graph, with MDSS held | no | bring-up DTB variant / USB console workaround |
| 0004 | 2 | arm64: dts: qcom: sm8350-microsoft-surface-duo2 | Add a variant without dispcc | no | bring-up DTB variant / USB console workaround |
| 0005 | 2 | arm64: dts: qcom: sm8350-microsoft-surface-duo2 | Add variant H without PCIe | no | bring-up DTB variant / USB console workaround |
| 0006 | 1 | arm64: dts: qcom: sm8350-microsoft-surface-duo2 | Keep S2E at the bootloader's 1.012 V | series | fold into 0001 (S2E is added there) |
| 0007 | 1 | arm64: dts: qcom: sm8350-microsoft-surface-duo2 | Raise the S11B maximum to the stock 1.012 V | first | fixes the upstream board DT; needs a Fixes: tag |
| 0008 | 2 | arm64: dts: qcom: sm8350-microsoft-surface-duo2 | Run variant H at USB 2.0 only | no | bring-up DTB variant / USB console workaround |
| 0009 | 1 | iommu/arm-smmu-qcom | Add SM8350 MDSS compatible to the identity-domain list | first |  |
| 0010 | 2 | arm64: dts: qcom: sm8350-microsoft-surface-duo2 | Disable usb_2 in variant H | no | bring-up DTB variant / USB console workaround |
| 0011 | 2 | arm64: dts: qcom: sm8350-microsoft-surface-duo2 | Add bring-up variant I | no | bring-up DTB variant / USB console workaround |
| 0012 | 3 | wifi: ath11k | add WCN6855 hw1.1 (WCN6851 / QCA6490 v1) support (bluelightsaber) | track | from the OnePlus 9 Pro port (no Signed-off-by); replace with Dmitry Baryshkov's WCN6851 series once merged |
| 0013 | 1 | drm/panel | Add driver for the Microsoft Surface Duo 2 panels | series | needs a DT binding (microsoft,elgin-panel); fold 0037, 0044, 0054, 0111 in |
| 0014 | 1 | arm64: dts: qcom: sm8350-microsoft-surface-duo2 | Enable the displays | series | board DT series (with the driver it needs) |
| 0015 | 2 | arm64: dts: qcom: sm8350-microsoft-surface-duo2 | Add bring-up variant J with dispcc | no | bring-up DTB variant / USB console workaround |
| 0016 | 1 | arm64: dts: qcom: sm8350-microsoft-surface-duo2 | Enable the GPU | series | board DT series (with the driver it needs) |
| 0017 | 1 | arm64: dts: qcom: sm8350-microsoft-surface-duo2 | Enable the PMIC RTC | series | board DT series (with the driver it needs) |
| 0018 | 1 | arm64: dts: qcom: sm8350-microsoft-surface-duo2 | Leave the gauge buses to the ADSP | series | board DT series (with the driver it needs) |
| 0019 | 1 | arm64: dts: qcom: sm8350-microsoft-surface-duo2 | Load the IPA firmware | series | board DT series (with the driver it needs) |
| 0020 | 1 | PCI: qcom | Do not advertise native hot-plug on the Root Ports | first | affects every qcom PCIe host: RFC to linux-pci |
| 0021 | 1 | clk: qcom: gcc-sm8350 | Use retention for the PCIe power domains | first |  |
| 0022 | 2 | HID: spi-hid | Add Microsoft's HID over SPI driver of the Surface Duo 2 | no | Microsoft's vendor HID-over-SPI driver; to be replaced by the upstream spi-hid transport |
| 0023 | 2 | arm64: dts: qcom: sm8350-microsoft-surface-duo2 | Add the touch digitizer | no | vendor binding (hid-over-spi) for the BRING-UP driver |
| 0024 | 2 | arm64: dts: qcom: sm8350-microsoft-surface-duo2 | Clock the high-speed-only USB PIPE from UTMI | no | bring-up DTB variant / USB console workaround |
| 0025 | 2 | soc: qcom: smp2p-sleepstate | Tell remote processors whether the APSS is awake | rework | needs a binding and a home (soc/qcom); the downstream concept, rewritten |
| 0026 | 2 | arm64: dts: qcom: sm8350-microsoft-surface-duo2 | Add the SLPI sleepstate SMP2P entries | rework | needs a binding and a home (soc/qcom); the downstream concept, rewritten |
| 0027 | 2 | ASoC: dt-bindings: qcom | Add the SM8350 LPASS WSA/VA macros and sound card | rework | audio: fallback compatibles and LPASS layout need the bindings discussed first |
| 0028 | 2 | ASoC: qcom: sm8250 | Add the SM8350 sound card | rework | audio: fallback compatibles and LPASS layout need the bindings discussed first |
| 0029 | 2 | arm64: dts: qcom: sm8350 | Add the LPASS WSA and VA macros and the WSA SoundWire | rework | audio: fallback compatibles and LPASS layout need the bindings discussed first |
| 0030 | 2 | arm64: dts: qcom: sm8350-microsoft-surface-duo2 | Add the speakers and microphones | rework | audio: fallback compatibles and LPASS layout need the bindings discussed first |
| 0031 | 1 | ASoC: qcom: q6afe | Order the LPASS core vote handle, accept the devote | first | follow-up to upstream f3c7bee984aa |
| 0032 | 1 | ASoC: codecs: wsa883x | Initialise an amplifier that attaches while suspended | first |  |
| 0033 | 2 | dt-bindings: clock: qcom,sc8280xp-lpasscc | Add the SM8350 LPASS audio CSR | rework | audio: fallback compatibles and LPASS layout need the bindings discussed first |
| 0034 | 2 | arm64: dts: qcom: sm8350 | Reset the WSA SoundWire clock gate | rework | audio: fallback compatibles and LPASS layout need the bindings discussed first |
| 0035 | 1 | net: qrtr: ns | Retry the announcement to a node that cannot receive it yet | first |  |
| 0036 | 1 | arm64: dts: qcom: sm8350 | Fix the IPA interrupt | first | fixes upstream sm8350.dtsi; needs a Fixes: tag |
| 0037 | 1 | drm/panel: microsoft-elgin | Add the 90 Hz mode | series | fold into 0013 (driver not upstream yet) |
| 0038 | 3 | drm/msm | don't wait for a fault devcoredump during GPU recovery (Nico Angelo Dela Cruz) | track | posted to freedreno (1/4 of "drm/msm: fix GPU recovery deadlocks"); drop once merged |
| 0039 | 1 | drm/msm/a6xx | Bound the GMU waits on a pending fault devcoredump | first | send after (or with) 0038, which they build on |
| 0040 | 1 | drm/msm | Don't start the CP when hw_init failed | first | send after (or with) 0038, which they build on |
| 0041 | 1 | drm/msm/a6xx | Force the GMU off after a GPU_SET OOB timeout | first | send after (or with) 0038, which they build on |
| 0042 | 2 | drm/msm/a6xx | Log the OOB latency, add a timeout knob and a GMU dump | no | diagnostics only |
| 0043 | 2 | HID: spi-hid | Keep the digitizer off long enough, quieten per-report logging | no | Microsoft's vendor HID-over-SPI driver; to be replaced by the upstream spi-hid transport |
| 0044 | 1 | drm/panel: microsoft-elgin | Leave the reset line alone until the first prepare | series | fold into 0013 (driver not upstream yet) |
| 0045 | 3 | input: misc | Add Qualcomm SPMI PMIC haptics driver (Fenglin Wu) | track | posted to linux-input (RESEND v7 2/2); drop once merged |
| 0046 | 1 | Input: qcom-spmi-haptics | Advertise the FF_SINE waveform | series | on top of 0045: send as replies to that series once it is merged |
| 0047 | 1 | dt-bindings: input: qcom,spmi-haptics | Add the PM8350B haptics | series | on top of 0045: send as replies to that series once it is merged |
| 0048 | 1 | Input: qcom-spmi-haptics | Add the PM8350B (HAP520) haptics | series | on top of 0045: send as replies to that series once it is merged |
| 0049 | 1 | arm64: dts: qcom: sm8350-microsoft-surface-duo2 | Enable the haptics | series | board DT series (with the driver it needs) |
| 0050 | 1 | arm64: dts: qcom: sm8350-microsoft-surface-duo2 | Enable the video codec | series | board DT series (with the driver it needs) |
| 0051 | 1 | dt-bindings: media: qcom,sm8250-venus | Allow dma-coherent | first |  |
| 0052 | 1 | arm64: dts: qcom: sm8350 | Mark the video codec dma-coherent | first |  |
| 0053 | 1 | arm64: dts: qcom: sm8350 | Add the CPU capacities and dynamic power coefficients | first |  |
| 0054 | 1 | drm/panel: microsoft-elgin | Keep a brightness set while the panel is off | series | fold into 0013 (driver not upstream yet) |
| 0055 | 1 | mfd: qcom-pm8008 | Make the interrupt optional | series | with the camera series (PM8008 without interrupt) |
| 0056 | 1 | arm64: dts: qcom: sm8350 | Add the QUP SE18 I2C controller | series | with the camera series |
| 0057 | 1 | arm64: dts: qcom: sm8350-microsoft-surface-duo2 | Add the camera PMICs | series | board DT series (with the driver it needs) |
| 0058 | 1 | media: qcom: camss | Add SM8350 support | series | SM8350 camera series: fold 0066/0067 into 0059, 0073/0078 into 0058, 0074 into 0068; split bindings out of the DT patches |
| 0059 | 1 | clk: qcom | Add the SM8350 camera clock controller | series | SM8350 camera series: fold 0066/0067 into 0059, 0073/0078 into 0058, 0074 into 0068; split bindings out of the DT patches |
| 0060 | 1 | arm64: dts: qcom: sm8350 | Add the camera clock controller and the CCIs | series | SM8350 camera series: fold 0066/0067 into 0059, 0073/0078 into 0058, 0074 into 0068; split bindings out of the DT patches |
| 0061 | 1 | arm64: dts: qcom: sm8350-microsoft-surface-duo2 | Enable the CCIs and the camera EEPROMs | series | board DT series (with the driver it needs) |
| 0062 | 1 | arm64: dts: qcom: sm8350 | Add the camera subsystem | series | SM8350 camera series: fold 0066/0067 into 0059, 0073/0078 into 0058, 0074 into 0068; split bindings out of the DT patches |
| 0063 | 1 | arm64: defconfig | Enable the SM8350 camera clock controller | series | SM8350 camera series: fold 0066/0067 into 0059, 0073/0078 into 0058, 0074 into 0068; split bindings out of the DT patches |
| 0064 | 2 | media: i2c | Add a power and identification driver for the IMX481 / IMX563 | rework | imx48x base (power + identification); the driver loads the module maker's register tables as firmware |
| 0065 | 2 | arm64: dts: qcom: sm8350-microsoft-surface-duo2 | Add the camera sensors and enable CAMSS | rework | board camera DT for the out-of-tree imx48x driver |
| 0066 | 1 | clk: qcom: camcc-sm8350 | Use the shared RCG ops and the MX power domain | series | SM8350 camera series: fold 0066/0067 into 0059, 0073/0078 into 0058, 0074 into 0068; split bindings out of the DT patches |
| 0067 | 1 | clk: qcom: camcc-sm8350 | Give the PLLs their configurations | series | SM8350 camera series: fold 0066/0067 into 0059, 0073/0078 into 0058, 0074 into 0068; split bindings out of the DT patches |
| 0068 | 1 | media: qcom: camss | Add the TPG 1.0.2 of SM8350 | series | SM8350 camera series: fold 0066/0067 into 0059, 0073/0078 into 0058, 0074 into 0068; split bindings out of the DT patches |
| 0069 | 1 | arm64: dts: qcom: sm8350 | Add the camera test pattern generators | series | SM8350 camera series: fold 0066/0067 into 0059, 0073/0078 into 0058, 0074 into 0068; split bindings out of the DT patches |
| 0070 | 2 | media: qcom: camss | Add receiver and PHY diagnostics | no | diagnostics only |
| 0071 | 1 | media: i2c: imx48x | Stream with the camera module's register tables | rework | imx48x: needs a binding and in-driver register lists before upstream |
| 0072 | 2 | arm64: dts: qcom: sm8350-microsoft-surface-duo2 | Add the ultrawide camera's endpoint | rework | board camera DT for the out-of-tree imx48x driver |
| 0073 | 1 | media: qcom: camss | Set the SM8350 camera NoC clock rates | series | SM8350 camera series: fold 0066/0067 into 0059, 0073/0078 into 0058, 0074 into 0068; split bindings out of the DT patches |
| 0074 | 1 | media: qcom: camss | Support a TPG on C-PHY trios, use three on SM8350 | series | SM8350 camera series: fold 0066/0067 into 0059, 0073/0078 into 0058, 0074 into 0068; split bindings out of the DT patches |
| 0075 | 1 | arm64: dts: qcom: sm8350 | Set the camera slow AHB clock rate | series | SM8350 camera series: fold 0066/0067 into 0059, 0073/0078 into 0058, 0074 into 0068; split bindings out of the DT patches |
| 0076 | 1 | media: i2c: imx48x | Per-mode colour order, solid colour, model name, IMX563 timing | rework | imx48x: needs a binding and in-driver register lists before upstream |
| 0077 | 2 | arm64: dts: qcom: sm8350-microsoft-surface-duo2 | Add all four cameras' endpoints | rework | board camera DT for the out-of-tree imx48x driver |
| 0078 | 1 | media: qcom: camss | Keep the SM8350 PHY timer at 300 MHz up to 2.5 Gb/s | series | SM8350 camera series: fold 0066/0067 into 0059, 0073/0078 into 0058, 0074 into 0068; split bindings out of the DT patches |
| 0079 | 2 | media: i2c: imx48x | Add debugfs register access | no | diagnostics only |
| 0080 | 1 | media: i2c: imx48x | Show the IMX563 test pattern over the whole frame | rework | imx48x: needs a binding and in-driver register lists before upstream |
| 0081 | 2 | media: qcom: camss | Mask the unmapped VC/DT interrupt in the diagnostics | no | diagnostics only |
| 0082 | 1 | media: i2c: imx48x | Retry the identification, defer on a busy CCI | rework | imx48x: needs a binding and in-driver register lists before upstream |
| 0083 | 1 | media: i2c: imx48x | Fix the per-mode colour order and crop, limit the IMX563's gain | rework | imx48x: needs a binding and in-driver register lists before upstream |
| 0084 | 2 | arm64: dts: qcom: sm8350-microsoft-surface-duo2 | Set the camera rotation | rework | board camera DT for the out-of-tree imx48x driver |
| 0085 | 1 | ASoC: qcom: qdsp6: q6asm-dai | Offer 24-bit PCM as S32_LE, not S24_LE | first |  |
| 0086 | 3 | ASoC: qdsp6 | Add shared session management for q6voice services (Stephan Gerhold) | track | q6voice series (Stephan Gerhold, msm8916-mainline; sc7280-mainline PR #35), out of tree |
| 0087 | 3 | ASoC: qdsp6 | Add driver for Q6 Multimode Voice Manager (q6mvm) (Stephan Gerhold) | track | q6voice series (Stephan Gerhold, msm8916-mainline; sc7280-mainline PR #35), out of tree |
| 0088 | 3 | ASoC: qdsp6 | Add driver for Q6 Core Voice Processor (q6cvp) (Stephan Gerhold) | track | q6voice series (Stephan Gerhold, msm8916-mainline; sc7280-mainline PR #35), out of tree |
| 0089 | 3 | ASoC: qdsp6 | Add driver for Q6 Core Voice Stream (q6cvs) (Stephan Gerhold) | track | q6voice series (Stephan Gerhold, msm8916-mainline; sc7280-mainline PR #35), out of tree |
| 0090 | 3 | ASoC: qdsp6 | Add voice call functionality in Q6 Voice driver (Stephan Gerhold) | track | q6voice series (Stephan Gerhold, msm8916-mainline; sc7280-mainline PR #35), out of tree |
| 0091 | 3 | ASoC: qdsp6 | Add Q6 Voice DAI driver (v2.1) (Stephan Gerhold) | track | q6voice series (Stephan Gerhold, msm8916-mainline; sc7280-mainline PR #35), out of tree |
| 0092 | 3 | ASoC: qdsp6: q6voice | Add getter/setter for rx and tx ports (Vincent Knecht) | track | q6voice series (Stephan Gerhold, msm8916-mainline; sc7280-mainline PR #35), out of tree |
| 0093 | 3 | ASoC: qdsp6: q6voice-dai | Add controls for voice rx and tx ports (Vincent Knecht) | track | q6voice series (Stephan Gerhold, msm8916-mainline; sc7280-mainline PR #35), out of tree |
| 0094 | 3 | ASoC: qdsp6: q6voice-dai | Add VoiceMMode1 DAI (Otto Pflüger) | track | q6voice series (Stephan Gerhold, msm8916-mainline; sc7280-mainline PR #35), out of tree |
| 0095 | 3 | ASoC: qdsp6: q6cvp | add commands in cvd 2.3 (Richard Acayan) | track | q6voice series (Stephan Gerhold, msm8916-mainline; sc7280-mainline PR #35), out of tree |
| 0096 | 3 | ASoC: qdsp6: q6voice | add cvd 2.3 initialization sequence (Richard Acayan) | track | q6voice series (Stephan Gerhold, msm8916-mainline; sc7280-mainline PR #35), out of tree |
| 0097 | 3 | ASoC: qdsp6: q6cvp | add topology ID parameters to session functions (Richard Acayan) | track | q6voice series (Stephan Gerhold, msm8916-mainline; sc7280-mainline PR #35), out of tree |
| 0098 | 3 | ASoC: qdsp6: q6voice | pass new topology property to q6cvp (Richard Acayan) | track | q6voice series (Stephan Gerhold, msm8916-mainline; sc7280-mainline PR #35), out of tree |
| 0099 | 3 | ASoC: qdsp6: q6voice-dai | add ALSA controls for the topology ID (Richard Acayan) | track | q6voice series (Stephan Gerhold, msm8916-mainline; sc7280-mainline PR #35), out of tree |
| 0100 | 3 | ASoC: qdsp6: q6voice | Constify APR callback response data (Richard Acayan) | track | q6voice series (Stephan Gerhold, msm8916-mainline; sc7280-mainline PR #35), out of tree |
| 0101 | 3 | ASoC: qdsp6: q6voice | rename snd_soc_dapm_kcontrol_dapm() to snd_soc_kcontrol_to_dapm() (Richard Acayan) | track | q6voice series (Stephan Gerhold, msm8916-mainline; sc7280-mainline PR #35), out of tree |
| 0102 | 3 | ASoC: qdsp6: q6voice-dai | add routes for TX_CODEC_DMA_TX to VOICEMMODE1 (Bhushan Shah) | track | q6voice series (Stephan Gerhold, msm8916-mainline; sc7280-mainline PR #35), out of tree |
| 0103 | 3 | ASoC: qdsp6: q6voice | default to NONE topology for TX (Val Packett) | track | q6voice series (Stephan Gerhold, msm8916-mainline; sc7280-mainline PR #35), out of tree |
| 0104 | 1 | ASoC: qdsp6: q6voice-dai | Add VoiceMMode2 and the LPASS codec DMA ports | series | on top of the q6voice series: send to its maintainers |
| 0105 | 1 | ASoC: qdsp6: q6voice | Set up CVD 2.3 vocprocs from the back ends | series | on top of the q6voice series: send to its maintainers |
| 0106 | 1 | ASoC: qdsp6: q6voice | Add uplink and downlink mute switches | series | on top of the q6voice series: send to its maintainers |
| 0107 | 1 | ASoC: codecs: wsa883x | Keep the PA off without a DAC port | first |  |
| 0108 | 1 | arm64: dts: qcom: sm8350 | Add the Q6 voice services | series | needs the q6voice bindings first |
| 0109 | 2 | arm64: dts: qcom: sm8350-microsoft-surface-duo2 | Enable voice calls and the earpiece | no | board voice routing; earpiece sharing SpkrLeft's ports |
| 0110 | 2 | ASoC: qdsp6: q6voice | Trace the voice services' messages | no | diagnostics only |
| 0111 | 1 | drm/panel: microsoft-elgin | Turn the display on only in enable | series | fold into 0013 (driver not upstream yet) |
| 0112 | 1 | drm/msm | Enable a command-mode panel only after its first frame | first | drm/msm: RFC (changes when command-mode bridges are enabled) |
| 0113 | 1 | arm64: dts: qcom: sm8350-microsoft-surface-duo2 | Hold the panels in reset from boot | series | board DT series (with the driver it needs) |
| 0114 | 1 | rtc: interface | Release the wakeup source when reading the time fails | first |  |
| 0115 | 1 | dt-bindings: remoteproc: qcom,glink-edge | Add wakeup-source | series | qrtr wakeup series (binding + qrtr + SMD transport): RFC |
| 0116 | 1 | power: supply: qcom_battmgr | Wake the system when a charger comes or goes | first |  |
| 0117 | 1 | PCI: qcom | Keep the link in system suspend while an endpoint may wake it | first | RFC to linux-pci |
| 0118 | 1 | arm64: dts: qcom: sm8350-microsoft-surface-duo2 | Wake on the modem and sensor DSP | series | board DT series (with the driver it needs) |
| 0119 | 1 | power: supply: qcom_battmgr | Return the firmware's answer from the charge thresholds | first |  |
| 0120 | 1 | power: supply: qcom_battmgr | Support Microsoft's charge hold on the Surface Duo 2 | series | device-specific firmware property: RFC |
| 0121 | 1 | arm64: dts: qcom: sm8350-microsoft-surface-duo2 | Add the board thermistors | series | board DT series (with the driver it needs) |
| 0122 | 2 | HID: spi-hid | Keep the interrupt off in system suspend | no | Microsoft's vendor HID-over-SPI driver; to be replaced by the upstream spi-hid transport |
| 0123 | 1 | net: qrtr | Wake the system on selected messages of a wakeup-source remote | series | qrtr wakeup series (binding + qrtr + SMD transport): RFC |
| 0124 | 1 | drm/msm/dpu | Clear a CTL's interface paths before a display sets it up | first |  |
| 0125 | 1 | drm/msm/dpu | Keep the command-mode ctl start count in step | first |  |
| 0126 | 1 | scsi: ufs: qcom | Don't enable the clocks again while they are enabled | first |  |
| 0127 | 1 | drm/msm/dsi/phy | Unprepare the interface clock across system sleep | first |  |
| 0128 | 1 | net: qrtr | Report request and indication wakeups as wakeup sources of their own | series | qrtr wakeup series (binding + qrtr + SMD transport): RFC |
| 0129 | 2 | arm64: dts: qcom: sm8350-microsoft-surface-duo2 | Drop the always-on display rails from variant J | no | bring-up DTB variant / USB console workaround |
| 0130 | 1 | arm64: dts: qcom: sm8350-microsoft-surface-duo2 | Name L5B after itself | first | fixes the upstream board DT; needs a Fixes: tag |
| 0131 | 1 | wifi: ath11k | Don't intersect a country with a world roaming default | first |  |
| 0132 | 1 | wifi: ath11k | Keep a user's country that 11d had already set | first |  |
| 0133 | 1 | arm64: dts: qcom: sm8350-microsoft-surface-duo2 | Enable the camera flash LED | series | board DT series (with the driver it needs) |
| 0134 | 1 | leds: flash: leds-qcom-flash | Use the 12.5 mA resolution for torch on 4ch rev 0.1 | first |  |
| 0135 | 1 | dt-bindings: crypto: ice | Document the SM8350 Inline Crypto Engine | series | with 0136 |
| 0136 | 1 | arm64: dts: qcom: sm8350 | Add the UFS Inline Crypto Engine | series | with 0135; on the Surface Duo 2: ICE v3.2.0, raw keys (the HWKM is not usable), AES-256-XTS for fscrypt with -o inlinecrypt; what it writes reads back through blk-crypto-fallback (standard XTS) |
| 0137 | 2 | HID: spi-hid | Do not return from perf_mode_store with the lock held | no | Microsoft's vendor HID-over-SPI driver (fixes 0022's sysfs store: a non-number left input_lock held with IRQs off) |
