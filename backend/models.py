from sqlalchemy import Column, String, DateTime, Boolean, Integer, Text, ForeignKey, Enum, DECIMAL, Date, JSON
from sqlalchemy.dialects.postgresql import UUID, INET
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum
import uuid

from database import Base

# Enums
class DocumentType(str, enum.Enum):
    CONTRATO_SOCIAL = "CONTRATO_SOCIAL"
    CND_FEDERAL = "CND_FEDERAL"
    CND_ESTADUAL = "CND_ESTADUAL"
    CND_MUNICIPAL = "CND_MUNICIPAL"
    CERTIDAO_FGTS = "CERTIDAO_FGTS"
    CERTIDAO_TRABALHISTA = "CERTIDAO_TRABALHISTA"
    ALVARA_FUNCIONAMENTO = "ALVARA_FUNCIONAMENTO"
    ATESTADO_CAPACIDADE_TECNICA = "ATESTADO_CAPACIDADE_TECNICA"
    BALANCO_PATRIMONIAL = "BALANCO_PATRIMONIAL"
    DEMONSTRACAO_RESULTADOS = "DEMONSTRACAO_RESULTADOS"
    CERTIDAO_FALENCIA = "CERTIDAO_FALENCIA"
    OUTROS = "OUTROS"

class ValidityStatus(str, enum.Enum):
    VALID = "VALID"
    EXPIRING_SOON = "EXPIRING_SOON"
    EXPIRED = "EXPIRED"
    NOT_APPLICABLE = "NOT_APPLICABLE"

class UserRole(str, enum.Enum):
    ADMIN = "ADMIN"
    MEMBER = "MEMBER"

class AnalysisStatus(str, enum.Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"

# Models
class Company(Base):
    __tablename__ = "companies"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    razao_social = Column(String(255), nullable=False)
    nome_fantasia = Column(String(255))
    cnpj = Column(String(18), unique=True, nullable=False)
    endereco = Column(Text)
    telefone = Column(String(20))
    email = Column(String(255))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    # Relationships
    users = relationship("User", back_populates="company", cascade="all, delete-orphan")
    documents = relationship("Document", back_populates="company", cascade="all, delete-orphan")
    editals = relationship("Edital", back_populates="company", cascade="all, delete-orphan")
    subscription = relationship("Subscription", back_populates="company", uselist=False)

class User(Base):
    __tablename__ = "users"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(255), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    first_name = Column(String(100), nullable=False)
    last_name = Column(String(100), nullable=False)
    role = Column(Enum(UserRole), default=UserRole.MEMBER)
    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"))
    is_active = Column(Boolean, default=True)
    email_verified = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    # Relationships
    company = relationship("Company", back_populates="users")
    created_documents = relationship("Document", back_populates="created_by_user", foreign_keys="Document.created_by")
    uploaded_editals = relationship("Edital", back_populates="uploaded_by_user")

class Document(Base):
    __tablename__ = "documents"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(255), nullable=False)
    type = Column(Enum(DocumentType), nullable=False)
    file_path = Column(String(500), nullable=False)
    file_size = Column(Integer)
    mime_type = Column(String(100))
    issue_date = Column(Date)
    expiry_date = Column(Date)
    validity_status = Column(Enum(ValidityStatus), default=ValidityStatus.VALID)
    version = Column(Integer, default=1)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    # Relationships
    company = relationship("Company", back_populates="documents")
    created_by_user = relationship("User", back_populates="created_documents", foreign_keys=[created_by])
    versions = relationship("DocumentVersion", back_populates="document", cascade="all, delete-orphan")
    alerts = relationship("ExpiryAlert", back_populates="document", cascade="all, delete-orphan")

class DocumentVersion(Base):
    __tablename__ = "document_versions"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    version = Column(Integer, nullable=False)
    file_path = Column(String(500), nullable=False)
    file_size = Column(Integer)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    document = relationship("Document", back_populates="versions")
    created_by_user = relationship("User", foreign_keys=[created_by])

class Edital(Base):
    __tablename__ = "editals"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    original_filename = Column(String(255), nullable=False)
    file_path = Column(String(500), nullable=False)
    file_size = Column(Integer)
    analysis_status = Column(Enum(AnalysisStatus), default=AnalysisStatus.PENDING)
    error_message = Column(Text)
    uploaded_by = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    # Relationships
    company = relationship("Company", back_populates="editals")
    uploaded_by_user = relationship("User", back_populates="uploaded_editals")
    analysis = relationship("EditalAnalysis", back_populates="edital", uselist=False, cascade="all, delete-orphan")
    entities = relationship("ExtractedEntity", back_populates="edital", cascade="all, delete-orphan")
    requirements = relationship("HabilitacaoRequirement", back_populates="edital", cascade="all, delete-orphan")

class EditalAnalysis(Base):
    __tablename__ = "edital_analyses"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    edital_id = Column(UUID(as_uuid=True), ForeignKey("editals.id", ondelete="CASCADE"), nullable=False)
    organizacao_licitante = Column(String(255))
    modalidade_licitacao = Column(String(100))
    numero_processo = Column(String(100))
    data_abertura_propostas = Column(DateTime(timezone=True))
    data_sessao_publica = Column(DateTime(timezone=True))
    objeto_licitacao = Column(Text)
    criterio_julgamento = Column(String(100))
    valor_estimado = Column(DECIMAL(15, 2))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    edital = relationship("Edital", back_populates="analysis")

class ExtractedEntity(Base):
    __tablename__ = "extracted_entities"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    edital_id = Column(UUID(as_uuid=True), ForeignKey("editals.id", ondelete="CASCADE"), nullable=False)
    entity_type = Column(String(100), nullable=False)
    entity_value = Column(Text, nullable=False)
    confidence = Column(DECIMAL(4, 3))
    start_position = Column(Integer)
    end_position = Column(Integer)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    edital = relationship("Edital", back_populates="entities")

class HabilitacaoRequirement(Base):
    __tablename__ = "habilitacao_requirements"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    edital_id = Column(UUID(as_uuid=True), ForeignKey("editals.id", ondelete="CASCADE"), nullable=False)
    requirement_type = Column(String(100), nullable=False)
    description = Column(Text, nullable=False)
    document_type = Column(Enum(DocumentType))
    is_mandatory = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    edital = relationship("Edital", back_populates="requirements")

class ExpiryAlert(Base):
    __tablename__ = "expiry_alerts"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    alert_type = Column(String(50), nullable=False)  # '30_DAYS', '15_DAYS', '7_DAYS', 'EXPIRED'
    sent_at = Column(DateTime(timezone=True))
    email_sent = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    document = relationship("Document", back_populates="alerts")

class SubscriptionPlan(Base):
    __tablename__ = "subscription_plans"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), nullable=False)
    price = Column(DECIMAL(10, 2), nullable=False)
    max_analyses_per_month = Column(Integer)
    max_users = Column(Integer)
    storage_limit_gb = Column(Integer)
    features = Column(JSON)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    subscriptions = relationship("Subscription", back_populates="plan")

class Subscription(Base):
    __tablename__ = "subscriptions"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    plan_id = Column(UUID(as_uuid=True), ForeignKey("subscription_plans.id"), nullable=False)
    status = Column(String(50), default="ACTIVE")  # 'ACTIVE', 'CANCELLED', 'EXPIRED'
    current_period_start = Column(Date, nullable=False)
    current_period_end = Column(Date, nullable=False)
    analyses_used = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    # Relationships
    company = relationship("Company", back_populates="subscription")
    plan = relationship("SubscriptionPlan", back_populates="subscriptions")

class AuditLog(Base):
    __tablename__ = "audit_logs"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id"))
    action = Column(String(100), nullable=False)
    entity_type = Column(String(50))
    entity_id = Column(UUID(as_uuid=True))
    old_values = Column(JSON)
    new_values = Column(JSON)
    ip_address = Column(INET)
    user_agent = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    user = relationship("User", foreign_keys=[user_id])
    company = relationship("Company", foreign_keys=[company_id])
