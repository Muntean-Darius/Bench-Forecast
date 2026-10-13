import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
import chromadb
from chromadb.config import Settings
from opentelemetry.instrumentation.chromadb import ChromaInstrumentor

# Instrument ChromaDB for Phoenix Tracing
ChromaInstrumentor().instrument()


class VectorStoreManager:
    """ChromaDB manager for semantic indexing and retrieval of employee profiles."""

    def __init__(
        self,
        persist_dir: Optional[str] = None,
        collection_name: str = "employee_profiles",
    ) -> None:
        if persist_dir is None:
            # Default to chroma_data relative to bench_forecast root (matches seed script)
            base_dir = Path(__file__).resolve().parents[2]
            self.persist_dir = str(base_dir / "chroma_data")
        else:
            self.persist_dir = persist_dir

        os.makedirs(self.persist_dir, exist_ok=True)
        self.client = chromadb.PersistentClient(path=self.persist_dir)
        
        from chromadb.utils import embedding_functions
        from src.core.config import Config
        self.embedding_fn = embedding_functions.OllamaEmbeddingFunction(
            url=f"{Config.OLLAMA_BASE_URL}/api/embeddings",
            model_name=Config.EMBEDDING_MODEL,
        )

        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    def index_employees(self, employees: List[Dict[str, Any]]) -> int:
        """Indexes employee unstructured bios and skills into ChromaDB."""
        if not employees:
            return 0

        ids: List[str] = []
        documents: List[str] = []
        metadatas: List[Dict[str, Any]] = []

        for emp in employees:
            emp_id = str(emp["id"])
            skills_str = ", ".join(emp.get("skills", []))
            bio = emp.get("bio", "")

            # Form rich textual document for semantic embedding
            doc_text = f"Nume: {emp.get('name')}\nCompetențe: {skills_str}\nExperiență: {bio}"

            ids.append(emp_id)
            documents.append(doc_text)
            metadatas.append({
                "id": emp_id,
                "name": str(emp.get("name", "")),
                "skills": skills_str,
                "experience_years": float(emp.get("experience_years", 0.0)),
                "available_from": str(emp.get("available_from", "")),
                "current_project": str(emp.get("current_project", "")),
            })

        self.collection.upsert(ids=ids, documents=documents, metadatas=metadatas)
        return len(ids)

    def similarity_search(
        self,
        query: str,
        k: int = 3,
        where: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Receives a narrative job requirement and returns the most relevant employee profiles."""
        if self.collection.count() == 0:
            return []

        # Ensure k does not exceed the collection size
        count = self.collection.count()
        n_results = min(k, count)

        # Compute embedding manually using Ollama to bypass ChromaDB's internal default
        query_embeddings = self.embedding_fn([query])

        query_kwargs: Dict[str, Any] = {
            "query_embeddings": query_embeddings,
            "n_results": n_results,
            "include": ["documents", "metadatas", "distances", "embeddings"],
        }
        if where:
            query_kwargs["where"] = where

        results = self.collection.query(**query_kwargs)

        matches: List[Dict[str, Any]] = []
        if not results or not results["ids"] or not results["ids"][0]:
            return matches

        ids = results["ids"][0]
        docs = results["documents"][0] if results["documents"] else []
        metas = results["metadatas"][0] if results["metadatas"] else []
        distances = results["distances"][0] if results["distances"] else []
        embeddings = results.get("embeddings", [[]])[0]

        q_emb = query_embeddings[0]
        import math
        q_norm = math.sqrt(sum(x * x for x in q_emb)) if q_emb is not None and len(q_emb) > 0 else 1.0

        for i in range(len(ids)):
            if i < len(embeddings) and embeddings[i] is not None and len(embeddings[i]) > 0:
                d_emb = embeddings[i]
                d_norm = math.sqrt(sum(x * x for x in d_emb))
                dot = sum(x * y for x, y in zip(q_emb, d_emb))
                if q_norm > 0 and d_norm > 0:
                    cos_sim = dot / (q_norm * d_norm)
                else:
                    cos_sim = 0.0
                similarity = max(0.0, min(1.0, cos_sim))
            else:
                distance = distances[i] if i < len(distances) else 1.0
                similarity = max(0.0, min(1.0, 1.0 - distance))

            meta = metas[i] if i < len(metas) and metas[i] else {}
            emp_id = meta.get("employee_id") or meta.get("id") or ids[i]

            matches.append({
                "id": str(emp_id),
                "employee_id": str(emp_id),
                "name": meta.get("name") or meta.get("employee_name") or "",
                "skills": meta.get("skills", "").split(", ") if meta.get("skills") else [],
                "experience_years": meta.get("experience_years", 0.0),
                "current_project": meta.get("current_project", ""),
                "available_from": meta.get("available_from", ""),
                "similarity_score": float(round(similarity, 4)),
                "document_excerpt": docs[i] if i < len(docs) else "",
            })

        return matches

    def load_and_index_mock_data(self, mock_data_path: Optional[str] = None) -> int:
        """Loads data from mock_data.json and indexes all employee profiles."""
        if mock_data_path is None:
            base_dir = Path(__file__).resolve().parents[2]
            mock_data_path = str(base_dir / "data" / "mock_data.json")

        with open(mock_data_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        employees = data.get("employees", [])
        return self.index_employees(employees)


if __name__ == "__main__":
    # Test indexing and retrieval
    manager = VectorStoreManager()
    count = manager.load_and_index_mock_data()
    print(f"Indexed {count} employees into ChromaDB.")

    sample_query = "Avem nevoie de un inginer pentru migrare microservicii Java Spring Boot cu Kafka."
    top_matches = manager.similarity_search(sample_query, k=2)
    print("\nTop matches for query:")
    for match in top_matches:
        print(f"- {match['name']} (Score: {match['similarity_score']}) - Skills: {match['skills']}")
