from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.security import HTTPBearer
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
import time
import structlog
import os
from pathlib import Path

from database import engine, Base
from config import settings
from routers import auth, companies, users, documents, editals, admin
from middleware import audit_middleware, rate_limit_middleware

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

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting Edital.AI API")
    
    # Create upload directories
    upload_dirs = ["uploads", "uploads/documents", "uploads/editals", "uploads/temp"]
    for dir_path in upload_dirs:
        Path(dir_path).mkdir(parents=True, exist_ok=True)
    
    # Create database tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    logger.info("Database tables created")
    
    yield
    
    # Shutdown
    logger.info("Shutting down Edital.AI API")
    await engine.dispose()

# Initialize FastAPI app
app = FastAPI(
    title="Edital.AI API",
    description="API para análise inteligente de editais de licitação",
    version="1.0.0",
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url="/redoc" if settings.DEBUG else None,
    lifespan=lifespan
)

# Security
security = HTTPBearer(auto_error=False)

# Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_HOSTS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=settings.ALLOWED_HOSTS
)

# Custom middleware
app.middleware("http")(audit_middleware)
app.middleware("http")(rate_limit_middleware)

# Static files
if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

# Health check
@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "version": "1.0.0",
        "timestamp": time.time()
    }

# Metrics endpoint for monitoring
@app.get("/metrics")
async def metrics():
    # Basic metrics - can be extended with Prometheus
    return {
        "status": "ok",
        "uptime": time.time(),
        "version": "1.0.0"
    }

# Include routers
app.include_router(auth.router, prefix="/auth", tags=["Authentication"])
app.include_router(companies.router, prefix="/companies", tags=["Companies"])
app.include_router(users.router, prefix="/users", tags=["Users"])
app.include_router(documents.router, prefix="/documents", tags=["Documents"])
app.include_router(editals.router, prefix="/editals", tags=["Editals"])
app.include_router(admin.router, prefix="/admin", tags=["Admin"])

# Global exception handler
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    logger.error(
        "HTTP exception occurred",
        status_code=exc.status_code,
        detail=exc.detail,
        path=request.url.path
    )
    return {
        "error": True,
        "message": exc.detail,
        "status_code": exc.status_code
    }

@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    logger.error(
        "Unexpected error occurred",
        error=str(exc),
        path=request.url.path,
        exc_info=True
    )
    return {
        "error": True,
        "message": "Internal server error",
        "status_code": 500
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.DEBUG
    )
