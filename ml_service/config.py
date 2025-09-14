from pydantic_settings import BaseSettings
from pathlib import Path

class Settings(BaseSettings):
    # Service configuration
    DEBUG: bool = True
    SERVICE_NAME: str = "edital_ai_ml"
    
    # Model paths
    MODEL_PATH: Path = Path("models")
    SPACY_MODEL: str = "pt_core_news_sm"
    
    # Redis for caching
    REDIS_URL: str = "redis://localhost:6379"
    CACHE_TTL: int = 3600  # 1 hour
    
    # NER model configuration
    NER_CONFIDENCE_THRESHOLD: float = 0.5
    MAX_TEXT_LENGTH: int = 1000000  # 1MB of text
    
    # Analysis configuration
    ENABLE_CACHE: bool = True
    BATCH_SIZE: int = 32
    
    class Config:
        env_file = ".env"
        case_sensitive = True

settings = Settings()

# Ensure model directory exists
settings.MODEL_PATH.mkdir(parents=True, exist_ok=True)
