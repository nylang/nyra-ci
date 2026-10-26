# Nyra private SDK CI

This repository contains CI orchestration only. Compiler/analyzer sources stay
on the private Git server. This repository must remain private: its runners can
access that server and must never execute public pull-request code.

- `Runner health`: runs on trusted `main` pushes or manual dispatch; checks
  native tools and private-source connectivity on all three runners.
- `Build private SDK candidates`: manual dispatch only; downloads checksum-pinned
  SDK 1.0.1 seeds, fetches an explicit source ref, bootstraps G1/G2/G3 and assembles
  a native candidate. Full regression/units are enabled by default and can be
  disabled explicitly. The final resolved source commit is recorded.

Runners: `nyra-macos-arm64`, `nyra-windows-x64`, `nyra-linux-x64`; all carry the
`nyra-sdk` label plus native OS/architecture labels. Linux uses rootless Podman
and a persistent state volume; workflow steps run inside that existing container.
Nested Docker/service-container jobs are not supported by this deployment.

Only infrastructure/build receipts are uploaded to this private repository.
Source snapshots, logs and candidate SDKs remain on the runner in the path named
by the receipt. No job publishes a release, installs over a local SDK, or promotes
the tracked bootstrap seed. Native candidate builds do not constitute mobile or
cross-target release acceptance. Cleanup of retained build trees is manual.

Keep runner registration files, API tokens and private Git credentials out of
Git. The runner provisioner lives in the private compiler repository's `tools/ci`.
