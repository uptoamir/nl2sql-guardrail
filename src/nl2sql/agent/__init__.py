"""Multi-stage agent pipeline.

Per `final_1.md` §4 + §36.2. The Pipeline class owns stage orchestration;
Session is identity + history (see §41.2). All stage handoffs are typed
Pydantic models defined in :mod:`nl2sql.agent.types`.
"""
