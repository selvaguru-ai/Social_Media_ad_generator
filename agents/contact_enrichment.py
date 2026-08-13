"""
Contact Enrichment agent

Finds brand/marketing managers and their contact information for each qualified lead.
"""
from typing import Any, Dict, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from agents.base import BaseAgent
from utils import config, settings


class ContactEnrichmentAgent(BaseAgent):
    """
    Enriches leads with contact information for brand/marketing managers.
    
    Data sources (in order of preference):
    1. Apollo.io - B2B contact database
    2. Hunter.io - Email finder
    3. Clearbit - Company enrichment
    """
    
    def __init__(self):
        super().__init__("ContactEnrichment")
        self.apollo_api_key = settings.apollo_api_key
        self.hunter_api_key = settings.hunter_api_key
        self.clearbit_api_key = settings.clearbit_api_key
    
    async def run(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Enrich leads with contact information.
        
        Args:
            context: Must contain 'leads' from Prospect agent
        
        Returns:
            Dictionary with:
                - enriched_leads: Leads with contact information added
                - enrichment_summary: Stats on successful enrichment
        """
        self.log_start()
        
        try:
            self.validate_context(context, ['leads'])
            leads = context['leads']
            
            # Check if enrichment APIs are configured
            if not self._has_enrichment_api():
                self.logger.warning("⚠️  No enrichment API configured. Using MOCK contact data.")
                return self._mock_enrichment(leads)
            
            enriched_leads = []
            successful = 0
            
            for lead in leads:
                contact_info = await self._find_contact(lead)
                
                if contact_info and contact_info.get('email'):
                    lead['contact'] = contact_info
                    enriched_leads.append(lead)
                    successful += 1
                else:
                    # Still include the lead, but mark as missing contact
                    lead['contact'] = None
                    enriched_leads.append(lead)
            
            result = {
                'enriched_leads': enriched_leads,
                'enrichment_summary': {
                    'total_leads': len(leads),
                    'successfully_enriched': successful,
                    'enrichment_rate': successful / len(leads) if leads else 0,
                },
            }
            
            self.log_complete(f"Enriched {successful}/{len(leads)} leads with contact info")
            return result
            
        except Exception as e:
            self.log_error(e)
            raise
    
    def _has_enrichment_api(self) -> bool:
        """Check if any enrichment API is configured."""
        return any([
            self.apollo_api_key and self.apollo_api_key != "your_apollo_api_key_here",
            self.hunter_api_key and self.hunter_api_key != "your_hunter_api_key_here",
            self.clearbit_api_key and self.clearbit_api_key != "your_clearbit_api_key_here",
        ])
    
    async def _find_contact(self, lead: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Find contact information for a lead.
        
        Returns contact dict with: name, title, email, linkedin, confidence
        """
        # Try Apollo first (best for B2B contacts)
        if self.apollo_api_key and self.apollo_api_key != "your_apollo_api_key_here":
            contact = await self._find_contact_apollo(lead)
            if contact:
                return contact
        
        # Try Hunter.io (good for email finding)
        if self.hunter_api_key and self.hunter_api_key != "your_hunter_api_key_here":
            contact = await self._find_contact_hunter(lead)
            if contact:
                return contact
        
        return None
    
    async def _find_contact_apollo(self, lead: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Find contact using Apollo API."""
        # TODO: Implement Apollo people search
        # Docs: https://apolloio.github.io/apollo-api-docs/?shell#search-for-people
        return None
    
    async def _find_contact_hunter(self, lead: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Find contact using Hunter.io API."""
        # TODO: Implement Hunter.io domain search
        # Docs: https://hunter.io/api-documentation/v2
        return None
    
    def _mock_enrichment(self, leads: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Generate mock contact data for testing."""
        import random
        
        job_titles = config.contact_enrichment.target_roles
        
        enriched_leads = []
        successful = 0
        
        for lead in leads:
            # 80% success rate for mock data
            if random.random() < 0.8:
                brand_name = lead['brand_name']
                first_name = random.choice(['Sarah', 'Michael', 'Jessica', 'David', 'Emily'])
                last_name = random.choice(['Smith', 'Johnson', 'Williams', 'Brown', 'Jones'])
                
                contact = {
                    'name': f"{first_name} {last_name}",
                    'title': random.choice(job_titles),
                    'email': f"{first_name.lower()}.{last_name.lower()}@{brand_name.lower().replace(' ', '')}.com",
                    'linkedin': f"https://linkedin.com/in/{first_name.lower()}-{last_name.lower()}",
                    'confidence': random.uniform(0.7, 0.95),
                    'source': 'mock',
                }
                lead['contact'] = contact
                successful += 1
            else:
                lead['contact'] = None
            
            enriched_leads.append(lead)
        
        return {
            'enriched_leads': enriched_leads,
            'enrichment_summary': {
                'total_leads': len(leads),
                'successfully_enriched': successful,
                'enrichment_rate': successful / len(leads) if leads else 0,
            },
            'is_mock_data': True,
        }
