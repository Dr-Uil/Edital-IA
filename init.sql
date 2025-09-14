-- Extensões necessárias
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Enum para tipos de documentos
CREATE TYPE document_type AS ENUM (
    'CONTRATO_SOCIAL',
    'CND_FEDERAL',
    'CND_ESTADUAL',
    'CND_MUNICIPAL',
    'CERTIDAO_FGTS',
    'CERTIDAO_TRABALHISTA',
    'ALVARA_FUNCIONAMENTO',
    'ATESTADO_CAPACIDADE_TECNICA',
    'BALANCO_PATRIMONIAL',
    'DEMONSTRACAO_RESULTADOS',
    'CERTIDAO_FALENCIA',
    'OUTROS'
);

-- Enum para status de validade
CREATE TYPE validity_status AS ENUM (
    'VALID',
    'EXPIRING_SOON',
    'EXPIRED',
    'NOT_APPLICABLE'
);

-- Enum para roles de usuário
CREATE TYPE user_role AS ENUM (
    'ADMIN',
    'MEMBER'
);

-- Enum para status de análise
CREATE TYPE analysis_status AS ENUM (
    'PENDING',
    'PROCESSING',
    'COMPLETED',
    'FAILED'
);

-- Tabela de empresas
CREATE TABLE companies (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    razao_social VARCHAR(255) NOT NULL,
    nome_fantasia VARCHAR(255),
    cnpj VARCHAR(18) UNIQUE NOT NULL,
    endereco TEXT,
    telefone VARCHAR(20),
    email VARCHAR(255),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Tabela de usuários
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    first_name VARCHAR(100) NOT NULL,
    last_name VARCHAR(100) NOT NULL,
    role user_role DEFAULT 'MEMBER',
    company_id UUID REFERENCES companies(id) ON DELETE CASCADE,
    is_active BOOLEAN DEFAULT true,
    email_verified BOOLEAN DEFAULT false,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Tabela de documentos
CREATE TABLE documents (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    type document_type NOT NULL,
    file_path VARCHAR(500) NOT NULL,
    file_size INTEGER,
    mime_type VARCHAR(100),
    issue_date DATE,
    expiry_date DATE,
    validity_status validity_status DEFAULT 'VALID',
    version INTEGER DEFAULT 1,
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Tabela de histórico de documentos (versionamento)
CREATE TABLE document_versions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    file_path VARCHAR(500) NOT NULL,
    file_size INTEGER,
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Tabela de editais
CREATE TABLE editals (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    original_filename VARCHAR(255) NOT NULL,
    file_path VARCHAR(500) NOT NULL,
    file_size INTEGER,
    analysis_status analysis_status DEFAULT 'PENDING',
    error_message TEXT,
    uploaded_by UUID REFERENCES users(id),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Tabela de análises de editais
CREATE TABLE edital_analyses (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    edital_id UUID NOT NULL REFERENCES editals(id) ON DELETE CASCADE,
    organizacao_licitante VARCHAR(255),
    modalidade_licitacao VARCHAR(100),
    numero_processo VARCHAR(100),
    data_abertura_propostas TIMESTAMP,
    data_sessao_publica TIMESTAMP,
    objeto_licitacao TEXT,
    criterio_julgamento VARCHAR(100),
    valor_estimado DECIMAL(15,2),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Tabela de entidades extraídas (NER)
CREATE TABLE extracted_entities (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    edital_id UUID NOT NULL REFERENCES editals(id) ON DELETE CASCADE,
    entity_type VARCHAR(100) NOT NULL,
    entity_value TEXT NOT NULL,
    confidence DECIMAL(4,3),
    start_position INTEGER,
    end_position INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Tabela de requisitos de habilitação
CREATE TABLE habilitacao_requirements (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    edital_id UUID NOT NULL REFERENCES editals(id) ON DELETE CASCADE,
    requirement_type VARCHAR(100) NOT NULL,
    description TEXT NOT NULL,
    document_type document_type,
    is_mandatory BOOLEAN DEFAULT true,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Tabela de alertas de vencimento
CREATE TABLE expiry_alerts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    alert_type VARCHAR(50) NOT NULL, -- '30_DAYS', '15_DAYS', '7_DAYS', 'EXPIRED'
    sent_at TIMESTAMP,
    email_sent BOOLEAN DEFAULT false,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Tabela de planos de assinatura
CREATE TABLE subscription_plans (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name VARCHAR(100) NOT NULL,
    price DECIMAL(10,2) NOT NULL,
    max_analyses_per_month INTEGER,
    max_users INTEGER,
    storage_limit_gb INTEGER,
    features JSONB,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Tabela de assinaturas
CREATE TABLE subscriptions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    plan_id UUID NOT NULL REFERENCES subscription_plans(id),
    status VARCHAR(50) DEFAULT 'ACTIVE', -- 'ACTIVE', 'CANCELLED', 'EXPIRED'
    current_period_start DATE NOT NULL,
    current_period_end DATE NOT NULL,
    analyses_used INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Tabela de logs de auditoria
CREATE TABLE audit_logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID REFERENCES users(id),
    company_id UUID REFERENCES companies(id),
    action VARCHAR(100) NOT NULL,
    entity_type VARCHAR(50),
    entity_id UUID,
    old_values JSONB,
    new_values JSONB,
    ip_address INET,
    user_agent TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Índices para performance
CREATE INDEX idx_users_email ON users(email);
CREATE INDEX idx_users_company_id ON users(company_id);
CREATE INDEX idx_documents_company_id ON documents(company_id);
CREATE INDEX idx_documents_type ON documents(type);
CREATE INDEX idx_documents_expiry_date ON documents(expiry_date);
CREATE INDEX idx_editals_company_id ON editals(company_id);
CREATE INDEX idx_editals_status ON editals(analysis_status);
CREATE INDEX idx_extracted_entities_edital_id ON extracted_entities(edital_id);
CREATE INDEX idx_habilitacao_requirements_edital_id ON habilitacao_requirements(edital_id);
CREATE INDEX idx_audit_logs_user_id ON audit_logs(user_id);
CREATE INDEX idx_audit_logs_created_at ON audit_logs(created_at);

-- Trigger para atualizar updated_at automaticamente
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_companies_updated_at BEFORE UPDATE ON companies FOR EACH ROW EXECUTE PROCEDURE update_updated_at_column();
CREATE TRIGGER update_users_updated_at BEFORE UPDATE ON users FOR EACH ROW EXECUTE PROCEDURE update_updated_at_column();
CREATE TRIGGER update_documents_updated_at BEFORE UPDATE ON documents FOR EACH ROW EXECUTE PROCEDURE update_updated_at_column();
CREATE TRIGGER update_editals_updated_at BEFORE UPDATE ON editals FOR EACH ROW EXECUTE PROCEDURE update_updated_at_column();
CREATE TRIGGER update_subscriptions_updated_at BEFORE UPDATE ON subscriptions FOR EACH ROW EXECUTE PROCEDURE update_updated_at_column();

-- Função para atualizar status de validade dos documentos
CREATE OR REPLACE FUNCTION update_document_validity_status()
RETURNS void AS $$
BEGIN
    UPDATE documents 
    SET validity_status = CASE
        WHEN expiry_date IS NULL THEN 'NOT_APPLICABLE'::validity_status
        WHEN expiry_date < CURRENT_DATE THEN 'EXPIRED'::validity_status
        WHEN expiry_date <= CURRENT_DATE + INTERVAL '30 days' THEN 'EXPIRING_SOON'::validity_status
        ELSE 'VALID'::validity_status
    END
    WHERE expiry_date IS NOT NULL OR validity_status != 'NOT_APPLICABLE';
END;
$$ LANGUAGE plpgsql;

-- Inserir planos padrão
INSERT INTO subscription_plans (name, price, max_analyses_per_month, max_users, storage_limit_gb, features) VALUES
('Essencial', 99.00, 5, 2, 1, '{"support": "email", "api_access": false}'),
('Profissional', 249.00, 20, 5, 10, '{"support": "priority", "api_access": false, "advanced_analytics": true}');

-- Função para criar empresa admin (para testes)
INSERT INTO companies (razao_social, nome_fantasia, cnpj, email) VALUES
('Edital AI Ltda', 'Edital.AI', '12.345.678/0001-90', 'admin@edital.ai');

-- Criar usuário admin padrão (senha: admin123)
INSERT INTO users (email, password_hash, first_name, last_name, role, company_id, email_verified) VALUES
('admin@edital.ai', crypt('admin123', gen_salt('bf')), 'Admin', 'Sistema', 'ADMIN', (SELECT id FROM companies WHERE cnpj = '12.345.678/0001-90'), true);
