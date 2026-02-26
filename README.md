# Geo-Architect

Design partner and institutional memory for Hum's multi-agent geospatial system.

## What This Is

This is a Claude Code project that serves as the architect agent for a multi-agent geospatial platform. It holds the full context of design decisions, architecture rationale, and scaling plans across the system's agents — so you can pick up where you left off without re-explaining everything.

The `CLAUDE.md` file contains the accumulated design context. When you run `claude` from this directory, you get a session that understands the system architecture and can help you iterate on it.

## The System

Hum's geospatial agent system is a set of specialized Claude-based agents that collaborate:

| Agent | Status | Role |
|-------|--------|------|
| **Librarian** | Built (21 datasets) | Curated dataset catalog. Recommends data for analyst queries with deep reasoning about tradeoffs. |
| **Analyst** | Planned | Performs geospatial analysis using librarian recommendations and the Hum Data Engine. |
| **Archivist** | Planned | Discovers, catalogs, and maintains the dataset library over time. |

The librarian lives in `../Geospatial Libraian/` and uses a two-tier catalog: a lightweight index scanned in full, with detailed profiles loaded on demand. A third tier (`recipes/`) holds Data Engine code snippets and access guides.

## Usage

```bash
cd geo-architect
claude
```

Then ask it to help with things like:

- "Review the librarian's system prompt and suggest improvements"
- "How should the analyst agent call the librarian?"
- "What would need to change to support 500 datasets?"
- "Help me design the archivist agent"
- "The librarian recommended X for this query — is that right?"

The architect can read into the librarian project via relative paths (`../Geospatial Libraian/`) to inspect profiles, the index, schemas, and the subagent definition.

## Key Architecture Decisions

These are documented in detail in `CLAUDE.md`, but the highlights:

- **YAML on disk** over databases or vector stores — auditable, version-controllable, zero infrastructure at current scale.
- **Capabilities over use cases** — dataset entries describe what the data can perceive, not what applications it's used for. The agent reasons from capabilities to applications at query time.
- **Just-in-time retrieval** — the full index is cheap to scan (~10-15k tokens for 200 datasets); full profiles are loaded only for candidates.
- **Prose over JSON** for inter-agent communication — since both agents are Claude, natural language with deep reasoning beats structured data.

## Scaling Roadmap

The current architecture handles ~200 datasets comfortably. Options evaluated for scaling beyond that:

- **QMD** — local hybrid search (BM25 + vector + LLM reranking) as an MCP server. Augments the index scan, doesn't replace it. Most likely next step.
- **GraphRAG** — for when dataset relationships outgrow what prose can capture. See the GeoGraphRAG paper for domain-specific precedent.
- **A-MEM** — Zettelkasten-style evolving memory, relevant for the archivist agent.
- **Supermemory** — experiential memory layer for tracking usage patterns. Later consideration.

## Files

```
CLAUDE.md    — Full design context (system prompt for the architect agent)
README.md    — This file
```
