import time
import json
from fastapi import Request, Response, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
import redis.asyncio as redis
import structlog
from typing import Callable, Dict, Any
import uuid

from database import async_session_maker
from models import AuditLog
from config import settings

logger = structlog.get_logger()

# Redis client for rate limiting
redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)

async def audit_middleware(request: Request, call_next: Callable) -> Response:
    """Middleware for logging user actions for audit purposes"""
    
    start_time = time.time()
    
    # Get user info from token if available
    user_id = None
    company_id = None
    
    # Extract from Authorization header if present
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        try:
            # This would typically decode JWT - simplified for demo
            # user_info = decode_jwt_token(auth_header.split(" ")[1])
            # user_id = user_info.get("user_id")
            # company_id = user_info.get("company_id")
            pass
        except Exception:
            pass
    
    # Store request info for later
    request_data = {
        "method": request.method,
        "url": str(request.url),
        "headers": dict(request.headers),
        "client_ip": request.client.host if request.client else None,
        "user_agent": request.headers.get("User-Agent"),
        "user_id": user_id,
        "company_id": company_id
    }
    
    # Process request
    response = await call_next(request)
    
    # Calculate processing time
    process_time = time.time() - start_time
    
    # Log important actions
    if should_audit_action(request.method, request.url.path, response.status_code):
        try:
            async with async_session_maker() as session:
                audit_log = AuditLog(
                    user_id=user_id,
                    company_id=company_id,
                    action=f"{request.method} {request.url.path}",
                    entity_type=extract_entity_type(request.url.path),
                    ip_address=request_data["client_ip"],
                    user_agent=request_data["user_agent"],
                    new_values={
                        "status_code": response.status_code,
                        "process_time": process_time
                    }
                )
                session.add(audit_log)
                await session.commit()
        except Exception as e:
            logger.error("Failed to create audit log", error=str(e))
    
    # Add process time header
    response.headers["X-Process-Time"] = str(process_time)
    
    return response

def should_audit_action(method: str, path: str, status_code: int) -> bool:
    """Determine if an action should be audited"""
    
    # Always audit write operations
    if method in ["POST", "PUT", "DELETE", "PATCH"]:
        return True
    
    # Audit failed authentication attempts
    if "auth" in path and status_code >= 400:
        return True
    
    # Audit sensitive GET operations
    sensitive_paths = ["/documents", "/editals", "/companies", "/users"]
    if any(sensitive in path for sensitive in sensitive_paths) and method == "GET":
        return True
    
    return False

def extract_entity_type(path: str) -> str:
    """Extract entity type from URL path"""
    path_segments = path.strip("/").split("/")
    
    if len(path_segments) > 0:
        entity_mapping = {
            "users": "USER",
            "companies": "COMPANY", 
            "documents": "DOCUMENT",
            "editals": "EDITAL",
            "auth": "AUTH"
        }
        return entity_mapping.get(path_segments[0], "UNKNOWN")
    
    return "UNKNOWN"

async def rate_limit_middleware(request: Request, call_next: Callable) -> Response:
    """Middleware for rate limiting requests"""
    
    # Get client identifier
    client_ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("User-Agent", "unknown")
    client_id = f"{client_ip}:{hash(user_agent) % 10000}"
    
    # Rate limit key
    rate_limit_key = f"rate_limit:{client_id}:{int(time.time() // settings.RATE_LIMIT_WINDOW)}"
    
    try:
        # Check current request count
        current_requests = await redis_client.get(rate_limit_key)
        current_requests = int(current_requests) if current_requests else 0
        
        # Check if rate limit exceeded
        if current_requests >= settings.RATE_LIMIT_REQUESTS:
            logger.warning(
                "Rate limit exceeded",
                client_id=client_id,
                requests=current_requests,
                limit=settings.RATE_LIMIT_REQUESTS
            )
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded. Please try again later."
            )
        
        # Process request
        response = await call_next(request)
        
        # Increment request count
        pipe = redis_client.pipeline()
        pipe.incr(rate_limit_key)
        pipe.expire(rate_limit_key, settings.RATE_LIMIT_WINDOW)
        await pipe.execute()
        
        # Add rate limit headers
        response.headers["X-RateLimit-Limit"] = str(settings.RATE_LIMIT_REQUESTS)
        response.headers["X-RateLimit-Remaining"] = str(settings.RATE_LIMIT_REQUESTS - current_requests - 1)
        response.headers["X-RateLimit-Reset"] = str(int(time.time()) + settings.RATE_LIMIT_WINDOW)
        
        return response
        
    except redis.RedisError as e:
        logger.error("Redis error in rate limiting", error=str(e))
        # If Redis is down, allow request to proceed
        return await call_next(request)
    except HTTPException:
        # Re-raise HTTP exceptions (like rate limit exceeded)
        raise
    except Exception as e:
        logger.error("Unexpected error in rate limiting", error=str(e))
        return await call_next(request)

class SecurityHeadersMiddleware:
    """Middleware to add security headers"""
    
    def __init__(self, app):
        self.app = app
    
    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            async def send_wrapper(message):
                if message["type"] == "http.response.start":
                    headers = dict(message.get("headers", []))
                    
                    # Add security headers
                    security_headers = {
                        b"X-Content-Type-Options": b"nosniff",
                        b"X-Frame-Options": b"DENY",
                        b"X-XSS-Protection": b"1; mode=block",
                        b"Strict-Transport-Security": b"max-age=31536000; includeSubDomains",
                        b"Referrer-Policy": b"strict-origin-when-cross-origin",
                        b"Content-Security-Policy": b"default-src 'self'; img-src 'self' data: https:; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'"
                    }
                    
                    # Update headers
                    for key, value in security_headers.items():
                        headers[key] = value
                    
                    message["headers"] = list(headers.items())
                
                await send(message)
            
            await self.app(scope, receive, send_wrapper)
        else:
            await self.app(scope, receive, send)
