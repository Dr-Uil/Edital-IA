from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import structlog

from database import get_db
from models import Company, User
from schemas import Company as CompanySchema, CompanyUpdate, APIResponse
from auth import get_current_user_with_company, require_admin

logger = structlog.get_logger()
router = APIRouter()

@router.get("/me", response_model=CompanySchema)
async def get_current_company(
    current_user_company: tuple[User, Company] = Depends(get_current_user_with_company)
):
    """Get current user's company information"""
    
    user, company = current_user_company
    
    return CompanySchema.model_validate(company)

@router.put("/me", response_model=CompanySchema)
async def update_current_company(
    company_update: CompanyUpdate,
    current_user_company: tuple[User, Company] = Depends(get_current_user_with_company),
    db: AsyncSession = Depends(get_db)
):
    """Update current company information (admin only)"""
    
    current_user, company = current_user_company
    
    # Only admins can update company info
    if current_user.role != "ADMIN":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins can update company information"
        )
    
    try:
        # Update fields
        update_data = company_update.model_dump(exclude_unset=True)
        
        for field, value in update_data.items():
            setattr(company, field, value)
        
        await db.commit()
        await db.refresh(company)
        
        logger.info("Company updated", company_id=str(company.id), updated_by=str(current_user.id))
        
        return CompanySchema.model_validate(company)
        
    except Exception as e:
        await db.rollback()
        logger.error("Company update error", error=str(e), company_id=str(company.id))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update company information"
        )
