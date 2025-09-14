from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Query, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, desc
from typing import List, Optional
import structlog
import uuid
import httpx

from database import get_db
from models import (
    Edital, EditalAnalysis, ExtractedEntity, HabilitacaoRequirement, 
    User, Company, Document, AnalysisStatus, DocumentType
)
from schemas import (
    Edital as EditalSchema, EditalWithAnalysis, EditalAnalysis as EditalAnalysisSchema,
    ExtractedEntity as ExtractedEntitySchema, HabilitacaoRequirement as HabilitacaoRequirementSchema,
    PaginatedResponse, APIResponse, EditalChecklist, ChecklistItem,
    MLAnalysisRequest, MLAnalysisResponse
)
from auth import get_current_user_with_company
from config import settings
from utils.file_storage import FileStorage, DocumentProcessor
from utils.email import send_analysis_complete_email

logger = structlog.get_logger()
router = APIRouter()

# Initialize services
file_storage = FileStorage()

@router.get("/", response_model=PaginatedResponse)
async def list_editals(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    status: Optional[AnalysisStatus] = None,
    search: Optional[str] = None,
    current_user_company: tuple[User, Company] = Depends(get_current_user_with_company),
    db: AsyncSession = Depends(get_db)
):
    """List company editals with pagination and filters"""
    
    user, company = current_user_company
    
    # Build query
    query = select(Edital).where(Edital.company_id == company.id)
    
    # Apply filters
    if status:
        query = query.where(Edital.analysis_status == status)
    
    if search:
        query = query.where(Edital.original_filename.ilike(f"%{search}%"))
    
    # Count total items
    count_result = await db.execute(
        select(Edital).where(Edital.company_id == company.id)
    )
    total = len(count_result.fetchall())
    
    # Apply pagination and ordering
    query = query.order_by(desc(Edital.created_at))
    query = query.offset((page - 1) * per_page).limit(per_page)
    
    # Execute query
    result = await db.execute(query)
    editals = result.scalars().all()
    
    # Calculate pagination info
    pages = (total + per_page - 1) // per_page
    
    return PaginatedResponse(
        items=[EditalSchema.model_validate(edital) for edital in editals],
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
        has_next=page < pages,
        has_prev=page > 1
    )

@router.post("/", response_model=EditalSchema)
async def upload_edital(
    file: UploadFile = File(...),
    background_tasks: BackgroundTasks,
    current_user_company: tuple[User, Company] = Depends(get_current_user_with_company),
    db: AsyncSession = Depends(get_db)
):
    """Upload a new edital for analysis"""
    
    user, company = current_user_company
    
    try:
        # Validate file type (must be PDF for editals)
        if file.content_type != "application/pdf":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only PDF files are allowed for editals"
            )
        
        # Validate file size
        content = await file.read()
        if len(content) > settings.MAX_FILE_SIZE:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"File too large. Maximum size: {settings.MAX_FILE_SIZE // 1024 // 1024}MB"
            )
        
        # Check subscription limits
        # TODO: Implement subscription checking logic
        
        # Store file
        file_path = await file_storage.store_edital(
            content=content,
            filename=file.filename,
            company_id=str(company.id)
        )
        
        # Create edital record
        edital = Edital(
            company_id=company.id,
            original_filename=file.filename,
            file_path=file_path,
            file_size=len(content),
            analysis_status=AnalysisStatus.PENDING,
            uploaded_by=user.id
        )
        
        db.add(edital)
        await db.commit()
        await db.refresh(edital)
        
        # Queue analysis task
        background_tasks.add_task(
            process_edital_analysis,
            edital.id,
            file_path,
            user.email,
            user.first_name
        )
        
        logger.info(
            "Edital uploaded successfully",
            edital_id=str(edital.id),
            company_id=str(company.id),
            user_id=str(user.id),
            filename=file.filename
        )
        
        return EditalSchema.model_validate(edital)
        
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        logger.error("Edital upload error", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to upload edital"
        )

@router.get("/{edital_id}", response_model=EditalWithAnalysis)
async def get_edital(
    edital_id: uuid.UUID,
    current_user_company: tuple[User, Company] = Depends(get_current_user_with_company),
    db: AsyncSession = Depends(get_db)
):
    """Get edital with analysis details"""
    
    user, company = current_user_company
    
    # Get edital
    result = await db.execute(
        select(Edital).where(
            and_(
                Edital.id == edital_id,
                Edital.company_id == company.id
            )
        )
    )
    edital = result.scalar_one_or_none()
    
    if not edital:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Edital not found"
        )
    
    # Get analysis
    result = await db.execute(
        select(EditalAnalysis).where(EditalAnalysis.edital_id == edital_id)
    )
    analysis = result.scalar_one_or_none()
    
    # Get entities
    result = await db.execute(
        select(ExtractedEntity).where(ExtractedEntity.edital_id == edital_id)
    )
    entities = result.scalars().all()
    
    # Get requirements
    result = await db.execute(
        select(HabilitacaoRequirement).where(HabilitacaoRequirement.edital_id == edital_id)
    )
    requirements = result.scalars().all()
    
    return EditalWithAnalysis(
        **edital.__dict__,
        analysis=EditalAnalysisSchema.model_validate(analysis) if analysis else None,
        entities=[ExtractedEntitySchema.model_validate(entity) for entity in entities],
        requirements=[HabilitacaoRequirementSchema.model_validate(req) for req in requirements]
    )

@router.get("/{edital_id}/checklist", response_model=EditalChecklist)
async def get_edital_checklist(
    edital_id: uuid.UUID,
    current_user_company: tuple[User, Company] = Depends(get_current_user_with_company),
    db: AsyncSession = Depends(get_db)
):
    """Get compliance checklist for edital"""
    
    user, company = current_user_company
    
    # Verify edital belongs to company
    result = await db.execute(
        select(Edital).where(
            and_(
                Edital.id == edital_id,
                Edital.company_id == company.id
            )
        )
    )
    edital = result.scalar_one_or_none()
    
    if not edital:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Edital not found"
        )
    
    # Get requirements
    result = await db.execute(
        select(HabilitacaoRequirement).where(HabilitacaoRequirement.edital_id == edital_id)
    )
    requirements = result.scalars().all()
    
    # Get company documents
    result = await db.execute(
        select(Document).where(Document.company_id == company.id)
    )
    company_docs = {doc.type: doc for doc in result.scalars().all()}
    
    # Build checklist
    checklist_items = []
    met_requirements = 0
    total_requirements = len(requirements)
    
    for req in requirements:
        # Find matching document
        matching_doc = company_docs.get(req.document_type)
        
        if matching_doc:
            # Check document status
            from datetime import date
            status_text = "available"
            days_until_expiry = None
            
            if matching_doc.expiry_date:
                days_until_expiry = (matching_doc.expiry_date - date.today()).days
                if days_until_expiry < 0:
                    status_text = "expired"
                elif days_until_expiry <= 30:
                    status_text = "expiring_soon"
                else:
                    status_text = "available"
            
            if status_text == "available":
                met_requirements += 1
            
            checklist_items.append(ChecklistItem(
                requirement_id=req.id,
                description=req.description,
                document_type=req.document_type,
                is_mandatory=req.is_mandatory,
                status=status_text,
                document_id=matching_doc.id,
                expiry_date=matching_doc.expiry_date,
                days_until_expiry=days_until_expiry
            ))
        else:
            # Document missing
            checklist_items.append(ChecklistItem(
                requirement_id=req.id,
                description=req.description,
                document_type=req.document_type,
                is_mandatory=req.is_mandatory,
                status="missing",
                document_id=None,
                expiry_date=None,
                days_until_expiry=None
            ))
    
    # Calculate compliance score
    compliance_score = (met_requirements / total_requirements * 100) if total_requirements > 0 else 100
    
    return EditalChecklist(
        edital_id=edital_id,
        items=checklist_items,
        compliance_score=compliance_score
    )

@router.delete("/{edital_id}", response_model=APIResponse)
async def delete_edital(
    edital_id: uuid.UUID,
    current_user_company: tuple[User, Company] = Depends(get_current_user_with_company),
    db: AsyncSession = Depends(get_db)
):
    """Delete edital"""
    
    user, company = current_user_company
    
    # Only admins can delete editals
    if user.role != "ADMIN":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins can delete editals"
        )
    
    # Get edital
    result = await db.execute(
        select(Edital).where(
            and_(
                Edital.id == edital_id,
                Edital.company_id == company.id
            )
        )
    )
    edital = result.scalar_one_or_none()
    
    if not edital:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Edital not found"
        )
    
    try:
        # Delete file from storage
        await file_storage.delete_file(edital.file_path)
        
        # Delete edital record (analysis, entities, requirements will be deleted by cascade)
        await db.delete(edital)
        await db.commit()
        
        logger.info("Edital deleted", edital_id=str(edital.id), user_id=str(user.id))
        
        return APIResponse(
            success=True,
            message="Edital deleted successfully"
        )
        
    except Exception as e:
        await db.rollback()
        logger.error("Edital deletion error", error=str(e), edital_id=str(edital_id))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete edital"
        )

@router.post("/{edital_id}/reanalyze", response_model=APIResponse)
async def reanalyze_edital(
    edital_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    current_user_company: tuple[User, Company] = Depends(get_current_user_with_company),
    db: AsyncSession = Depends(get_db)
):
    """Reanalyze an edital"""
    
    user, company = current_user_company
    
    # Get edital
    result = await db.execute(
        select(Edital).where(
            and_(
                Edital.id == edital_id,
                Edital.company_id == company.id
            )
        )
    )
    edital = result.scalar_one_or_none()
    
    if not edital:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Edital not found"
        )
    
    try:
        # Update status to processing
        edital.analysis_status = AnalysisStatus.PROCESSING
        edital.error_message = None
        await db.commit()
        
        # Queue analysis task
        background_tasks.add_task(
            process_edital_analysis,
            edital.id,
            edital.file_path,
            user.email,
            user.first_name
        )
        
        logger.info("Edital reanalysis queued", edital_id=str(edital.id))
        
        return APIResponse(
            success=True,
            message="Edital analysis queued successfully"
        )
        
    except Exception as e:
        await db.rollback()
        logger.error("Edital reanalysis error", error=str(e), edital_id=str(edital_id))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to queue edital analysis"
        )

# Background task functions
async def process_edital_analysis(
    edital_id: uuid.UUID,
    file_path: str,
    user_email: str,
    user_name: str
):
    """Process edital analysis using ML service"""
    
    logger.info("Starting edital analysis", edital_id=str(edital_id))
    
    async with async_session_maker() as db:
        try:
            # Update status to processing
            result = await db.execute(select(Edital).where(Edital.id == edital_id))
            edital = result.scalar_one_or_none()
            
            if not edital:
                logger.error("Edital not found for analysis", edital_id=str(edital_id))
                return
            
            edital.analysis_status = AnalysisStatus.PROCESSING
            await db.commit()
            
            # Extract text from PDF
            text_content = await DocumentProcessor.extract_text_from
