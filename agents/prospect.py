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
import json
import re
from typing import Any, Dict, List, Optional
from datetime import datetime

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from agents.base import BaseAgent
from utils import config, settings
from utils.scoring import qualify_leads

# Commercial-ad fields that the Ad Library actually returns without extra
# permissions. spend and impressions are usually ABSENT for non-EU commercial
# ads; they are requested only so EU/DSA responses can be used when present.
COMMERCIAL_AD_FIELDS = (
    "id,page_id,page_name,ad_delivery_start_time,ad_delivery_stop_time,"
    "ad_snapshot_url,publisher_platforms,impressions,spend"
)

_PLACEHOLDER_TOKENS = {
    None,
    "",
    "your_meta_access_token_here",
}

_COMPANY_SUFFIXES = re.compile(
    r"\b(inc|incorporated|llc|ltd|limited|co|corp|corporation|company|"
    r"studio|lab|labs|collective|house|group|brands?)\b",
    re.IGNORECASE,
)


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

    def _meta_api_configured(self) -> bool:
        """True when a real (non-placeholder) Meta access token is set."""
        token = (self.meta_access_token or "").strip()
        return token not in _PLACEHOLDER_TOKENS and not token.startswith("your_")
    
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
        - Estimated spend (if available — usually not, for commercial ads)
        - Impression counts (if available — usually not, for commercial ads)
        """
        analyzed = []
        use_real_api = self._meta_api_configured()
        
        if use_real_api:
            self.logger.info(
                "Using REAL Meta Ad Library API for ad-presence checks "
                "(META_ACCESS_TOKEN is set)"
            )
        else:
            self.logger.warning(
                "⚠️  Meta API not configured (META_ACCESS_TOKEN missing or placeholder). "
                "Using MOCK ad presence data."
            )
        
        for i, brand in enumerate(brands):
            self.logger.debug(
                f"Analyzing ad presence for {brand['name']} ({i+1}/{len(brands)})"
            )
            
            if use_real_api:
                ad_data = await self._check_meta_ad_library(
                    brand['name'],
                    regions,
                    brand_domain=brand.get('domain')
                )
            else:
                import random
                ad_data = {
                    'active_ads_count': random.randint(0, 3),
                    'estimated_spend': random.randint(0, 1000),
                    'match_confidence': random.choice([1.0, 1.0, 0.7]),
                    'has_low_presence': random.choice([True, True, True, False]),
                    'source': 'mock',
                    'spend_present': True,
                    'impressions_present': True,
                    'page_name': None,
                }
                self.logger.info(
                    f"[MOCK] {brand['name']}: {ad_data['active_ads_count']} ads "
                    f"(synthetic data — not from Meta)"
                )
            
            brand['ad_presence'] = ad_data
            analyzed.append(brand)
            
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
        3. Fall back to search_terms (fuzzy) if no Page found
        4. Re-score match_confidence from returned page_name vs brand name
        
        Note: spend and impressions are usually NULL for commercial (non-EU)
        ads. Presence is judged on active-ad COUNT, not spend. Advertiser
        identity comes from page_name.
        
        The default ads_archive response is nearly empty without an explicit
        `fields` parameter — always request COMMERCIAL_AD_FIELDS.
        """
        try:
            page_id = await self._resolve_facebook_page_id(brand_name, brand_domain)
            
            params = {
                'access_token': self.meta_access_token,
                'ad_active_status': 'ACTIVE',
                'limit': 100,
                # Explicit fields are required; the default payload is nearly empty.
                'fields': COMMERCIAL_AD_FIELDS,
            }
            if regions:
                # ads_archive requires ad_reached_countries.
                params['ad_reached_countries'] = json.dumps(regions)
            
            lookup = 'search_page_ids'
            if page_id:
                params['search_page_ids'] = json.dumps([str(page_id)])
                self.logger.debug(
                    f"Using exact Page ID match for {brand_name}: {page_id}"
                )
            else:
                params['search_terms'] = brand_name
                lookup = 'search_terms'
                self.logger.debug(
                    f"Using fuzzy text search for {brand_name} (no Page ID found)"
                )
            
            session = self._get_session()
            response = session.get(f"{self.meta_api_base}/ads_archive", params=params)
            response.raise_for_status()
            
            data = response.json()
            ads = data.get('data') or []
            
            total_spend, spend_present = self._sum_optional_range(ads, 'spend')
            total_impressions, impressions_present = self._sum_optional_range(
                ads, 'impressions'
            )
            
            page_names = [
                ad.get('page_name') for ad in ads if ad.get('page_name')
            ]
            unique_page_names = sorted(set(page_names))
            primary_page_name = unique_page_names[0] if unique_page_names else None
            
            match_confidence = self._match_confidence_from_page_names(
                brand_name,
                unique_page_names,
                used_page_id=bool(page_id),
            )
            
            # Presence is ad-count based. Do not treat missing spend as $0 spend.
            has_low_presence = len(ads) < 5
            if spend_present:
                has_low_presence = (
                    has_low_presence
                    and total_spend < config.prospect.max_ad_spend_threshold
                )
            
            self.logger.info(
                f"[REAL Meta API] {brand_name}: {len(ads)} ads | "
                f"page_name={'present (' + ', '.join(unique_page_names[:3]) + ')' if unique_page_names else 'absent'} | "
                f"spend={'present' if spend_present else 'absent'} | "
                f"impressions={'present' if impressions_present else 'absent'} | "
                f"lookup={lookup} | "
                f"match_confidence={match_confidence:.2f}"
            )
            
            return {
                'active_ads_count': len(ads),
                'estimated_spend': total_spend if spend_present else 0,
                'total_impressions': total_impressions if impressions_present else 0,
                'spend_present': spend_present,
                'impressions_present': impressions_present,
                'page_name': primary_page_name,
                'page_names': unique_page_names,
                'page_id': page_id,
                'match_confidence': match_confidence,
                'has_low_presence': has_low_presence,
                'source': 'meta_api',
            }
            
        except Exception as e:
            self.logger.error(f"[REAL Meta API] Error checking ad library for {brand_name}: {e}")
            return {
                'active_ads_count': 0,
                'estimated_spend': 0,
                'total_impressions': 0,
                'spend_present': False,
                'impressions_present': False,
                'page_name': None,
                'match_confidence': 1.0,
                'has_low_presence': True,
                'source': 'meta_api_error',
                'error': str(e),
            }
    
    @staticmethod
    def _sum_optional_range(ads: List[Dict[str, Any]], field: str) -> tuple:
        """
        Sum a Meta range field like spend/impressions without crashing when
        the field is missing, null, or not a dict (typical for commercial ads).
        
        Returns (total, was_present).
        """
        total = 0
        present = False
        for ad in ads:
            value = ad.get(field)
            if value in (None, "", {}, []):
                continue
            present = True
            if isinstance(value, dict):
                total += int(value.get('lower_bound') or 0)
            elif isinstance(value, (int, float)):
                total += int(value)
        return total, present
    
    @staticmethod
    def _normalize_brand_name(name: str) -> str:
        """Lowercase, strip punctuation and common company suffixes."""
        if not name:
            return ""
        cleaned = name.lower()
        cleaned = re.sub(r"https?://(www\.)?", "", cleaned)
        cleaned = re.sub(r"[^a-z0-9\s]", " ", cleaned)
        cleaned = _COMPANY_SUFFIXES.sub("", cleaned)
        return " ".join(cleaned.split())
    
    def _name_similarity(self, brand_name: str, page_name: str) -> float:
        """Cheap token-overlap similarity in [0, 1] for brand vs page_name."""
        a = self._normalize_brand_name(brand_name)
        b = self._normalize_brand_name(page_name)
        if not a or not b:
            return 0.0
        if a == b:
            return 1.0
        if a in b or b in a:
            return 0.9
        a_tokens = set(a.split())
        b_tokens = set(b.split())
        if not a_tokens or not b_tokens:
            return 0.0
        overlap = len(a_tokens & b_tokens)
        return overlap / max(len(a_tokens), len(b_tokens))
    
    def _match_confidence_from_page_names(
        self,
        brand_name: str,
        page_names: List[str],
        used_page_id: bool,
    ) -> float:
        """
        Use returned page_name to strengthen (or penalize) the brand match.
        
        High confidence when page_name closely matches the target brand.
        Below 1.0 when it doesn't, so scoring down-weights the lead.
        """
        if not page_names:
            # No ads (or no page_name on the payload). Page-ID lookup is still
            # a reasonably exact empty-footprint signal; text search is not.
            return 0.9 if used_page_id else 0.6
        
        best = max(self._name_similarity(brand_name, pn) for pn in page_names)
        if best >= 0.85:
            return 1.0
        if best >= 0.6:
            return 0.85
        if used_page_id:
            return 0.7
        return 0.5
    
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
