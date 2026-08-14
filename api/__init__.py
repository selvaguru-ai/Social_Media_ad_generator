"""HTTP API layer for the dashboard.

Read-mostly wrapper around the pipeline's on-disk artifacts (`data/*.json`)
plus the approval-gate write path. The pipeline agents are not imported here
except by `runs.py`, which shells out to the existing CLI.
"""
from api.store import PipelineStore

__all__ = ["PipelineStore"]
