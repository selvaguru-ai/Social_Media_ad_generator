#!/usr/bin/env python3
"""
Full pipeline CLI runner.

Usage:
    python run_pipeline.py [niche] [--regions US,GB,CA] [--resume] [--until prospect] [--full]
    
Examples:
    python run_pipeline.py "sustainable fashion"
    python run_pipeline.py "pet food" --regions US,CA
    python run_pipeline.py --resume  # Resume remaining stages after verification
    python run_pipeline.py "sports shoes" --full  # Skip the verify pause
"""
import asyncio
import sys

import click

from orchestrator import main as run_orchestrator
from utils import console, config


async def run_pipeline_async(niche: str, regions: str, resume: bool, until: str, full: bool):
    """Run the prospect-to-pitch pipeline (async implementation)."""
    regions_list = [item.strip().upper() for item in regions.split(",") if item.strip()] if regions else None
    until_stage = None if resume or full or until == "end" else until

    await run_orchestrator(
        niche=niche,
        regions=regions_list,
        resume=resume,
        until=until_stage,
    )


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
@click.option(
    '--until',
    default='prospect',
    type=click.Choice(['prospect', 'end'], case_sensitive=False),
    help='Stop after this stage so Ad Library pages can be verified (default: prospect).',
)
@click.option(
    '--full',
    is_flag=True,
    help='Run every remaining stage without the verification pause.',
)
def run_pipeline(niche: str, regions: str, resume: bool, until: str, full: bool):
    """Run the prospect-to-pitch pipeline."""
    asyncio.run(run_pipeline_async(niche, regions, resume, until, full))


if __name__ == "__main__":
    run_pipeline()
