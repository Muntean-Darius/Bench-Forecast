from typing import Any, Dict, List


class VectorStoreManager:
    """ChromaDB vector store manager for semantic skill retrieval."""

    def __init__(self, persist_dir: str = "./data/chroma_store") -> None:
        ...

    def similarity_search(self, query: str, k: int = 5) -> List[Dict[str, Any]]:
        ...

