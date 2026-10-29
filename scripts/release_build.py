"""Qualify native tools once, then prepare assigned target runtime catalogs."""
import os
from build_sdk import build
from runtime_targets import produce
from release_common import spec

plan = spec(os.environ['NYRA_CI_RELEASE_TAG'])
os.environ['NYRA_CI_SOURCE_REF'] = plan['source_commit']
build()
produce()
