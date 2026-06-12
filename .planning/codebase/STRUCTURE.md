# Project Structure

Generated: 2026-06-12

```
novel-mind/
├── AGENTS.md                       # AI agent instructions
├── README.md                       # Public project overview
├── IMPLEMENTATION-STATUS.md        # Authoritative implementation status
├── docker-compose.yml              # Dev services (PostgreSQL + ChromaDB)
├── Makefile                        # Convenience commands
│
├── backend/
│   ├── app/
│   │   ├── main.py                 # FastAPI entry point
│   │   ├── config.py               # pydantic-settings config
│   │   ├── api/                    # Route layer (8 modules)
│   │   ├── core/                   # Infrastructure (DB, security, crypto, SSRF, logging)
│   │   ├── models/                 # ORM layer (13 tables)
│   │   ├── schemas/                # Pydantic contract layer
│   │   └── services/               # Business logic (7 modules)
│   ├── tests/                      # pytest (172 tests)
│   ├── migrations/                 # Alembic (head: f3c8b7b2dbf7)
│   └── uploads/                    # Novel TXT files
│
├── frontend/
│   └── src/
│       ├── app/                    # Next.js App Router pages
│       │   ├── layout.tsx          # Root layout + AuthGate
│       │   ├── page.tsx            # Home
│       │   ├── novels/             # Novel list + detail
│       │   ├── settings/           # AI model settings
│       │   └── writing/            # Writing page (skeleton)
│       ├── components/             # React components
│       │   ├── auth-gate.tsx
│       │   ├── reader/             # Reader (sidebar/content/progress)
│       │   └── ui/                 # shadcn/ui (10 components)
│       ├── lib/                    # API client + utils
│       ├── stores/                 # Zustand state
│       ├── hooks/                  # Custom hooks
│       └── __tests__/              # Vitest (22 tests)
│
├── docs/
│   ├── README.md                   # Doc index
│   ├── API.md                      # API reference
│   ├── GETTING-STARTED.md          # Local setup
│   ├── DEVELOPMENT.md              # Dev conventions
│   ├── TESTING.md                  # Testing guide
│   ├── CONFIGURATION.md            # Config reference
│   ├── DEPLOYMENT.md               # Deploy guide
│   ├── 需求文档.md                  # Product requirements
│   ├── 路线图.md                    # Product roadmap
│   ├── 技术架构.md                  # Target architecture
│   ├── 竞品调研报告.md              # Competitive analysis
│   └── architecture/               # System architecture docs (11 docs + 5 diagrams)
│
└── .planning/                      # GSD AI workspace
    ├── config.json                 # Active milestone/plan routing
    ├── PROJECT.md                  # Core value + execution rules
    ├── STATE.md                    # Single execution cursor
    ├── ROADMAP.md                  # Milestone roadmap
    ├── REQUIREMENTS.md             # Tracked requirements
    ├── phases/                     # Plan + summary per phase
    ├── backlog/                    # Deferred plans
    ├── codebase/                   # Generated codebase maps
    └── intel/                      # Structured intelligence
```
