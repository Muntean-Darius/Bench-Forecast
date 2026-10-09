"""Phase 2 — ChromaDB ingestion pipeline package.

Modules:
    pdf_extractor  — PDF text extraction via pdfplumber + chunking utilities
    embedder       — Ollama nomic-embed-text embedding generation
    chroma_store   — ChromaDB upsert logic (employee_profiles collection)
    document_crud  — SQLAlchemy helpers to update documents.chroma_doc_id / ingestion_status
"""

