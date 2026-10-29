"""Resolve a manual candidate source ref once for every matrix worker."""
import os
from pathlib import Path
import re
import tempfile
from release_common import checkout

ref = os.environ['NYRA_CI_SOURCE_REF']
if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]*', ref):
    raise ValueError('invalid source ref')
with tempfile.TemporaryDirectory(prefix='nyra-ci-source-') as temp:
    commit = checkout(ref, Path(temp) / 'source')
with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
    output.write('source_commit=' + commit + '\n')
print('Frozen source commit: ' + commit)
