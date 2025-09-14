from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import structlog
import re
from datetime import datetime
from decimal import Decimal
import uuid

from models import EditalAnalyzer
from config import settings

# Configure logging
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer()
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()

app = FastAPI(
    title="Edital.AI ML Service",
    description="Machine Learning service for analyzing legal documents",
    version="1.0.0"
)

# Schemas
class MLAnalysisRequest(BaseModel):
    edital_id: str
    file_path: str
    text_content: Optional[str] = None

class ExtractedEntityBase(BaseModel):
    entity_type: str
    entity_value: str
    confidence: Optional[Decimal] = None
    start_position: Optional[int] = None
    end_position: Optional[int] = None

class HabilitacaoRequirementBase(BaseModel):
    requirement_type: str
    description: str
    document_type: Optional[str] = None
    is_mandatory: bool = True

class EditalAnalysisBase(BaseModel):
    organizacao_licitante: Optional[str] = None
    modalidade_licitacao: Optional[str] = None
    numero_processo: Optional[str] = None
    data_abertura_propostas: Optional[datetime] = None
    data_sessao_publica: Optional[datetime] = None
    objeto_licitacao: Optional[str] = None
    criterio_julgamento: Optional[str] = None
    valor_estimado: Optional[Decimal] = None

class MLAnalysisResponse(BaseModel):
    success: bool
    analysis: Optional[EditalAnalysisBase] = None
    entities: List[ExtractedEntityBase] = []
    requirements: List[HabilitacaoRequirementBase] = []
    error: Optional[str] = None

# Initialize analyzer
analyzer = EditalAnalyzer()

@app.on_event("startup")
async def startup_event():
    """Initialize ML models on startup"""
    logger.info("Starting ML service")
    await analyzer.initialize()
    logger.info("ML service initialized successfully")

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": "ml_service",
        "version": "1.0.0"
    }

@app.post("/analyze", response_model=MLAnalysisResponse)
async def analyze_edital(request: MLAnalysisRequest):
    """Analyze edital document using ML models"""
    
    logger.info("Starting edital analysis", edital_id=request.edital_id)
    
    try:
        if not request.text_content:
            return MLAnalysisResponse(
                success=False,
                error="No text content provided"
            )
        
        # Perform analysis
        analysis_result = await analyzer.analyze_text(request.text_content)
        
        logger.info("Analysis completed", edital_id=request.edital_id)
        
        return MLAnalysisResponse(
            success=True,
            analysis=analysis_result.get("analysis"),
            entities=analysis_result.get("entities", []),
            requirements=analysis_result.get("requirements", [])
        )
        
    except Exception as e:
        logger.error("Analysis failed", error=str(e), edital_id=request.edital_id)
        return MLAnalysisResponse(
            success=False,
            error=f"Analysis failed: {str(e)}"
        )

@app.get("/models/status")
async def get_models_status():
    """Get status of loaded ML models"""
    return await analyzer.get_status()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8001, reload=True)
