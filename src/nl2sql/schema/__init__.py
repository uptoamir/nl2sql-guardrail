"""Schema graph + descriptions for prompt rendering.

Per `final_1.md` §5 (Graph-RAG schema understanding). v0 ships a minimal
token-match implementation (no embeddings); v1 swaps in dense retrieval
behind the same ``relevant_tables_for_question(q) -> list[str]`` API.
"""
