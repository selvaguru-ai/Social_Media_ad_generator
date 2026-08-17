"""
Ad Discovery / Market agent

Discovers what ads are currently running in the target niche using the Meta Ad Library API.
Identifies dominant advertisers and recurring ad patterns.
"""
import asyncio
import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple
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

# Hard cap on Meta paging to stay under ~200 calls/hour.
MAX_META_PAGES = 3
_PLACEHOLDER_KEY_PREFIX = "your_"


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
        self._llm_skip_logged = False
    
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
        Fetch ads from Meta Ad Library API, paginate, then keep on-niche ads.

        Follows paging.next until max_kept_ads is reached or MAX_META_PAGES
        pages have been fetched. Each page is classified by an LLM (one call
        per page); falls back to a simple niche-word check if the LLM is
        unavailable.
        """
        all_ads: List[Dict[str, Any]] = []
        max_kept = getattr(config.ad_discovery, "max_kept_ads", 10) or 10

        for i, region in enumerate(regions):
            self.logger.info(f"Fetching ads for '{niche}' in {region}")

            try:
                kept, fetched_n, pages, filter_mode = await self._fetch_region_ads(
                    niche, region, max_kept
                )
            except requests.exceptions.RequestException as e:
                self.logger.error(f"Failed to fetch ads for {region}: {e}")
                continue

            page_names = sorted({
                ad.get("page_name") or "Unknown" for ad in kept
            })
            self.logger.info(
                f"[REAL Meta API] '{niche}' in {region}: fetched {fetched_n} ads "
                f"across {pages} pages, kept {len(kept)} on-niche ({filter_mode})"
            )
            if page_names:
                self.logger.info(f"  Kept advertisers: {', '.join(page_names)}")
            for ad in kept:
                self.logger.info(
                    f"  Ad {ad.get('id')} | {ad.get('page_name')}: "
                    f"{self._public_ad_link(ad)}"
                )

            all_ads.extend(kept)

            if i < len(regions) - 1:
                await asyncio.sleep(self.rate_limit_delay)

        return all_ads

    async def _fetch_region_ads(
        self,
        niche: str,
        region: str,
        max_kept: int,
    ) -> Tuple[List[Dict[str, Any]], int, int, str]:
        """Paginate Meta ads_archive for one region and filter each page."""
        session = self._get_session()
        url: Optional[str] = f"{self.api_base}/ads_archive"
        params: Optional[Dict[str, Any]] = {
            "access_token": self.access_token,
            "search_terms": niche,
            "ad_reached_countries": region,
            "ad_active_status": "ACTIVE",
            "limit": config.ad_discovery.ads_per_query,
            "fields": COMMERCIAL_AD_FIELDS,
        }

        kept: List[Dict[str, Any]] = []
        seen_ids: set = set()
        fetched_n = 0
        pages = 0
        modes: List[str] = []

        while url and pages < MAX_META_PAGES and len(kept) < max_kept:
            if pages > 0:
                await asyncio.sleep(self.rate_limit_delay)

            if params:
                response = session.get(url, params=params)
            else:
                response = session.get(url)
            response.raise_for_status()
            data = response.json()

            pages += 1
            raw_ads = data.get("data") or []
            fetched_n += len(raw_ads)

            for ad in raw_ads:
                ad["target_region"] = region
                ad["niche"] = niche

            if raw_ads:
                page_kept, mode = self._filter_page_on_niche(raw_ads, niche)
                modes.append(mode)
                for ad in page_kept:
                    ad_id = str(ad.get("id") or "")
                    if not ad_id or ad_id in seen_ids:
                        continue
                    seen_ids.add(ad_id)
                    kept.append(ad)
                    if len(kept) >= max_kept:
                        break

            next_url = (data.get("paging") or {}).get("next")
            url = next_url if next_url else None
            params = None  # paging.next is a full URL

        kept = kept[:max_kept]
        filter_mode = self._summarize_filter_mode(modes)
        return kept, fetched_n, pages, filter_mode

    @staticmethod
    def _summarize_filter_mode(modes: List[str]) -> str:
        if not modes:
            return "no ads fetched"
        unique = set(modes)
        if unique == {"llm"}:
            return "LLM filter"
        if unique == {"fallback"}:
            return "keyword fallback — LLM filter skipped"
        return "LLM filter + keyword fallback on some pages"

    def _filter_page_on_niche(
        self, ads: List[Dict[str, Any]], niche: str
    ) -> Tuple[List[Dict[str, Any]], str]:
        """
        Keep ads genuinely about the niche. One LLM call for the whole page;
        keyword fallback if the LLM is disabled, unconfigured, or fails.
        """
        nf = getattr(config.ad_discovery, "niche_filter", None)
        enabled = bool(getattr(nf, "enabled", True)) if nf is not None else True

        if not enabled:
            if not self._llm_skip_logged:
                self.logger.warning(
                    "⚠️  LLM niche filter disabled in config. Using keyword fallback."
                )
                self._llm_skip_logged = True
            return self._fallback_filter_ads(ads, niche), "fallback"

        key = self._llm_api_key()
        if not key:
            if not self._llm_skip_logged:
                provider = (getattr(nf, "provider", None) or "anthropic").lower()
                env_name = (
                    "ANTHROPIC_API_KEY" if provider == "anthropic" else "OPENAI_API_KEY"
                )
                self.logger.warning(
                    f"⚠️  LLM niche filter skipped ({env_name} missing or placeholder). "
                    "Using keyword fallback."
                )
                self._llm_skip_logged = True
            return self._fallback_filter_ads(ads, niche), "fallback"

        try:
            kept_ids = self._llm_select_on_niche_ids(ads, niche, key)
            id_set = {str(i) for i in kept_ids}
            kept = [ad for ad in ads if str(ad.get("id") or "") in id_set]
            self.logger.info(
                f"LLM niche filter ran: kept {len(kept)}/{len(ads)} ads on this page"
            )
            return kept, "llm"
        except Exception as e:
            self.logger.warning(
                f"⚠️  LLM niche filter failed ({e}). Using keyword fallback."
            )
            return self._fallback_filter_ads(ads, niche), "fallback"

    def _llm_api_key(self) -> Optional[str]:
        nf = getattr(config.ad_discovery, "niche_filter", None)
        provider = (getattr(nf, "provider", None) or "anthropic").lower()
        if provider == "anthropic":
            key = settings.anthropic_api_key or os.getenv("ANTHROPIC_API_KEY")
        elif provider == "openai":
            key = settings.openai_api_key or os.getenv("OPENAI_API_KEY")
        else:
            return None
        key = (key or "").strip()
        if not key or key.lower().startswith(_PLACEHOLDER_KEY_PREFIX):
            return None
        return key

    def _llm_select_on_niche_ids(
        self, ads: List[Dict[str, Any]], niche: str, api_key: str
    ) -> List[str]:
        compact = []
        for ad in ads:
            compact.append({
                "id": str(ad.get("id") or ""),
                "page_name": ad.get("page_name") or "",
                "ad_copy": self._ad_copy(ad),
            })
        numbered = "\n".join(
            f"{idx}. {json.dumps(item, ensure_ascii=False)}"
            for idx, item in enumerate(compact, 1)
        )
        prompt = (
            f"Here is a numbered list of ads. The target niche is '{niche}'. "
            "Return a JSON array of the ids that are genuinely about this niche "
            "(the product/category), excluding unrelated ads that only matched "
            "by loose keyword. Return only JSON.\n\n"
            f"{numbered}"
        )
        nf = config.ad_discovery.niche_filter
        provider = (nf.provider or "anthropic").lower()
        model = nf.model or "claude-sonnet-4-6"
        raw_text = self._call_llm(provider, model, prompt, api_key)
        return self._parse_id_list(raw_text)

    def _call_llm(
        self, provider: str, model: str, prompt: str, api_key: str
    ) -> str:
        if provider == "anthropic":
            response = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": model,
                    "max_tokens": 1024,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=60,
            )
            response.raise_for_status()
            body = response.json()
            parts = body.get("content") or []
            texts = [
                p.get("text", "") for p in parts if isinstance(p, dict)
            ]
            return "\n".join(texts).strip()

        if provider == "openai":
            response = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "content-type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0,
                },
                timeout=60,
            )
            response.raise_for_status()
            body = response.json()
            choices = body.get("choices") or []
            if not choices:
                raise ValueError("OpenAI response had no choices")
            return (choices[0].get("message") or {}).get("content") or ""

        raise ValueError(f"Unsupported niche_filter.provider: {provider}")

    @staticmethod
    def _parse_id_list(text: str) -> List[str]:
        """Parse a JSON array of ad ids from an LLM response."""
        if not text or not str(text).strip():
            raise ValueError("empty LLM response")
        cleaned = str(text).strip()
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        start = cleaned.find("[")
        end = cleaned.rfind("]")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("LLM response did not contain a JSON array")
        data = json.loads(cleaned[start:end + 1])
        if not isinstance(data, list):
            raise ValueError("LLM JSON was not an array")
        return [str(item) for item in data if item is not None and str(item)]

    @staticmethod
    def _ad_copy(ad: Dict[str, Any]) -> str:
        parts: List[str] = []
        for field in ("ad_creative_bodies", "ad_creative_link_titles"):
            value = ad.get(field) or []
            if isinstance(value, list):
                parts.extend(str(v) for v in value if v)
            elif value:
                parts.append(str(value))
        return " ".join(parts)[:400]

    def _fallback_filter_ads(
        self, ads: List[Dict[str, Any]], niche: str
    ) -> List[Dict[str, Any]]:
        """Keep ads whose page_name or copy contains all niche words."""
        words = [
            w for w in re.findall(r"[a-z0-9]+", (niche or "").lower()) if len(w) >= 2
        ]
        if not words:
            return []
        kept = []
        for ad in ads:
            text = f"{ad.get('page_name') or ''} {self._ad_copy(ad)}".lower()
            if all(word in text for word in words):
                kept.append(ad)
        return kept

    @staticmethod
    def _public_ad_link(ad: Dict[str, Any]) -> str:
        """Public Ad Library URL. Never log ad_snapshot_url — it embeds the access token."""
        ad_id = ad.get('id')
        if ad_id:
            return f"https://www.facebook.com/ads/library/?id={ad_id}"
        return '(no ad id)'
    
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
    
    @staticmethod
    def _as_int(value: Any, default: int = 0) -> int:
        """Coerce Meta range bounds (often numeric strings) to int."""
        if value in (None, ""):
            return default
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default

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
            # assume a missing field is a dict. Meta returns bounds as strings.
            impressions = ad.get('impressions')
            if isinstance(impressions, dict):
                advertiser_counts[page_name]['total_impressions'] += self._as_int(
                    impressions.get('lower_bound')
                )
            elif isinstance(impressions, (int, float, str)):
                advertiser_counts[page_name]['total_impressions'] += self._as_int(impressions)
            
            spend = ad.get('spend')
            if isinstance(spend, dict):
                advertiser_counts[page_name]['estimated_spend'] += self._as_int(
                    spend.get('lower_bound')
                )
            elif isinstance(spend, (int, float, str)):
                advertiser_counts[page_name]['estimated_spend'] += self._as_int(spend)
        
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
