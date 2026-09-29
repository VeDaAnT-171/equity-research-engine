"""The hosted app: type a company, the engine fetches its filings and runs the pipeline.

This is a different deployment from `serve`. `serve` is a read-only view of a local workspace and
refuses to leave loopback. The hosted app is meant to be public, so it only ever writes into its
own data directory, only builds configs from the SEC's own company index (never from text a
visitor supplies), and strips local paths from every response.
"""

from .jobs import Job, JobRunner, RunRefused
from .lookup import CompanyIndex, IndexEntry

__all__ = ["CompanyIndex", "IndexEntry", "Job", "JobRunner", "RunRefused"]
