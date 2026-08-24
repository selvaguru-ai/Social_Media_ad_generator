"""
Orchestrator - Pipeline coordinator

Sequences all agents, manages state, handles retries, and logs progress.
"""
import asyncio
import json
from pathlib import Path
from typing import Any, Dict, Optional
from datetime import datetime

from agents import (
    AdDiscoveryAgent,
    ProspectAgent,
    ContactEnrichmentAgent,
    CreativeAgent,
    OutreachAgent,
)
from utils import config, get_logger, console, DATA_DIR


class PipelineOrchestrator:
    """
    Orchestrates the full prospect-to-pitch pipeline.
    
    Pipeline stages:
    1. Ad Discovery - find what's running in the market
    2. Prospect - identify low-spend brands (the leads)
    3. Contact Enrichment - find brand manager contacts
    4. Creative - generate sample video ads
    5. Outreach - send personalized pitches (gated)
    
    State is persisted to allow resuming interrupted runs.
    """
    
    def __init__(self):
        self.logger = get_logger("orchestrator")
        self.state_file = Path(config.orchestrator.state_file)
        self.state_file.parent.mkdir(exist_ok=True, parents=True)
        
        # Initialize agents
        self.ad_discovery = AdDiscoveryAgent()
        self.prospect = ProspectAgent()
        self.contact_enrichment = ContactEnrichmentAgent()
        self.creative = CreativeAgent()
        self.outreach = OutreachAgent()
    
    async def run_pipeline(
        self,
        niche: Optional[str] = None,
        regions: Optional[list[str]] = None,
        resume_from_state: bool = False,
        until: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Run the pipeline, optionally pausing after Prospect.

        until='prospect' stops after scoring so the dashboard can verify
        Ad Library pages before people search / video / outreach spend credits.
        """
        console.print("\n[bold cyan]🚀 Starting Ad Generator & Outreach Pipeline[/bold cyan]\n")
        
        # Initialize context
        context = self._initialize_context(niche, regions, resume_from_state)
        
        # Save initial state
        self._save_state(context)
        
        try:
            # Stage 1: Ad Discovery
            if 'ad_discovery' not in context.get('completed_stages', []):
                console.print("[bold]Stage 1: Ad Discovery[/bold]")
                ad_discovery_result = await self._run_with_retry(
                    self.ad_discovery,
                    context
                )
                context.update(ad_discovery_result)
                context['completed_stages'].append('ad_discovery')
                self._save_state(context)
            else:
                console.print("[dim]Stage 1: Ad Discovery (skipped - already completed)[/dim]")
            
            # Stage 2: Prospect Finding (CRITICAL STAGE)
            if 'prospect' not in context.get('completed_stages', []):
                console.print("\n[bold]Stage 2: Prospect Finding (Critical)[/bold]")
                prospect_result = await self._run_with_retry(
                    self.prospect,
                    context
                )
                context.update(prospect_result)
                context['prospect_leads'] = list(context.get('leads') or [])
                context['completed_stages'].append('prospect')
                self._save_state(context)
                
                # Check if we found leads
                if not context.get('leads'):
                    console.print("[red]❌ No qualified leads found. Pipeline stopped.[/red]")
                    context["status"] = "completed"
                    context["error"] = "No qualified leads found."
                    self._save_state(context)
                    return context

                if until == "prospect":
                    context["status"] = "awaiting_verification"
                    self._save_state(context)
                    console.print(
                        "\n[yellow]Paused after Prospect.[/yellow] Open the dashboard, "
                        "verify each Ad Library page, mark Quiet leads, then Continue.\n"
                    )
                    return context
            else:
                console.print("[dim]Stage 2: Prospect Finding (skipped - already completed)[/dim]")

            context = self._apply_verified_leads(context)
            if context.get("status") == "awaiting_verification":
                self._save_state(context)
                return context

            # Stage 3: Contact Enrichment
            if 'contact_enrichment' not in context.get('completed_stages', []):
                console.print("\n[bold]Stage 3: Contact Enrichment[/bold]")
                enrichment_result = await self._run_with_retry(
                    self.contact_enrichment,
                    context
                )
                context.update(enrichment_result)
                context['completed_stages'].append('contact_enrichment')
                self._save_state(context)
            else:
                console.print("[dim]Stage 3: Contact Enrichment (skipped - already completed)[/dim]")
            
            # Stage 4: Creative Generation
            if 'creative' not in context.get('completed_stages', []):
                console.print("\n[bold]Stage 4: Video Generation[/bold]")
                creative_result = await self._run_with_retry(
                    self.creative,
                    context
                )
                context.update(creative_result)
                context['completed_stages'].append('creative')
                self._save_state(context)
            else:
                console.print("[dim]Stage 4: Video Generation (skipped - already completed)[/dim]")
            
            # Stage 5: Outreach (with approval gate)
            if 'outreach' not in context.get('completed_stages', []):
                console.print("\n[bold]Stage 5: Outreach (Human Approval Required)[/bold]")
                outreach_result = await self._run_with_retry(
                    self.outreach,
                    context
                )
                context.update(outreach_result)
                context['completed_stages'].append('outreach')
                self._save_state(context)
            else:
                console.print("[dim]Stage 5: Outreach (skipped - already completed)[/dim]")
            
            # Mark as complete
            context['status'] = 'completed'
            context['completed_at'] = datetime.now().isoformat()
            self._save_state(context)
            
            # Print summary
            self._print_summary(context)
            
            return context
            
        except Exception as e:
            self.logger.error(f"Pipeline failed: {e}", exc_info=True)
            context['status'] = 'failed'
            context['error'] = str(e)
            self._save_state(context)
            raise
    
    def _initialize_context(
        self,
        niche: Optional[str],
        regions: Optional[list[str]],
        resume: bool
    ) -> Dict[str, Any]:
        """Initialize or resume pipeline context."""
        if resume and self.state_file.exists():
            self.logger.info(f"Resuming from state file: {self.state_file}")
            with open(self.state_file, 'r') as f:
                context = json.load(f)
            return context
        
        # Fresh start
        context = {
            'niche': niche or config.market.niche,
            'regions': [str(r).upper() for r in (regions or config.market.regions)],
            'started_at': datetime.now().isoformat(),
            'status': 'running',
            'completed_stages': [],
        }
        
        return context
    
    async def _run_with_retry(
        self,
        agent,
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Run an agent with retry logic."""
        max_retries = config.orchestrator.max_retries
        backoff = config.orchestrator.retry_backoff_seconds
        
        for attempt in range(max_retries):
            try:
                result = await agent.run(context)
                return result
            except Exception as e:
                if attempt < max_retries - 1:
                    self.logger.warning(
                        f"{agent.name} failed (attempt {attempt + 1}/{max_retries}), "
                        f"retrying in {backoff}s: {e}"
                    )
                    await asyncio.sleep(backoff)
                    backoff *= 2  # Exponential backoff
                else:
                    self.logger.error(f"{agent.name} failed after {max_retries} attempts")
                    raise
    
    def _apply_verified_leads(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Keep only Quiet leads before people / video / outreach.

        A full run with no verify decisions keeps every scored lead.
        After a dashboard pause, enrichment does not start until at least
        one lead is marked Quiet.
        """
        pool = context.get("prospect_leads") or context.get("leads") or []
        quiet = [
            lead
            for lead in pool
            if (lead.get("verification") or {}).get("status") == "quiet"
        ]
        if quiet:
            context["leads"] = quiet
            if context.get("status") == "awaiting_verification":
                context["status"] = "running"
            return context

        decided = any(
            (lead.get("verification") or {}).get("status") in {"quiet", "has_ads", "skip"}
            for lead in pool
        )
        if decided or context.get("status") == "awaiting_verification":
            context["status"] = "awaiting_verification"
            console.print(
                "[yellow]No Quiet leads selected. Verify Ad Library pages "
                "in the dashboard, then Continue.[/yellow]"
            )
        return context

    def _save_state(self, context: Dict[str, Any]) -> None:
        """Save pipeline state to file."""
        try:
            with open(self.state_file, 'w') as f:
                json.dump(context, f, indent=2)
            self.logger.debug(f"State saved to {self.state_file}")
        except Exception as e:
            self.logger.error(f"Failed to save state: {e}")
    
    def _print_summary(self, context: Dict[str, Any]) -> None:
        """Print pipeline completion summary."""
        console.print("\n[bold green]✅ Pipeline Completed![/bold green]\n")
        
        console.print("[bold]Summary:[/bold]")
        console.print(f"  Niche: {context['niche']}")
        console.print(f"  Regions: {', '.join(context['regions'])}")
        console.print(f"  Started: {context['started_at']}")
        console.print(f"  Completed: {context['completed_at']}")
        
        if context.get('dominant_advertisers'):
            console.print(f"\n  Dominant Advertisers: {len(context['dominant_advertisers'])}")
        
        if context.get('leads'):
            console.print(f"  Qualified Leads: {len(context['leads'])}")
            console.print(f"  Average Lead Score: {context['qualification_summary']['average_score']:.3f}")
        
        if context.get('enriched_leads'):
            enrichment = context['enrichment_summary']
            console.print(f"  Enriched with Contacts: {enrichment['successfully_enriched']}/{enrichment['total_leads']}")
        
        if context.get('leads_with_videos'):
            generation = context['generation_summary']
            console.print(f"  Videos Generated: {generation['successful_generations']}/{generation['total_attempts']}")
        
        if context.get('send_summary'):
            send = context['send_summary']
            console.print(f"\n  Messages Drafted: {send['total_drafted']}")
            console.print(f"  Messages Approved: {send['approved_count']}")
            console.print(f"  Messages Sent: {send['sent_count']}")
        
        # Save detailed report
        report_file = DATA_DIR / f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(report_file, 'w') as f:
            json.dump(context, f, indent=2)
        
        console.print(f"\n[cyan]📄 Detailed report saved to: {report_file}[/cyan]")


async def main(
    niche: Optional[str] = None,
    regions: Optional[list[str]] = None,
    resume: bool = False,
    until: Optional[str] = None,
):
    """Main entry point for running the pipeline."""
    orchestrator = PipelineOrchestrator()
    result = await orchestrator.run_pipeline(niche, regions, resume, until)
    return result


if __name__ == "__main__":
    import sys
    
    # Simple CLI
    niche_arg = sys.argv[1] if len(sys.argv) > 1 else None
    
    asyncio.run(main(niche=niche_arg))
