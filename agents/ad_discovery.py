"""
Ad Discovery / Market agent

Discovers what ads are currently running in the target niche using the Meta Ad Library API.
Identifies dominant advertisers and recurring ad patterns.
"""
import asyncio
from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta

import aiohttp
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from agents.base import BaseAgent
from utils import config, settings

# Same commercial-ad field set as the Prospect agent. spend/impressions are
# usually absent for non-EU commercial ads; page_name is the advertiser identity.
COMMERCIAL_AD_FIELDS = (
    "id,page_id,page_name,ad_delivery_start_time,ad_delivery_stop_time,"
    "ad_snapshot_url,publisher_platforms,ad_creative_bodies,"
    "ad_creative_link_titles,impressions,spend"
)


class AdDiscoveryAgent(BaseAgent):
    """
    Discovers active ads in target niche using Meta Ad Library.
    
    The Meta Ad Library API provides:
    - Currently active ads (limited historical data for non-political ads)
    - Ad creative, copy, format
    - Impression counts and spend data (varies by region/category)
    
    Rate limit: ~200 calls/hour per app (standard access)
    """
    
    def __init__(self):
        super().__init__("AdDiscovery")
        self.api_base = "https://graph.facebook.com/v18.0"
        self.access_token = settings.meta_access_token
        self.rate_limit_delay = 3600 / config.ad_discovery.max_requests_per_hour
    
    async def run(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Discover active ads in the target niche.
        
        Args:
            context: Must contain 'niche' and 'regions'
        
        Returns:
            Dictionary with:
                - active_ads: List of ad records
                - dominant_advertisers: List of top advertisers by volume
                - ad_patterns: Summary of common themes/formats
        """
        self.log_start()
        
        try:
            niche = context.get('niche') or config.market.niche
            regions = context.get('regions') or config.market.regions
            
            # Check if API credentials are available
            token = (self.access_token or "").strip()
            if not token or token.startswith("your_"):
                self.logger.warning(
                    "⚠️  Meta API credentials not configured (META_ACCESS_TOKEN "
                    "missing or placeholder). Using MOCK data."
                )
                return self._mock_response(niche, regions)
            
            self.logger.info(
                "Using REAL Meta Ad Library API for market discovery "
                "(META_ACCESS_TOKEN is set)"
            )
            
            # Fetch ads from Meta Ad Library
            ads = await self._fetch_ads(niche, regions)
            
            # Analyze dominant advertisers
            dominant_advertisers = self._identify_dominant_advertisers(ads)
            
            # Extract ad patterns
            ad_patterns = self._extract_ad_patterns(ads)
            
            result = {
                'active_ads': ads,
                'dominant_advertisers': dominant_advertisers,
                'ad_patterns': ad_patterns,
                'discovered_at': datetime.now().isoformat(),
            }
            
            self.log_complete(f"Found {len(ads)} active ads from {len(dominant_advertisers)} advertisers")
            return result
            
        except Exception as e:
            self.log_error(e)
            raise
    
    async def _fetch_ads(self, niche: str, regions: List[str]) -> List[Dict[str, Any]]:
        """
        Fetch ads from Meta Ad Library API.
        
        API endpoint: /ads_archive
        Docs: https://developers.facebook.com/docs/marketing-api/reference/ads-archive/
        """
        all_ads = []
        
        for region in regions:
            self.logger.info(f"Fetching ads for '{niche}' in {region}")
            
            params = {
                'access_token': self.access_token,
                'search_terms': niche,
                'ad_reached_countries': region,
                'ad_active_status': 'ACTIVE',
                'limit': config.ad_discovery.ads_per_query,
                # Explicit fields are required; the default payload is nearly empty.
                'fields': COMMERCIAL_AD_FIELDS,
            }
            
            try:
                # Use requests with retry logic
                session = self._get_session()
                response = session.get(f"{self.api_base}/ads_archive", params=params)
                response.raise_for_status()
                
                data = response.json()
                ads = data.get('data', [])
                
                # Enrich with region
                for ad in ads:
                    ad['target_region'] = region
                    ad['niche'] = niche
                
                all_ads.extend(ads)
                
                with_page = sum(1 for ad in ads if ad.get('page_name'))
                with_spend = sum(1 for ad in ads if ad.get('spend'))
                with_impressions = sum(1 for ad in ads if ad.get('impressions'))
                self.logger.info(
                    f"[REAL Meta API] '{niche}' in {region}: {len(ads)} ads | "
                    f"page_name on {with_page}/{len(ads)} | "
                    f"spend on {with_spend}/{len(ads)} | "
                    f"impressions on {with_impressions}/{len(ads)}"
                )
                
                # Rate limiting
                await asyncio.sleep(self.rate_limit_delay)
                
            except requests.exceptions.RequestException as e:
                self.logger.error(f"Failed to fetch ads for {region}: {e}")
                continue
        
        return all_ads
    
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
    
    def _identify_dominant_advertisers(self, ads: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Identify advertisers with the most active ads."""
        advertiser_counts = {}
        
        for ad in ads:
            page_name = ad.get('page_name') or 'Unknown'
            if page_name not in advertiser_counts:
                advertiser_counts[page_name] = {
                    'name': page_name,
                    'ad_count': 0,
                    'total_impressions': 0,
                    'estimated_spend': 0,
                }
            
            advertiser_counts[page_name]['ad_count'] += 1
            
            # spend/impressions are usually absent for commercial ads — never
            # assume a missing field is a dict.
            impressions = ad.get('impressions')
            if isinstance(impressions, dict):
                advertiser_counts[page_name]['total_impressions'] += impressions.get('lower_bound') or 0
            elif isinstance(impressions, (int, float)):
                advertiser_counts[page_name]['total_impressions'] += impressions
            
            spend = ad.get('spend')
            if isinstance(spend, dict):
                advertiser_counts[page_name]['estimated_spend'] += spend.get('lower_bound') or 0
            elif isinstance(spend, (int, float)):
                advertiser_counts[page_name]['estimated_spend'] += spend
        
        # Sort by ad count
        sorted_advertisers = sorted(
            advertiser_counts.values(),
            key=lambda x: x['ad_count'],
            reverse=True
        )
        
        return sorted_advertisers[:20]  # Top 20
    
    def _extract_ad_patterns(self, ads: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Extract common themes and patterns from ads."""
        patterns = {
            'total_ads': len(ads),
            'common_hooks': [],
            'formats': {},
            'average_impressions': 0,
        }
        
        # Analyze ad copy for common hooks (simple keyword extraction)
        all_bodies = []
        for ad in ads:
            bodies = ad.get('ad_creative_bodies', [])
            if bodies:
                all_bodies.extend(bodies)
        
        # Count word frequency (simple pattern detection)
        # In production, use NLP for better pattern extraction
        patterns['sample_copy'] = all_bodies[:5] if all_bodies else []
        
        return patterns
    
    def _mock_response(self, niche: str, regions: List[str]) -> Dict[str, Any]:
        """
        Return mock data when API credentials are not configured.
        
        This allows testing the pipeline without real API access.
        """
        self.logger.info("📝 Generating MOCK ad discovery data")
        
        mock_ads = [
            {
                'id': 'mock_ad_001',
                'page_name': f'{niche.title()} Brand A',
                'ad_creative_bodies': [f'Discover the best {niche} products'],
                'ad_delivery_start_time': (datetime.now() - timedelta(days=30)).isoformat(),
                'impressions': {'lower_bound': 50000, 'upper_bound': 100000},
                'spend': {'lower_bound': 5000, 'upper_bound': 8000},
                'target_region': regions[0] if regions else 'US',
                'niche': niche,
            },
            {
                'id': 'mock_ad_002',
                'page_name': f'{niche.title()} Brand B',
                'ad_creative_bodies': [f'Premium {niche} - Shop Now'],
                'ad_delivery_start_time': (datetime.now() - timedelta(days=15)).isoformat(),
                'impressions': {'lower_bound': 100000, 'upper_bound': 200000},
                'spend': {'lower_bound': 10000, 'upper_bound': 15000},
                'target_region': regions[0] if regions else 'US',
                'niche': niche,
            },
            {
                'id': 'mock_ad_003',
                'page_name': f'{niche.title()} Brand C',
                'ad_creative_bodies': [f'Sustainable {niche} for everyone'],
                'ad_delivery_start_time': (datetime.now() - timedelta(days=45)).isoformat(),
                'impressions': {'lower_bound': 30000, 'upper_bound': 60000},
                'spend': {'lower_bound': 3000, 'upper_bound': 5000},
                'target_region': regions[0] if regions else 'US',
                'niche': niche,
            },
        ]
        
        dominant_advertisers = [
            {'name': f'{niche.title()} Brand A', 'ad_count': 15, 'total_impressions': 75000, 'estimated_spend': 6500},
            {'name': f'{niche.title()} Brand B', 'ad_count': 25, 'total_impressions': 150000, 'estimated_spend': 12500},
            {'name': f'{niche.title()} Brand C', 'ad_count': 8, 'total_impressions': 45000, 'estimated_spend': 4000},
        ]
        
        ad_patterns = {
            'total_ads': len(mock_ads),
            'common_hooks': ['discover', 'premium', 'sustainable', 'shop now'],
            'formats': {'image': 2, 'video': 1},
            'average_impressions': 90000,
            'sample_copy': [ad['ad_creative_bodies'][0] for ad in mock_ads],
        }
        
        return {
            'active_ads': mock_ads,
            'dominant_advertisers': dominant_advertisers,
            'ad_patterns': ad_patterns,
            'discovered_at': datetime.now().isoformat(),
            'is_mock_data': True,
        }
