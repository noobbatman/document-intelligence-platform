from app.extraction.affidavit import AffidavitExtractor
from app.extraction.bank_statement import BankStatementExtractor
from app.extraction.base import Extractor
from app.extraction.case_brief import CaseBriefExtractor
from app.extraction.contract import ContractExtractor
from app.extraction.invoice import InvoiceExtractor
from app.extraction.legal_complaint import LegalComplaintExtractor
from app.extraction.legal_notice import LegalNoticeExtractor
from app.extraction.receipt import ReceiptExtractor
from app.extraction.schema_extractor import SchemaDrivenExtractor, schema_exists
from app.extraction.unknown import UnknownExtractor

_REGISTRY: dict[str, type[Extractor]] = {
    "invoice": InvoiceExtractor,
    "bank_statement": BankStatementExtractor,
    "receipt": ReceiptExtractor,
    "contract": ContractExtractor,
    "legal_complaint": LegalComplaintExtractor,
    "legal_notice": LegalNoticeExtractor,
    "case_brief": CaseBriefExtractor,
    "affidavit": AffidavitExtractor,
}


def get_extractor(document_type: str) -> Extractor:
    if schema_exists(document_type):
        return SchemaDrivenExtractor(document_type)
    cls = _REGISTRY.get(document_type, UnknownExtractor)
    return cls()
