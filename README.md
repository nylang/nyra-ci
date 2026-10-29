# Nyra private release CI

This private control repository orchestrates native qualification and publishes
source-free archives to [nylang/sdk](https://github.com/nylang/sdk). Compiler
implementation source stays on the private source server; publishing workers
have neither its SSH key nor a compiler checkout.

## Workflows

- **Runner health** runs on control-main pushes and manually. It checks four
  self-hosted service accounts, pinned source SSH access, architecture, managed
  provider inventories and platform permissions.
- **Release infrastructure preflight (no rebuild)** checks a frozen source
  checkout, the pinned 1.0.4 bootstrap SDK, tool hashes/versions, CMake/Ninja and
  the normal bootstrap dry-run on all four runners. A GitHub-hosted worker runs
  controller tests and proves the actual publication secret can create, upload,
  digest-check and remove a disposable draft. No compiler or complete suites
  are rebuilt/repeated.
- **Qualify private native SDK candidates** resolves one source commit and runs
  the complete native policy on all four hosts. It saves source-free SDK
  archives and receipts; it does not publish.
- **Qualify and release SDK** accepts an immutable `releases/vX.Y.Z.json` plan.
  A pushed control-repository version tag qualifies and publishes automatically.
  Manual dispatch can qualify only (`publish: false`, default), or qualify and
  publish. All four native jobs and every assigned runtime target must pass.
- **Publish retained qualified bundle** takes the release tag and the original
  qualification run ID. It downloads that run's `qualified-release` artifact
  and publishes the exact bytes without invoking any compiler or test suite.
  Use it after a failed upload or after a qualification-only run. It verifies
  workflow identity, successful assembly, source/control identity, asset
  inventory, archive contents and GitHub SHA-256 digests. Retention is 30 days;
  expiration is an error, never an implicit rebuild.

**Windows release preflight (no rebuild)** can repeat only the Windows seed/plan
checks after a platform-specific orchestration correction; it does not rerun
compiler suites. It replaces the historical G3 Core replay workflow.

## Native acceptance policy

The frozen source, dependencies, native inputs and release options are shared
by G1/G2. Require `SHA256(G1) == SHA256(G2)`. Run the complete compiler regression
suite once with G2 and the complete compiler/dependency unit inventory once with
G2. Ship that exact G2 executable. Build `nyra` and `nyra-analyzer` once with the
selected bootstrap compiler and run each complete tool unit suite once with that
bootstrap compiler. There is no G3 or later product rebuild.

The source repository owns this policy (`.agents/rules/sdk-validation.md`) and
its validated exporters. The CI invokes those maintained build entry points.
Assembly and publication check hashes/inventories/licenses without repeating
complete qualification. Private receipts retain source snapshot and tool hashes.

## Delivery matrix

Four native SDKs: macOS arm64, macOS x86_64 (currently Rosetta execution), Linux
x86_64 and Windows x86_64. Each carries only its matching native runtime.

Ten independent runtime archives cover 18 profiles. CPU/ABI bundles carry kernel
and ntest for RV64IMAC/LP64, RV64GC/LP64D, arm64 and x86_64. Hosted bundles carry
kernel/ntest/stdlib/platform for macOS arm64/x86_64, Windows arm64/x86_64, Linux
glibc arm64/x86_64/riscv64, Linux musl arm64/x86_64, Android arm64/x86_64 and iOS
arm64 device plus arm64/x86_64 simulators. Select package roots via their
`packages/<name>/package.yaml` indexes. No unsupported cross-runtime whitelist
is injected into native SDK implicit lookup.

`release-policy.json` assigns each profile to one producer and records required
physical, Rosetta, virtual or compile-only levels. Available execution targets
cannot silently fall back to compilation. Complete tool object worksets and both native bridges are also compiled for
Linux arm64/riscv64 and Windows arm64, without claiming executable SDKs.
Windows ARM64 is compile-only while
its MSVC static CRT is unavailable. Compile-only does not claim executable,
fixed-point or runtime-suite acceptance. Freestanding packages require explicit
consumer startup, heap, output, counter and compiler-helper providers.

## Runner inputs

Labels are `self-hosted` + `nyra-sdk`, with one host label:
`nyra-macos-arm64`, `nyra-macos-x86_64`, `nyra-linux-x64`, `nyra-windows-x64`.
The Intel Mac job uses an actual x86 Python under Rosetta. Builds serialize via
`sdk-source-build`; the two macOS native builds additionally serialize on their
shared machine. Completed build scratch is removed only after private evidence
and exact public archives are uploaded; a small assembly tree remains retained.
The Mac caches live under ~/.nyra/ci/cache to avoid background-service access
to an external volume.

`NYRA_CI_CACHE_DIRECTORY` overrides `RUNNER_TOOL_CACHE`. Required managed files:

- `nyra-sdk/<version>/<host>/sdk` and `verified-archive.json`: checksum-pinned
  bootstrap selected by `sdk-seeds.json`; missing seeds download automatically.
- `nyra-providers/openssl-3.5.9`: static provider with headers, libraries and legal
  notices for that host. No shared system OpenSSL is silently substituted.
- `nyra-providers/targets.json`: explicit NDK, sysroots, Windows include/library
  paths, QEMU programs, compiler-rt comparison sources and iOS simulator identity
  for that producer. Apple SDKs resolve through Xcode.
- `nyra-providers/inputs.json`: per-file SHA-256 inventory of the prepared native
  inputs and target settings. Qualification refuses changed/missing files.

Current providers were promoted from already accepted 1.0.4 inputs. Keep them
outside ephemeral checkouts. Changing toolchains/sysroots requires a deliberate
provider inventory update and new qualification. Normal publication retries
never reinterpret or rebuild those inputs.

Source access uses the service's dedicated forced-command SSH read key under
`runner/keys/`, with `IdentitiesOnly` and pinned known hosts. It can read only the
compiler repository, cannot push or run a shell, and is never passed to the
GitHub-hosted publisher. Public dependency commits are pinned by source gitlinks.

Configure `SDK_RELEASE_TOKEN` as a fine-grained token restricted to `nylang/sdk`
with Contents read/write. Do not copy account-wide credentials into this secret.
Only destination preparation, the disposable credential probe and publication
steps receive it. Archive upload recovery removes only incomplete uploads in a
draft belonging to the exact immutable release plan; published bytes cannot be
replaced.

## Prepare a future release

1. Push the intended private source commit and every pinned public dependency.
2. On a configured runner, set `NYRA_CI_RUNNER_DIRECTORY` to its runner directory
   and run `python3 scripts/new_release.py --version X.Y.Z --source-ref COMMIT
   --notes "Release notes" --write`. This verifies source VERSION and lock
   identity and saves the exact commit; it does not build or publish.
3. Review/commit/push `releases/vX.Y.Z.json` to this control repository.
4. Dispatch **Qualify and release SDK** with that tag and `publish: true`, or
   push the matching version tag. CI qualifies once and publishes automatically.
5. If only upload fails, dispatch **Publish retained qualified bundle** with the
   same tag and original run ID. Do not rerun all qualification jobs.

The already accepted/published 1.0.4 is not requalified merely to migrate CI.
Future source changes use a new release plan/version.

After updating SDK_RELEASE_TOKEN, run **Publication credential preflight (no builds)**. It probes only destination permissions and does not repeat native seed checks or compiler suites.
