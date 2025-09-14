import os
import uuid
from pathlib import Path
from typing import Optional, BinaryIO
import aiofiles
import structlog
from minio import Minio
from minio.error import S3Error
import hashlib
from datetime import timedelta

from config import settings

logger = structlog.get_logger()

class FileStorage:
    """Handles file storage operations (local and S3-compatible)"""
    
    def __init__(self):
        self.use_s3 = settings.MINIO_ENDPOINT is not None
        
        if self.use_s3:
            self.client = Minio(
                settings.MINIO_ENDPOINT,
                access_key=settings.MINIO_ACCESS_KEY,
                secret_key=settings.MINIO_SECRET_KEY,
                secure=settings.MINIO_SECURE
            )
            self._ensure_bucket()
        
        # Ensure local directories exist
        for directory in [settings.DOCUMENT_DIR, settings.EDITAL_DIR, settings.TEMP_DIR]:
            directory.mkdir(parents=True, exist_ok=True)
    
    def _ensure_bucket(self):
        """Ensure the S3 bucket exists"""
        try:
            if not self.client.bucket_exists(settings.BUCKET_NAME):
                self.client.make_bucket(settings.BUCKET_NAME)
                logger.info("Created S3 bucket", bucket=settings.BUCKET_NAME)
        except S3Error as e:
            logger.error("Failed to create S3 bucket", error=str(e))
    
    def _generate_file_path(self, filename: str, folder: str, company_id: str) -> str:
        """Generate a unique file path"""
        # Extract file extension
        file_ext = Path(filename).suffix.lower()
        
        # Generate unique filename
        unique_id = str(uuid.uuid4())
        safe_filename = f"{unique_id}{file_ext}"
        
        # Create path structure: folder/company_id/year/month/filename
        from datetime import datetime
        now = datetime.now()
        
        if self.use_s3:
            return f"{folder}/{company_id}/{now.year}/{now.month:02d}/{safe_filename}"
        else:
            # Local file system
            local_path = settings.UPLOAD_DIR / folder / company_id / str(now.year) / f"{now.month:02d}"
            local_path.mkdir(parents=True, exist_ok=True)
            return str(local_path / safe_filename)
    
    async def store_document(self, content: bytes, filename: str, company_id: str) -> str:
        """Store a document file"""
        return await self._store_file(content, filename, "documents", company_id)
    
    async def store_edital(self, content: bytes, filename: str, company_id: str) -> str:
        """Store an edital file"""
        return await self._store_file(content, filename, "editals", company_id)
    
    async def _store_file(self, content: bytes, filename: str, folder: str, company_id: str) -> str:
        """Store a file in the configured storage"""
        
        file_path = self._generate_file_path(filename, folder, company_id)
        
        try:
            if self.use_s3:
                # Store in S3-compatible storage
                from io import BytesIO
                data = BytesIO(content)
                
                self.client.put_object(
                    settings.BUCKET_NAME,
                    file_path,
                    data,
                    length=len(content),
                    content_type=self._get_content_type(filename)
                )
                
                logger.info("File stored in S3", path=file_path, size=len(content))
                return file_path
            else:
                # Store in local file system
                async with aiofiles.open(file_path, 'wb') as f:
                    await f.write(content)
                
                logger.info("File stored locally", path=file_path, size=len(content))
                return file_path
                
        except Exception as e:
            logger.error("File storage error", error=str(e), path=file_path)
            raise
    
    async def get_file(self, file_path: str) -> Optional[bytes]:
        """Retrieve a file's content"""
        
        try:
            if self.use_s3:
                # Get from S3
                response = self.client.get_object(settings.BUCKET_NAME, file_path)
                return response.read()
            else:
                # Get from local file system
                if os.path.exists(file_path):
                    async with aiofiles.open(file_path, 'rb') as f:
                        return await f.read()
                return None
                
        except Exception as e:
            logger.error("File retrieval error", error=str(e), path=file_path)
            return None
    
    async def delete_file(self, file_path: str) -> bool:
        """Delete a file"""
        
        try:
            if self.use_s3:
                # Delete from S3
                self.client.remove_object(settings.BUCKET_NAME, file_path)
            else:
                # Delete from local file system
                if os.path.exists(file_path):
                    os.remove(file_path)
            
            logger.info("File deleted", path=file_path)
            return True
            
        except Exception as e:
            logger.error("File deletion error", error=str(e), path=file_path)
            return False
    
    async def get_download_url(self, file_path: str, expiry: int = 3600) -> Optional[str]:
        """Get a presigned download URL (for S3) or return None for direct file serving"""
        
        if self.use_s3:
            try:
                # Generate presigned URL for S3
                url = self.client.presigned_get_object(
                    settings.BUCKET_NAME,
                    file_path,
                    expires=timedelta(seconds=expiry)
                )
                return url
            except Exception as e:
                logger.error("Failed to generate presigned URL", error=str(e), path=file_path)
                return None
        
        # For local files, return None to indicate direct file serving
        return None
    
    def _get_content_type(self, filename: str) -> str:
        """Get content type from filename extension"""
        
        ext = Path(filename).suffix.lower()
        content_types = {
            '.pdf': 'application/pdf',
            '.doc': 'application/msword',
            '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.png': 'image/png',
            '.gif': 'image/gif',
            '.txt': 'text/plain',
            '.csv': 'text/csv',
            '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            '.xls': 'application/vnd.ms-excel'
        }
        
        return content_types.get(ext, 'application/octet-stream')
    
    def calculate_file_hash(self, content: bytes) -> str:
        """Calculate MD5 hash of file content"""
        return hashlib.md5(content).hexdigest()
    
    async def file_exists(self, file_path: str) -> bool:
        """Check if a file exists"""
        
        try:
            if self.use_s3:
                # Check in S3
                try:
                    self.client.stat_object(settings.BUCKET_NAME, file_path)
                    return True
                except S3Error as e:
                    if e.code == 'NoSuchKey':
                        return False
                    raise
            else:
                # Check in local file system
                return os.path.exists(file_path)
                
        except Exception as e:
            logger.error("File existence check error", error=str(e), path=file_path)
            return False
    
    async def get_file_info(self, file_path: str) -> Optional[dict]:
        """Get file information (size, modified time, etc.)"""
        
        try:
            if self.use_s3:
                # Get from S3
                stat = self.client.stat_object(settings.BUCKET_NAME, file_path)
                return {
                    'size': stat.size,
                    'last_modified': stat.last_modified,
                    'etag': stat.etag,
                    'content_type': stat.content_type
                }
            else:
                # Get from local file system
                if os.path.exists(file_path):
                    stat = os.stat(file_path)
                    return {
                        'size': stat.st_size,
                        'last_modified': stat.st_mtime,
                        'content_type': self._get_content_type(file_path)
                    }
                return None
                
        except Exception as e:
            logger.error("File info error", error=str(e), path=file_path)
            return None
    
    async def cleanup_temp_files(self, older_than_hours: int = 24):
        """Clean up temporary files older than specified hours"""
        
        try:
            from datetime import datetime, timedelta
            cutoff_time = datetime.now() - timedelta(hours=older_than_hours)
            
            if self.use_s3:
                # List and delete old temp files in S3
                objects = self.client.list_objects(
                    settings.BUCKET_NAME,
                    prefix="temp/",
                    recursive=True
                )
                
                deleted_count = 0
                for obj in objects:
                    if obj.last_modified < cutoff_time:
                        self.client.remove_object(settings.BUCKET_NAME, obj.object_name)
                        deleted_count += 1
                
                logger.info(f"Cleaned up {deleted_count} temp files from S3")
            else:
                # Clean up local temp files
                temp_dir = settings.TEMP_DIR
                deleted_count = 0
                
                for file_path in temp_dir.rglob("*"):
                    if file_path.is_file():
                        file_time = datetime.fromtimestamp(file_path.stat().st_mtime)
                        if file_time < cutoff_time:
                            file_path.unlink()
                            deleted_count += 1
                
                logger.info(f"Cleaned up {deleted_count} temp files locally")
                
        except Exception as e:
            logger.error("Temp file cleanup error", error=str(e))

class DocumentProcessor:
    """Handles document processing tasks"""
    
    @staticmethod
    async def extract_text_from_pdf(file_path: str) -> str:
        """Extract text from PDF file"""
        
        try:
            import fitz  # PyMuPDF
            
            doc = fitz.open(file_path)
            text = ""
            
            for page_num in range(len(doc)):
                page = doc.load_page(page_num)
                text += page.get_text()
            
            doc.close()
            return text
            
        except Exception as e:
            logger.error("PDF text extraction error", error=str(e), path=file_path)
            return ""
    
    @staticmethod
    async def extract_text_with_ocr(file_path: str) -> str:
        """Extract text using OCR for scanned documents"""
        
        try:
            import pytesseract
            from PIL import Image
            
            # For PDF files, convert to images first
            if file_path.lower().endswith('.pdf'):
                import fitz
                doc = fitz.open(file_path)
                text = ""
                
                for page_num in range(len(doc)):
                    page = doc.load_page(page_num)
                    pix = page.get_pixmap()
                    img_data = pix.tobytes("ppm")
                    
                    # Use OCR on the image
                    img = Image.open(BytesIO(img_data))
                    page_text = pytesseract.image_to_string(img, lang='por')
                    text += page_text + "\n"
                
                doc.close()
                return text
            else:
                # Direct OCR for image files
                img = Image.open(file_path)
                return pytesseract.image_to_string(img, lang='por')
                
        except Exception as e:
            logger.error("OCR text extraction error", error=str(e), path=file_path)
            return ""
    
    @staticmethod
    def validate_document_format(file_content: bytes, filename: str) -> tuple[bool, str]:
        """Validate document format and integrity"""
        
        try:
            # Check file signature (magic bytes)
            import magic
            
            mime_type = magic.from_buffer(file_content, mime=True)
            
            # Validate against allowed types
            if mime_type not in settings.ALLOWED_FILE_TYPES:
                return False, f"File type {mime_type} not allowed"
            
            # Additional format-specific validations
            if mime_type == 'application/pdf':
                # Basic PDF validation
                if not file_content.startswith(b'%PDF-'):
                    return False, "Invalid PDF format"
            
            return True, "Valid document format"
            
        except Exception as e:
            logger.error("Document validation error", error=str(e))
            return False, "Unable to validate document format"
