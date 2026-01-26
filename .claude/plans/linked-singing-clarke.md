# FastAPI Backend Setup Implementation Plan

## Overview
Setting up a FastAPI backend project structure with Notion, Supabase, and AI service integrations for the memo-synthesizer application.

## Current State
- Repository contains only README.md and LICENSE
- No existing Python code or backend structure
- Clean Git history on main branch
- No .gitignore file (security risk for API keys)

## Implementation Strategy

### Phase 1: Root-Level Configuration (2 files)
Essential security and project configuration files.

1. **[.gitignore](.gitignore)** - CRITICAL for security
   - Prevent .env files with API keys from being committed
   - Exclude Python cache, virtual environments, IDE files
   - Based on GitHub's Python template

2. **[.python-version](.python-version)**
   - Specify Python 3.11 (recommended for FastAPI performance)
   - Auto-detected by pyenv and other tools

### Phase 2: Backend Package Structure (3 files)
Core backend package with dependencies and configuration.

3. **[backend/__init__.py](backend/__init__.py)**
   - Make backend a proper Python package
   - Define `__version__ = "0.1.0"`

4. **[backend/requirements.txt](backend/requirements.txt)** - Core dependencies
   ```
   fastapi==0.109.0
   uvicorn==0.27.0
   python-dotenv==1.0.0
   pydantic-settings==2.1.0  # ADDED: Required for config.py
   httpx==0.26.0
   notion-client==2.2.1
   supabase==2.3.4
   openai==1.12.0
   anthropic==0.18.1
   ```

5. **[backend/.env.example](backend/.env.example)** - Environment variable template
   - NOTION_API_KEY, NOTION_DATABASE_ID
   - SUPABASE_URL, SUPABASE_KEY
   - OPENAI_API_KEY, ANTHROPIC_API_KEY
   - Optional: ENVIRONMENT, HOST, PORT, CORS_ORIGINS

### Phase 3: Configuration Management (1 file)
Type-safe configuration with validation.

6. **[backend/config.py](backend/config.py)**
   - Use pydantic-settings BaseSettings
   - Load all environment variables with validation
   - Fail fast at startup if required config missing
   - Helper properties: `cors_origins_list`, `is_development`, `is_production`
   - `@lru_cache` on `get_settings()` for single instance

### Phase 4: API Application (4 files)
FastAPI app setup with health endpoint.

7. **[backend/schemas/__init__.py](backend/schemas/__init__.py)** & **[backend/schemas/health.py](backend/schemas/health.py)**
   - Pydantic model for health check response
   - Returns: status, version, environment

8. **[backend/routers/__init__.py](backend/routers/__init__.py)** & **[backend/routers/health.py](backend/routers/health.py)**
   - Health check endpoint at `/health`
   - Returns `{"status": "ok", "version": "0.1.0", "environment": "..."}`
   - Uses dependency injection for settings

9. **[backend/main.py](backend/main.py)** - Application entry point
   - Application factory pattern: `create_app()`
   - CORS middleware with localhost:3000 allowed
   - Interactive docs (/docs, /redoc) only in development
   - Startup/shutdown event handlers
   - Include health_router

### Phase 5: Service Layer Scaffolding (3 files)
Package structure for future business logic.

10. **[backend/services/__init__.py](backend/services/__init__.py)** - Business logic layer
11. **[backend/models/__init__.py](backend/models/__init__.py)** - Data models
12. **[backend/utils/__init__.py](backend/utils/__init__.py)** - Helper functions

### Phase 6: Documentation (1 file)

13. **[backend/README.md](backend/README.md)**
    - Setup instructions (venv, dependencies, .env)
    - Running the app (dev and prod modes)
    - API endpoints documentation
    - Project structure overview

## Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Python Version | 3.11+ | Better async performance, improved type hints for FastAPI |
| Config Management | pydantic-settings | Type-safe, auto-validation, excellent developer experience |
| CORS Origins | localhost:3000 default | Standard frontend development port |
| Documentation | Dev-only | Security: hide /docs in production |
| Dependencies | Pinned versions | Reproducibility and stability |
| App Pattern | Factory function | Enables testing with multiple app instances |

## Critical Files (Implementation Priority)

1. **[.gitignore](.gitignore)** - Security: prevents API key leaks
2. **[backend/requirements.txt](backend/requirements.txt)** - Must include pydantic-settings
3. **[backend/config.py](backend/config.py)** - Configuration backbone with validation
4. **[backend/main.py](backend/main.py)** - Application entry point
5. **[backend/.env.example](backend/.env.example)** - Configuration documentation

## Verification Steps

After implementation:

```bash
cd backend
python3.11 -m venv venv
source venv/bin/activate  # macOS/Linux
pip install -r requirements.txt
cp .env.example .env
# Edit .env with actual API keys
uvicorn main:app --reload
```

Test endpoints:
- http://localhost:8000/health → `{"status": "ok", "version": "0.1.0", ...}`
- http://localhost:8000/docs → Interactive API documentation

## Total Files to Create

**16 files:**
- 2 root-level configuration files
- 14 backend files (main app, routers, schemas, services, models, utils, docs)

## Implementation Roadmap

### ✅ Phase 1: Basic Setup (COMPLETED)
- Backend structure with FastAPI
- Notion API integration (database query working)
- Environment configuration
- Health check endpoint

### 🎯 Phase 2: MVP - Single Database Mode (CURRENT)
**Goal**: Complete core functionality with single Notion database

**Features to implement**:
1. Extract all data from Notion DB (제목, 본문, 키워드)
2. AI-powered memo synthesis (using OpenAI or Anthropic)
3. Save synthesized results to Supabase
4. Query/search synthesized memos

**Current config**: Single `NOTION_DATABASE_ID` in .env

### 📋 Phase 3: Multi-Source Mode (FUTURE)
**Goal**: Support multiple Notion sources (databases + pages with sub-pages)

**Required changes**:
- Change from single `NOTION_DATABASE_ID` to multiple source IDs
- Support different source types:
  - Database IDs
  - Page IDs (with recursive sub-page crawling)
- Unified data extraction regardless of source type

**Future config example**:
```
NOTION_SOURCES=db:2cc2686c...,page:abc123...,db:def456...
```

---

## Current Focus: Complete Phase 2 MVP
