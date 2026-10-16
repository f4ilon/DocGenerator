import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from testcontainers.community.postgres import PostgresContainer

from app.main import create_app
from app.models import Project

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_INTEGRATION_TESTS") != "1",
        reason="set RUN_INTEGRATION_TESTS=1 to run PostgreSQL Testcontainers",
    ),
]


@pytest.fixture
def postgres_app():
    with PostgresContainer("postgres:15-alpine") as postgres:
        url = postgres.get_connection_url()
        url = url.replace("postgresql+psycopg2://", "postgresql+psycopg://")
        url = url.replace("postgresql://", "postgresql+psycopg://")
        application = create_app(url)
        with TestClient(application) as client:
            yield client, url


def test_integration_project_crud_postgres(postgres_app):
    client, url = postgres_app
    created = client.post("/projects", json={"name": "postgres-project"})
    assert created.status_code == 201
    project_id = created.json()["id"]
    assert client.get(f"/projects/{project_id}").status_code == 200
    assert client.delete(f"/projects/{project_id}").status_code == 204
    engine = create_engine(url)
    with Session(engine) as session:
        assert session.scalar(select(Project).where(Project.id == project_id)) is None
    engine.dispose()


def test_integration_parse_and_save(postgres_app):
    client, url = postgres_app
    project_id = client.post("/projects", json={"name": "parse-postgres"}).json()["id"]
    response = client.post(
        f"/projects/{project_id}/parse",
        json={
            "files": [
                {"path": f"file_{index}.py", "content": f"def f_{index}():\n    return {index}\n"}
                for index in range(10)
            ]
        },
    )
    assert response.status_code == 202
    assert response.json()["functions"] == 10
    assert len(client.get(f"/projects/{project_id}/tree").json()["files"]) == 10


def test_integration_full_flow_t1_t2_t3(postgres_app):
    client, _ = postgres_app
    project_id = client.post("/projects", json={"name": "full-flow"}).json()["id"]
    parsed = client.post(
        f"/projects/{project_id}/parse",
        json={"files": [{"path": "main.py", "content": "def main():\n    return 1\n"}]},
    )
    assert parsed.status_code == 202
    generated = client.post(f"/projects/{project_id}/generate")
    assert generated.status_code == 200
    exported = client.post(f"/projects/{project_id}/export", json={"format": "pdf"})
    assert exported.status_code == 201
    assert client.get(exported.json()["download_url"]).content.startswith(b"%PDF")
