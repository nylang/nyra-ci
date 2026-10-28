# Nyra private SDK CI

This repository contains CI orchestration only. Compiler/analyzer sources stay
on the private Git server. This repository must remain private: its runners can
access that server and must never execute public pull-request code.

- `Runner health`: runs on trusted `main` pushes or manual dispatch; checks
  native tools and private-source connectivity on all four runners. Windows
  also probes file, dangling and directory symbolic links under the actual
  service account; interactive administrator checks are insufficient.
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

Current release source and bootstrap toolchain lock are stamped `1.0.2`.
The macOS Intel runner uses an explicit `LLVM_PREFIX` built with Apple Clang
from the official LLVM 21.1.8 source archive pinned by the compiler repository's
`third_party/llvm/SOURCE.json`. The SDK 1.0.1 compiler remains the unmodified seed;
its historical Intel LLVM payload is not the release toolchain. Linux runs with
a 12-CPU/12-GiB quota and two concurrent regression cases; independent 1.0.3
qualification currently shares that same container.
Each macOS runner uses one regression worker; both architectures share the same
desktop. The existing harness outer timeout floor is 600 seconds on macOS and 1800
seconds on Linux, retained in each bootstrap report. A loaded ARM compiler/link case completed
with all assertions passing in 352 seconds after exceeding its default 180-second
outer budget. API deadline assertions and the complete 492-case selection remain
required for every generation.

On 2026-10-27 the user explicitly authorized one exception to the immutable
private tag rule: rebuild the **unpublished private** `v1.0.2` tag after failed
run `40510719258`, incorporating official-source Intel LLVM build support.
The replaced plan pinned source `ccdd53b7a4ea79fcfbda4c8f053d38c04368ce5d`;
the replacement pins `abc82ec742ecef694659044d5e0d31457cba5032`.
No public SDK 1.0.2 release or tag existed when the exception was approved.
This does not authorize changing an existing public release or relaxing gates.

The prepublication run `40525185668` was cancelled to include the upstream
compiler-rt license and contributor credits in every exported LLVM kit.
The complete gates are rerun for this updated source plan.

Run `40528036309` exposed the same short outer compilation budget on Linux
(`windows_absolute_paths`, 180 seconds). Unix release runners now use the
validated 600-second floor; no case is filtered or converted to expected failure.

Windows release units require symbolic links under the runner service account.
The user authorized Developer Mode on 2026-10-28; the pre-change registry state
is retained on the host. Runner-local process Git configuration also sets
`core.autocrlf=false`: the system Git default otherwise rewrites exact YAML
corpus goldens to CRLF. Do not normalize the expected outputs or skip those tests.
The health workflow verifies both requirements under the actual service token.

The Linux container service search path includes the unchanged SDK 1.0.1 LLVM
21.1.8 `bin` directory. Core C projection units invoke `clang` by name, in addition
to the explicit CC used for bootstrap. Preserve that seed cache while this runner
configuration depends on it; health checks now require clang to be discoverable.

Windows also prefixes its runner-local PATH with the unchanged pinned SDK LLVM
kit. Core projection units launch clang by name; the service does not inherit
the interactive administrator's user PATH. `Windows service Core preflight`
replays all retained Core units under the actual service token without replacing
that PATH, so interactive checks cannot conceal this difference.

The fourth attempt of run `40531841067` completed the Windows native SDK gates:
three complete 492-case generations, G2/G3 fixed point and all 1,040 native units.
Concurrent 1.0.3 qualification in the Linux container then pushed a complete
compiler build to 816 seconds and `face_cvarargs_variadic` past its 600-second
outer process budget. Two concurrent complete diagnostic replays passed in
293.81 and 250.81 seconds with a 2400-second diagnostic ceiling. Linux now has
an 1800-second release ceiling to accommodate the observed shared-host peak.
Runtime assertions and all 492 cases remain required; no expected output changed.
The unpublished private tag is rebuilt under the previously approved exception,
with the same frozen compiler source `abc82ec742ecef694659044d5e0d31457cba5032`.

### Linux artifact assembly prerequisites

The runner image includes `file`, `readelf` and `ldd`; health checks verify these
commands before release work. SDK 1.0.2 run `40574272950` passed all three
492-case generations, fixed point and 1040 native units on every host, but its
Linux artifact collection stopped at the previously missing `file` command.
Post-bootstrap preflight then exposed the system ICU dependencies of Ubuntu's
`libxml2`. The source fix recognizes `libicuuc` and `libicudata` only from the
existing system library paths; missing and developer-directory libraries remain
rejected. The unchanged SDK 1.0.1 seed was preserved.

The running container received the inspection utility without a restart. A clean
extension image `localhost/nyra-ci-linux:2.338.1-release-tools` contains the same
packages, and the backed-up Quadlet definition points to it for future recreation.
Concurrent qualification processes and their files were preserved. Diagnostic
continuation from the retained G3 is not formal release acceptance; the final
source and control identities require a fresh complete release workflow.

The RISC-V GNU/Linux producer queries `riscv64-linux-gnu-gcc -print-sysroot`
unless `NYRA_CI_RISCV_SYSROOT` is explicitly configured. Ubuntu's installed
cross driver reports `/`; `/usr/riscv64-linux-gnu` is its libc package prefix,
not the complete GCC sysroot. Using that prefix hid the installed GCC CRT
objects from Clang. Native assembly and offline acceptance passed before this
configuration fault was found by the assigned cross-profile preflight. Health
also checks that the distribution cross driver can report its sysroot.

### Publication API recovery

SDK 1.0.2 run `40597935931` passed all four native builds and public export
jobs from source `44c7223c571fc964d3c159410d6467db8b260181` and control
`e6bff1137e67781756405fd02dddaa635e284db2`. Its final release-creation API call
returned HTTP 403. Publication-only retries retain those exact verified
artifacts; they do not require repeating successful compiler acceptance.

Draft recovery uses the releases list: GitHub's by-tag endpoint returns
published releases and cannot reliably discover a draft. Multiple matching
releases fail closed. API errors preserve HTTP status and report GitHub's
message and required-permission header without logging credentials. Five focused
regression tests cover draft lookup, pagination, conflicts and authorization
failures. The SDK release secret remains restricted to `nylang/sdk` and requires
Contents write. A target that changes workflow files relative to the default
branch additionally requires Workflows write, as described in the
[official GitHub release API documentation](https://docs.github.com/en/rest/releases/releases#create-a-release).
Do not copy account-wide credentials into this CI secret as a workaround.
