"""
Lead scoring for the Prospect agent (replacement for the step-function logic).

Design goals (fixing the original flaws):
  * competitor_presence is PER-LEAD (derived from competitor activity in the
    lead's own region), not one global constant applied to every lead.
  * All three components are CONTINUOUS, so scores spread across a range
    instead of collapsing onto {1.0, 0.94, 0.85}.
  * The final ranking score is MIN-MAX NORMALIZED across the batch, so the
    best lead sits near 1.0 and the weakest near 0.0 regardless of absolute
    values -- giving usable ranking resolution.
  * Ad SPEND is only used when it is actually present (it is usually null for
    commercial ads on the Meta Ad Library); otherwise presence is judged on
    active-ad COUNT alone.
  * A fuzzy-match (low-confidence) ad lookup applies a small penalty rather
    than silently scoring like an exact match.

Pure functions, no dependency on the rest of the codebase, so it can be unit
tested and dropped into agents/prospect.py by importing `qualify_leads`.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


# --------------------------------------------------------------------------- #
# Component scores (each returns a float in [0, 1])
# --------------------------------------------------------------------------- #

def presence_score(
    active_ads_count: int,
    estimated_spend: float = 0.0,
    spend_is_reliable: bool = False,
    spend_threshold: float = 5000.0,
    count_half_life: float = 4.0,
) -> float:
    """
    Higher score = LESS advertising = better lead.

    Continuous exponential decay on active-ad count with a configurable
    half-life (default: 4 ads -> 0.5). Spend is blended in ONLY when the
    caller says it is reliable, because commercial ads usually report no spend.
    """
    count_score = 0.5 ** (max(0, active_ads_count) / max(0.1, count_half_life))

    if spend_is_reliable and spend_threshold > 0:
        # 1.0 at zero spend, decaying to ~0 as spend approaches the threshold.
        spend_score = max(0.0, 1.0 - (estimated_spend / spend_threshold))
        # Weight count and spend evenly when both are meaningful.
        return round(0.5 * count_score + 0.5 * spend_score, 4)

    return round(count_score, 4)


def maturity_score(
    size: Optional[int],
    ideal_min: int = 10,
    ideal_max: int = 500,
) -> float:
    """
    Smooth 'tent': ramps up to the ideal band, plateaus at 1.0 inside it,
    then decays past it (big companies are less likely to convert / need us).
    Replaces the original hard 1.0 / 0.5 / 0.3 step function.
    """
    if not size or size <= 0:
        return 0.2  # unknown size -> weak but not disqualifying

    if size < ideal_min:
        # Ramp 0.2 -> 1.0 across (0, ideal_min).
        return round(0.2 + 0.8 * (size / ideal_min), 4)

    if size <= ideal_max:
        return 1.0

    # Above the band: decay 1.0 -> 0.3 across (ideal_max, 2*ideal_max), floor 0.3.
    over = (size - ideal_max) / ideal_max
    return round(max(0.3, 1.0 - 0.7 * min(1.0, over)), 4)


def _region_competitor_intensity(
    active_ads: List[Dict[str, Any]],
    dominant_advertisers: List[Dict[str, Any]],
) -> Dict[str, float]:
    """
    Build a raw competitor-intensity value per region from the ad-discovery
    output. A region where competitors run many high-impression ads is a
    market that rewards advertising -> a non-advertiser there is a stronger
    lead. Returns {region: raw_intensity}. Normalization happens later.
    """
    intensity: Dict[str, float] = {}

    for ad in active_ads or []:
        region = ad.get("target_region") or ad.get("region") or "UNKNOWN"
        impressions = ad.get("impressions") or {}
        raw_imp = impressions.get("lower_bound", 0) if isinstance(impressions, dict) else 0
        try:
            imp = float(raw_imp or 0)
        except (TypeError, ValueError):
            imp = 0.0
        # Each ad contributes 1 unit + an impressions bonus (log-damped).
        intensity[region] = intensity.get(region, 0.0) + 1.0 + (imp / 100_000.0)

    # If ad discovery only gave us aggregate advertisers (no per-region ads),
    # fall back to a single global intensity applied to every region.
    if not intensity and dominant_advertisers:
        global_units = sum(a.get("ad_count", 1) for a in dominant_advertisers)
        intensity["__GLOBAL__"] = float(global_units)

    return intensity


def _minmax(values: List[float]) -> List[float]:
    """Min-max normalize to [0, 1]. If all equal, return 0.5 for every item."""
    if not values:
        return []
    lo, hi = min(values), max(values)
    if hi - lo < 1e-9:
        return [0.5 for _ in values]
    return [(v - lo) / (hi - lo) for v in values]


# --------------------------------------------------------------------------- #
# Main entry point
# --------------------------------------------------------------------------- #

def qualify_leads(
    brands: List[Dict[str, Any]],
    dominant_advertisers: Optional[List[Dict[str, Any]]] = None,
    active_ads: Optional[List[Dict[str, Any]]] = None,
    weights: Optional[Dict[str, float]] = None,
    ideal_min_size: int = 10,
    ideal_max_size: int = 500,
    spend_is_reliable: bool = False,
    spend_threshold: float = 5000.0,
    gate_min_presence: float = 0.15,
) -> List[Dict[str, Any]]:
    """
    Score and rank brands as leads.

    Args:
        brands: each needs 'name', optional 'size'/'region'/'domain', and
                'ad_presence' = {'active_ads_count', 'estimated_spend',
                                 optional 'match_confidence' in [0,1]}.
        dominant_advertisers / active_ads: output of the Ad Discovery agent.
        weights: {'competitor_presence', 'brand_maturity', 'low_ad_presence'}.
        spend_is_reliable: set True only for EU/DSA or political ads where the
                           Meta spend field is populated.
        gate_min_presence: drop brands whose presence score is below this
                           (they are clearly already advertising heavily).

    Returns:
        Leads sorted by 'qualification_score' (raw weighted, descending).
    """
    weights = weights or {
        "competitor_presence": 0.4,
        "brand_maturity": 0.3,
        "low_ad_presence": 0.3,
    }
    dominant_advertisers = dominant_advertisers or []
    active_ads = active_ads or []

    region_intensity = _region_competitor_intensity(active_ads, dominant_advertisers)
    global_intensity = (
        sum(region_intensity.values()) / len(region_intensity)
        if region_intensity else 0.0
    )

    # First pass: raw component scores per brand.
    raw: List[Dict[str, Any]] = []
    for brand in brands:
        ap = brand.get("ad_presence", {}) or {}
        count = int(ap.get("active_ads_count", 0) or 0)
        spend = float(ap.get("estimated_spend", 0) or 0)
        confidence = float(ap.get("match_confidence", 1.0) or 1.0)
        if ap.get("page_unresolved"):
            continue  # unknown footprint, not a confirmed low-spend lead

        p = presence_score(count, spend, spend_is_reliable, spend_threshold)
        if p < gate_min_presence:
            continue  # already advertising heavily -> not a lead

        m = maturity_score(brand.get("size"), ideal_min_size, ideal_max_size)

        region = brand.get("region") or "UNKNOWN"
        comp_raw = region_intensity.get(
            region, region_intensity.get("__GLOBAL__", global_intensity)
        )

        raw.append({
            "brand": brand,
            "presence": p,
            "maturity": m,
            "comp_raw": comp_raw,
            "confidence": confidence,
        })

    if not raw:
        return []

    # Normalize competitor intensity across the batch so it actually spreads.
    comp_norm = _minmax([r["comp_raw"] for r in raw])

    # Second pass: weighted score (raw, in [0,1]).
    # Do NOT min-max the final score — on clustered data a 0.048 gap becomes
    # 1.000 vs 0.000, which is misleading. Use raw weighted scores for ranking.
    for r, cn in zip(raw, comp_norm):
        r["competitor"] = round(cn, 4)
        score = (
            weights["competitor_presence"] * r["competitor"]
            + weights["brand_maturity"] * r["maturity"]
            + weights["low_ad_presence"] * r["presence"]
        )
        score *= r["confidence"]  # low-confidence matches get penalized
        r["weighted"] = round(score, 4)

    # Optional: compute relative rank for context (0=worst in batch, 1=best).
    weighted_vals = [r["weighted"] for r in raw]
    rank_norm = _minmax(weighted_vals)

    leads: List[Dict[str, Any]] = []
    for r, rn in zip(raw, rank_norm):
        brand = r["brand"]
        ap = brand.get("ad_presence", {}) or {}
        reasons = []
        if r["presence"] >= 0.7:
            reasons.append("minimal ad presence")
        if r["maturity"] >= 0.7:
            reasons.append("good company size")
        if r["competitor"] >= 0.6:
            reasons.append("competitive market in region")
        if r["confidence"] < 0.8:
            reasons.append("brand match is approximate (verify)")

        leads.append({
            "brand_name": brand.get("name"),
            "domain": brand.get("domain"),
            "company_size": brand.get("size"),
            "industry": brand.get("industry"),
            "region": brand.get("region"),
            "ad_presence_summary": {
                "active_ads": ap.get("active_ads_count", 0),
                "estimated_spend": ap.get("estimated_spend", 0),
                "spend_reliable": spend_is_reliable,
                "library_url": ap.get("library_url"),
                "page_id": ap.get("page_id"),
            },
            "qualification_score": r["weighted"],  # raw weighted, for real differences
            "relative_rank_score": round(rn, 4),   # 0=worst, 1=best in this batch
            "score_breakdown": {
                "competitor_presence": r["competitor"],
                "brand_maturity": r["maturity"],
                "low_ad_presence": r["presence"],
                "match_confidence": r["confidence"],
            },
            "qualification_reason": ", ".join(reasons) or "meets basic criteria",
        })

    leads.sort(key=lambda x: x["qualification_score"], reverse=True)
    return leads


# --------------------------------------------------------------------------- #
# Smoke test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import random

    random.seed(7)
    regions = ["US", "GB", "CA"]

    active = []
    # US = hot market, GB = medium, CA = quiet.
    for _ in range(12):
        active.append({"target_region": "US", "impressions": {"lower_bound": random.randint(40_000, 200_000)}})
    for _ in range(5):
        active.append({"target_region": "GB", "impressions": {"lower_bound": random.randint(10_000, 60_000)}})
    for _ in range(1):
        active.append({"target_region": "CA", "impressions": {"lower_bound": 8_000}})

    brands = []
    for i in range(12):
        brands.append({
            "name": f"Brand {i:02d}",
            "domain": f"https://brand{i:02d}.com",
            "size": random.choice([5, 25, 60, 140, 320, 700]),
            "industry": "sustainable fashion",
            "region": random.choice(regions),
            "ad_presence": {
                "active_ads_count": random.choice([0, 0, 1, 2, 4, 8, 15]),
                "estimated_spend": 0,
                "match_confidence": random.choice([1.0, 1.0, 0.7]),
            },
        })

    leads = qualify_leads(
        brands,
        active_ads=active,
        dominant_advertisers=[{"name": "X", "ad_count": 18}],
    )

    print(f"{len(leads)} leads (of {len(brands)} brands)\n")
    print(f"{'Brand':10} {'Rgn':4} {'Size':>5} {'Ads':>4} "
          f"{'comp':>5} {'mat':>5} {'pres':>5} {'SCORE':>6}")
    print("-" * 56)
    for ld in leads:
        b = ld["score_breakdown"]
        print(f"{ld['brand_name']:10} {ld['region']:4} "
              f"{str(ld['company_size']):>5} "
              f"{ld['ad_presence_summary']['active_ads']:>4} "
              f"{b['competitor_presence']:>5.2f} {b['brand_maturity']:>5.2f} "
              f"{b['low_ad_presence']:>5.2f} {ld['qualification_score']:>6.3f}")

    scores = [ld["qualification_score"] for ld in leads]
    print(f"\nscore spread: min={min(scores):.3f} max={max(scores):.3f} "
          f"distinct={len(set(scores))}")
