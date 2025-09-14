from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from fastapi import HTTPException, Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import structlog
import uuid

from database import get_db
from models import User, Company
from schemas import TokenData
from config import settings

# Logging
logger = structlog.get_logger()

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# JWT Bearer scheme
security = HTTPBearer(auto_error=False)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash"""
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    """Hash a password"""
    return pwd_context.hash(password)

def create_access_token(data: Dict[str, Any], expires_delta: Optional[timedelta] = None) -> str:
    """Create a JWT access token"""
    to_encode = data.copy()
    
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=settings.JWT_EXPIRATION_TIME)
    
    to_encode.update({"exp": expire})
    
    encoded_jwt = jwt.encode(
        to_encode,
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM
    )
    
    return encoded_jwt

def decode_access_token(token: str) -> TokenData:
    """Decode and validate JWT token"""
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM]
        )
        
        user_id: str = payload.get("sub")
        if user_id is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token: missing user ID",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
        token_data = TokenData(user_id=uuid.UUID(user_id))
        return token_data
        
    except JWTError as e:
        logger.error("JWT decode error", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except ValueError as e:
        logger.error("Invalid UUID in token", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token format",
            headers={"WWW-Authenticate": "Bearer"},
        )

async def authenticate_user(db: AsyncSession, email: str, password: str) -> Optional[User]:
    """Authenticate user with email and password"""
    try:
        result = await db.execute(
            select(User).where(
                User.email == email.lower(),
                User.is_active == True
            )
        )
        user = result.scalar_one_or_none()
        
        if not user:
            return None
        
        if not verify_password(password, user.password_hash):
            return None
        
        return user
        
    except Exception as e:
        logger.error("Authentication error", error=str(e), email=email)
        return None

async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: AsyncSession = Depends(get_db)
) -> User:
    """Get current authenticated user from token"""
    
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    token_data = decode_access_token(credentials.credentials)
    
    try:
        result = await db.execute(
            select(User).where(
                User.id == token_data.user_id,
                User.is_active == True
            )
        )
        user = result.scalar_one_or_none()
        
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found or inactive",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
        return user
        
    except Exception as e:
        logger.error("Error getting current user", error=str(e), user_id=token_data.user_id)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication error",
            headers={"WWW-Authenticate": "Bearer"},
        )

async def get_current_active_user(current_user: User = Depends(get_current_user)) -> User:
    """Get current active user (additional validation)"""
    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Inactive user"
        )
    return current_user

async def get_current_user_with_company(
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
) -> tuple[User, Company]:
    """Get current user with company data"""
    
    if not current_user.company_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User has no associated company"
        )
    
    try:
        result = await db.execute(
            select(Company).where(Company.id == current_user.company_id)
        )
        company = result.scalar_one_or_none()
        
        if not company:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Company not found"
            )
        
        return current_user, company
        
    except Exception as e:
        logger.error("Error getting user company", error=str(e), user_id=current_user.id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error retrieving company data"
        )

def require_admin(current_user: User = Depends(get_current_active_user)) -> User:
    """Require user to have admin role"""
    if current_user.role != "ADMIN":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required"
        )
    return current_user

def require_same_company(target_company_id: uuid.UUID):
    """Require user to belong to the same company"""
    async def _require_same_company(
        current_user: User = Depends(get_current_active_user)
    ) -> User:
        if current_user.company_id != target_company_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: different company"
            )
        return current_user
    
    return _require_same_company

async def verify_company_access(
    user: User,
    company_id: uuid.UUID
) -> bool:
    """Verify if user has access to a specific company"""
    return user.company_id == company_id or user.role == "ADMIN"

def create_user_token_data(user: User, company: Optional[Company] = None) -> Dict[str, Any]:
    """Create token payload data for user"""
    token_data = {
        "sub": str(user.id),
        "email": user.email,
        "role": user.role,
        "company_id": str(user.company_id) if user.company_id else None,
    }
    
    if company:
        token_data["company_name"] = company.razao_social
        token_data["cnpj"] = company.cnpj
    
    return token_data

# Email verification utilities
def create_email_verification_token(user_id: uuid.UUID) -> str:
    """Create email verification token"""
    data = {"sub": str(user_id), "type": "email_verification"}
    expire = datetime.utcnow() + timedelta(hours=24)  # 24 hour expiry
    data.update({"exp": expire})
    
    return jwt.encode(
        data,
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM
    )

def verify_email_token(token: str) -> Optional[uuid.UUID]:
    """Verify email verification token"""
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM]
        )
        
        if payload.get("type") != "email_verification":
            return None
        
        user_id = payload.get("sub")
        if user_id is None:
            return None
        
        return uuid.UUID(user_id)
        
    except JWTError:
        return None
    except ValueError:
        return None

# Password reset utilities
def create_password_reset_token(user_id: uuid.UUID) -> str:
    """Create password reset token"""
    data = {"sub": str(user_id), "type": "password_reset"}
    expire = datetime.utcnow() + timedelta(hours=1)  # 1 hour expiry
    data.update({"exp": expire})
    
    return jwt.encode(
        data,
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM
    )

def verify_password_reset_token(token: str) -> Optional[uuid.UUID]:
    """Verify password reset token"""
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM]
        )
        
        if payload.get("type") != "password_reset":
            return None
        
        user_id = payload.get("sub")
        if user_id is None:
            return None
        
        return uuid.UUID(user_id)
        
    except JWTError:
        return None
    except ValueError:
        return None

# Session management
async def invalidate_user_sessions(user_id: uuid.UUID):
    """Invalidate all user sessions (would require session store in production)"""
    # In a real implementation, this would blacklist tokens or increment a user version
    # For now, we'll log the event
    logger.info("Sessions invalidated for user", user_id=str(user_id))

# Security utilities
def validate_password_strength(password: str) -> tuple[bool, list[str]]:
    """Validate password strength"""
    errors = []
    
    if len(password) < 8:
        errors.append("Password must be at least 8 characters long")
    
    if not any(c.isupper() for c in password):
        errors.append("Password must contain at least one uppercase letter")
    
    if not any(c.islower() for c in password):
        errors.append("Password must contain at least one lowercase letter")
    
    if not any(c.isdigit() for c in password):
        errors.append("Password must contain at least one number")
    
    # Check for common weak passwords
    common_weak = [
        "password", "12345678", "qwerty123", "admin123", 
        "letmein", "welcome123", "password123"
    ]
    if password.lower() in common_weak:
        errors.append("Password is too common")
    
    return len(errors) == 0, errors
