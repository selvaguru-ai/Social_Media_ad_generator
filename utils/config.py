"""Base configuration and settings management."""
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings

# Load environment variables
load_dotenv()

PROJECT_ROOT = Path(__file__).parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.yaml"
DATA_DIR = PROJECT_ROOT / "data"
LOGS_DIR = PROJECT_ROOT / "logs"

# Ensure directories exist
DATA_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)


class MarketConfig(BaseModel):
    """Market targeting configuration."""
    niche: str
    regions: List[str]
    seed_brands: List[str] = Field(default_factory=list)


class NicheFilterConfig(BaseModel):
    """LLM relevance filter for Ad Discovery."""
    provider: str = "anthropic"
    model: str = "claude-sonnet-4-6"
    enabled: bool = True


class AdDiscoveryConfig(BaseModel):
    """Ad discovery agent configuration."""
    ads_per_query: int = 100
    max_kept_ads: int = 10
    min_impressions: int = 100
    lookback_days: int = 90
    max_requests_per_hour: int = 180
    backoff_seconds: int = 3
    niche_filter: NicheFilterConfig = Field(default_factory=NicheFilterConfig)


class ProspectConfig(BaseModel):
    """Prospect agent configuration."""
    max_leads: int = 50
    min_company_size: int = 10
    max_company_size: int = 500
    max_ad_spend_threshold: int = 5000
    qualification_weights: Dict[str, float] = Field(default_factory=dict)
    # ads_archive search_terms + page_name match. Off by default: it burns
    # rate limit and rarely resolves major brands (keyword junk, no classic ID).
    enable_name_search_resolver: bool = False
    # Extra countries unioned into ads_archive presence checks. Meta often
    # tags commercial ads as GB/EU even when the Ad Library UI shows them
    # for a US page (US-only then false-zeros the brand).
    presence_country_fallback: List[str] = Field(
        default_factory=lambda: ["GB", "CA", "AU", "DE", "FR", "IE"]
    )


class ContactEnrichmentConfig(BaseModel):
    """Contact enrichment agent configuration."""
    target_roles: List[str]
    min_confidence: float = 0.7
    max_requests_per_minute: int = 60


class CreativeConfig(BaseModel):
    """Creative agent configuration."""
    video_duration: int = 15
    video_format: str = "mp4"
    video_resolution: str = "1080p"
    generation_timeout_seconds: int = 300
    poll_interval_seconds: int = 10
    max_poll_attempts: int = 30
    default_style: str = "modern and professional"
    max_concurrent_generations: int = 3


class OutreachConfig(BaseModel):
    """Outreach agent configuration."""
    personalization_level: str = "high"
    max_sends_per_day: int = 50
    min_seconds_between_sends: int = 60
    compliance: Dict[str, Any] = Field(default_factory=dict)


class OrchestratorConfig(BaseModel):
    """Orchestrator configuration."""
    max_retries: int = 3
    retry_backoff_seconds: int = 5
    state_file: str = "data/pipeline_state.json"
    log_level: str = "INFO"
    log_file: str = "logs/pipeline.log"


class ApprovalConfig(BaseModel):
    """Human approval gate configuration."""
    enabled: bool = True
    method: str = "cli"
    auto_approve: bool = False


class Config(BaseModel):
    """Main configuration model."""
    market: MarketConfig
    ad_discovery: AdDiscoveryConfig
    prospect: ProspectConfig
    contact_enrichment: ContactEnrichmentConfig
    creative: CreativeConfig
    outreach: OutreachConfig
    orchestrator: OrchestratorConfig
    approval: ApprovalConfig


class Settings(BaseSettings):
    """Environment variables and secrets."""
    
    # Meta Ad Library
    meta_access_token: Optional[str] = None
    meta_app_id: Optional[str] = None
    meta_app_secret: Optional[str] = None
    
    # Contact enrichment
    apollo_api_key: Optional[str] = None
    hunter_api_key: Optional[str] = None
    clearbit_api_key: Optional[str] = None
    
    # Higgsfield
    higgsfield_api_key: Optional[str] = None
    higgsfield_base_url: str = "https://cloud.higgsfield.ai/api"
    
    # Email
    smtp_host: Optional[str] = None
    smtp_port: int = 587
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None
    sender_email: Optional[str] = None
    sender_name: Optional[str] = None
    
    # Database
    database_url: str = "sqlite:///./data/pipeline.db"
    
    # Optional
    crunchbase_api_key: Optional[str] = None

    # LLM niche filter (Ad Discovery)
    anthropic_api_key: Optional[str] = None
    openai_api_key: Optional[str] = None
    
    class Config:
        env_file = ".env"
        case_sensitive = False


def load_config(config_path: Path = CONFIG_PATH) -> Config:
    """Load configuration from YAML file."""
    with open(config_path, 'r') as f:
        config_dict = yaml.safe_load(f)
    return Config(**config_dict)


def get_settings() -> Settings:
    """Get environment settings."""
    return Settings()


# Global instances
config = load_config()
settings = get_settings()
