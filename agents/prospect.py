"""
Prospect agent - THE CRITICAL COMPONENT

Finds low-spend brands in the target niche (the actual sales leads).
This is the "inverted" problem: finding brands that are NOT advertising heavily.

Approach:
1. Get a list of brands/companies in the niche (from business databases)
2. Cross-reference against Meta Ad Library to check their ad presence
3. Surface brands with low/no ad footprint but strong business fundamentals
"""
import asyncio
from typing import Any, Dict, List, Optional
from datetime import datetime

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from agents.base import BaseAgent
from utils import config, settings
from utils.scoring import qualify_leads


class ProspectAgent(BaseAgent):
    """
    Finds low-spend brands that are good sales leads.
    
    This is the RISKIEST component - it must be proven to work before building
    the rest of the pipeline.
    
    Strategy:
    1. Get brands in the niche from:
       - Apollo (company database with revenue, size, industry)
       - Crunchbase (startups and growth companies)
       - Or a manual/custom brand list
    2. For each brand, check Meta Ad Library for ad presence
    3. Score and rank brands based on:
       - Competitor ad presence (high = good market)
       - Brand maturity (not too small, not too large)
       - Low ad footprint (the key qualifier)
    """
    
    def __init__(self):
        super().__init__("Prospect")
        self.apollo_api_key = settings.apollo_api_key
        self.meta_access_token = settings.meta_access_token
        self.meta_api_base = "https://graph.facebook.com/v18.0"
    
    async def run(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Find low-spend brand leads.
        
        Args:
            context: Must contain:
                - niche: Target industry/niche
                - regions: Target regions
                - dominant_advertisers: (optional) From Ad Discovery agent
        
        Returns:
            Dictionary with:
                - leads: List of qualified lead records with scores
                - total_brands_analyzed: Total brands checked
                - qualification_summary: Stats on the lead generation
        """
        self.log_start()
        
        try:
            niche = context.get('niche') or config.market.niche
            regions = context.get('regions') or config.market.regions
            dominant_advertisers = context.get('dominant_advertisers', [])
            
            # Get list of brands in the niche
            brands = await self._get_brands_in_niche(niche, regions)
            
            self.logger.info(f"Found {len(brands)} brands to analyze")
            
            # Check ad presence for each brand
            analyzed_brands = await self._analyze_ad_presence(brands, niche, regions)
            
            # Score and qualify leads using the new scoring module
            leads = qualify_leads(
                brands=analyzed_brands,
                dominant_advertisers=dominant_advertisers,
                active_ads=context.get('active_ads', []),
                weights=config.prospect.qualification_weights,
                ideal_min_size=config.prospect.min_company_size,
                ideal_max_size=config.prospect.max_company_size,
                spend_is_reliable=False,  # Meta commercial ads usually don't report spend
                spend_threshold=config.prospect.max_ad_spend_threshold,
                gate_min_presence=0.6,  # Filter out brands with >~3 active ads
            )
            
            # Limit to max_leads
            leads = leads[:config.prospect.max_leads]
            
            result = {
                'leads': leads,
                'total_brands_analyzed': len(analyzed_brands),
                'qualification_summary': {
                    'total_qualified': len(leads),
                    'average_score': sum(l['qualification_score'] for l in leads) / len(leads) if leads else 0,
                    'top_score': leads[0]['qualification_score'] if leads else 0,
                },
                'analyzed_at': datetime.now().isoformat(),
            }
            
            self.log_complete(f"Identified {len(leads)} qualified leads")
            return result
            
        except Exception as e:
            self.log_error(e)
            raise
    
    async def _get_brands_in_niche(
        self,
        niche: str,
        regions: List[str]
    ) -> List[Dict[str, Any]]:
        """
        Get a list of brands/companies in the target niche.
        
        Data sources (in order of preference):
        1. Apollo API (if configured)
        2. Crunchbase API (if configured)
        3. Mock data (for testing)
        """
        # Try Apollo first
        if self.apollo_api_key and self.apollo_api_key != "your_apollo_api_key_here":
            self.logger.info("Fetching brands from Apollo API")
            return await self._fetch_from_apollo(niche, regions)
        
        # Try Crunchbase
        if settings.crunchbase_api_key and settings.crunchbase_api_key != "your_crunchbase_key_here":
            self.logger.info("Fetching brands from Crunchbase API")
            return await self._fetch_from_crunchbase(niche, regions)
        
        # Fall back to mock data
        self.logger.warning("⚠️  No business data API configured. Using MOCK brand data.")
        return self._mock_brands(niche, regions)
    
    async def _fetch_from_apollo(
        self,
        niche: str,
        regions: List[str]
    ) -> List[Dict[str, Any]]:
        """
        Fetch companies from Apollo.io API.
        
        Docs: https://apolloio.github.io/apollo-api-docs/
        """
        brands = []
        
        headers = {
            'Content-Type': 'application/json',
            'Cache-Control': 'no-cache',
        }
        
        for region in regions:
            payload = {
                'api_key': self.apollo_api_key,
                'q_organization_keyword_tags': [niche],
                'page': 1,
                'per_page': 50,
                'organization_locations': [region],
                'organization_num_employees_ranges': [
                    f"{config.prospect.min_company_size}-{config.prospect.max_company_size}"
                ],
            }
            
            try:
                session = self._get_session()
                response = session.post(
                    'https://api.apollo.io/v1/mixed_companies/search',
                    headers=headers,
                    json=payload
                )
                response.raise_for_status()
                
                data = response.json()
                companies = data.get('organizations', [])
                
                for company in companies:
                    brands.append({
                        'name': company.get('name'),
                        'domain': company.get('website_url'),
                        'size': company.get('estimated_num_employees'),
                        'industry': company.get('industry'),
                        'region': region,
                        'source': 'apollo',
                    })
                
                self.logger.info(f"Fetched {len(companies)} companies from Apollo for {region}")
                
            except requests.exceptions.RequestException as e:
                self.logger.error(f"Apollo API error for {region}: {e}")
                continue
        
        return brands
    
    async def _fetch_from_crunchbase(
        self,
        niche: str,
        regions: List[str]
    ) -> List[Dict[str, Any]]:
        """
        Fetch companies from Crunchbase API.
        
        Docs: https://data.crunchbase.com/docs/
        """
        # Placeholder - implement if Crunchbase is primary source
        self.logger.warning("Crunchbase integration not yet implemented")
        return self._mock_brands(niche, regions)
    
    async def _analyze_ad_presence(
        self,
        brands: List[Dict[str, Any]],
        niche: str,
        regions: List[str]
    ) -> List[Dict[str, Any]]:
        """
        Check Meta Ad Library for each brand's ad presence.
        
        For each brand, query the Ad Library to see:
        - How many active ads they have
        - Estimated spend (if available)
        - Impression counts
        """
        analyzed = []
        
        # Check if Meta API is configured
        use_real_api = (
            self.meta_access_token and 
            self.meta_access_token != "your_meta_access_token_here"
        )
        
        if not use_real_api:
            self.logger.warning("⚠️  Meta API not configured. Using MOCK ad presence data.")
        
        for i, brand in enumerate(brands):
            self.logger.debug(f"Analyzing ad presence for {brand['name']} ({i+1}/{len(brands)})")
            
            if use_real_api:
                ad_data = await self._check_meta_ad_library(
                    brand['name'], 
                    regions,
                    brand_domain=brand.get('domain')
                )
            else:
                # Mock: randomly assign low ad presence
                import random
                ad_data = {
                    'active_ads_count': random.randint(0, 3),
                    'estimated_spend': random.randint(0, 1000),
                    'match_confidence': random.choice([1.0, 1.0, 0.7]),  # Vary confidence
                    'has_low_presence': random.choice([True, True, True, False]),  # 75% low presence
                }
            
            brand['ad_presence'] = ad_data
            analyzed.append(brand)
            
            # Rate limiting for real API
            if use_real_api:
                await asyncio.sleep(3600 / config.ad_discovery.max_requests_per_hour)
        
        return analyzed
    
    async def _check_meta_ad_library(
        self,
        brand_name: str,
        regions: List[str],
        brand_domain: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Query Meta Ad Library for a specific brand.
        
        Strategy:
        1. First try to resolve the brand's Facebook Page ID (exact match)
        2. Query Ad Library with search_page_ids for precise footprint
        3. Fall back to search_terms (fuzzy, unreliable) if no Page found
        
        Note: spend and impressions are usually NULL for commercial ads
        (only populated for EU/DSA regulated ads and political/issue ads).
        
        Returns summary of their ad presence with match_confidence.
        """
        try:
            # Step 1: Try to resolve Facebook Page ID for exact matching
            page_id = await self._resolve_facebook_page_id(brand_name, brand_domain)
            
            params = {
                'access_token': self.meta_access_token,
                'ad_active_status': 'ACTIVE',
                'limit': 100,
                'fields': 'id,impressions,spend',
            }
            
            match_confidence = 1.0
            
            if page_id:
                # Exact match via Page ID
                params['search_page_ids'] = page_id
                self.logger.debug(f"Using exact Page ID match for {brand_name}: {page_id}")
            else:
                # Fall back to fuzzy text search (less reliable)
                params['search_terms'] = brand_name
                match_confidence = 0.7  # Penalize fuzzy matches in scoring
                self.logger.debug(f"Using fuzzy text search for {brand_name} (no Page ID found)")
            
            session = self._get_session()
            response = session.get(f"{self.meta_api_base}/ads_archive", params=params)
            response.raise_for_status()
            
            data = response.json()
            ads = data.get('data', [])
            
            # Calculate totals (note: usually null for commercial ads)
            total_spend = 0
            total_impressions = 0
            
            for ad in ads:
                spend = ad.get('spend', {})
                if isinstance(spend, dict):
                    total_spend += spend.get('lower_bound', 0)
                
                impressions = ad.get('impressions', {})
                if isinstance(impressions, dict):
                    total_impressions += impressions.get('lower_bound', 0)
            
            return {
                'active_ads_count': len(ads),
                'estimated_spend': total_spend,
                'total_impressions': total_impressions,
                'match_confidence': match_confidence,
                'has_low_presence': (
                    len(ads) < 5 and 
                    total_spend < config.prospect.max_ad_spend_threshold
                ),
            }
            
        except Exception as e:
            self.logger.error(f"Error checking ad library for {brand_name}: {e}")
            return {
                'active_ads_count': 0,
                'estimated_spend': 0,
                'total_impressions': 0,
                'match_confidence': 1.0,
                'has_low_presence': True,
                'error': str(e),
            }
    
    async def _resolve_facebook_page_id(
        self,
        brand_name: str,
        brand_domain: Optional[str] = None
    ) -> Optional[str]:
        """
        Resolve a brand's Facebook Page ID for exact ad matching.
        
        Tries multiple strategies:
        1. Search by brand name via Pages Search API
        2. Look for Facebook URL in brand domain
        
        Returns Page ID if found, None otherwise.
        """
        try:
            # Strategy 1: Use Facebook Pages Search API
            params = {
                'access_token': self.meta_access_token,
                'q': brand_name,
                'type': 'page',
                'fields': 'id,name,link,verification_status',
                'limit': 5,
            }
            
            session = self._get_session()
            response = session.get(f"{self.meta_api_base}/search", params=params)
            response.raise_for_status()
            
            data = response.json()
            pages = data.get('data', [])
            
            # Look for exact or close name match
            for page in pages:
                page_name = page.get('name', '').lower()
                if brand_name.lower() in page_name or page_name in brand_name.lower():
                    # Prefer verified pages
                    if page.get('verification_status') == 'blue_verified':
                        return page['id']
                    # Otherwise take first match
                    if not pages[0].get('id'):
                        continue
                    return page['id']
            
            # If we got any results, return the first one
            if pages:
                return pages[0].get('id')
            
        except Exception as e:
            self.logger.debug(f"Could not resolve Facebook Page ID for {brand_name}: {e}")
        
        return None
    
    def _get_session(self) -> requests.Session:
        """Create a requests session with retry logic."""
        session = requests.Session()
        retry_strategy = Retry(
            total=3,
            backoff_factor=2,
            status_forcelist=[429, 500, 502, 503, 504],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        return session
    
    def _mock_brands(self, niche: str, regions: List[str]) -> List[Dict[str, Any]]:
        """
        Generate mock brand data for testing.
        
        This allows testing the prospect logic without real API access.
        """
        import random
        
        mock_brands = []
        brand_names = [
            f"{niche.title()} Co",
            f"Green {niche.title()}",
            f"{niche.title()} Studio",
            f"Modern {niche.title()}",
            f"{niche.title()} Collective",
            f"Eco {niche.title()}",
            f"{niche.title()} Lab",
            f"Urban {niche.title()}",
            f"{niche.title()} House",
            f"Pure {niche.title()}",
        ]
        
        for i, name in enumerate(brand_names):
            mock_brands.append({
                'name': name,
                'domain': f"https://{name.lower().replace(' ', '')}.com",
                'size': random.randint(15, 200),
                'industry': niche,
                'region': random.choice(regions) if regions else 'US',
                'source': 'mock',
            })
        
        return mock_brands
