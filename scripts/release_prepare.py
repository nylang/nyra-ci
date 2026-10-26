"""Validate a tag and source identity before any expensive release builds."""
import json
import os
from pathlib import Path
import tempfile
import urllib.error
import urllib.request
from release_common import spec, policy, checkout, check_source

tag = os.environ["GITHUB_REF_NAME"]
if os.environ.get("GITHUB_REF_TYPE") != "tag":
    raise ValueError("SDK publishing requires a pushed version tag")
plan = spec(tag)
token = os.environ.get("SDK_RELEASE_TOKEN", "").strip()
if not token:
    raise ValueError("configure SDK_RELEASE_TOKEN for nylang/sdk Contents read/write before tagging")
repository = policy()["public_repository"]
def get(path):
    request = urllib.request.Request("https://api.github.com/repos/" + repository + path,
        headers={"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json", "User-Agent": "Nyra-release"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)
identity = get("")
if identity["full_name"] != repository or identity["private"]:
    raise ValueError("publication destination must be the public SDK-only repository")
try:
    existing = get("/releases/tags/" + tag)
except urllib.error.HTTPError as error:
    if error.code != 404:
        raise
else:
    if not existing["draft"]:
        raise ValueError("this version is already published; use a new version")
with tempfile.TemporaryDirectory(prefix="nyra-release-preflight-", dir=os.environ["RUNNER_TEMP"]) as temporary:
    source = Path(temporary) / "source"
    checkout(plan["source_commit"], source)
    check_source(source, plan)
with open(os.environ["GITHUB_OUTPUT"], "a") as stream:
    stream.write("source_commit=" + plan["source_commit"] + "\n")
    stream.write("version=" + plan["version"] + "\n")
print("Release source pinned: " + plan["source_commit"] + "; version " + plan["version"])
