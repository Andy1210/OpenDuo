# Audio

## Hardware

- Speakers: three Qualcomm WSA883x smart amplifiers on the WSA macro's
  SoundWire bus: `SpkrLeft` and `SpkrRight` (the stereo pair) and `Receiver`
  (the earpiece, on the left channel, instead of `SpkrLeft`). There is no I2C
  amplifier and no IRQ line.
- Microphones: digital microphones (DMIC) straight on the LPASS DMIC pins. The
  stock system uses two (DMIC3 is the main one, DMIC1 the second). There is no
  WCD codec and no 3.5 mm jack.
- The ADSP firmware speaks the old q6 APR / AVS protocol, not AudioReach: the
  mainline `sm8350.dtsi` APR node is right, no topology file is needed and the
  machine driver is the `sm8250.c` family.
- Stock speaker protection (ADSP module + calibration) is not available; the
  system uses a volume limit instead.
- Bluetooth audio is host-side BlueZ + PipeWire (A2DP, and HFP over HCI). The
  stock SLIMbus offload is not used.

## Software

- UCM profiles in `rootfs/overlay/usr/share/alsa/ucm2/Qualcomm/sm8350/`:
  `HiFi` (speaker + microphones) and `VoiceCall` (phone calls). PipeWire and
  WirePlumber run as the GUI user's systemd user units (with lingering); the
  default sink is the speaker, starting at 30 %. Hardware gains are fixed by
  the UCM profile.
- The ADSP sleeps while PipeWire is idle (runtime PM votes are released).

## Quirks the code relies on

- **24-bit PCM is MSB-aligned.** The DSP wants 24-bit samples in the upper 24
  bits of a 32-bit word; ALSA's `S24_LE` is the lower 24 bits. With `S24_LE`
  playback is 48 dB too quiet and capture 48 dB too loud. The kernel fix
  (`q6asm-dai` offers `S32_LE` for 24-bit PCM) is in `kernel/patches/`.
  `duo2-tone` (`--format s32`) is the audible check.
- **Stereo:** the direct path and the PipeWire paths put left on the left
  speaker and right on the right one; `duo2-tone --equal left|right VOL`
  plays one channel with both volumes equal, which is the test that would
  expose a channel duplicate.
- After `duo2-tone` has run, it takes the path down (WSA muxes to ZERO); the
  PipeWire sink stays silent until `systemctl --user restart wireplumber`
  re-runs the UCM.
- `pw-play --raw` exits early here; use a WAV, `pw-cat` or `paplay`.
- The speaker bring-up scripts (`scripts/duo2-spk-silence`,
  `scripts/duo2-mic-levels`) play digital silence or only record, and do not
  read SoundWire debugfs dumps.

## Call audio

- Stock path: `voicemmode1-call`. The earpiece uses the `Receiver` amplifier
  in Receiver mode; the microphone is DMIC3 alone (the stock HAL does not use
  the two-microphone "fluence" device in calls). On speaker both loudspeakers
  play.
- Our path: the modem hands the call audio to the ADSP voice services (APR:
  MVM is the session, CVP the vocproc). The vocproc reads the microphone
  directly from the `VA_CODEC_DMA_TX_0` AFE port and plays the far end into
  `WSA_CODEC_DMA_RX_0`: no audio data crosses Linux. The kernel side is the
  postmarketOS `q6voice` series plus fixes (`kernel/patches/`,
  `CONFIG_SND_SOC_QDSP6_Q6VOICE=m`).
- With the V3 vocproc setup the downlink works and the uplink does not (the
  back end is fixed at two channels while the single-microphone topology
  expects one), so the V2 setup is the default; V3 runs only when the device
  tree asks for it. A passive CVS session does not exist on this modem and is
  not used. Measured on real calls: earpiece, speaker, mute, volume and DTMF
  work in both directions.
- `telephony/duo2-callaudio` opens the UCM voice path when a call connects.
  `TX_TOPOLOGY` in `telephony/callaudio.conf` is `none`; the `ecns` topology
  (single-microphone echo and noise suppression) is tried with
  `scripts/duo2-call-echo-test` during a speakerphone call.
- Not working: Bluetooth headsets in calls (the stock path uses SLIMbus
  offload, which mainline lacks), wired/USB-C headsets (no jack, no codec),
  call tuning (no ACDB calibration). A second-subscription call (VoiceMMode2)
  is wired but not tested.

## Debugging

```sh
wpctl status
wpctl get-volume @DEFAULT_AUDIO_SINK@
pactl list sinks short                    # SUSPENDED / IDLE / RUNNING
cat /proc/asound/card0/pcm0p/sub0/status  # closed when PipeWire suspended it
sudo alsaucm -c hw:0 dump text            # the UCM as alsa-lib sees it
journalctl --user -u wireplumber
speaker-test -D default -c 2 -t wav -l 1  # Front Left, then Front Right
```
