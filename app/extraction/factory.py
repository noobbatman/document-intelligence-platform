from app.extraction.base import Extractor
from app.extraction.schema_extractor import SchemaDrivenExtractor, schema_exists


def get_extractor(document_type: str) -> Extractor:
    if schema_exists(document_type):
        return SchemaDrivenExtractor(document_type)
    return SchemaDrivenExtractor("unknown")
