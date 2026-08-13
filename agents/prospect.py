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
            
            # Score and qualify leads
            leads = self._qualify_leads(
                analyzed_brands,
                dominant_advertisers=dominant_advertisers
            )
            
            # Sort by qualification score
            leads.sort(key=lambda x: x['qualification_score'], reverse=True)
            
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
                ad_data = await self._check_meta_ad_library(brand['name'], regions)
            else:
                # Mock: randomly assign low ad presence
                import random
                ad_data = {
                    'active_ads_count': random.randint(0, 3),
                    'estimated_spend': random.randint(0, 1000),
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
        regions: List[str]
    ) -> Dict[str, Any]:
        """
        Query Meta Ad Library for a specific brand.
        
        Returns summary of their ad presence.
        """
        try:
            params = {
                'access_token': self.meta_access_token,
                'search_terms': brand_name,
                'ad_active_status': 'ACTIVE',
                'limit': 100,
                'fields': 'id,impressions,spend',
            }
            
            session = self._get_session()
            response = session.get(f"{self.meta_api_base}/ads_archive", params=params)
            response.raise_for_status()
            
            data = response.json()
            ads = data.get('data', [])
            
            # Calculate totals
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
                'has_low_presence': True,
                'error': str(e),
            }
    
    def _qualify_leads(
        self,
        brands: List[Dict[str, Any]],
        dominant_advertisers: List[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """
        Score and qualify brands as leads.
        
        Scoring criteria (from config.prospect.qualification_weights):
        1. competitor_presence: Market has active advertisers (good signal)
        2. brand_maturity: Company is established but not too large
        3. low_ad_presence: Brand is not advertising (the key qualifier)
        """
        leads = []
        weights = config.prospect.qualification_weights
        
        # Market competitiveness signal
        market_has_competitors = bool(dominant_advertisers and len(dominant_advertisers) > 0)
        
        for brand in brands:
            ad_presence = brand['ad_presence']
            
            # Only consider brands with low ad presence
            if not ad_presence.get('has_low_presence', False):
                continue
            
            # Calculate score components
            scores = {}
            
            # 1. Competitor presence score (0-1)
            scores['competitor_presence'] = 1.0 if market_has_competitors else 0.5
            
            # 2. Brand maturity score (0-1)
            size = brand.get('size', 0)
            if size >= config.prospect.min_company_size and size <= config.prospect.max_company_size:
                scores['brand_maturity'] = 1.0
            elif size < config.prospect.min_company_size:
                scores['brand_maturity'] = 0.3  # Too small
            else:
                scores['brand_maturity'] = 0.5  # Too large
            
            # 3. Low ad presence score (0-1)
            ad_count = ad_presence.get('active_ads_count', 0)
            ad_spend = ad_presence.get('estimated_spend', 0)
            
            if ad_count == 0:
                scores['low_ad_presence'] = 1.0  # No ads at all
            elif ad_count <= 3 and ad_spend < 1000:
                scores['low_ad_presence'] = 0.8  # Very few ads
            else:
                scores['low_ad_presence'] = 0.5  # Some ads but below threshold
            
            # Calculate weighted total
            total_score = sum(
                scores[key] * weights.get(key, 0)
                for key in scores
            )
            
            # Build qualification reason
            reasons = []
            if scores['competitor_presence'] > 0.7:
                reasons.append("active market with competitors")
            if scores['brand_maturity'] > 0.7:
                reasons.append("good company size")
            if scores['low_ad_presence'] > 0.7:
                reasons.append("minimal ad presence")
            
            lead = {
                'brand_name': brand['name'],
                'domain': brand.get('domain'),
                'company_size': brand.get('size'),
                'industry': brand.get('industry'),
                'region': brand.get('region'),
                'ad_presence_summary': {
                    'active_ads': ad_count,
                    'estimated_spend': ad_spend,
                },
                'qualification_score': round(total_score, 3),
                'score_breakdown': scores,
                'qualification_reason': ', '.join(reasons),
            }
            
            leads.append(lead)
        
        return leads
    
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
