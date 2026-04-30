"""Agent pipeline stages.

Per `final_1.md` §41.1. Each stage is a module exporting a single
function with a typed signature. Stages run in order:
``intent → linker → drafter → critic → executor → interpreter``.

The Pipeline (§36.2 / §41.2) calls them; this module keeps them
loose so each can be tested in isolation.
"""
