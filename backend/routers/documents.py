from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, desc
from typing import List, Optional
from datetime import date, datetime
import structlog
import uuid
import os
from pathlib import Path
import magic
import aiofiles

from database import get_db
from models import Document, DocumentVersion, User, Company, DocumentType, ValidityStatus
from schemas import (
    Document as DocumentSchema, DocumentCreate, DocumentUpdate, 
    DocumentWithStatus, PaginatedResponse, APIResponse,
    DocumentSummary
)
from auth import get_current_user_with_company
from config import settings
from utils.file_storage import FileStorage

logger = structlog.get_logger()
router = APIRouter()

# Initialize file storage
file_storage = FileStorage()

@router.get("/", response_model=PaginatedResponse)
async def list_documents(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    document_type: Optional[DocumentType] = None,
    status: Optional[ValidityStatus] = None,
    search: Optional[str] = None,
    current_user_company: tuple[User, Company] = Depends(get_current_user_with_company),
    db: AsyncSession = Depends(get_db)
):
    """List company documents with pagination and filters"""
    
    user, company = current_user_company
    
    # Build query
    query = select(Document).where(Document.company_id == company.id)
    
    # Apply filters
    if document_type:
        query = query.where(Document.type == document_type)
    
    if status:
        query = query.where(Document.validity_status == status)
    
    if search:
        query = query.where(Document.name.ilike(f"%{search}%"))
    
    # Count total items
    count_result = await db.execute(
        select(Document).where(Document.company_id == company.id)
    )
    total = len(count_result.fetchall())
    
    # Apply pagination and ordering
    query = query.order_by(desc(Document.created_at))
    query = query.offset((page - 1) * per_page).limit(per_page)
    
    # Execute query
    result = await db.execute(query)
    documents = result.scalars().all()
    
    # Convert to response format with additional status info
    document_items = []
    for doc in documents:
        doc_data = DocumentWithStatus.model_validate(doc)
        
        # Calculate days until expiry
        if doc.expiry_date:
            days_until_expiry = (doc.expiry_date - date.today()).days
            doc_data.days_until_expiry = days_until_expiry
            doc_data.is_expired = days_until_expiry < 0
        
        document_items.append(doc_data)
    
    # Calculate pagination info
    pages = (total + per_page - 1) // per_page
    
    return PaginatedResponse(
        items=document_items,
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
        has_next=page < pages,
        has_prev=page > 1
    )

@router.get("/summary", response_model=DocumentSummary)
async def get_documents_summary(
    current_user_company: tuple[User, Company] = Depends(get_current_user_with_company),
    db: AsyncSession = Depends(get_db)
):
    """Get documents summary for dashboard"""
    
    user, company = current_user_company
    
    # Get all company documents
    result = await db.execute(
        select(Document).where(Document.company_id == company.id)
    )
    documents = result.scalars().all()
    
    # Calculate statistics
    total_documents = len(documents)
    by_type = {}
    by_status = {}
    expiring_soon = []
    
    for doc in documents:
        # Count by type
        doc_type = doc.type.value
        by_type[doc_type] = by_type.get(doc_type, 0) + 1
        
        # Count by status
        status = doc.validity_status.value
        by_status[status] = by_status.get(status, 0) + 1
        
        # Check if expiring soon (next 30 days)
        if doc.expiry_date and doc.validity_status == ValidityStatus.EXPIRING_SOON:
            expiring_soon.append(doc)
    
    return DocumentSummary(
        total_documents=total_documents,
        by_type=by_type,
        by_status=by_status,
        expiring_soon=expiring_soon[:10]  # Limit to 10 items
    )

@router.post("/", response_model=DocumentSchema)
async def upload_document(
    file: UploadFile = File(...),
    name: str = Form(...),
    document_type: DocumentType = Form(...),
    issue_date: Optional[str] = Form(None),
    expiry_date: Optional[str] = Form(None),
    current_user_company: tuple[User, Company] = Depends(get_current_user_with_company),
    db: AsyncSession = Depends(get_db)
):
    """Upload a new document"""
    
    user, company = current_user_company
    
    try:
        # Validate file type
        if file.content_type not in settings.ALLOWED_FILE_TYPES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"File type {file.content_type} not allowed"
            )
        
        # Validate file size
        content = await file.read()
        if len(content) > settings.MAX_FILE_SIZE:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"File too large. Maximum size: {settings.MAX_FILE_SIZE // 1024 // 1024}MB"
            )
        
        # Parse dates
        parsed_issue_date = None
        parsed_expiry_date = None
        
        if issue_date:
            try:
                parsed_issue_date = datetime.strptime(issue_date, "%Y-%m-%d").date()
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid issue_date format. Use YYYY-MM-DD"
                )
        
        if expiry_date:
            try:
                parsed_expiry_date = datetime.strptime(expiry_date, "%Y-%m-%d").date()
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid expiry_date format. Use YYYY-MM-DD"
                )
        
        # Store file
        file_path = await file_storage.store_document(
            content=content,
            filename=file.filename,
            company_id=str(company.id)
        )
        
        # Calculate validity status
        validity_status = ValidityStatus.NOT_APPLICABLE
        if parsed_expiry_date:
            days_until_expiry = (parsed_expiry_date - date.today()).days
            if days_until_expiry < 0:
                validity_status = ValidityStatus.EXPIRED
            elif days_until_expiry <= 30:
                validity_status = ValidityStatus.EXPIRING_SOON
            else:
                validity_status = ValidityStatus.VALID
        
        # Create document record
        document = Document(
            company_id=company.id,
            name=name,
            type=document_type,
            file_path=file_path,
            file_size=len(content),
            mime_type=file.content_type,
            issue_date=parsed_issue_date,
            expiry_date=parsed_expiry_date,
            validity_status=validity_status,
            version=1,
            created_by=user.id
        )
        
        db.add(document)
        await db.commit()
        await db.refresh(document)
        
        # Create initial version record
        doc_version = DocumentVersion(
            document_id=document.id,
            version=1,
            file_path=file_path,
            file_size=len(content),
            created_by=user.id
        )
        
        db.add(doc_version)
        await db.commit()
        
        logger.info(
            "Document uploaded successfully",
            document_id=str(document.id),
            company_id=str(company.id),
            user_id=str(user.id),
            filename=file.filename
        )
        
        return DocumentSchema.model_validate(document)
        
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        logger.error("Document upload error", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to upload document"
        )

@router.get("/{document_id}", response_model=DocumentWithStatus)
async def get_document(
    document_id: uuid.UUID,
    current_user_company: tuple[User, Company] = Depends(get_current_user_with_company),
    db: AsyncSession = Depends(get_db)
):
    """Get document by ID"""
    
    user, company = current_user_company
    
    result = await db.execute(
        select(Document).where(
            and_(
                Document.id == document_id,
                Document.company_id == company.id
            )
        )
    )
    document = result.scalar_one_or_none()
    
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found"
        )
    
    # Convert to response format with additional status info
    doc_data = DocumentWithStatus.model_validate(document)
    
    # Calculate days until expiry
    if document.expiry_date:
        days_until_expiry = (document.expiry_date - date.today()).days
        doc_data.days_until_expiry = days_until_expiry
        doc_data.is_expired = days_until_expiry < 0
    
    return doc_data

@router.put("/{document_id}", response_model=DocumentSchema)
async def update_document(
    document_id: uuid.UUID,
    document_update: DocumentUpdate,
    current_user_company: tuple[User, Company] = Depends(get_current_user_with_company),
    db: AsyncSession = Depends(get_db)
):
    """Update document metadata"""
    
    user, company = current_user_company
    
    # Get document
    result = await db.execute(
        select(Document).where(
            and_(
                Document.id == document_id,
                Document.company_id == company.id
            )
        )
    )
    document = result.scalar_one_or_none()
    
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found"
        )
    
    try:
        # Update fields
        update_data = document_update.model_dump(exclude_unset=True)
        
        for field, value in update_data.items():
            setattr(document, field, value)
        
        # Recalculate validity status if expiry date changed
        if "expiry_date" in update_data and document.expiry_date:
            days_until_expiry = (document.expiry_date - date.today()).days
            if days_until_expiry < 0:
                document.validity_status = ValidityStatus.EXPIRED
            elif days_until_expiry <= 30:
                document.validity_status = ValidityStatus.EXPIRING_SOON
            else:
                document.validity_status = ValidityStatus.VALID
        elif "expiry_date" in update_data and not document.expiry_date:
            document.validity_status = ValidityStatus.NOT_APPLICABLE
        
        await db.commit()
        await db.refresh(document)
        
        logger.info("Document updated", document_id=str(document.id))
        
        return DocumentSchema.model_validate(document)
        
    except Exception as e:
        await db.rollback()
        logger.error("Document update error", error=str(e), document_id=str(document_id))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update document"
        )

@router.post("/{document_id}/new-version", response_model=DocumentSchema)
async def upload_document_new_version(
    document_id: uuid.UUID,
    file: UploadFile = File(...),
    current_user_company: tuple[User, Company] = Depends(get_current_user_with_company),
    db: AsyncSession = Depends(get_db)
):
    """Upload a new version of an existing document"""
    
    user, company = current_user_company
    
    # Get document
    result = await db.execute(
        select(Document).where(
            and_(
                Document.id == document_id,
                Document.company_id == company.id
            )
        )
    )
    document = result.scalar_one_or_none()
    
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found"
        )
    
    try:
        # Validate file type
        if file.content_type not in settings.ALLOWED_FILE_TYPES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"File type {file.content_type} not allowed"
            )
        
        # Validate file size
        content = await file.read()
        if len(content) > settings.MAX_FILE_SIZE:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"File too large. Maximum size: {settings.MAX_FILE_SIZE // 1024 // 1024}MB"
            )
        
        # Store new file
        file_path = await file_storage.store_document(
            content=content,
            filename=file.filename,
            company_id=str(company.id)
        )
        
        # Update document
        new_version = document.version + 1
        old_file_path = document.file_path
        
        document.file_path = file_path
        document.file_size = len(content)
        document.mime_type = file.content_type
        document.version = new_version
        
        # Create version record
        doc_version = DocumentVersion(
            document_id=document.id,
            version=new_version,
            file_path=file_path,
            file_size=len(content),
            created_by=user.id
        )
        
        db.add(doc_version)
        await db.commit()
        await db.refresh(document)
        
        logger.info(
            "Document version uploaded",
            document_id=str(document.id),
            version=new_version,
            user_id=str(user.id)
        )
        
        return DocumentSchema.model_validate(document)
        
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        logger.error("Document version upload error", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to upload document version"
        )

@router.delete("/{document_id}", response_model=APIResponse)
async def delete_document(
    document_id: uuid.UUID,
    current_user_company: tuple[User, Company] = Depends(get_current_user_with_company),
    db: AsyncSession = Depends(get_db)
):
    """Delete document"""
    
    user, company = current_user_company
    
    # Only admins can delete documents
    if user.role != "ADMIN":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins can delete documents"
        )
    
    # Get document
    result = await db.execute(
        select(Document).where(
            and_(
                Document.id == document_id,
                Document.company_id == company.id
            )
        )
    )
    document = result.scalar_one_or_none()
    
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found"
        )
    
    try:
        # Delete file from storage
        await file_storage.delete_file(document.file_path)
        
        # Delete document record (versions will be deleted by cascade)
        await db.delete(document)
        await db.commit()
        
        logger.info("Document deleted", document_id=str(document.id), user_id=str(user.id))
        
        return APIResponse(
            success=True,
            message="Document deleted successfully"
        )
        
    except Exception as e:
        await db.rollback()
        logger.error("Document deletion error", error=str(e), document_id=str(document_id))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete document"
        )

@router.get("/{document_id}/download")
async def download_document(
    document_id: uuid.UUID,
    version: Optional[int] = None,
    current_user_company: tuple[User, Company] = Depends(get_current_user_with_company),
    db: AsyncSession = Depends(get_db)
):
    """Download document file"""
    
    user, company = current_user_company
    
    # Get document
    result = await db.execute(
        select(Document).where(
            and_(
                Document.id == document_id,
                Document.company_id == company.id
            )
        )
    )
    document = result.scalar_one_or_none()
    
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found"
        )
    
    # Get file path (current version or specific version)
    file_path = document.file_path
    
    if version and version != document.version:
        # Get specific version
        result = await db.execute(
            select(DocumentVersion).where(
                and_(
                    DocumentVersion.document_id == document_id,
                    DocumentVersion.version == version
                )
            )
        )
        doc_version = result.scalar_one_or_none()
        
        if not doc_version:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Document version not found"
            )
        
        file_path = doc_version.file_path
    
    # Return file download URL or stream
    download_url = await file_storage.get_download_url(file_path)
    
    if download_url:
        # Return redirect to signed URL
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url=
