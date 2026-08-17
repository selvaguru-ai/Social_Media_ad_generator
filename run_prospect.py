#!/usr/bin/env python3
"""
CLI runner for the Prospect agent.

This is the FIRST component to test - it proves the core concept works.

Usage:
    python run_prospect.py [niche] [--regions US,GB,CA]
    
Examples:
    python run_prospect.py "sustainable fashion"
    python run_prospect.py "fitness apps" --regions US,CA
"""
import asyncio
import sys
from typing import Optional

import click
from rich.table import Table

from agents import AdDiscoveryAgent, ProspectAgent
from utils import console, config


async def run_prospect_async(
    niche: Optional[str],
    regions: Optional[str],
    max_leads: Optional[int],
    with_ad_discovery: bool
):
    """
    Run the Prospect agent to find low-spend brand leads (async implementation).
    """
    console.print("\n[bold cyan]🎯 Prospect Agent - Lead Finder[/bold cyan]\n")
    
    # Parse inputs
    niche = niche or config.market.niche
    regions_list = regions.split(',') if regions else config.market.regions
    
    console.print(f"[bold]Niche:[/bold] {niche}")
    console.print(f"[bold]Regions:[/bold] {', '.join(regions_list)}")
    console.print()
    
    # Build context
    context = {
        'niche': niche,
        'regions': regions_list,
    }
    
    # Optionally run Ad Discovery first for competitor context
    if with_ad_discovery:
        console.print("[dim]Running Ad Discovery agent first...[/dim]\n")
        ad_agent = AdDiscoveryAgent()
        ad_result = await ad_agent.run(context)
        context.update(ad_result)
    
    # Run Prospect agent
    prospect_agent = ProspectAgent()
    result = await prospect_agent.run(context)
    
    # Display results
    display_results(result, max_leads)


@click.command()
@click.argument('niche', default=None, required=False)
@click.option(
    '--regions',
    default=None,
    help='Comma-separated list of regions (e.g., US,GB,CA)'
)
@click.option(
    '--max-leads',
    default=None,
    type=int,
    help='Maximum number of leads to return'
)
@click.option(
    '--with-ad-discovery',
    is_flag=True,
    help='Run Ad Discovery agent first to get competitor context'
)
def run_prospect(
    niche: Optional[str],
    regions: Optional[str],
    max_leads: Optional[int],
    with_ad_discovery: bool
):
    """
    Run the Prospect agent to find low-spend brand leads.
    
    This is the CRITICAL component - prove it works before building the rest.
    """
    asyncio.run(run_prospect_async(niche, regions, max_leads, with_ad_discovery))


def display_results(result: dict, max_leads: Optional[int] = None):
    """Display prospect results in a nice table."""
    leads = result['leads']
    
    if max_leads:
        leads = leads[:max_leads]
    
    if not leads:
        console.print("[yellow]No qualified leads found.[/yellow]")
        return
    
    console.print(f"\n[bold green]Found {len(leads)} qualified leads![/bold green]\n")
    
    # Create table
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("#", style="dim", width=3)
    table.add_column("Brand Name", style="bold")
    table.add_column("Website")
    table.add_column("Size", justify="right")
    table.add_column("Industry")
    table.add_column("Region", justify="center")
    table.add_column("Score", justify="right", style="green")
    table.add_column("Ad Presence", justify="right")
    table.add_column("Qualification Reason", style="dim")
    
    for i, lead in enumerate(leads, 1):
        ad_presence = lead['ad_presence_summary']
        
        table.add_row(
            str(i),
            lead['brand_name'],
            lead.get('domain') or '—',
            str(lead.get('company_size') or 'N/A'),
            lead.get('industry', 'N/A'),
            lead.get('region', 'N/A'),
            f"{lead['qualification_score']:.3f}",
            f"{ad_presence['active_ads']} ads / ${ad_presence['estimated_spend']}",
            lead['qualification_reason']
        )
    
    console.print(table)

    console.print("\n[bold]Verify in Ad Library:[/bold]")
    for lead in leads:
        url = (lead.get("ad_presence_summary") or {}).get("library_url") or lead.get("domain")
        if url:
            console.print(f"  {lead['brand_name']}: {url}")
    
    # Summary stats
    console.print(f"\n[bold]Summary:[/bold]")
    console.print(f"  Total brands analyzed: {result['total_brands_analyzed']}")
    console.print(f"  Qualified leads: {result['qualification_summary']['total_qualified']}")
    console.print(f"  Average score: {result['qualification_summary']['average_score']:.3f}")
    console.print(f"  Top score: {result['qualification_summary']['top_score']:.3f}")
    
    console.print("\n[cyan]✅ Prospect agent test complete![/cyan]\n")


if __name__ == "__main__":
    run_prospect()
