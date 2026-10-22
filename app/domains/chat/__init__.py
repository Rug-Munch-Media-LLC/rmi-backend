"""Canonical chat domain — RAG-grounded, web-augmented, never-fail answers.

Public surface: ``app.domains.chat.routes.router`` (POST /api/v1/chat).
Personas and the model chain live in sibling modules owned by other agents;
:mod:`app.domains.chat.ports` resolves them and falls back safely.
"""
