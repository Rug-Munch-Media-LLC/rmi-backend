"""Specialist agents pre-configured for RMI domains."""

from __future__ import annotations
from .base import BaseAgent

def create_scanner() -> BaseAgent:
    return BaseAgent(name="scanner", model="deepseek-chat")

def create_auditor() -> BaseAgent:
    return BaseAgent(name="auditor", model="deepseek-reasoner")

def create_analyst() -> BaseAgent:
    return BaseAgent(name="analyst", model="deepseek-chat")

def create_writer() -> BaseAgent:
    return BaseAgent(name="writer", model="mistral-large")

# Crew-style agent composition
AGENTS = {
    "scanner": create_scanner,
    "auditor": create_auditor,
    "analyst": create_analyst,
    "writer": create_writer,
}
