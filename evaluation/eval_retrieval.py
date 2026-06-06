"""Evaluate retrieval quality for legal RAG chunks.

Expected input JSON, DB-backed:
[
  {
    "document_id": "...",
    "query": "payment terms",
    "relevant_chunk_ids": ["..."]
  }
]

Offline synthetic cases can also provide chunks directly:
[
  {
    "query": "payment terms",
    "chunks": [{"id": "c1", "text": "..."}],
    "relevant_chunk_ids": ["c1"]
  }
]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def evaluate(cases: list[dict], *, top_k: int = 5) -> dict:
    from app.db.session import SessionLocal
    from app.rag.embedder import get_embedder
    from app.rag.retrieval_service import RetrievalService, _cosine

    service = RetrievalService()
    embedder = get_embedder()
    recalls: list[float] = []
    reciprocal_ranks: list[float] = []
    db = SessionLocal()
    try:
        for case in cases:
            relevant = set(case.get("relevant_chunk_ids", []))
            if case.get("chunks"):
                query_vec = embedder.encode_query(case["query"])
                chunk_vectors = embedder.encode_passages([chunk["text"] for chunk in case["chunks"]])
                ranked = sorted(
                    zip(case["chunks"], chunk_vectors, strict=True),
                    key=lambda item: _cosine(query_vec, item[1]),
                    reverse=True,
                )
                returned = [chunk["id"] for chunk, _ in ranked[:top_k]]
            else:
                results = service.retrieve(
                    case["document_id"],
                    case["query"],
                    top_k=top_k,
                    min_score=-1.0,
                    session=db,
                )
                returned = [item.chunk_id for item in results]
            recalls.append(1.0 if relevant.intersection(returned) else 0.0)
            rank = next((idx + 1 for idx, chunk_id in enumerate(returned) if chunk_id in relevant), None)
            reciprocal_ranks.append(1.0 / rank if rank else 0.0)
    finally:
        db.close()

    return {
        "cases": len(cases),
        f"recall@{top_k}": round(sum(recalls) / len(recalls), 4) if recalls else 0.0,
        "mrr": round(sum(reciprocal_ranks) / len(reciprocal_ranks), 4) if reciprocal_ranks else 0.0,
    }


def main() -> None:
    import sys

    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    parser = argparse.ArgumentParser()
    parser.add_argument("cases", type=Path)
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    print(json.dumps(evaluate(cases, top_k=args.top_k), indent=2))


if __name__ == "__main__":
    main()
