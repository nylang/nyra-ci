"""Build a private native SDK candidate from a frozen source commit."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from source_access import SOURCE_URL, source_environment

control = Path(__file__).resolve().parents[1]
host = os.environ["NYRA_CI_HOST"]
ref = os.environ.get("NYRA_CI_SOURCE_REF", "main")
if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", ref):
    raise ValueError("invalid source reference")
config = json.loads((control / "sdk-seeds.json").read_text())
asset = config["hosts"][host]
cache_root = Path(os.environ.get("NYRA_CI_CACHE_DIRECTORY", os.environ["RUNNER_TOOL_CACHE"]))
cache = cache_root / "nyra-sdk" / config["version"] / host
sdk = cache / "sdk"
release_mode = os.environ.get("NYRA_CI_RELEASE") == "true"
work_root = cache_root / "nyra-release-work" if release_mode else Path(os.environ["RUNNER_TEMP"])
work_root.mkdir(parents=True, exist_ok=True)
work = work_root / ("nyra-sdk-" + os.environ["GITHUB_RUN_ID"] + "-" + os.environ["GITHUB_RUN_ATTEMPT"])
work.mkdir(parents=True, exist_ok=False)
receipt = {"host": host, "source_ref": ref, "seed_version": config["version"],
           "workspace": str(work), "execution_mode": os.environ.get("NYRA_CI_EXECUTION_MODE", "native"), "success": False, "published": False}


def run(command, cwd=None, env=None):
    print("BEGIN " + command[0] + " " + " ".join(command[1:]), flush=True)
    subprocess.run(command, cwd=cwd, env=env, check=True)


try:
    if not sdk.exists():
        cache.mkdir(parents=True, exist_ok=True)
        archive = cache / asset["file"]
        request = urllib.request.Request(asset["url"], headers={"User-Agent": "Nyra-private-CI"})
        with urllib.request.urlopen(request, timeout=180) as source, archive.open("wb") as target:
            shutil.copyfileobj(source, target)
        digest = hashlib.sha256()
        with archive.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
        if digest.hexdigest() != asset["sha256"]:
            raise RuntimeError("pinned SDK seed checksum mismatch")
        stage = cache / "extract"
        if stage.exists():
            shutil.rmtree(stage)
        stage.mkdir()
        if archive.suffix == ".zip":
            with zipfile.ZipFile(archive) as source:
                source.extractall(stage)
        else:
            with tarfile.open(archive) as source:
                source.extractall(stage, filter="data")
        roots = [p.parent for p in stage.glob("*/manifest.json")]
        if len(roots) != 1:
            raise RuntimeError("SDK archive layout is ambiguous")
        roots[0].rename(sdk)
        shutil.rmtree(stage)
        archive.unlink()
    seed = sdk / "hosts" / host / "bin" / ("nyrac.exe" if os.name == "nt" else "nyrac")
    source = work / "source"
    run(["git", "init", str(source)])
    run(["git", "remote", "add", "origin", SOURCE_URL], source)
    run(["git", "fetch", "--depth", "1", "origin", ref], source, source_environment())
    run(["git", "checkout", "--detach", "FETCH_HEAD"], source)
    run(["git", "submodule", "update", "--init", "--recursive", "--",
         "third_party/nllvm", "third_party/json", "third_party/yaml", "third_party/net_core"], source)
    receipt["source_commit"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
    if release_mode:
        from release_common import spec, check_source
        check_source(source, spec(os.environ["GITHUB_REF_NAME"]))
    environment = os.environ.copy()
    for key in tuple(environment):
        if key.startswith("NYRA"):
            environment.pop(key)
    # LLVM links share host resources with independent qualification work.
    # The outer process budget does not change runtime deadline assertions.
    if host.startswith(("darwin-", "linux-")):
        environment["NYRA_REGRESSION_MIN_TIMEOUT"] = "1800" if host == "linux-x86_64" else "600"
    bootstrap = source / "cache/ci/bootstrap"
    # Both macOS architectures share one physical host. LLVM links also create
    # their own workers; preserve capacity for cases with bounded timeout clocks.
    jobs = "1" if host.startswith("darwin-") else "2" if host == "linux-x86_64" else "4"
    command = [sys.executable, "-B", "build.py", "bootstrap", "--seed", str(seed),
               "--out-dir", str(bootstrap), "-j", jobs, "--timeout", "7200"]
    if release_mode or os.environ.get("NYRA_CI_FULL_REGRESSION", "true").lower() == "true":
        command.append("--regression")
    run(command, source, environment)
    candidate = work / "sdk"
    command = [sys.executable, "-B", "build.py", "sdk", "--input-dir", str(bootstrap), "--out-dir", str(candidate)]
    if not release_mode:
        command.append("--candidate")
    run(command, source, environment)
    receipt.update(success=True, sdk=str(candidate), bootstrap_report=str(bootstrap / "bootstrap-report.json"))
finally:
    (control / "build-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
