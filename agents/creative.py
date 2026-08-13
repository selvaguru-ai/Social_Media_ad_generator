"""
Creative agent - Video generation using Higgsfield

Generates sample video ads for each lead using Higgsfield API.
"""
import asyncio
from typing import Any, Dict, Optional
from datetime import datetime
import time

import httpx

from agents.base import BaseAgent
from utils import config, settings


class CreativeAgent(BaseAgent):
    """
    Generates sample video ads using Higgsfield.
    
    Higgsfield provides AI-powered video generation.
    API access options:
    1. Higgsfield MCP (cleanest for agent integration)
    2. Higgsfield Cloud API (cloud.higgsfield.ai)
    3. Third-party aggregator
    
    Pattern: POST-then-poll with exponential backoff
    1. Submit generation job
    2. Poll status endpoint
    3. Download result when COMPLETED
    """
    
    def __init__(self):
        super().__init__("Creative")
        self.api_key = settings.higgsfield_api_key
        self.base_url = settings.higgsfield_base_url
        self.timeout = config.creative.generation_timeout_seconds
        self.poll_interval = config.creative.poll_interval_seconds
    
    async def run(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Generate sample videos for enriched leads.
        
        Args:
            context: Must contain 'enriched_leads'
        
        Returns:
            Dictionary with:
                - leads_with_videos: Leads with video URLs added
                - generation_summary: Stats on video generation
        """
        self.log_start()
        
        try:
            self.validate_context(context, ['enriched_leads'])
            leads = context['enriched_leads']
            
            # Filter leads with contacts only
            leads_with_contacts = [l for l in leads if l.get('contact')]
            
            # Check if API is configured
            if not self.api_key or self.api_key == "your_higgsfield_api_key_here":
                self.logger.warning("⚠️  Higgsfield API not configured. Using MOCK video URLs.")
                return self._mock_videos(leads_with_contacts)
            
            # Generate videos (with concurrency limit)
            leads_with_videos = await self._generate_videos_batch(leads_with_contacts)
            
            successful = sum(1 for l in leads_with_videos if l.get('video'))
            
            result = {
                'leads_with_videos': leads_with_videos,
                'generation_summary': {
                    'total_attempts': len(leads_with_contacts),
                    'successful_generations': successful,
                    'success_rate': successful / len(leads_with_contacts) if leads_with_contacts else 0,
                },
            }
            
            self.log_complete(f"Generated {successful}/{len(leads_with_contacts)} videos")
            return result
            
        except Exception as e:
            self.log_error(e)
            raise
    
    async def _generate_videos_batch(
        self,
        leads: list[Dict[str, Any]]
    ) -> list[Dict[str, Any]]:
        """Generate videos with concurrency control."""
        max_concurrent = config.creative.max_concurrent_generations
        
        semaphore = asyncio.Semaphore(max_concurrent)
        
        async def generate_with_semaphore(lead):
            async with semaphore:
                return await self._generate_video_for_lead(lead)
        
        # Create tasks
        tasks = [generate_with_semaphore(lead) for lead in leads]
        
        # Wait for all to complete
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Merge results back into leads
        for lead, result in zip(leads, results):
            if isinstance(result, Exception):
                self.logger.error(f"Failed to generate video for {lead['brand_name']}: {result}")
                lead['video'] = None
            else:
                lead['video'] = result
        
        return leads
    
    async def _generate_video_for_lead(self, lead: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Generate a video for a single lead.
        
        Returns:
            Dictionary with: url, prompt, generated_at
        """
        brand_name = lead['brand_name']
        niche = lead.get('industry', 'product')
        
        # Create prompt
        prompt = self._create_video_prompt(lead)
        
        self.logger.info(f"Generating video for {brand_name}")
        
        try:
            # Submit job
            job_id = await self._submit_generation_job(prompt)
            
            # Poll until complete
            video_url = await self._poll_until_complete(job_id)
            
            return {
                'url': video_url,
                'prompt': prompt,
                'generated_at': datetime.now().isoformat(),
            }
            
        except Exception as e:
            self.logger.error(f"Video generation failed for {brand_name}: {e}")
            return None
    
    def _create_video_prompt(self, lead: Dict[str, Any]) -> str:
        """Create a video generation prompt for the lead."""
        brand_name = lead['brand_name']
        industry = lead.get('industry', 'products')
        style = config.creative.default_style
        
        prompt = (
            f"Create a {config.creative.video_duration}-second promotional video for {brand_name}, "
            f"a brand in the {industry} industry. "
            f"Style: {style}. "
            f"Show the brand's products in an engaging way with dynamic visuals and text overlay."
        )
        
        return prompt
    
    async def _submit_generation_job(self, prompt: str) -> str:
        """
        Submit a video generation job to Higgsfield.
        
        Returns job_id for polling.
        """
        # TODO: Implement actual Higgsfield API integration
        # This is a placeholder structure
        
        async with httpx.AsyncClient() as client:
            headers = {
                'Authorization': f'Bearer {self.api_key}',
                'Content-Type': 'application/json',
            }
            
            payload = {
                'prompt': prompt,
                'duration': config.creative.video_duration,
                'format': config.creative.video_format,
                'resolution': config.creative.video_resolution,
            }
            
            response = await client.post(
                f"{self.base_url}/generate",
                headers=headers,
                json=payload,
                timeout=30.0,
            )
            
            response.raise_for_status()
            data = response.json()
            
            return data['job_id']
    
    async def _poll_until_complete(self, job_id: str) -> str:
        """
        Poll job status until complete.
        
        Returns the video URL when ready.
        """
        start_time = time.time()
        max_attempts = config.creative.max_poll_attempts
        
        async with httpx.AsyncClient() as client:
            headers = {
                'Authorization': f'Bearer {self.api_key}',
            }
            
            for attempt in range(max_attempts):
                # Check timeout
                if time.time() - start_time > self.timeout:
                    raise TimeoutError(f"Video generation timed out after {self.timeout}s")
                
                response = await client.get(
                    f"{self.base_url}/jobs/{job_id}",
                    headers=headers,
                    timeout=10.0,
                )
                
                response.raise_for_status()
                data = response.json()
                
                status = data['status']
                
                if status == 'COMPLETED':
                    return data['video_url']
                elif status == 'FAILED':
                    raise Exception(f"Generation failed: {data.get('error', 'Unknown error')}")
                
                # Still processing, wait before next poll
                await asyncio.sleep(self.poll_interval)
        
        raise TimeoutError(f"Video generation exceeded max polling attempts ({max_attempts})")
    
    def _mock_videos(self, leads: list[Dict[str, Any]]) -> Dict[str, Any]:
        """Generate mock video URLs for testing."""
        for lead in leads:
            prompt = self._create_video_prompt(lead)
            
            lead['video'] = {
                'url': f"https://mock-cdn.higgsfield.ai/videos/{lead['brand_name'].lower().replace(' ', '-')}-sample.mp4",
                'prompt': prompt,
                'generated_at': datetime.now().isoformat(),
                'is_mock': True,
            }
        
        successful = len(leads)
        
        return {
            'leads_with_videos': leads,
            'generation_summary': {
                'total_attempts': len(leads),
                'successful_generations': successful,
                'success_rate': 1.0,
            },
            'is_mock_data': True,
        }
