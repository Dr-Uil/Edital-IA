import spacy
import re
import dateparser
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional, Tuple, Any
import structlog
import redis.asyncio as redis
import json
import hashlib

from config import settings

logger = structlog.get_logger()

class EditalAnalyzer:
    """Main analyzer for edital documents using NLP and rule-based methods"""
    
    def __init__(self):
        self.nlp = None
        self.redis_client = None
        self.patterns = self._load_patterns()
        self.document_types_mapping = self._load_document_types()
        
    async def initialize(self):
        """Initialize models and connections"""
        try:
            # Load spaCy model
            logger.info(f"Loading spaCy model: {settings.SPACY_MODEL}")
            self.nlp = spacy.load(settings.SPACY_MODEL)
            
            # Add custom components
            self._add_custom_components()
            
            # Initialize Redis connection
            if settings.ENABLE_CACHE:
                self.redis_client = redis.from_url(settings.REDIS_URL)
            
            logger.info("EditalAnalyzer initialized successfully")
            
        except Exception as e:
            logger.error(f"Failed to initialize analyzer: {e}")
            raise
    
    def _add_custom_components(self):
        """Add custom NLP components"""
        
        # Add entity ruler for specific patterns
        if "entity_ruler" not in self.nlp.pipe_names:
            entity_ruler = self.nlp.add_pipe("entity_ruler", before="ner")
            
            # Legal entity patterns
            patterns = [
                {"label": "MODALIDADE", "pattern": [{"LOWER": "pregão"}, {"LOWER": "eletrônico"}]},
                {"label": "MODALIDADE", "pattern": [{"LOWER": "concorrência"}]},
                {"label": "MODALIDADE", "pattern": [{"LOWER": "tomada"}, {"LOWER": "de"}, {"LOWER": "preços"}]},
                {"label": "CRITERIO", "pattern": [{"LOWER": "menor"}, {"LOWER": "preço"}]},
                {"label": "CRITERIO", "pattern": [{"LOWER": "técnica"}, {"LOWER": "e"}, {"LOWER": "preço"}]},
            ]
            entity_ruler.add_patterns(patterns)
    
    def _load_patterns(self) -> Dict[str, List[str]]:
        """Load regex patterns for entity extraction"""
        return {
            "numero_processo": [
                r"processo\s+n[ºª°]?\s*(\d{1,3}[\/\-\.]\d{4}(?:[\/\-\.]\d{2,4})?)",
                r"edital\s+n[ºª°]?\s*(\d{1,3}[\/\-\.]\d{4})",
                r"licitação\s+n[ºª°]?\s*(\d{1,3}[\/\-\.]\d{4})"
            ],
            "cnpj": [
                r"(\d{2}\.?\d{3}\.?\d{3}\/?\d{4}-?\d{2})"
            ],
            "valor_estimado": [
                r"valor\s+(?:total\s+)?estimado[:\s]+r\$?\s*([\d.,]+)",
                r"valor\s+(?:máximo\s+)?aceito[:\s]+r\$?\s*([\d.,]+)",
                r"orçamento\s+estimado[:\s]+r\$?\s*([\d.,]+)"
            ],
            "data_abertura": [
                r"(?:abertura|entrega)\s+(?:das?\s+)?propostas?[:\s]+(\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4})",
                r"até\s+(?:às?\s+)?(\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4})",
            ],
            "data_sessao": [
                r"sessão\s+pública[:\s]+(\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4})",
                r"disputa\s+de\s+lances[:\s]+(\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4})"
            ]
        }
    
    def _load_document_types(self) -> Dict[str, str]:
        """Map document descriptions to types"""
        return {
            "contrato social": "CONTRATO_SOCIAL",
            "ato constitutivo": "CONTRATO_SOCIAL",
            "estatuto social": "CONTRATO_SOCIAL",
            "certidão negativa de débitos federais": "CND_FEDERAL",
            "cnd federal": "CND_FEDERAL",
            "certidão conjunta": "CND_FEDERAL",
            "certidão negativa de débitos estaduais": "CND_ESTADUAL",
            "certidão negativa de débitos municipais": "CND_MUNICIPAL",
            "certidão de regularidade do fgts": "CERTIDAO_FGTS",
            "crf": "CERTIDAO_FGTS",
            "certidão negativa de débitos trabalhistas": "CERTIDAO_TRABALHISTA",
            "cndt": "CERTIDAO_TRABALHISTA",
            "alvará de funcionamento": "ALVARA_FUNCIONAMENTO",
            "licença de funcionamento": "ALVARA_FUNCIONAMENTO",
            "atestado de capacidade técnica": "ATESTADO_CAPACIDADE_TECNICA",
            "comprovação de aptidão": "ATESTADO_CAPACIDADE_TECNICA",
            "balanço patrimonial": "BALANCO_PATRIMONIAL",
            "demonstração de resultados": "DEMONSTRACAO_RESULTADOS",
            "dre": "DEMONSTRACAO_RESULTADOS",
            "certidão de falência": "CERTIDAO_FALENCIA"
        }
    
    async def analyze_text(self, text: str) -> Dict[str, Any]:
        """Main analysis method"""
        
        # Check cache first
        if settings.ENABLE_CACHE and self.redis_client:
            cache_key = self._get_cache_key(text)
            cached_result = await self.redis_client.get(cache_key)
            if cached_result:
                logger.info("Returning cached analysis result")
                return json.loads(cached_result)
        
        # Perform analysis
        result = {
            "analysis": self._extract_basic_info(text),
            "entities": self._extract_entities(text),
            "requirements": self._extract_requirements(text)
        }
        
        # Cache result
        if settings.ENABLE_CACHE and self.redis_client:
            await self.redis_client.setex(
                cache_key,
                settings.CACHE_TTL,
                json.dumps(result, default=str)
            )
        
        return result
    
    def _get_cache_key(self, text: str) -> str:
        """Generate cache key for text"""
        text_hash = hashlib.md5(text.encode()).hexdigest()
        return f"analysis:{text_hash}"
    
    def _extract_basic_info(self, text: str) -> Dict[str, Any]:
        """Extract basic edital information"""
        
        text_lower = text.lower()
        
        # Extract organization
        organizacao = self._extract_organizacao(text)
        
        # Extract modality
        modalidade = self._extract_modalidade(text_lower)
        
        # Extract process number
        numero_processo = self._extract_numero_processo(text)
        
        # Extract dates
        data_abertura = self._extract_data_abertura(text)
        data_sessao = self._extract_data_sessao(text)
        
        # Extract object
        objeto = self._extract_objeto_licitacao(text)
        
        # Extract judgment criteria
        criterio = self._extract_criterio_julgamento(text_lower)
        
        # Extract estimated value
        valor_estimado = self._extract_valor_estimado(text)
        
        return {
            "organizacao_licitante": organizacao,
            "modalidade_licitacao": modalidade,
            "numero_processo": numero_processo,
            "data_abertura_propostas": data_abertura,
            "data_sessao_publica": data_sessao,
            "objeto_licitacao": objeto,
            "criterio_julgamento": criterio,
            "valor_estimado": valor_estimado
        }
    
    def _extract_organizacao(self, text: str) -> Optional[str]:
        """Extract organization name"""
        
        patterns = [
            r"(?:município|prefeitura|câmara|estado|governo)\s+(?:municipal\s+)?(?:de\s+)?([A-Z][a-zA-ZÀ-ÿ\s]+)",
            r"([A-Z][A-Z\s]+LTDA\.?)",
            r"(UNIVERSIDADE[A-Z\s]+)",
            r"(INSTITUTO[A-Z\s]+)"
        ]
        
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                org_name = match.group(1).strip()
                if len(org_name) > 3:  # Avoid very short matches
                    return org_name
        
        return None
    
    def _extract_modalidade(self, text: str) -> Optional[str]:
        """Extract bidding modality"""
        
        modalities = {
            "pregão eletrônico": "Pregão Eletrônico",
            "pregão presencial": "Pregão Presencial", 
            "concorrência": "Concorrência",
            "tomada de preços": "Tomada de Preços",
            "convite": "Convite",
            "concurso": "Concurso",
            "leilão": "Leilão"
        }
        
        for key, value in modalities.items():
            if key in text:
                return value
        
        return None
    
    def _extract_numero_processo(self, text: str) -> Optional[str]:
        """Extract process number"""
        
        for pattern in self.patterns["numero_processo"]:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1)
        
        return None
    
    def _extract_data_abertura(self, text: str) -> Optional[datetime]:
        """Extract proposal opening date"""
        
        for pattern in self.patterns["data_abertura"]:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                date_str = match.group(1)
                parsed_date = dateparser.parse(date_str, languages=['pt'])
                if parsed_date:
                    return parsed_date
        
        return None
    
    def _extract_data_sessao(self, text: str) -> Optional[datetime]:
        """Extract public session date"""
        
        for pattern in self.patterns["data_sessao"]:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                date_str = match.group(1)
                parsed_date = dateparser.parse(date_str, languages=['pt'])
                if parsed_date:
                    return parsed_date
        
        return None
    
    def _extract_objeto_licitacao(self, text: str) -> Optional[str]:
        """Extract bidding object"""
        
        patterns = [
            r"objeto[:\s]+(.{10,200}?)(?:\n|\.)",
            r"contratação\s+de\s+(.{10,200}?)(?:\n|\.)",
            r"aquisição\s+de\s+(.{10,200}?)(?:\n|\.)"
        ]
        
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
            if match:
                objeto = match.group(1).strip()
                if len(objeto) > 10:
                    return objeto
        
        return None
    
    def _extract_criterio_julgamento(self, text: str) -> Optional[str]:
        """Extract judgment criteria"""
        
        criteria = {
            "menor preço": "Menor Preço",
            "técnica e preço": "Técnica e Preço",
            "melhor técnica": "Melhor Técnica",
            "maior desconto": "Maior Desconto"
        }
        
        for key, value in criteria.items():
            if key in text:
                return value
        
        return None
    
    def _extract_valor_estimado(self, text: str) -> Optional[Decimal]:
        """Extract estimated value"""
        
        for pattern in self.patterns["valor_estimado"]:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                value_str = match.group(1)
                # Clean and convert to decimal
                value_str = value_str.replace(".", "").replace(",", ".")
                try:
                    return Decimal(value_str)
                except:
                    continue
        
        return None
    
    def _extract_entities(self, text: str) -> List[Dict[str, Any]]:
        """Extract named entities using spaCy"""
        
        if not self.nlp:
            return []
        
        # Limit text length for performance
        if len(text) > settings.MAX_TEXT_LENGTH:
            text = text[:settings.MAX_TEXT_LENGTH]
        
        doc = self.nlp(text)
        entities = []
        
        for ent in doc.ents:
            if ent.label_ in ["PERSON", "ORG", "GPE", "MONEY", "DATE"]:
                entities.append({
                    "entity_type": ent.label_,
                    "entity_value": ent.text,
                    "confidence": 0.8,  # Default confidence
                    "start_position": ent.start_char,
                    "end_position": ent.end_char
                })
        
        return entities
    
    def _extract_requirements(self, text: str) -> List[Dict[str, Any]]:
        """Extract habilitacao requirements"""
        
        text_lower = text.lower()
        requirements = []
        
        # Common requirement patterns
        requirement_patterns = [
            (r"(?:apresentar|juntar|anexar)\s+(.{5,100}?)(?:\n|;|\.|,)", "DOCUMENTO_EXIGIDO"),
            (r"certidão\s+(?:negativa\s+)?(?:de\s+)?(.{5,50}?)(?:\n|;|\.|,)", "CERTIDAO"),
            (r"comprovante\s+(?:de\s+)?(.{5,50}?)(?:\n|;|\.|,)", "COMPROVANTE"),
            (r"declaração\s+(?:de\s+)?(.{5,50}?)(?:\n|;|\.|,)", "DECLARACAO"),
            (r"atestado\s+(?:de\s+)?(.{5,50}?)(?:\n|;|\.|,)", "ATESTADO")
        ]
        
        for pattern, req_type in requirement_patterns:
            matches = re.finditer(pattern, text, re.IGNORECASE | re.MULTILINE)
            for match in matches:
                description = match.group(1).strip()
                
                # Skip very short or generic descriptions
                if len(description) < 5 or description.lower() in ["de", "da", "do", "das", "dos"]:
                    continue
                
                # Map to document type
                document_type = self._map_to_document_type(description.lower())
                
                requirements.append({
                    "requirement_type": req_type,
                    "description": description,
                    "document_type": document_type,
                    "is_mandatory": True  # Assume mandatory by default
                })
        
        return requirements
    
    def _map_to_document_type(self, description: str) -> Optional[str]:
        """Map requirement description to document type"""
        
        for key, doc_type in self.document_types_mapping.items():
            if key in description:
                return doc_type
        
        return None
    
    async def get_status(self) -> Dict[str, Any]:
        """Get analyzer status"""
        
        status = {
            "spacy_model_loaded": self.nlp is not None,
            "spacy_model_name": settings.SPACY_MODEL if self.nlp else None,
            "redis_connected": False,
            "patterns_loaded": len(self.patterns),
            "document_types_loaded": len(self.document_types_mapping)
        }
        
        # Check Redis connection
        if self.redis_client:
            try:
                await self.redis_client.ping()
                status["redis_connected"] = True
            except Exception:
                status["redis_connected"] = False
        
        return status
