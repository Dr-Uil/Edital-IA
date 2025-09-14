from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc
from typing import Optional
from datetime import datetime, timedelta
import structlog

from database import get_db
from models import User, Company, Edital, Document, AuditLog, Subscription
from schemas import DashboardStats, PaginatedResponse, AuditLog as AuditLogSchema
from auth import require_admin

logger = structlog.get_logger()
router = APIRouter()

@router.get("/dashboard", response_model=DashboardStats)
async def get_admin_dashboard(
    admin_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Get dashboard statistics for admin"""
    
    company_id = admin_user.company_id
    
    # Get total editals
    result = await db.execute(
        select(func.count(Edital.id)).where(Edital.company_id == company_id)
    )
    total_editals = result.scalar() or 0
    
    # Get editals this month
    current_month_start = datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    result = await db.execute(
        select(func.count(Edital.id)).where(
            Edital.company_id == company_id,
            Edital.created_at >= current_month_start
        )
    )
    editals_this_month = result.scalar() or 0
    
    # Get documents expiring soon (next 30 days)
    from datetime import date
    thirty_days_from_now = date.today() + timedelta(days=30)
    result = await db.execute(
        select(func.count(Document.id)).where(
            Document.company_id == company_id,
            Document.expiry_date <= thirty_days_from_now,
            Document.expiry_date > date.today()
        )
    )
    documents_expiring = result.scalar() or 0
    
    # Get expired documents
    result = await db.execute(
        select(func.count(Document.id)).where(
            Document.company_id == company_id,
            Document.expiry_date < date.today()
        )
    )
    documents_expired = result.scalar() or 0
    
    # Calculate compliance score (percentage of non-expired documents)
    result = await db.execute(
        select(func.count(Document.id)).where(Document.company_id == company_id)
    )
    total_documents = result.scalar() or 0
    
    compliance_score = 0.0
    if total_documents > 0:
        valid_documents = total_documents - documents_expired
        compliance_score = (valid_documents / total_documents) * 100
    
    return DashboardStats(
        total_editals=total_editals,
        editals_this_month=editals_this_month,
        documents_expiring=documents_expiring,
        documents_expired=documents_expired,
        compliance_score=compliance_score
    )

@router.get("/audit-logs", response_model=PaginatedResponse)
async def get_audit_logs(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
    action: Optional[str] = None,
    user_id: Optional[str] = None,
    days_back: int = Query(30, ge=1, le=365),
    admin_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Get audit logs for the company"""
    
    company_id = admin_user.company_id
    
    # Calculate date range
    date_from = datetime.now() - timedelta(days=days_back)
    
    # Build query
    query = select(AuditLog).where(
        AuditLog.company_id == company_id,
        AuditLog.created_at >= date_from
    )
    
    # Apply filters
    if action:
        query = query.where(AuditLog.action.ilike(f"%{action}%"))
    
    if user_id:
        try:
            import uuid
            query = query.where(AuditLog.user_id == uuid.UUID(user_id))
        except ValueError:
            pass  # Invalid UUID, ignore filter
    
    # Count total items
    count_result = await db.execute(
        select(func.count(AuditLog.id)).where(
            AuditLog.company_id == company_id,
            AuditLog.created_at >= date_from
        )
    )
    total = count_result.scalar() or 0
    
    # Apply pagination and ordering
    query = query.order_by(desc(AuditLog.created_at))
    query = query.offset((page - 1) * per_page).limit(per_page)
    
    # Execute query
    result = await db.execute(query)
    audit_logs = result.scalars().all()
    
    # Calculate pagination info
    pages = (total + per_page - 1) // per_page
    
    return PaginatedResponse(
        items=[AuditLogSchema.model_validate(log) for log in audit_logs],
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
        has_next=page < pages,
        has_prev=page > 1
    )

@router.get("/statistics")
async def get_company_statistics(
    admin_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Get detailed company statistics"""
    
    company_id = admin_user.company_id
    
    # Users statistics
    result = await db.execute(
        select(func.count(User.id)).where(
            User.company_id == company_id,
            User.is_active == True
        )
    )
    active_users = result.scalar() or 0
    
    result = await db.execute(
        select(func.count(User.id)).where(User.company_id == company_id)
    )
    total_users = result.scalar() or 0
    
    # Documents statistics
    result = await db.execute(
        select(func.count(Document.id)).where(Document.company_id == company_id)
    )
    total_documents = result.scalar() or 0
    
    # Documents by type
    result = await db.execute(
        select(Document.type, func.count(Document.id)).where(
            Document.company_id == company_id
        ).group_by(Document.type)
    )
    documents_by_type = {row[0].value: row[1] for row in result.fetchall()}
    
    # Editals by status
    result = await db.execute(
        select(Edital.analysis_status, func.count(Edital.id)).where(
            Edital.company_id == company_id
        ).group_by(Edital.analysis_status)
    )
    editals_by_status = {row[0].value: row[1] for row in result.fetchall()}
    
    # Monthly activity (last 6 months)
    monthly_stats = []
    for i in range(6):
        month_start = datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0) - timedelta(days=i*30)
        month_end = month_start + timedelta(days=30)
        
        # Editals uploaded this month
        result = await db.execute(
            select(func.count(Edital.id)).where(
                Edital.company_id == company_id,
                Edital.created_at >= month_start,
                Edital.created_at < month_end
            )
        )
        editals_count = result.scalar() or 0
        
        # Documents uploaded this month
        result = await db.execute(
            select(func.count(Document.id)).where(
                Document.company_id == company_id,
                Document.created_at >= month_start,
                Document.created_at < month_end
            )
        )
        documents_count = result.scalar() or 0
        
        monthly_stats.append({
            "month": month_start.strftime("%Y-%m"),
            "editals": editals_count,
            "documents": documents_count
        })
    
    # Storage usage (approximate)
    result = await db.execute(
        select(func.sum(Document.file_size)).where(Document.company_id == company_id)
    )
    documents_storage = result.scalar() or 0
    
    result = await db.execute(
        select(func.sum(Edital.file_size)).where(Edital.company_id == company_id)
    )
    editals_storage = result.scalar() or 0
    
    total_storage_bytes = documents_storage + editals_storage
    total_storage_mb = total_storage_bytes / (1024 * 1024) if total_storage_bytes else 0
    
    return {
        "users": {
            "total": total_users,
            "active": active_users,
            "inactive": total_users - active_users
        },
        "documents": {
            "total": total_documents,
            "by_type": documents_by_type
        },
        "editals": {
            "by_status": editals_by_status
        },
        "storage": {
            "total_mb": round(total_storage_mb, 2),
            "documents_mb": round(documents_storage / (1024 * 1024), 2) if documents_storage else 0,
            "editals_mb": round(editals_storage / (1024 * 1024), 2) if editals_storage else 0
        },
        "monthly_activity": list(reversed(monthly_stats))
    }
