"""Immutable source identity, release policy, and private catalog validation."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tempfile
from source_access import SOURCE_URL, source_environment

ROOT = Path(__file__).resolve().parents[1]
GATES = ("clean_source", "fixed_point", "full_regression", "full_unit")


def sha(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, sort_keys=True, indent=2) + "\n")


def policy():
    return json.loads((ROOT / "release-policy.json").read_text())


def spec(tag):
    if not re.fullmatch(r"v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", tag):
        raise ValueError("release tags must be canonical stable versions such as v1.0.2")
    data = json.loads((ROOT / "releases" / (tag + ".json")).read_text())
    if set(data) != {"schema_version", "version", "source_commit", "notes"} or data["schema_version"] != 1:
        raise ValueError("invalid release plan schema")
    if data["version"] != tag[1:] or not re.fullmatch(r"[0-9a-f]{40}", data["source_commit"]):
        raise ValueError("tag/version/source commit mismatch")
    if not isinstance(data["notes"], str):
        raise ValueError("release notes must be text")
    return data


def checkout(ref, destination):
    destination = Path(destination)
    subprocess.run(["git", "init", str(destination)], check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "remote", "add", "origin", SOURCE_URL], cwd=destination, check=True)
    subprocess.run(["git", "fetch", "--depth", "1", "origin", ref], cwd=destination,
                   env=source_environment(), check=True)
    subprocess.run(["git", "checkout", "--detach", "FETCH_HEAD"], cwd=destination, check=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=destination, text=True).strip()
    return commit


def check_source(source, plan):
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
    lock = json.loads((Path(source) / "source/bootstrap/package-lock.json").read_text())
    if commit != plan["source_commit"] or (Path(source) / "VERSION").read_text().strip() != plan["version"] or lock["toolchain_version"] != plan["version"]:
        raise ValueError(f"release identity mismatch: commit={commit}, VERSION={(Path(source) / 'VERSION').read_text().strip()}, toolchain_lock={lock['toolchain_version']}; expected commit={plan['source_commit']}, version={plan['version']}")


def sdk_source_matches(data, plan, host):
    """Keep actual build revisions; admit only a reviewed, hash-bound reuse proof."""
    if data.get("source_revision") == plan["source_commit"]:
        return True
    catalog = ROOT / "qualified-inputs" / (plan["version"] + ".json")
    if not catalog.is_file():
        return False
    config = json.loads(catalog.read_text())
    if config.get("source_commit") != plan["source_commit"]:
        return False
    pin = config.get("native_source_equivalence", {}).get(host)
    if not isinstance(pin, dict):
        return False
    path = ROOT / safe_name(pin["path"])
    if not path.resolve().is_relative_to(ROOT.resolve()) or sha(path) != pin["sha256"]:
        raise ValueError("native source equivalence proof changed")
    proof = json.loads(path.read_text())
    frontend = proof.get("frontend_gate", {})
    nyra = proof.get("nyra_gate", {})
    return (proof.get("kind") == "approved-platform-effective-input-reuse-v1"
            and proof.get("status") == "complete"
            and proof.get("host") == host
            and proof.get("actual_product_build_source_commit") == data.get("source_revision")
            and proof.get("release_source_commit") == plan["source_commit"]
            and proof.get("tools") == {name: row.get("sha256") for name, row in data["tools"].items()}
            and frontend.get("status") == "ok"
            and type(frontend.get("passed")) is int and frontend["passed"] > 0
            and frontend.get("ran") == frontend["passed"]
            and all(frontend.get(key) == 0 for key in ("failed", "skipped", "exit_code"))
            and nyra.get("status") == "ok"
            and type(nyra.get("passed")) is int and nyra["passed"] > 0
            and nyra.get("ran") == nyra["passed"]
            and all(nyra.get(key) == 0 for key in ("failed", "skipped", "exit_code")))


def check_sdk(data, plan, host):
    if (data.get("format") != "nyra-sdk-v2" or (data.get("version"), data.get("host")) != (plan["version"], host)
            or not sdk_source_matches(data, plan, host)):
        raise ValueError("SDK identity differs from immutable release plan")
    if any(data.get("acceptance", {}).get(gate) is not True for gate in GATES):
        raise ValueError("release requires all clean-source, fixed-point, regression and unit gates")
    if set(data["tools"]) != {"nyrac", "nyra-analyzer", "nyra"} or any(row["version"] != plan["version"] for row in data["tools"].values()):
        raise ValueError("compiler, analyzer and package-manager versions must match the release")


def safe_name(name):
    p = PurePosixPath(name)
    if not name or any(ord(c) < 32 for c in name) or p.is_absolute() or ".." in p.parts or "\\" in name or ":" in name or p.as_posix() != name:
        raise ValueError("unsafe archive or inventory path")
    return p


def catalog_check(directory, plan, host):
    directory = Path(directory)
    data = json.loads((directory / "manifest.json").read_text())
    receipt = json.loads((directory / "catalog.json").read_text())
    check_sdk(data, plan, host)
    if receipt.get("source_commit") != plan["source_commit"] or receipt.get("host") != host or receipt.get("manifest_sha256") != sha(directory / "manifest.json"):
        raise ValueError("catalog receipt identity mismatch")
    index = {row["path"]: row for row in data["files"]}
    actual = {p.relative_to(directory).as_posix(): p for p in directory.rglob("*") if p.is_file()}
    expected = set(receipt["files"]) | {"manifest.json", "catalog.json"}
    if set(actual) != expected:
        raise ValueError("catalog inventory mismatch")
    for name in receipt["files"]:
        safe_name(name)
        if not name.startswith(("targets/", "share/nyra/", "frameworks/")) or name not in index or actual[name].is_symlink() or sha(actual[name]) != index[name]["sha256"]:
            raise ValueError("catalog file or hash mismatch: " + name)
    return data


def state_path(host):
    return Path(os.environ.get("NYRA_CI_CACHE_DIRECTORY", os.environ["RUNNER_TOOL_CACHE"])) / "nyra-release-state" / os.environ["GITHUB_RUN_ID"] / (host + ".json")
