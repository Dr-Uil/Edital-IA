from pydantic import BaseModel, EmailStr, validator, Field
from typing import Optional, List, Dict, Any
from datetime import datetime, date
from decimal import Decimal
import uuid
from enum import Enum

from models import DocumentType, ValidityStatus, UserRole, AnalysisStatus

# Base schemas
class BaseSchema(BaseModel):
    class Config:
        from_attributes = True
        arbitrary_types_allowed = True

# Company schemas
class CompanyBase(BaseSchema):
    razao_social: str = Field(..., max_length=255)
    nome_fantasia: Optional[str] = Field(None, max_length=255)
    cnpj: str = Field(..., regex=r'^\d{2}\.\d{3}\.\d{3}\/\d{4}-\d{2}$')
    endereco: Optional[str] = None
    telefone: Optional[str] = Field(None, max_length=20)
    email: Optional[EmailStr] = None

class CompanyCreate(CompanyBase):
    pass

class CompanyUpdate(BaseSchema):
    razao_social: Optional[str] = Field(None, max_length=255)
    nome_fantasia: Optional[str] = Field(None, max_length=255)
    endereco: Optional[str] = None
    telefone: Optional[str] = Field(None, max_length=20)
    email: Optional[EmailStr] = None

class Company(CompanyBase):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime

# User schemas
class UserBase(BaseSchema):
    email: EmailStr
    first_name: str = Field(..., max_length=100)
    last_name: str = Field(..., max_length=100)
    role: UserRole = UserRole.MEMBER

class UserCreate(UserBase):
    password: str = Field(..., min_length=8)
    company_id: Optional[uuid.UUID] = None

class UserUpdate(BaseSchema):
    first_name: Optional[str] = Field(None, max_length=100)
    last_name: Optional[str] = Field(None, max_length=100)
    role: Optional[UserRole] = None
    is_active: Optional[bool] = None

class UserChangePassword(BaseSchema):
    old_password: str
    new_password: str = Field(..., min_length=8)

class User(UserBase):
    id: uuid.UUID
    company_id: Optional[uuid.UUID]
    is_active: bool
    email_verified: bool
    created_at: datetime
    updated_at: datetime

class UserWithCompany(User):
    company: Optional[Company] = None

# Authentication schemas
class Token(BaseSchema):
    access_token: str
    token_type: str = "bearer"
    expires_in: int

class TokenData(BaseSchema):
    user_id: Optional[uuid.UUID] = None

class LoginRequest(BaseSchema):
    email: EmailStr
    password: str

class RegisterRequest(BaseSchema):
    email: EmailStr
    password: str = Field(..., min_length=8)
    first_name: str = Field(..., max_length=100)
    last_name: str = Field(..., max_length=100)
    company_data: CompanyCreate

# Document schemas
class DocumentBase(BaseSchema):
    name: str = Field(..., max_length=255)
    type: DocumentType
    issue_date: Optional[date] = None
    expiry_date: Optional[date] = None

class DocumentCreate(DocumentBase):
    pass

class DocumentUpdate(BaseSchema):
    name: Optional[str] = Field(None, max_length=255)
    type: Optional[DocumentType] = None
    issue_date: Optional[date] = None
    expiry_date: Optional[date] = None

class Document(DocumentBase):
    id: uuid.UUID
    company_id: uuid.UUID
    file_path: str
    file_size: Optional[int]
    mime_type: Optional[str]
    validity_status: ValidityStatus
    version: int
    created_by: Optional[uuid.UUID]
    created_at: datetime
    updated_at: datetime

class DocumentWithStatus(Document):
    days_until_expiry: Optional[int] = None
    is_expired: bool = False

# Document Version schemas
class DocumentVersion(BaseSchema):
    id: uuid.UUID
    document_id: uuid.UUID
    version: int
    file_path: str
    file_size: Optional[int]
    created_by: Optional[uuid.UUID]
    created_at: datetime

# Edital schemas
class EditalBase(BaseSchema):
    original_filename: str = Field(..., max_length=255)

class EditalCreate(EditalBase):
    pass

class Edital(EditalBase):
    id: uuid.UUID
    company_id: uuid.UUID
    file_path: str
    file_size: Optional[int]
    analysis_status: AnalysisStatus
    error_message: Optional[str]
    uploaded_by: Optional[uuid.UUID]
    created_at: datetime
    updated_at: datetime

# Edital Analysis schemas
class EditalAnalysisBase(BaseSchema):
    organizacao_licitante: Optional[str] = None
    modalidade_licitacao: Optional[str] = None
    numero_processo: Optional[str] = None
    data_abertura_propostas: Optional[datetime] = None
    data_sessao_publica: Optional[datetime] = None
    objeto_licitacao: Optional[str] = None
    criterio_julgamento: Optional[str] = None
    valor_estimado: Optional[Decimal] = None

class EditalAnalysis(EditalAnalysisBase):
    id: uuid.UUID
    edital_id: uuid.UUID
    created_at: datetime

# Extracted Entity schemas
class ExtractedEntityBase(BaseSchema):
    entity_type: str = Field(..., max_length=100)
    entity_value: str
    confidence: Optional[Decimal] = Field(None, ge=0, le=1)
    start_position: Optional[int] = None
    end_position: Optional[int] = None

class ExtractedEntity(ExtractedEntityBase):
    id: uuid.UUID
    edital_id: uuid.UUID
    created_at: datetime

# Habilitacao Requirement schemas
class HabilitacaoRequirementBase(BaseSchema):
    requirement_type: str = Field(..., max_length=100)
    description: str
    document_type: Optional[DocumentType] = None
    is_mandatory: bool = True

class HabilitacaoRequirement(HabilitacaoRequirementBase):
    id: uuid.UUID
    edital_id: uuid.UUID
    created_at: datetime

# Full Edital with Analysis
class EditalWithAnalysis(Edital):
    analysis: Optional[EditalAnalysis] = None
    entities: List[ExtractedEntity] = []
    requirements: List[HabilitacaoRequirement] = []

# Checklist schemas
class ChecklistItem(BaseSchema):
    requirement_id: uuid.UUID
    description: str
    document_type: Optional[DocumentType] = None
    is_mandatory: bool
    status: str  # 'available', 'expired', 'missing'
    document_id: Optional[uuid.UUID] = None
    expiry_date: Optional[date] = None
    days_until_expiry: Optional[int] = None

class EditalChecklist(BaseSchema):
    edital_id: uuid.UUID
    items: List[ChecklistItem]
    compliance_score: float  # Percentage of requirements met

# Dashboard schemas
class DashboardStats(BaseSchema):
    total_editals: int
    editals_this_month: int
    documents_expiring: int
    documents_expired: int
    compliance_score: float

class DocumentSummary(BaseSchema):
    total_documents: int
    by_type: Dict[str, int]
    by_status: Dict[str, int]
    expiring_soon: List[Document]

# Subscription schemas
class SubscriptionPlanBase(BaseSchema):
    name: str = Field(..., max_length=100)
    price: Decimal = Field(..., ge=0)
    max_analyses_per_month: Optional[int] = None
    max_users: Optional[int] = None
    storage_limit_gb: Optional[int] = None
    features: Optional[Dict[str, Any]] = None

class SubscriptionPlan(SubscriptionPlanBase):
    id: uuid.UUID
    is_active: bool
    created_at: datetime

class SubscriptionBase(BaseSchema):
    plan_id: uuid.UUID
    current_period_start: date
    current_period_end: date

class Subscription(SubscriptionBase):
    id: uuid.UUID
    company_id: uuid.UUID
    status: str
    analyses_used: int
    created_at: datetime
    updated_at: datetime
    plan: SubscriptionPlan

# Alert schemas
class ExpiryAlert(BaseSchema):
    id: uuid.UUID
    document_id: uuid.UUID
    alert_type: str
    sent_at: Optional[datetime]
    email_sent: bool
    created_at: datetime
    document: Document

# File upload schemas
class FileUploadResponse(BaseSchema):
    file_id: str
    filename: str
    file_size: int
    mime_type: str
    upload_url: Optional[str] = None

# API Response schemas
class APIResponse(BaseSchema):
    success: bool
    message: str
    data: Optional[Any] = None

class PaginatedResponse(BaseSchema):
    items: List[Any]
    total: int
    page: int
    per_page: int
    pages: int
    has_next: bool
    has_prev: bool

# ML Service schemas
class MLAnalysisRequest(BaseSchema):
    edital_id: uuid.UUID
    file_path: str
    text_content: Optional[str] = None

class MLAnalysisResponse(BaseSchema):
    success: bool
    analysis: Optional[EditalAnalysisBase] = None
    entities: List[ExtractedEntityBase] = []
    requirements: List[HabilitacaoRequirementBase] = []
    error: Optional[str] = None

# Email schemas
class EmailTemplate(BaseSchema):
    template_name: str
    subject: str
    variables: Dict[str, Any]

class NotificationPreferences(BaseSchema):
    email_alerts: bool = True
    expiry_notifications: bool = True
    analysis_complete: bool = True
    weekly_summary: bool = False
