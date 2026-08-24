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
from utils import DATA_DIR, config, settings

PAGE_ID_CACHE_PATH = DATA_DIR / "page_id_cache.json"

# Same commercial-ad field set as the Prospect agent. spend/impressions are
# usually absent for non-EU commercial ads; page_name is the advertiser identity.
COMMERCIAL_AD_FIELDS = (
    "id,page_id,page_name,ad_delivery_start_time,ad_delivery_stop_time,"
    "publisher_platforms,ad_creative_bodies,"
    "ad_creative_link_titles,impressions,spend"
)

# Hard cap on Meta paging per search term to stay under ~200 calls/hour.
MAX_META_PAGES = 3
_PLACEHOLDER_KEY_PREFIX = "your_"

# Extra keyword variants when the niche is a common compact form.
_NICHE_ALIASES = {
    "tshirt": ["t-shirt", "t shirt", "tee"],
    "tshirts": ["t-shirts", "t shirts", "tee shirts", "tees"],
    "tee": ["t-shirt", "tshirt"],
    "tees": ["t-shirts", "tshirts"],
    "sportsshoes": ["sports shoes", "running shoes", "athletic shoes"],
    "sneakers": ["running shoes", "trainers"],
}


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
            
            # Harvest unique advertisers from keyword variants. Do not
            # LLM-filter this list to empty — page_id is the company signal.
            terms = self._search_term_variants(niche)
            ads = await self._fetch_ads(niche, regions)

            dominant_advertisers = self._identify_dominant_advertisers(ads, regions)
            if getattr(config.ad_discovery, "seed_page_id_cache", True):
                written = self._seed_page_id_cache(dominant_advertisers)
                if written:
                    self.logger.info(
                        f"Seeded {written} Page IDs into {PAGE_ID_CACHE_PATH}"
                    )

            ad_patterns = self._extract_ad_patterns(ads)
            ad_patterns["search_terms"] = terms

            sample = self._fallback_filter_ads(ads, niche) or ads
            max_kept = getattr(config.ad_discovery, "max_kept_ads", 40) or 40
            stored_ads = [self._sanitize_ad(ad) for ad in sample[:max_kept]]

            result = {
                'active_ads': stored_ads,
                'dominant_advertisers': dominant_advertisers,
                'ad_patterns': ad_patterns,
                'search_terms': terms,
                'discovered_at': datetime.now().isoformat(),
            }

            self.log_complete(
                f"Found {len(ads)} ads from {len(dominant_advertisers)} advertisers"
            )
            return result
            
        except Exception as e:
            self.log_error(e)
            raise

    def log_error(self, error: Exception):
        safe = self._redact(str(error))
        self.logger.error(f"❌ {self.name} agent failed: {safe}", exc_info=True)

    @staticmethod
    def _redact(text: str) -> str:
        return re.sub(r"access_token=[^&\s]+", "access_token=REDACTED", str(text or ""))

    def _search_term_variants(self, niche: str) -> List[str]:
        """Hyphen/space forms plus a few compact-niche aliases. Deduped, capped."""
        max_terms = getattr(config.ad_discovery, "max_search_terms", 5) or 5
        raw = re.sub(r"\s+", " ", (niche or "").strip())
        if not raw:
            return []

        seen: set = set()
        out: List[str] = []

        def add(term: str) -> None:
            t = re.sub(r"\s+", " ", (term or "").strip())
            if not t:
                return
            key = t.lower()
            if key in seen:
                return
            seen.add(key)
            out.append(t)

        add(raw)
        if "-" in raw:
            add(raw.replace("-", " "))
        if " " in raw:
            add(raw.replace(" ", "-"))

        aliases = list(_NICHE_ALIASES.get(self._alnum_key(raw), []))
        for alias in aliases:
            add(alias)
        for alias in aliases:
            if "-" in alias:
                add(alias.replace("-", " "))
            if " " in alias:
                add(alias.replace(" ", "-"))

        return out[:max_terms]

    @staticmethod
    def _alnum_key(text: str) -> str:
        return re.sub(r"[^a-z0-9]", "", (text or "").lower())

    @staticmethod
    def _is_phrase(term: str) -> bool:
        return any(ch in (term or "") for ch in (" ", "-"))

    async def _fetch_ads(self, niche: str, regions: List[str]) -> List[Dict[str, Any]]:
        """
        Harvest ads for several search_terms. Keep every unique ad.

        Advertisers are unique page_id values from this harvest — they are
        not LLM-filtered. Keyword matching is only used later for sample copy.
        """
        terms = self._search_term_variants(niche)
        pages_per_term = min(
            MAX_META_PAGES,
            getattr(config.ad_discovery, "harvest_pages_per_term", 2) or 2,
        )
        self.logger.info(
            f"Harvesting advertisers for {niche!r} with search_terms={terms} "
            f"({pages_per_term} pages/term)"
        )

        all_ads: List[Dict[str, Any]] = []
        seen_ids: set = set()

        for region in regions:
            for term in terms:
                try:
                    raw, fetched_n, pages = await self._harvest_term_ads(
                        term, niche, region, pages_per_term
                    )
                except requests.exceptions.RequestException as e:
                    self.logger.error(
                        f"Failed to fetch ads for {term!r} in {region}: {self._redact(str(e))}"
                    )
                    continue

                added = 0
                for ad in raw:
                    ad_id = str(ad.get("id") or "")
                    if not ad_id or ad_id in seen_ids:
                        continue
                    seen_ids.add(ad_id)
                    all_ads.append(ad)
                    added += 1

                unique_pages = {
                    str(ad.get("page_id"))
                    for ad in all_ads
                    if ad.get("page_id")
                }
                self.logger.info(
                    f"[REAL Meta API] term={term!r} in {region}: fetched {fetched_n} ads "
                    f"across {pages} pages, +{added} new "
                    f"(unique pages so far: {len(unique_pages)})"
                )
                is_last = region == regions[-1] and term == terms[-1]
                if not is_last:
                    await asyncio.sleep(self.rate_limit_delay)

        page_names = sorted({
            (ad.get("page_name") or "Unknown") for ad in all_ads
        })
        if page_names:
            preview = ", ".join(page_names[:25])
            extra = f" (+{len(page_names) - 25} more)" if len(page_names) > 25 else ""
            self.logger.info(f"Harvested advertisers: {preview}{extra}")
        return all_ads

    async def _harvest_term_ads(
        self,
        term: str,
        niche: str,
        region: str,
        pages_per_term: int,
    ) -> Tuple[List[Dict[str, Any]], int, int]:
        """Paginate ads_archive for one search term. No relevance filter."""
        session = self._get_session()
        url: Optional[str] = f"{self.api_base}/ads_archive"
        params: Optional[Dict[str, Any]] = {
            "access_token": self.access_token,
            "search_terms": term,
            "ad_reached_countries": region,
            "ad_active_status": "ACTIVE",
            "limit": config.ad_discovery.ads_per_query,
            "fields": COMMERCIAL_AD_FIELDS,
        }
        exact_phrase = self._is_phrase(term)
        if exact_phrase:
            params["search_type"] = "KEYWORD_EXACT_PHRASE"

        kept: List[Dict[str, Any]] = []
        fetched_n = 0
        pages = 0

        while url and pages < pages_per_term:
            if pages > 0:
                await asyncio.sleep(self.rate_limit_delay)

            if params:
                response = session.get(url, params=params)
            else:
                response = session.get(url)

            if (
                response.status_code == 400
                and exact_phrase
                and pages == 0
                and params is not None
            ):
                params.pop("search_type", None)
                exact_phrase = False
                self.logger.info(
                    f"KEYWORD_EXACT_PHRASE rejected for {term!r}; retrying unordered"
                )
                continue

            response.raise_for_status()
            data = response.json()

            pages += 1
            raw_ads = data.get("data") or []
            fetched_n += len(raw_ads)
            for ad in raw_ads:
                ad["target_region"] = region
                ad["niche"] = niche
                ad["search_term"] = term
                kept.append(ad)

            next_url = (data.get("paging") or {}).get("next")
            url = next_url if next_url else None
            params = None

        return kept, fetched_n, pages

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
        Unused by keyword harvest (advertisers are unique pages).
        Kept for optional sample-copy tagging.
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
        session.trust_env = False
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

    def _identify_dominant_advertisers(
        self,
        ads: List[Dict[str, Any]],
        regions: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Group harvested ads by page_id. Every unique page is an advertiser."""
        advertisers: Dict[str, Dict[str, Any]] = {}

        for ad in ads:
            page_id = str(ad.get("page_id") or "").strip()
            page_name = (ad.get("page_name") or "Unknown").strip() or "Unknown"
            key = page_id or f"name:{page_name.lower()}"
            if key not in advertisers:
                advertisers[key] = {
                    "name": page_name,
                    "page_id": page_id or None,
                    "ad_count": 0,
                    "total_impressions": 0,
                    "estimated_spend": 0,
                    "library_url": self._ad_library_page_url(page_id, regions or []),
                }

            row = advertisers[key]
            row["ad_count"] += 1
            if page_name != "Unknown":
                row["name"] = page_name

            impressions = ad.get("impressions")
            if isinstance(impressions, dict):
                row["total_impressions"] += self._as_int(impressions.get("lower_bound"))
            elif isinstance(impressions, (int, float, str)):
                row["total_impressions"] += self._as_int(impressions)

            spend = ad.get("spend")
            if isinstance(spend, dict):
                row["estimated_spend"] += self._as_int(spend.get("lower_bound"))
            elif isinstance(spend, (int, float, str)):
                row["estimated_spend"] += self._as_int(spend)

        return sorted(
            advertisers.values(),
            key=lambda x: x["ad_count"],
            reverse=True,
        )

    @staticmethod
    def _ad_library_page_url(page_id: Optional[str], regions: List[str]) -> Optional[str]:
        if not page_id:
            return None
        country = (regions[0] if regions else "US")
        return (
            "https://www.facebook.com/ads/library/"
            f"?active_status=all&ad_type=all&country={country}"
            "&is_targeted_country=false&media_type=all&search_type=page"
            "&sort_data[direction]=desc&sort_data[mode]=total_impressions"
            f"&view_all_page_id={page_id}"
        )

    @staticmethod
    def _sanitize_ad(ad: Dict[str, Any]) -> Dict[str, Any]:
        """Drop snapshot URLs so pipeline_state.json never stores the token."""
        return {key: value for key, value in ad.items() if key != "ad_snapshot_url"}

    def _seed_page_id_cache(self, advertisers: List[Dict[str, Any]]) -> int:
        """
        Merge harvested page_id + page_name into data/page_id_cache.json.

        Never overwrites an existing key (including ads_count objects). Skips
        when the alphanumeric form of the name is already present.
        """
        existing: Dict[str, Any] = {}
        if PAGE_ID_CACHE_PATH.is_file():
            try:
                raw = PAGE_ID_CACHE_PATH.read_text(encoding="utf-8").strip()
                if raw:
                    parsed = json.loads(raw)
                    if isinstance(parsed, dict):
                        existing = parsed
            except (OSError, json.JSONDecodeError) as e:
                self.logger.warning(
                    f"Page ID cache unreadable ({PAGE_ID_CACHE_PATH}): {e}; "
                    "will write a new merge file"
                )
                existing = {}

        occupied = {
            self._alnum_key(str(key))
            for key in existing
            if self._alnum_key(str(key))
        }
        added = 0
        for adv in advertisers:
            page_id = str(adv.get("page_id") or "").strip()
            name = (adv.get("name") or "").strip()
            if not page_id.isdigit() or not name or name.lower() == "unknown":
                continue
            key_alnum = self._alnum_key(name)
            if not key_alnum or key_alnum in occupied:
                continue
            existing[name] = page_id
            occupied.add(key_alnum)
            added += 1

        if added == 0:
            return 0

        PAGE_ID_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = PAGE_ID_CACHE_PATH.with_name(PAGE_ID_CACHE_PATH.name + ".tmp")
        tmp_path.write_text(
            json.dumps(existing, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp_path, PAGE_ID_CACHE_PATH)
        return added
    
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
            {
                "name": f"{niche.title()} Brand A",
                "page_id": "111111111111111",
                "ad_count": 15,
                "total_impressions": 75000,
                "estimated_spend": 6500,
                "library_url": self._ad_library_page_url("111111111111111", regions),
            },
            {
                "name": f"{niche.title()} Brand B",
                "page_id": "222222222222222",
                "ad_count": 25,
                "total_impressions": 150000,
                "estimated_spend": 12500,
                "library_url": self._ad_library_page_url("222222222222222", regions),
            },
            {
                "name": f"{niche.title()} Brand C",
                "page_id": "333333333333333",
                "ad_count": 8,
                "total_impressions": 45000,
                "estimated_spend": 4000,
                "library_url": self._ad_library_page_url("333333333333333", regions),
            },
        ]
        
        ad_patterns = {
            'total_ads': len(mock_ads),
            'common_hooks': ['discover', 'premium', 'sustainable', 'shop now'],
            'formats': {'image': 2, 'video': 1},
            'average_impressions': 90000,
            'sample_copy': [ad['ad_creative_bodies'][0] for ad in mock_ads],
            'search_terms': self._search_term_variants(niche),
        }
        
        return {
            'active_ads': mock_ads,
            'dominant_advertisers': dominant_advertisers,
            'ad_patterns': ad_patterns,
            'search_terms': ad_patterns['search_terms'],
            'discovered_at': datetime.now().isoformat(),
            'is_mock_data': True,
        }
