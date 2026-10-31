"""Route this job's publication inputs to its managed assembly filesystem."""
import json
import os
from pathlib import Path
import shutil
from build_sdk import cache_root
from release_common import ROOT,write


def main():
    key=os.environ['GITHUB_RUN_ID']+'-'+os.environ['GITHUB_RUN_ATTEMPT']
    if not all(part.isdigit() for part in key.split('-')):
        raise ValueError('invalid assembly job identity')
    owner=cache_root()/'nyra-release-work'/('assembly-'+key)/'publication-inputs'
    if shutil.disk_usage(owner.parent if owner.parent.exists() else owner.parent.parent).free<86*1024**3:
        raise ValueError('assembly requires 80 GiB reserve plus 6 GiB input headroom')
    owner.mkdir(parents=True,exist_ok=False)
    for name in ('catalog-input','publish-input'):
        path=ROOT/name
        if path.exists() or path.is_symlink():raise ValueError('unexpected existing assembly input path')
        destination=owner/name
        destination.mkdir()
        path.symlink_to(destination,target_is_directory=True)
    write(owner/'owner.json',dict(owner='1.0.5 exact publication assembly',run=os.environ['GITHUB_RUN_ID'],
        cleanup='Keep sealed bundle until publication; then move accepted archives to product storage and remove owned input links/scratch'))


if __name__=='__main__':main()
