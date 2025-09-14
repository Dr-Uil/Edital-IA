import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from typing import List, Optional
import structlog
from jinja2 import Environment, FileSystemLoader
import os
from pathlib import Path

from config import settings

logger = structlog.get_logger()

# Initialize Jinja2 environment for email templates
template_dir = Path(__file__).parent.parent / "templates" / "email"
template_dir.mkdir(parents=True, exist_ok=True)

jinja_env = Environment(
    loader=FileSystemLoader(str(template_dir)),
    autoescape=True
)

class EmailService:
    """Email service for sending various types of emails"""
    
    def __init__(self):
        self.smtp_server = settings.MAIL_SERVER
        self.smtp_port = settings.MAIL_PORT
        self.username = settings.MAIL_USERNAME
        self.password = settings.MAIL_PASSWORD
        self.from_email = settings.MAIL_FROM
        self.from_name = settings.MAIL_FROM_NAME

    async def send_email(
        self,
        to_email: str,
        subject: str,
        html_content: str,
        text_content: Optional[str] = None,
        to_name: Optional[str] = None
    ) -> bool:
        """Send an email"""
        
        if not self.username or not self.password:
            logger.warning("Email credentials not configured")
            return False
        
        try:
            # Create message
            msg = MIMEMultipart('alternative')
            msg['From'] = formataddr((self.from_name, self.from_email))
            msg['To'] = formataddr((to_name or "", to_email))
            msg['Subject'] = subject
            
            # Add text version if provided
            if text_content:
                text_part = MIMEText(text_content, 'plain')
                msg.attach(text_part)
            
            # Add HTML version
            html_part = MIMEText(html_content, 'html')
            msg.attach(html_part)
            
            # Connect to server and send email
            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                server.starttls()
                server.login(self.username, self.password)
                server.send_message(msg)
            
            logger.info("Email sent successfully", to=to_email, subject=subject)
            return True
            
        except Exception as e:
            logger.error("Failed to send email", error=str(e), to=to_email, subject=subject)
            return False

    def render_template(self, template_name: str, **kwargs) -> tuple[str, str]:
        """Render email template"""
        try:
            # Try to load HTML template
            html_template = jinja_env.get_template(f"{template_name}.html")
            html_content = html_template.render(**kwargs)
            
            # Try to load text template
            text_content = ""
            try:
                text_template = jinja_env.get_template(f"{template_name}.txt")
                text_content = text_template.render(**kwargs)
            except:
                # Generate simple text version from HTML if no text template exists
                import re
                text_content = re.sub('<[^<]+?>', '', html_content)
            
            return html_content, text_content
            
        except Exception as e:
            logger.error("Failed to render email template", template=template_name, error=str(e))
            return "", ""

# Create email service instance
email_service = EmailService()

# Email template functions
async def send_verification_email(email: str, name: str, token: str) -> bool:
    """Send email verification email"""
    
    verification_url = f"http://localhost:3000/verify-email?token={token}"
    
    html_content, text_content = email_service.render_template(
        "verification",
        name=name,
        verification_url=verification_url,
        app_name="Edital.AI"
    )
    
    # Fallback if template doesn't exist
    if not html_content:
        html_content = f"""
        <html>
            <body>
                <h2>Bem-vindo ao Edital.AI, {name}!</h2>
                <p>Clique no link abaixo para verificar seu email:</p>
                <a href="{verification_url}" style="background: #007bff; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">
                    Verificar Email
                </a>
                <p>Se o link não funcionar, copie e cole este endereço no seu navegador:</p>
                <p>{verification_url}</p>
                <p>Este link expira em 24 horas.</p>
            </body>
        </html>
        """
        
        text_content = f"""
        Bem-vindo ao Edital.AI, {name}!
        
        Clique no link abaixo para verificar seu email:
        {verification_url}
        
        Este link expira em 24 horas.
        """
    
    return await email_service.send_email(
        to_email=email,
        to_name=name,
        subject="Verifique seu email - Edital.AI",
        html_content=html_content,
        text_content=text_content
    )

async def send_password_reset_email(email: str, name: str, token: str) -> bool:
    """Send password reset email"""
    
    reset_url = f"http://localhost:3000/reset-password?token={token}"
    
    html_content, text_content = email_service.render_template(
        "password_reset",
        name=name,
        reset_url=reset_url,
        app_name="Edital.AI"
    )
    
    # Fallback if template doesn't exist
    if not html_content:
        html_content = f"""
        <html>
            <body>
                <h2>Redefinição de Senha - Edital.AI</h2>
                <p>Olá {name},</p>
                <p>Você solicitou a redefinição de sua senha. Clique no link abaixo:</p>
                <a href="{reset_url}" style="background: #dc3545; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">
                    Redefinir Senha
                </a>
                <p>Se o link não funcionar, copie e cole este endereço no seu navegador:</p>
                <p>{reset_url}</p>
                <p>Este link expira em 1 hora.</p>
                <p>Se você não solicitou esta redefinição, ignore este email.</p>
            </body>
        </html>
        """
        
        text_content = f"""
        Redefinição de Senha - Edital.AI
        
        Olá {name},
        
        Você solicitou a redefinição de sua senha. Clique no link abaixo:
        {reset_url}
        
        Este link expira em 1 hora.
        
        Se você não solicitou esta redefinição, ignore este email.
        """
    
    return await email_service.send_email(
        to_email=email,
        to_name=name,
        subject="Redefinir senha - Edital.AI",
        html_content=html_content,
        text_content=text_content
    )

async def send_document_expiry_alert(email: str, name: str, documents: List[dict]) -> bool:
    """Send document expiry alert email"""
    
    html_content, text_content = email_service.render_template(
        "document_expiry",
        name=name,
        documents=documents,
        app_name="Edital.AI",
        dashboard_url="http://localhost:3000/dashboard"
    )
    
    # Fallback if template doesn't exist
    if not html_content:
        doc_list = "<ul>"
        for doc in documents:
            doc_list += f"<li><strong>{doc['name']}</strong> - Vence em {doc['days_until_expiry']} dias</li>"
        doc_list += "</ul>"
        
        html_content = f"""
        <html>
            <body>
                <h2>Alerta de Vencimento - Edital.AI</h2>
                <p>Olá {name},</p>
                <p>Os seguintes documentos estão próximos do vencimento:</p>
                {doc_list}
                <p>Acesse seu painel para atualizar os documentos:</p>
                <a href="http://localhost:3000/dashboard" style="background: #ffc107; color: black; padding: 10px 20px; text-decoration: none; border-radius: 5px;">
                    Acessar Painel
                </a>
            </body>
        </html>
        """
        
        text_content = f"""
        Alerta de Vencimento - Edital.AI
        
        Olá {name},
        
        Os seguintes documentos estão próximos do vencimento:
        """
        for doc in documents:
            text_content += f"- {doc['name']} - Vence em {doc['days_until_expiry']} dias\n"
        
        text_content += "\nAcesse seu painel para atualizar os documentos: http://localhost:3000/dashboard"
    
    return await email_service.send_email(
        to_email=email,
        to_name=name,
        subject="Documentos próximos do vencimento - Edital.AI",
        html_content=html_content,
        text_content=text_content
    )

async def send_analysis_complete_email(email: str, name: str, edital_name: str, edital_id: str) -> bool:
    """Send analysis complete notification email"""
    
    analysis_url = f"http://localhost:3000/editals/{edital_id}"
    
    html_content, text_content = email_service.render_template(
        "analysis_complete",
        name=name,
        edital_name=edital_name,
        analysis_url=analysis_url,
        app_name="Edital.AI"
    )
    
    # Fallback if template doesn't exist
    if not html_content:
        html_content = f"""
        <html>
            <body>
                <h2>Análise Concluída - Edital.AI</h2>
                <p>Olá {name},</p>
                <p>A análise do edital <strong>{edital_name}</strong> foi concluída!</p>
                <p>Clique no link abaixo para ver os resultados:</p>
                <a href="{analysis_url}" style="background: #28a745; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">
                    Ver Análise
                </a>
            </body>
        </html>
        """
        
        text_content = f"""
        Análise Concluída - Edital.AI
        
        Olá {name},
        
        A análise do edital {edital_name} foi concluída!
        
        Acesse: {analysis_url}
        """
    
    return await email_service.send_email(
        to_email=email,
        to_name=name,
        subject=f"Análise concluída: {edital_name} - Edital.AI",
        html_content=html_content,
        text_content=text_content
    )

async def send_welcome_email(email: str, name: str, company_name: str) -> bool:
    """Send welcome email to new users"""
    
    html_content, text_content = email_service.render_template(
        "welcome",
        name=name,
        company_name=company_name,
        dashboard_url="http://localhost:3000/dashboard",
        support_email=settings.MAIL_FROM,
        app_name="Edital.AI"
    )
    
    # Fallback if template doesn't exist
    if not html_content:
        html_content = f"""
        <html>
            <body>
                <h2>Bem-vindo ao Edital.AI, {name}!</h2>
                <p>É um prazer ter você e a {company_name} conosco!</p>
                <p>O Edital.AI vai transformar a forma como sua empresa participa de licitações:</p>
                <ul>
                    <li>✅ Análise inteligente de editais com IA</li>
                    <li>✅ Hub centralizado de documentos</li>
                    <li>✅ Alertas automáticos de vencimento</li>
                    <li>✅ Checklist de conformidade dinâmico</li>
                </ul>
                <p>Comece agora:</p>
                <a href="http://localhost:3000/dashboard" style="background: #007bff; color: white; padding: 15px 30px; text-decoration: none; border-radius: 5px; font-size: 16px;">
                    Acessar Painel
                </a>
                <p>Precisa de ajuda? Entre em contato: {settings.MAIL_FROM}</p>
            </body>
        </html>
        """
        
        text_content = f"""
        Bem-vindo ao Edital.AI, {name}!
        
        É um prazer ter você e a {company_name} conosco!
        
        O Edital.AI vai transformar a forma como sua empresa participa de licitações:
        - Análise inteligente de editais com IA
        - Hub centralizado de documentos  
        - Alertas automáticos de vencimento
        - Checklist de conformidade dinâmico
        
        Acesse seu painel: http://localhost:3000/dashboard
        
        Precisa de ajuda? Entre em contato: {settings.MAIL_FROM}
        """
    
    return await email_service.send_email(
        to_email=email,
        to_name=name,
        subject="Bem-vindo ao Edital.AI! 🚀",
        html_content=html_content,
        text_content=text_content
    )
