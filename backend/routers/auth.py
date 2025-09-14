from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import timedelta
import structlog
import uuid

from database import get_db
from models import User, Company, Subscription, SubscriptionPlan
from schemas import (
    LoginRequest, RegisterRequest, Token, User as UserSchema, 
    UserWithCompany, APIResponse
)
from auth import (
    authenticate_user, create_access_token, get_password_hash,
    create_user_token_data, get_current_active_user,
    create_email_verification_token, verify_email_token,
    create_password_reset_token, verify_password_reset_token,
    validate_password_strength
)
from config import settings
from utils.email import send_verification_email, send_password_reset_email

logger = structlog.get_logger()
router = APIRouter()

@router.post("/login", response_model=Token)
async def login(
    login_data: LoginRequest,
    db: AsyncSession = Depends(get_db)
):
    """User login endpoint"""
    
    user = await authenticate_user(db, login_data.email, login_data.password)
    
    if not user:
        logger.warning("Failed login attempt", email=login_data.email)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    if not user.email_verified:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email not verified. Please check your email and verify your account.",
        )
    
    # Get user's company
    company = None
    if user.company_id:
        result = await db.execute(select(Company).where(Company.id == user.company_id))
        company = result.scalar_one_or_none()
    
    # Create token
    token_data = create_user_token_data(user, company)
    access_token_expires = timedelta(minutes=settings.JWT_EXPIRATION_TIME)
    access_token = create_access_token(token_data, expires_delta=access_token_expires)
    
    logger.info("User logged in successfully", user_id=str(user.id), email=user.email)
    
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": settings.JWT_EXPIRATION_TIME * 60  # Convert to seconds
    }

@router.post("/register", response_model=APIResponse)
async def register(
    register_data: RegisterRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    """User registration endpoint"""
    
    try:
        # Check if user already exists
        result = await db.execute(select(User).where(User.email == register_data.email.lower()))
        existing_user = result.scalar_one_or_none()
        
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already registered"
            )
        
        # Check if company CNPJ already exists
        result = await db.execute(select(Company).where(Company.cnpj == register_data.company_data.cnpj))
        existing_company = result.scalar_one_or_none()
        
        if existing_company:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Company CNPJ already registered"
            )
        
        # Validate password strength
        is_strong, errors = validate_password_strength(register_data.password)
        if not is_strong:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Password requirements not met: {'; '.join(errors)}"
            )
        
        # Create company
        company = Company(
            razao_social=register_data.company_data.razao_social,
            nome_fantasia=register_data.company_data.nome_fantasia,
            cnpj=register_data.company_data.cnpj,
            endereco=register_data.company_data.endereco,
            telefone=register_data.company_data.telefone,
            email=register_data.company_data.email
        )
        db.add(company)
        await db.flush()  # Get the company ID
        
        # Create user as admin of the company
        hashed_password = get_password_hash(register_data.password)
        user = User(
            email=register_data.email.lower(),
            password_hash=hashed_password,
            first_name=register_data.first_name,
            last_name=register_data.last_name,
            role="ADMIN",
            company_id=company.id,
            is_active=True,
            email_verified=False
        )
        db.add(user)
        await db.flush()
        
        # Create trial subscription (Essencial plan)
        result = await db.execute(
            select(SubscriptionPlan).where(SubscriptionPlan.name == "Essencial")
        )
        essencial_plan = result.scalar_one_or_none()
        
        if essencial_plan:
            from datetime import date
            subscription = Subscription(
                company_id=company.id,
                plan_id=essencial_plan.id,
                status="ACTIVE",
                current_period_start=date.today(),
                current_period_end=date.today().replace(day=28) if date.today().day <= 28 else date.today().replace(month=date.today().month + 1, day=28),
                analyses_used=0
            )
            db.add(subscription)
        
        await db.commit()
        
        # Send email verification
        verification_token = create_email_verification_token(user.id)
        background_tasks.add_task(
            send_verification_email,
            user.email,
            user.first_name,
            verification_token
        )
        
        logger.info(
            "User registered successfully",
            user_id=str(user.id),
            email=user.email,
            company_id=str(company.id)
        )
        
        return APIResponse(
            success=True,
            message="Registration successful. Please check your email to verify your account.",
            data={"user_id": str(user.id), "company_id": str(company.id)}
        )
        
    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        logger.error("Registration error", error=str(e), email=register_data.email)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Registration failed"
        )

@router.post("/verify-email", response_model=APIResponse)
async def verify_email(
    token: str,
    db: AsyncSession = Depends(get_db)
):
    """Verify email address"""
    
    user_id = verify_email_token(token)
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired verification token"
        )
    
    try:
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )
        
        if user.email_verified:
            return APIResponse(
                success=True,
                message="Email already verified"
            )
        
        user.email_verified = True
        await db.commit()
        
        logger.info("Email verified successfully", user_id=str(user.id))
        
        return APIResponse(
            success=True,
            message="Email verified successfully. You can now log in."
        )
        
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        logger.error("Email verification error", error=str(e), user_id=str(user_id))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Email verification failed"
        )

@router.post("/resend-verification", response_model=APIResponse)
async def resend_verification_email(
    email: str,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    """Resend email verification"""
    
    try:
        result = await db.execute(select(User).where(User.email == email.lower()))
        user = result.scalar_one_or_none()
        
        if not user:
            # Don't reveal if email exists or not
            return APIResponse(
                success=True,
                message="If the email exists, a verification link has been sent."
            )
        
        if user.email_verified:
            return APIResponse(
                success=True,
                message="Email is already verified."
            )
        
        verification_token = create_email_verification_token(user.id)
        background_tasks.add_task(
            send_verification_email,
            user.email,
            user.first_name,
            verification_token
        )
        
        logger.info("Verification email resent", user_id=str(user.id))
        
        return APIResponse(
            success=True,
            message="If the email exists, a verification link has been sent."
        )
        
    except Exception as e:
        logger.error("Resend verification error", error=str(e), email=email)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to resend verification email"
        )

@router.post("/forgot-password", response_model=APIResponse)
async def forgot_password(
    email: str,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    """Initiate password reset"""
    
    try:
        result = await db.execute(select(User).where(User.email == email.lower()))
        user = result.scalar_one_or_none()
        
        if user and user.email_verified:
            reset_token = create_password_reset_token(user.id)
            background_tasks.add_task(
                send_password_reset_email,
                user.email,
                user.first_name,
                reset_token
            )
            
            logger.info("Password reset requested", user_id=str(user.id))
        
        # Always return success to prevent email enumeration
        return APIResponse(
            success=True,
            message="If the email exists, a password reset link has been sent."
        )
        
    except Exception as e:
        logger.error("Forgot password error", error=str(e), email=email)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to process password reset request"
        )

@router.post("/reset-password", response_model=APIResponse)
async def reset_password(
    token: str,
    new_password: str,
    db: AsyncSession = Depends(get_db)
):
    """Reset password with token"""
    
    user_id = verify_password_reset_token(token)
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token"
        )
    
    # Validate password strength
    is_strong, errors = validate_password_strength(new_password)
    if not is_strong:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Password requirements not met: {'; '.join(errors)}"
        )
    
    try:
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )
        
        user.password_hash = get_password_hash(new_password)
        await db.commit()
        
        logger.info("Password reset successfully", user_id=str(user.id))
        
        return APIResponse(
            success=True,
            message="Password reset successfully. You can now log in with your new password."
        )
        
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        logger.error("Password reset error", error=str(e), user_id=str(user_id))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Password reset failed"
        )

@router.get("/me", response_model=UserWithCompany)
async def get_current_user_info(
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """Get current user information"""
    
    # Get company info
    company = None
    if current_user.company_id:
        result = await db.execute(select(Company).where(Company.id == current_user.company_id))
        company = result.scalar_one_or_none()
    
    return UserWithCompany(
        **current_user.__dict__,
        company=company
    )

@router.post("/logout", response_model=APIResponse)
async def logout(current_user: User = Depends(get_current_active_user)):
    """User logout (client-side token invalidation)"""
    
    logger.info("User logged out", user_id=str(current_user.id))
    
    return APIResponse(
        success=True,
        message="Logged out successfully"
    )
