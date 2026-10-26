# Nyra private SDK CI

This repository contains CI orchestration only. Compiler/analyzer sources stay
on the private Git server. This repository must remain private: its runners can
access that server and must never execute public pull-request code.

- `Runner health`: runs on trusted `main` pushes or manual dispatch; checks
  native tools and private-source connectivity on all four runners.
- `Build private SDK candidates`: manual dispatch only; downloads checksum-pinned
  SDK 1.0.1 seeds, fetches an explicit source ref, bootstraps G1/G2/G3 and assembles
  a native candidate. Full regression/units are enabled by default and can be
  disabled explicitly. The final resolved source commit is recorded.

Runners: `nyra-macos-arm64`, `nyra-macos-x64-rosetta`, `nyra-windows-x64`,
`nyra-linux-podman-x64`; all carry the
`nyra-sdk` label plus native OS/architecture labels. Linux uses rootless Podman
and a persistent state volume; workflow steps run inside that existing container.
Nested Docker/service-container jobs are not supported by this deployment.

Manual candidate jobs upload only infrastructure/build receipts. Source
snapshots, logs and candidate SDKs remain on their runners. Stable tag builds
exchange private runtime catalogs and upload source-free SDK exports, as described
below. No job installs over a local SDK or promotes the tracked bootstrap seed.
Native candidates alone do not constitute cross-target release acceptance.

Keep runner registration files, API tokens and private Git credentials out of
Git. The runner provisioner lives in the private compiler repository's `tools/ci`.

## Stable release tags

`Release SDK from version tag` handles tags `vX.Y.Z` in this **private control
repository**. The public SDK repository's tag identifies its own public metadata
commit; private compiler implementation is never copied into that repository.

Before tagging:

1. Commit/push the compiler change with `VERSION` and
   `source/bootstrap/package-lock.json` set to the intended version. Include the
   public exporter's `minimum_os_version` preservation fix.
2. Configure `SDK_RELEASE_TOKEN` in this repository's Actions Secrets: a
   fine-grained token restricted to `nylang/sdk`, Contents read/write. Review its
   expiry and any organization approval requirement; never put it in a plan.
3. Confirm all four runners are online. macOS x86_64 currently runs under Rosetta
   on the ARM Mac, with a separate x86_64 Python/runtime and dedicated source key.
   It is not physical Intel-hardware acceptance.
4. Generate and review an immutable source plan from this checkout:

   ```sh
   export NYRA_CI_RUNNER_DIRECTORY="$HOME/.nyra/ci/macos/runner"
   python3 -B scripts/new_release.py --version 1.0.2 --source-ref main --notes "Release notes" --write
   git add releases/v1.0.2.json
   git commit -m "Pin SDK 1.0.2 release source"
   git push origin main
   git tag -a v1.0.2 -m "Nyra SDK 1.0.2"
   git push origin v1.0.2
   ```

The example is not a pre-created release. The helper refuses a mismatching
source VERSION/lock; the tag workflow refuses an already published version.
Never move an existing release tag to another control commit.

The pipeline performs G1/G2/G3 bootstrap, fixed-point verification, full
regression and units for each of four hosts. macOS arm64 builds Android/iOS
runtime libraries; Linux builds RISC-V Linux, bare-metal and NyraOS profiles.
These cross profiles retain **library/link validation**, not device-run claims.
Every host export must retain all eleven profiles in `release-policy.json` and
kernel/ntest/stdlib (kernel only for bare-metal profiles). yaml/json/nllvm remain
ecosystem packages and legal notices, not implicit SDK package dependencies.

Private runtime catalogs are exchanged only inside this private Actions run.
Each packaging job returns to its producer runner and validates retained
run/attempt/source/control identity. Build work and seeds use
`NYRA_CI_CACHE_DIRECTORY` when configured, otherwise `RUNNER_TOOL_CACHE`.
macOS caches currently use the ignored workspace `cache/ci-runners/` on the
Work volume to avoid filling the system disk. Retained source/log trees need
periodic manual cleanup **after** the run; never delete a running build tree.

A GitHub-hosted publishing worker downloads only the public exports, streams
archive verification, checks exact host/target/version/hash inventories, uploads
a draft, compares GitHub asset SHA-256/size, then publishes. It has no private
compiler checkout or source-read SSH key. Only prepare/publish steps receive the
SDK release credential. Existing published releases are immutable; conflicting
draft assets stop the run instead of being deleted or overwritten.

Retry infrastructure failures with **Re-run all jobs**, preserving the original
tag/plan. A matching partial draft can resume; a new source fix requires a new
release version and tag. macOS binaries remain unsigned and unnotarized until
Developer ID credentials are deliberately configured. The workflow checks actual
Mach-O deployment versions against macOS 14.0; it never patches load commands to
claim older compatibility.

The pipeline configuration is deployed, but a successful health run is not a
release acceptance run. No new SDK is published until a version tag is pushed
and every release gate succeeds.

Current source baseline: root `VERSION` is `1.0.1`, while the checked-in
bootstrap toolchain lock is `1.0.0`. Existing public 1.0.1 SDKs were packaging
patches. The release-plan helper correctly rejects that mismatch; it does not
silently relabel existing compiler binaries. Align the next source release's
version, lock and tool versions before creating its plan.
