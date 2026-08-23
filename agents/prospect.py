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
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from agents.base import BaseAgent
from utils import DATA_DIR, config, settings
from utils.scoring import qualify_leads

PAGE_ID_CACHE_PATH = DATA_DIR / "page_id_cache.json"

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
        self._page_id_cache, self._page_id_ads_floor = self._load_page_id_cache()

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
            'X-Api-Key': self.apollo_api_key,
        }
        
        for region in regions:
            payload = {
                'q_organization_keyword_tags': [niche],
                'page': 1,
                'per_page': 10,
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
                if not response.ok:
                    self.logger.error(
                        f"Apollo API error for {region}: {response.status_code} "
                        f"{response.text[:500]}"
                    )
                    continue
                response.raise_for_status()
                
                data = response.json()
                companies = data.get('organizations', [])
                
                for company in companies:
                    website = company.get('website_url') or company.get('primary_domain')
                    brands.append({
                        'name': company.get('name'),
                        'domain': website,
                        'facebook_url': company.get('facebook_url'),
                        'linkedin_url': company.get('linkedin_url'),
                        'size': company.get('estimated_num_employees'),
                        'industry': company.get('industry') or niche,
                        'region': region,
                        'source': 'apollo',
                    })
                    self.logger.info(
                        f"  Apollo: {company.get('name')} | {website or '(no website)'}"
                    )
                
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
                    brand_domain=brand.get('domain'),
                    facebook_url=brand.get('facebook_url'),
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
        brand_domain: Optional[str] = None,
        facebook_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Query Meta Ad Library for a specific brand by Facebook Page ID.

        Page IDs come from the manual cache, then a numeric URL, then (if
        enabled) ads_archive name search. Presence is counted with
        search_page_ids. Unrelated keyword hits are never treated as ads.
        """
        try:
            page_id, resolved_via = await self._resolve_facebook_page_id(
                brand_name, brand_domain, facebook_url, regions
            )
            library_url = self._ad_library_page_url(page_id, regions)

            if not page_id:
                self.logger.info(
                    f"[REAL Meta API] {brand_name}: page unresolved | "
                    f"facebook_url={facebook_url or 'absent'} | "
                    f"resolved_via=unresolved | lookup=unresolved | "
                    f"match_confidence=0.20 (not treated as 0 ads)"
                )
                return {
                    'active_ads_count': 0,
                    'estimated_spend': 0,
                    'total_impressions': 0,
                    'spend_present': False,
                    'impressions_present': False,
                    'page_name': None,
                    'page_names': [],
                    'page_id': None,
                    'match_confidence': 0.2,
                    'has_low_presence': False,
                    'page_unresolved': True,
                    'library_url': facebook_url,
                    'source': 'meta_api',
                    'lookup': 'unresolved',
                }

            params = {
                'access_token': self.meta_access_token,
                'search_page_ids': json.dumps([str(page_id)]),
                # ACTIVE alone often returns 0 for commercial pages the UI
                # still shows; ALL is the footprint that matches Ad Library.
                'ad_active_status': 'ALL',
                'limit': 100,
                'fields': COMMERCIAL_AD_FIELDS,
            }
            presence_countries = self._presence_countries(regions)
            if presence_countries:
                params['ad_reached_countries'] = json.dumps(presence_countries)

            session = self._get_session()
            response = session.get(f"{self.meta_api_base}/ads_archive", params=params)
            ads = []
            lookup = "search_page_ids"
            if response.status_code == 400:
                self.logger.info(
                    f"[REAL Meta API] {brand_name}: search_page_ids rejected "
                    f"page_id={page_id}; using page_name-matched ads as footprint"
                )
                ads = self._matched_ads_for_brand(brand_name, regions)
                lookup = "page_name_match"
            else:
                response.raise_for_status()
                ads = response.json().get('data') or []

            total_spend, spend_present = self._sum_optional_range(ads, 'spend')
            total_impressions, impressions_present = self._sum_optional_range(
                ads, 'impressions'
            )

            page_names = [
                ad.get('page_name') for ad in ads if ad.get('page_name')
            ]
            unique_page_names = sorted(set(page_names))
            primary_page_name = unique_page_names[0] if unique_page_names else None

            api_count = len(ads)
            ads_count = api_count
            floor = self._ads_floor_from_cache(brand_name)
            if floor is not None and floor > ads_count:
                # ads_archive often omits US commercial ads the Ad Library UI
                # still shows. Cache ads_count is the UI figure.
                ads_count = floor
                lookup = f"{lookup}+ui_override"
                self.logger.info(
                    f"[REAL Meta API] {brand_name}: ads_archive returned "
                    f"{api_count}; using cache ads_count={floor} from Ad Library UI"
                )

            # Page-ID (or page_name) match is a high-confidence identity.
            match_confidence = 1.0 if ads_count else 0.85

            has_low_presence = ads_count < 5
            if spend_present:
                has_low_presence = (
                    has_low_presence
                    and total_spend < config.prospect.max_ad_spend_threshold
                )

            self.logger.info(
                f"[REAL Meta API] {brand_name}: {ads_count} ads | "
                f"page_id={page_id} | "
                f"page_name={'present (' + ', '.join(unique_page_names[:3]) + ')' if unique_page_names else 'absent'} | "
                f"resolved_via={resolved_via} | lookup={lookup} | "
                f"reached_countries={','.join(presence_countries)} | "
                f"match_confidence={match_confidence:.2f}"
            )
            if library_url:
                self.logger.info(f"  Ad Library: {library_url}")

            return {
                'active_ads_count': ads_count,
                'estimated_spend': total_spend if spend_present else 0,
                'total_impressions': total_impressions if impressions_present else 0,
                'spend_present': spend_present,
                'impressions_present': impressions_present,
                'page_name': primary_page_name,
                'page_names': unique_page_names,
                'page_id': page_id,
                'match_confidence': match_confidence,
                'has_low_presence': has_low_presence,
                'page_unresolved': False,
                'library_url': library_url,
                'source': 'meta_api',
                'lookup': lookup,
            }

        except Exception as e:
            safe = re.sub(r"access_token=[^&\s]+", "access_token=REDACTED", str(e))
            self.logger.error(
                f"[REAL Meta API] Error checking ad library for {brand_name}: {safe}"
            )
            return {
                'active_ads_count': 0,
                'estimated_spend': 0,
                'total_impressions': 0,
                'spend_present': False,
                'impressions_present': False,
                'page_name': None,
                'match_confidence': 0.2,
                'has_low_presence': False,
                'page_unresolved': True,
                'library_url': facebook_url,
                'source': 'meta_api_error',
                'error': re.sub(r"access_token=[^&\s]+", "access_token=REDACTED", str(e)),
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
                bound = value.get('lower_bound') or 0
                try:
                    total += int(float(bound))
                except (TypeError, ValueError):
                    pass
            elif isinstance(value, (int, float, str)):
                try:
                    total += int(float(value))
                except (TypeError, ValueError):
                    pass
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
    
    @staticmethod
    def _alnum_key(text: str) -> str:
        """Lowercase and strip non-alphanumerics for page_name matching."""
        return re.sub(r"[^a-z0-9]", "", (text or "").lower())

    def _page_name_matches_brand(self, brand_name: str, page_name: str) -> bool:
        """
        True when page_name is the brand, not a loose keyword hit.

        Both sides are lowercased with non-alphanumerics stripped. Exact
        match always counts; substring match only when the brand token is
        at least 4 characters (avoids short-name collisions).
        """
        brand_key = self._alnum_key(brand_name)
        page_key = self._alnum_key(page_name)
        if not brand_key or not page_key:
            return False
        if brand_key == page_key:
            return True
        if len(brand_key) >= 4 and (brand_key in page_key or page_key in brand_key):
            return True
        return False

    def _resolve_page_id_from_ad_library(
        self, brand_name: str, regions: List[str]
    ) -> Tuple[Optional[str], int, int]:
        """
        Find a Facebook Page ID by searching ads_archive, then keeping only
        ads whose page_name matches the brand.

        Returns (page_id, fetched_count, matched_count).
        """
        params = {
            "access_token": self.meta_access_token,
            "search_terms": brand_name,
            "ad_type": "ALL",
            "ad_active_status": "ALL",
            "limit": 100,
            "fields": "id,page_id,page_name",
        }
        if regions:
            params["ad_reached_countries"] = json.dumps(regions)

        try:
            response = self._get_session().get(
                f"{self.meta_api_base}/ads_archive", params=params, timeout=30
            )
            response.raise_for_status()
            ads = response.json().get("data") or []
        except Exception as e:
            safe = re.sub(r"access_token=[^&\s]+", "access_token=REDACTED", str(e))
            self.logger.warning(
                f"[REAL Meta API] {brand_name}: ads_archive name search failed ({safe})"
            )
            return None, 0, 0

        fetched = len(ads)
        matched = [
            ad for ad in ads
            if ad.get("page_id") and self._page_name_matches_brand(
                brand_name, ad.get("page_name") or ""
            )
        ]
        matched_n = len(matched)
        page_id = None
        if matched:
            counts = Counter(str(ad.get("page_id")) for ad in matched)
            page_id = counts.most_common(1)[0][0]
            verdict = "advertising"
        else:
            verdict = "no ads found"

        self.logger.info(
            f"[REAL Meta API] {brand_name}: fetched {fetched}, "
            f"matched {matched_n} by page_name, resolved page_id={page_id or 'none'}, "
            f"verdict={verdict}"
        )
        return page_id, fetched, matched_n

    def _matched_ads_for_brand(
        self, brand_name: str, regions: List[str]
    ) -> List[Dict[str, Any]]:
        """Return ads_archive hits whose page_name matches the brand."""
        params = {
            "access_token": self.meta_access_token,
            "search_terms": brand_name,
            "ad_type": "ALL",
            "ad_active_status": "ALL",
            "limit": 100,
            "fields": COMMERCIAL_AD_FIELDS,
        }
        if regions:
            params["ad_reached_countries"] = json.dumps(regions)
        try:
            response = self._get_session().get(
                f"{self.meta_api_base}/ads_archive", params=params, timeout=30
            )
            response.raise_for_status()
            ads = response.json().get("data") or []
        except Exception:
            return []
        return [
            ad for ad in ads
            if self._page_name_matches_brand(brand_name, ad.get("page_name") or "")
        ]

    def _load_page_id_cache(self) -> Tuple[Dict[str, str], Dict[str, int]]:
        """
        Read data/page_id_cache.json.

        Values may be a classic Page ID string, or an object:
          {"page_id": "123", "ads_count": 280}
        ads_count is an optional Ad Library UI floor when ads_archive
        returns 0 for a page the UI still shows as advertising.

        Missing, empty, or invalid files are treated as no cache. Never writes.
        """
        path = PAGE_ID_CACHE_PATH
        empty: Tuple[Dict[str, str], Dict[str, int]] = ({}, {})
        if not path.is_file():
            self.logger.info(
                f"Page ID cache absent ({path}); brands resolve as unresolved "
                "unless a numeric Facebook URL is present"
            )
            return empty
        try:
            raw = path.read_text(encoding="utf-8").strip()
            if not raw:
                self.logger.info(f"Page ID cache empty ({path})")
                return empty
            data = json.loads(raw)
        except (OSError, json.JSONDecodeError) as e:
            self.logger.warning(
                f"Page ID cache unreadable ({path}): {e}; treating as no cache"
            )
            return empty
        if not isinstance(data, dict):
            self.logger.warning(
                f"Page ID cache is not a JSON object ({path}); treating as no cache"
            )
            return empty

        cache: Dict[str, str] = {}
        floors: Dict[str, int] = {}
        for key, value in data.items():
            norm = self._alnum_key(str(key))
            if not norm:
                continue
            page_id = ""
            ads_count = None
            if isinstance(value, dict):
                page_id = str(value.get("page_id") or value.get("id") or "").strip()
                raw_count = value.get("ads_count")
                if raw_count is not None:
                    try:
                        ads_count = int(raw_count)
                    except (TypeError, ValueError):
                        ads_count = None
            elif value is not None:
                page_id = str(value).strip()
            if page_id:
                cache[norm] = page_id
            if ads_count is not None and ads_count >= 0:
                floors[norm] = ads_count
        self.logger.info(
            f"Loaded {len(cache)} page IDs from {path}"
            + (f" ({len(floors)} with UI ads_count)" if floors else "")
        )
        return cache, floors

    def _page_id_from_cache(self, brand_name: str) -> Optional[str]:
        key = self._alnum_key(brand_name)
        if not key:
            return None
        return self._page_id_cache.get(key)

    def _ads_floor_from_cache(self, brand_name: str) -> Optional[int]:
        key = self._alnum_key(brand_name)
        if not key:
            return None
        return self._page_id_ads_floor.get(key)

    async def _resolve_facebook_page_id(
        self,
        brand_name: str,
        brand_domain: Optional[str] = None,
        facebook_url: Optional[str] = None,
        regions: Optional[List[str]] = None,
    ) -> Tuple[Optional[str], str]:
        """
        Resolve a classic Facebook Page ID for ads_archive search_page_ids.

        Order: manual cache, numeric ID on a known URL, then (if enabled)
        ads_archive search_terms + page_name match. Returns (page_id, source)
        where source is cache | url | name-search | unresolved.
        """
        regions = regions or ["US"]

        cached = self._page_id_from_cache(brand_name)
        if cached:
            self.logger.info(
                f"[REAL Meta API] {brand_name}: page_id={cached} resolved via cache"
            )
            return cached, "cache"

        for candidate in (facebook_url, brand_domain):
            page_id = self._page_id_from_facebook_url(candidate)
            if page_id and self._ads_archive_accepts_page_id(page_id, regions):
                self.logger.info(
                    f"[REAL Meta API] {brand_name}: page_id={page_id} resolved via url"
                )
                return page_id, "url"

        if config.prospect.enable_name_search_resolver:
            page_id, fetched, matched_n = self._resolve_page_id_from_ad_library(
                brand_name, regions
            )
            if page_id:
                self.logger.info(
                    f"[REAL Meta API] {brand_name}: page_id={page_id} "
                    "resolved via name-search"
                )
                return page_id, "name-search"
            # Matched 0 (or fetch failed): do not treat keyword junk as this page.
            self.logger.info(
                f"[REAL Meta API] {brand_name}: page_id unresolved "
                f"(name-search fetched {fetched}, matched {matched_n})"
            )
            return None, "unresolved"

        self.logger.info(f"[REAL Meta API] {brand_name}: page_id unresolved")
        return None, "unresolved"

    @staticmethod
    def _page_id_from_facebook_url(url: Optional[str]) -> Optional[str]:
        if not url:
            return None
        patterns = [
            r'view_all_page_id=(\d{5,})',
            r'profile\.php\?id=(\d{5,})',
            r'/pages/[^/]+/(\d{5,})',
            r'facebook\.com/(?:profile\.php\?id=)?(\d{5,})\b',
        ]
        for pat in patterns:
            match = re.search(pat, url)
            if match:
                return match.group(1)
        return None

    def _ads_archive_accepts_page_id(
        self, page_id: str, regions: Optional[List[str]] = None
    ) -> bool:
        """True when ads_archive will query this page id (rejects new-style IDs)."""
        try:
            params = {
                "access_token": self.meta_access_token,
                "search_page_ids": json.dumps([str(page_id)]),
                "ad_reached_countries": json.dumps(regions or ["US"]),
                "ad_active_status": "ALL",
                "fields": "id,page_name",
                "limit": 1,
            }
            response = self._get_session().get(
                f"{self.meta_api_base}/ads_archive", params=params, timeout=20
            )
            if response.status_code == 400:
                return False
            response.raise_for_status()
            return True
        except Exception:
            return False

    def _presence_countries(self, regions: Optional[List[str]]) -> List[str]:
        """
        Countries to send as ad_reached_countries for a presence check.

        Target regions first, then a fallback list. Meta frequently tags
        commercial ads as GB/EU even when the Ad Library UI shows them on
        a US Page — US-only then reports 0 ads and false-qualifies the brand.
        """
        fallback = getattr(config.prospect, "presence_country_fallback", None) or [
            "GB", "CA", "AU", "DE", "FR", "IE"
        ]
        out: List[str] = []
        for country in list(regions or []) + list(fallback):
            if country and country not in out:
                out.append(country)
        return out

    @staticmethod
    def _ad_library_page_url(page_id: Optional[str], regions: List[str]) -> Optional[str]:
        if not page_id:
            return None
        country = (regions[0] if regions else "US")
        return (
            "https://www.facebook.com/ads/library/"
            f"?active_status=active&ad_type=all&country={country}"
            "&is_targeted_country=false&media_type=all&search_type=page"
            "&sort_data[direction]=desc&sort_data[mode]=total_impressions"
            f"&view_all_page_id={page_id}"
        )
    
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
