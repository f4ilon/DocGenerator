import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client(tmp_path):
    application = create_app(f"sqlite:///{tmp_path / 'test.db'}")
    with TestClient(application) as test_client:
        yield test_client


@pytest.fixture
def project_id(client):
    response = client.post("/projects", json={"name": "sample"})
    assert response.status_code == 201
    return response.json()["id"]


@pytest.fixture
def parsed_project(client, project_id):
    response = client.post(
        f"/projects/{project_id}/parse",
        json={"files": [{"path": "sample.py", "content": "def greet(name):\n    return name\n"}]},
    )
    assert response.status_code == 202
    return project_id

