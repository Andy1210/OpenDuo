## What and why

<!-- One topic per pull request. What does it change, and why? Link the issue if there is one. -->

Components:

## Testing

<!-- Host tests and lint: which commands you ran. -->
- Host tests:
- Lint:

<!--
On a real Surface Duo 2: the OpenDuo build (Settings > About or /etc/duo2-release),
the kernel release (uname -r), what you did and what you saw.
If it was not tested on a phone, say so and why.
-->
- On a Surface Duo 2:

## AI use

<!-- Required. See CONTRIBUTING.md, "AI use: disclose it". -->
- [ ] No AI tools were used.
- [ ] AI tools were used. Tool and model (for example "Claude Code, Claude Opus"):
      What they did (code, tests, docs, review, research):

## Self-review checklist

- [ ] One topic; the description says what and why.
- [ ] Host tests pass for every component this touches.
- [ ] Lint is clean (`scripts/lint-services <component>`; Rust: `build-aarch64.sh check`; kernel patches: `checkpatch.pl --strict`).
- [ ] Tested on a real Surface Duo 2 as described above, or the description says it was not.
- [ ] `scripts/check-personal-data` is clean on the staged changes; no identifiers in logs, screenshots or test data.
- [ ] No vendor files, firmware, blobs or build output in the diff.
- [ ] Docs updated: the component's README, CHANGELOG.md ("Unreleased"), the hardware status if it changed.
- [ ] Kernel patches only: my own `Signed-off-by:`; `Assisted-by:` if an AI helped; SERIES.md updated.
- [ ] I followed the device-safety rules in CONTRIBUTING.md (and my agent followed AGENTS.md).
