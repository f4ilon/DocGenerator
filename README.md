# DocGen

DocGen is a FastAPI prototype for parsing Python, JavaScript, and Java projects; generating documentation drafts; measuring documentation coverage; exporting Markdown, HTML, PDF, and PlantUML; and predicting generation time with a synthetic-data digital twin.

## Quick start

### Python

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
$env:DATABASE_URL = "sqlite:///./docgen.db"
uvicorn app.main:app --reload
```

Open <http://localhost:8000/docs> for the OpenAPI interface, <http://localhost:8000/dashboard/ml> for the prediction form, and <http://localhost:8000/health> for the service health check. Without `OPENAI_API_KEY`, generation uses clearly labelled deterministic placeholder drafts; configure the key to use the OpenAI API.

### Docker Compose

```powershell
Copy-Item .env.example .env
docker compose up --build
```

Compose starts the API, PostgreSQL, PlantUML server, Prometheus, and Grafana. Their local URLs are `:8000`, `:8080`, `:9090`, and `:3000`. The Grafana dashboard is provisioned automatically. The sample Compose credentials are for local development only.

## API workflow

1. Create a project with `POST /projects`. A source can be supplied as JSON `{"type":"files","files":[{"path":"src/main.py","content":"def main():\\n    return 1"}]}`, a base64 ZIP (`{"type":"zip","content_base64":"..."}`), or a public Git URL (`{"type":"git","url":"https://github.com/owner/repo"}`).
2. Rename or delete a project with `PATCH /projects/{id}` and `DELETE /projects/{id}`. Parse with `POST /projects/{id}/parse`; inspect classes and functions at `GET /projects/{id}/tree`.
3. Generate documentation with `POST /projects/{id}/generate`; check `GET /projects/{id}/docs` and `GET /projects/{id}/coverage`.
4. Export with `POST /projects/{id}/export`, passing `{"format":"markdown"}`, `html`, `pdf`, or `plantuml`. Browse export history at `GET /projects/{id}/exports`; retrieve an export through `GET /exports/{id}/download`.
5. Search generated text with `GET /search?q=...`.
6. Estimate generation time with `POST /predict`. Its six validated features are `files`, `funcs`, `complexity`, `cycles`, `doc_before`, and `lang_py`.

ZIP and Git uploads accept at most 1000 supported source files and 50 MB of content. The Git loader permits HTTPS clone URLs on GitHub, GitLab, and Bitbucket. JavaScript and Java extraction uses lightweight signature parsing; Python uses the standard-library AST parser.

## Testing and model training

```powershell
pytest
ruff check app tests scripts
python scripts/train_model.py
```

The training script deterministically creates 1000 synthetic samples, writes `artifacts/generation_metrics.csv` and `artifacts/model.joblib`, and prints held-out R², MAE, and RMSE. These simulated-data metrics are not a substitute for measurements from real projects. The `/predict` API trains the same deterministic linear regression lazily in process.

PostgreSQL integration tests use Testcontainers and require Docker:

```powershell
$env:RUN_INTEGRATION_TESTS = "1"
pytest -m integration
```

## Requirements implemented

The service includes the six core relational models (Project, SourceFile, FunctionEntity, DocumentationDraft, CoverageReport, ExportTask), project CRUD and parsing/generation/export/search routes, health and Prometheus metrics, a four-panel Grafana dashboard, Docker deployment, a GitHub Actions lint/test/image workflow, 10+ API unit tests, and three PostgreSQL Testcontainers integration tests.
