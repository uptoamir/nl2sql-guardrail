"""Retrieval-augmented few-shot for the drafter prompt.

Per `final_1.md` §6 + §41.5. v0 ships BM25-only (no dense retrieval, no
chromadb); v1 adds bge-small + RRF behind the same retriever API.
"""
