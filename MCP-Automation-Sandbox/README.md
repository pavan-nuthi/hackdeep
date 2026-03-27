# Test Pilot

**Automated MCP server generation, deployment, and AI-agent testing — from GitHub repos to live endpoints in one click.**

Test Pilot takes GitHub repository URLs containing OpenAPI/Swagger specs, generates MCP (Model Context Protocol) servers, deploys them and the upstream APIs to [Blaxel](https://blaxel.ai), then runs an AI agent to validate everything end-to-end.

---

## Repository Structure

```
.
├── generate.py                          # CLI: standalone spec → MCP generator
├── pipeline/                            # Core pipeline library
│   ├── ingest.py                        #   Parse OpenAPI 3.x / Swagger 2.x / Postman v2.1
│   ├── mine.py                          #   Discover MCP tools from API endpoints
│   ├── safety.py                        #   Safety classification & execution policy
│   ├── codegen.py                       #   LLM-powered MCP server code generation
│   ├── models.py                        #   Shared data models
│   ├── reasoning.py                     #   LLM reasoning helpers
│   └── logger.py                        #   Logging setup
├── blaxel-swagger-finder/               # Backend API server
│   ├── backend.py                       #   FastAPI + SSE pipeline orchestrator
│   ├── scanner.py                       #   Blaxel sandbox repo cloner & spec extractor
│   ├── agent_tester.py                  #   AI agent end-user testing engine
│   └── upstream_services/               #   Auto-generated upstream API deployments
├── Columbia-Hackathon-Test-Pilot-Frontend/  # React frontend (Vite + TailwindCSS)
│   ├── src/
│   │   ├── components/pipeline/         #   Pipeline UI (sidebar, stepper, step content)
│   │   ├── hooks/usePipeline.ts         #   SSE-driven pipeline state management
│   │   └── pages/Pipeline.tsx           #   Main pipeline page
│   └── vite.config.ts
├── examples/                            # Sample OpenAPI specs
├── output/                              # Generated MCP server output
└── .env                                 # API keys (not committed)
```

## How It Works

```
  GitHub Repo URLs
         │
         ▼
  ┌──────────────────┐
  │  1. CLONE        │  Clone repos into Blaxel sandbox
  └──────┬───────────┘
         ▼
  ┌──────────────────┐
  │  2. EXTRACT      │  Find & extract OpenAPI/Swagger specs
  └──────┬───────────┘
         ▼
  ┌──────────────────┐
  │  3. INGEST       │  Parse specs → endpoints, schemas, metadata
  └──────┬───────────┘
         ▼
  ┌──────────────────┐
  │  4. DISCOVER     │  Mine MCP tool capabilities from endpoints
  └──────┬───────────┘
         ▼
  ┌──────────────────┐
  │  5. POLICY       │  Classify safety levels, apply execution rules
  └──────┬───────────┘
         ▼
  ┌──────────────────┐
  │  6. GENERATE     │  DeepSeek-V3 LLM → FastMCP server code
  └──────┬───────────┘
         ▼
  ┌──────────────────┐
  │  7. MCP TESTS    │  Validate generated code (syntax, deps, tools)
  └──────┬───────────┘
         ▼
  ┌──────────────────┐
  │  8. DEPLOY       │  Deploy upstream APIs + MCP servers to Blaxel
  └──────┬───────────┘
         ▼
  ┌──────────────────┐
  │  9. AGENT TEST   │  AI agent calls live MCP tools end-to-end
  └──────┬───────────┘
         ▼
  ✅ Live MCP endpoints + test report
```

## Quick Start

### Prerequisites

- **Python 3.11+**
- **Node.js 18+** and **npm**
- **Blaxel CLI**: `brew tap blaxel-ai/blaxel && brew install blaxel`

### 1. Install dependencies

```bash
# Backend
cd blaxel-swagger-finder
pip install -r requirements.txt

# Frontend
cd ../Columbia-Hackathon-Test-Pilot-Frontend
npm install
```

### 2. Configure environment

Create a `.env` in the project root:

```bash
BL_API_KEY=your-blaxel-api-key
BL_WORKSPACE=your-workspace-name
FEATHERLESS_API_KEY=your-featherless-key
GEMINI_API_KEY=your-gemini-key          # optional fallback
```

### 3. Run

```bash
# Terminal 1 — Backend (port 8000)
cd blaxel-swagger-finder
python backend.py

# Terminal 2 — Frontend (port 8080)
cd Columbia-Hackathon-Test-Pilot-Frontend
npm run dev
```

Open **http://localhost:8080**, paste GitHub repo URLs, and hit Start.

### Standalone CLI

Generate an MCP server from a spec file without the web UI:

```bash
python generate.py examples/sample.yaml
python generate.py https://petstore.swagger.io/v2/swagger.json --name petstore
python generate.py path/to/spec.json --no-deploy -v
```

## Generated Output

Each MCP server is written to `output/<server-name>/`:

```
output/<server-name>/
├── src/server.py        # Complete FastMCP server (LLM-generated)
├── blaxel.toml          # Blaxel deployment config + env vars
├── pyproject.toml       # Python project config
├── test_server.py       # Auto-generated test suite
└── requirements.txt     # Dependencies
```

## Tech Stack

- **Backend**: Python, FastAPI, SSE streaming, Blaxel SDK
- **Frontend**: React, Vite, TailwindCSS, shadcn/ui, Framer Motion
- **LLM**: DeepSeek-V3 via Featherless (code generation), Gemini (spec cleanup fallback)
- **Infrastructure**: Blaxel (sandboxes, serverless functions, MCP hosting)
- **Testing**: AI agent with LLM-generated test plans, narrative + analytical result summaries
