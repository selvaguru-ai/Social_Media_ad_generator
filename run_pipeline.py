#!/usr/bin/env python3
"""
Full pipeline CLI runner.

Usage:
    python run_pipeline.py [niche] [--regions US,GB,CA] [--resume]
    
Examples:
    python run_pipeline.py "sustainable fashion"
    python run_pipeline.py "pet food" --regions US,CA
    python run_pipeline.py --resume  # Resume from saved state
"""
import asyncio
import sys

import click

from orchestrator import main as run_orchestrator
from utils import console, config


@click.command()
@click.argument('niche', default=None, required=False)
@click.option(
    '--regions',
    default=None,
    help='Comma-separated list of regions (e.g., US,GB,CA)'
)
@click.option(
    '--resume',
    is_flag=True,
    help='Resume from saved pipeline state'
)
async def run_pipeline(niche: str, regions: str, resume: bool):
    """Run the full prospect-to-pitch pipeline."""
    regions_list = regions.split(',') if regions else None
    
    await run_orchestrator(
        niche=niche,
        regions=regions_list,
        resume=resume
    )


if __name__ == "__main__":
    run_pipeline(_anyio_backend="asyncio")
