import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
import chromadb
from chromadb.config import Settings


class VectorStoreManager:
    """ChromaDB manager for semantic indexing and retrieval of employee profiles."""

    def __init__(
        self,
        persist_dir: Optional[str] = None,
        collection_name: str = "employee_profiles",
    ) -> None:
        if persist_dir is None:
            # Default to data/chroma_store relative to bench_forecast root
            base_dir = Path(__file__).resolve().parents[2]
            self.persist_dir = str(base_dir / "data" / "chroma_store")
        else:
            self.persist_dir = persist_dir

        os.makedirs(self.persist_dir, exist_ok=True)
        self.client = chromadb.PersistentClient(path=self.persist_dir)
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

    def similarity_search(self, query: str, k: int = 3) -> List[Dict[str, Any]]:
        """Receives a narrative job requirement and returns the most relevant employee profiles."""
        if self.collection.count() == 0:
            return []

        # Ensure k does not exceed the collection size
        count = self.collection.count()
        n_results = min(k, count)

        results = self.collection.query(
            query_texts=[query],
            n_results=n_results,
            include=["documents", "metadatas", "distances"],
        )

        matches: List[Dict[str, Any]] = []
        if not results or not results["ids"] or not results["ids"][0]:
            return matches

        ids = results["ids"][0]
        docs = results["documents"][0] if results["documents"] else []
        metas = results["metadatas"][0] if results["metadatas"] else []
        distances = results["distances"][0] if results["distances"] else []

        for i in range(len(ids)):
            # Convert cosine distance to similarity score (0.0 to 1.0)
            distance = distances[i] if i < len(distances) else 1.0
            similarity = max(0.0, min(1.0, 1.0 - distance))

            matches.append({
                "id": ids[i],
                "name": metas[i].get("name") if i < len(metas) else "",
                "skills": metas[i].get("skills", "").split(", ") if i < len(metas) and metas[i].get("skills") else [],
                "experience_years": metas[i].get("experience_years", 0.0) if i < len(metas) else 0.0,
                "current_project": metas[i].get("current_project", "") if i < len(metas) else "",
                "available_from": metas[i].get("available_from", "") if i < len(metas) else "",
                "similarity_score": round(similarity, 4),
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
