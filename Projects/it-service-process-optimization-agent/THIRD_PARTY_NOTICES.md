# Third-party notices

The MIT license in [LICENSE](LICENSE) covers only the source code, tests, scripts and
documentation written for this project. It does **not** cover the third-party software,
datasets or model weights below, which keep their own licenses. None of them is
redistributed in this directory. Dependencies are installed from PyPI, and models are
downloaded by the user.

## Python dependencies

Licenses as declared in the package metadata of the versions in `ci/requirements.lock`
(checked 2026-11-06). Check the upstream projects for authoritative terms.

| Package | Used for | License |
|---|---|---|
| FastAPI, Starlette | HTTP API | MIT, BSD-3-Clause |
| Uvicorn | ASGI server | BSD-3-Clause |
| LangGraph | Agent orchestration | MIT |
| Pydantic | Schemas and validation | MIT |
| SQLAlchemy | Persistence | MIT |
| pandas, NumPy, PyArrow | Analytics and Parquet | BSD-3-Clause; BSD-3-Clause and others; Apache-2.0 |
| httpx | HTTP client (Ollama, tests) | BSD-3-Clause |
| pytest, Ruff, PyYAML | Development tooling | MIT |
| **pm4py** | XES ingestion only (`ingestion` extra, also pulled in by `dev`) | **AGPL-3.0-or-later** |
| **psycopg** | PostgreSQL driver (`cloud` extra) | **LGPL-3.0-only** |
| PyJWT | Entra token validation (`cloud` extra) | MIT |
| azure-identity, azure-ai-projects, azure-ai-contentunderstanding, langchain-azure-ai, langchain-openai | Optional Azure boundary (`agents` extra) | MIT |
| opentelemetry-api, opentelemetry-sdk; microsoft-opentelemetry | Optional tracing (`observability` extra) | Apache-2.0; MIT |

**pm4py (AGPL-3.0).** This project imports pm4py only to parse XES event logs during
offline ingestion. The interactive demo, the synthetic package generator and the runtime
container images do not need it. If you distribute or provide network access to a
combined work that includes pm4py, the AGPL obligations apply to that combined work. pm4py
also offers commercial licensing. See [docs/data_governance.md](docs/data_governance.md).

## Local model weights

The optional local AI mode uses a model served by [Ollama](https://ollama.com) (MIT).
The default model, `qwen3.5:9b`, is published under the Apache License 2.0
(see `ollama show qwen3.5:9b --license`). Any other model you select keeps its own license.
No model weights are included in this repository.

## Dataset

The pipeline was designed for the **BPI Challenge 2013 – Incidents** event log
(4TU.ResearchData, DOI [10.4121/uuid:500573e6-accc-4b0c-9576-aa5468b10cee](https://doi.org/10.4121/uuid:500573e6-accc-4b0c-9576-aa5468b10cee)),
published under the [4TU General Terms of Use](https://doi.org/10.4121/resource:terms_of_use).
The dataset is neither included in this repository nor covered by its MIT license. The
public demo data is synthetic and generated locally by `scripts/prepare_public_demo_data.sh`.
