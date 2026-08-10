"""Canonical Snapshot domain shared by indexing, admin and future context layers.

Keep package import side effects minimal. Consumers should import contracts,
repository or service explicitly so tools/tests that only need identity helpers do
not require PostgreSQL drivers at import time.
"""

