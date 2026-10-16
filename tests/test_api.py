import base64
import io
import zipfile

import pytest


def test_create_project_success(client):
    response = client.post("/projects", json={"name": "demo"})
    assert response.status_code == 201
    assert response.json()["file_count"] == 0
    assert client.get(f"/projects/{response.json()['id']}").status_code == 200
    assert client.get("/projects").json()["total"] == 1


def test_delete_project(client, project_id):
    response = client.delete(f"/projects/{project_id}")
    assert response.status_code == 204
    assert client.get(f"/projects/{project_id}").status_code == 404


def test_parse_project_success(client, project_id):
    response = client.post(
        f"/projects/{project_id}/parse",
        json={
            "files": [
                {
                    "path": "sample.py",
                    "content": 'class Greeter:\n    def greet(self, name):\n        """Say hello."""\n        return name\n',
                }
            ]
        },
    )
    assert response.status_code == 202
    assert response.json()["functions"] == 1
    tree = client.get(f"/projects/{project_id}/tree").json()
    assert tree["files"][0]["classes"][0]["name"] == "Greeter"
    assert tree["files"][0]["functions"][0]["documented"] is True


def test_generate_docs_success(client, parsed_project):
    response = client.post(f"/projects/{parsed_project}/generate")
    assert response.status_code == 200
    assert response.json()["coverage"]["percent"] == 100
    assert response.json()["drafts"][0]["model"] == "local-placeholder"
    assert len(client.get(f"/projects/{parsed_project}/docs").json()) == 1


def test_export_markdown_success(client, parsed_project):
    client.post(f"/projects/{parsed_project}/generate")
    response = client.post(
        f"/projects/{parsed_project}/export", json={"format": "markdown"}
    )
    assert response.status_code == 201
    download = client.get(response.json()["download_url"])
    assert download.status_code == 200
    assert b"greet" in download.content


def test_zip_project_upload_and_pdf_export(client):
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("src/main.py", "def main():\n    return 1\n")
    created = client.post(
        "/projects",
        json={
            "name": "zip-project",
            "source": {
                "type": "zip",
                "content_base64": base64.b64encode(archive.getvalue()).decode("ascii"),
            },
        },
    )
    assert created.status_code == 201
    project_id = created.json()["id"]
    assert client.post(f"/projects/{project_id}/parse").status_code == 202
    exported = client.post(f"/projects/{project_id}/export", json={"format": "pdf"})
    assert exported.status_code == 201
    assert client.get(exported.json()["download_url"]).content.startswith(b"%PDF")


def test_create_project_empty_name(client):
    response = client.post("/projects", json={"name": ""})
    assert response.status_code == 422


def test_get_nonexistent_project(client):
    response = client.get("/projects/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404


def test_export_invalid_format(client, project_id):
    response = client.post(f"/projects/{project_id}/export", json={"format": "exe"})
    assert response.status_code == 422


def test_parse_too_many_files(client, project_id):
    response = client.post(
        f"/projects/{project_id}/parse",
        json={"files": [{"path": f"{index}.py", "content": ""} for index in range(1001)]},
    )
    assert response.status_code == 422


def test_parse_500_files_boundary(client, project_id):
    response = client.post(
        f"/projects/{project_id}/parse",
        json={
            "files": [
                {"path": f"pkg_{index}.py", "content": f"def fn_{index}():\n    return {index}\n"}
                for index in range(500)
            ]
        },
    )
    assert response.status_code == 202
    assert response.json()["files_parsed"] == 500


def test_coverage_empty_project(client):
    project_id = client.post("/projects", json={"name": "empty"}).json()["id"]
    response = client.get(f"/projects/{project_id}/coverage")
    assert response.status_code == 200
    assert response.json()["percent"] == 0


def test_parse_python_syntax_error(client, project_id):
    response = client.post(
        f"/projects/{project_id}/parse",
        json={"files": [{"path": "broken.py", "content": "def incomplete("}]},
    )
    assert response.status_code == 422


def test_predict_validates_and_returns_metrics(client):
    response = client.post(
        "/predict",
        json={
            "files": 50,
            "funcs": 200,
            "complexity": 5,
            "cycles": 10,
            "doc_before": 30,
            "lang_py": 1,
        },
    )
    assert response.status_code == 200
    assert response.json()["predicted_time_sec"] > 0
    assert {"r2", "mae_seconds", "rmse_seconds"} == set(response.json()["model_metrics"])
    assert client.get("/dashboard/ml").status_code == 200
    assert client.get("/metrics").status_code == 200


def test_search_finds_generated_docs(client, parsed_project):
    client.post(f"/projects/{parsed_project}/generate")
    response = client.get("/search", params={"q": "greet"})
    assert response.status_code == 200
    assert response.json()[0]["function"] == "greet"


def test_generate_jsdoc_style(client, parsed_project):
    response = client.post(
        f"/projects/{parsed_project}/generate", json={"style": "jsdoc"}
    )
    assert response.status_code == 200
    assert response.json()["drafts"][0]["generated_text"].startswith("/**")


def test_health_reports_database_status(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["db"] == "ok"


def test_project_update_and_export_history(client, parsed_project):
    updated = client.patch(f"/projects/{parsed_project}", json={"name": "renamed"})
    assert updated.status_code == 200
    assert updated.json()["name"] == "renamed"
    export = client.post(
        f"/projects/{parsed_project}/export", json={"format": "markdown"}
    )
    assert export.status_code == 201
    history = client.get(f"/projects/{parsed_project}/exports")
    assert history.status_code == 200
    assert history.json()[0]["id"] == export.json()["id"]
    assert client.get(f"/exports/{export.json()['id']}").status_code == 200


def test_unsupported_source_and_invalid_git_url(client):
    unsupported = client.post(
        "/projects",
        json={
            "name": "unsupported",
            "source": {"type": "files", "files": [{"path": "README.txt", "content": "text"}]},
        },
    )
    assert unsupported.status_code == 422
    invalid_git = client.post(
        "/projects",
        json={"name": "invalid-git", "source": {"type": "git", "url": "http://github.com/a/b"}},
    )
    assert invalid_git.status_code == 422


def test_generate_requires_successful_parse(client, project_id):
    response = client.post(f"/projects/{project_id}/generate")
    assert response.status_code == 409


def test_export_html_and_plantuml(client, parsed_project):
    html_export = client.post(
        f"/projects/{parsed_project}/export", json={"format": "html"}
    )
    uml_export = client.post(
        f"/projects/{parsed_project}/export", json={"format": "plantuml"}
    )
    assert html_export.status_code == 201
    assert client.get(html_export.json()["download_url"]).headers["content-type"].startswith("text/html")
    assert uml_export.status_code == 201
    assert client.get(uml_export.json()["download_url"]).text.startswith("@startuml")


@pytest.mark.parametrize("language,source", [
    ("javascript", "class Greeter {\n  greet(name) { return name; }\n}"),
    ("java", "class Greeter {\n  public String greet(String name) { return name; }\n}"),
])
def test_parse_supported_languages(client, project_id, language, source):
    extension = {"javascript": "js", "java": "java"}[language]
    response = client.post(
        f"/projects/{project_id}/parse",
        json={"files": [{"path": f"src/Main.{extension}", "content": source}]},
    )
    assert response.status_code == 202
    assert response.json()["functions"] == 1
    tree = client.get(f"/projects/{project_id}/tree").json()
    assert tree["files"][0]["classes"][0]["name"] == "Greeter"
    assert tree["files"][0]["classes"][0]["methods"] == ["greet"]
