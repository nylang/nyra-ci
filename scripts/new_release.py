"""Prepare a release plan without tagging, building, or publishing."""
import argparse
from pathlib import Path
import re
import tempfile
from release_common import ROOT, checkout, check_source, policy, write

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--version", required=True)
parser.add_argument("--source-ref", default="main")
parser.add_argument("--notes", default="")
parser.add_argument("--write", action="store_true", help="save releases/v<version>.json; default prints only")
args = parser.parse_args()
if not re.fullmatch(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", args.version):
    parser.error("expected a stable version such as 1.0.2")
if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", args.source_ref):
    parser.error("invalid source ref")
with tempfile.TemporaryDirectory(prefix="nyra-release-plan-") as temporary:
    source = Path(temporary) / "source"
    commit = checkout(args.source_ref, source)
    data = {"schema_version": 1, "version": args.version, "source_commit": commit, "notes": args.notes}
    check_source(source, data)
import json
print(json.dumps(data, indent=2))
if args.write:
    output = ROOT / "releases" / ("v" + args.version + ".json")
    if output.exists():
        raise ValueError("release plan already exists; never silently replace a pinned plan")
    write(output, data)
