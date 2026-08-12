from __future__ import annotations

import io
import socket

import httpx
import pytest

from control_translator.projects import (
    SourceIngestionService,
    UnsafeSourceURLError,
    UnsupportedSourceError,
)
from control_translator.projects.store import ProjectStore

openpyxl = pytest.importorskip("openpyxl")


def _public_resolver(_host: str, port: int):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]


def test_upload_csv_normalizes_and_writes_under_project_source(tmp_path):
    store = ProjectStore(tmp_path / "projects")
    project = store.create("ingest")
    service = SourceIngestionService(store, resolver=_public_resolver)

    result = service.ingest_upload(
        project_id=project.id,
        filename="sample.csv",
        payload=b"A,B\r\n1,2\r\n",
        content_type="text/csv",
    )

    assert result.filename.endswith(".csv")
    assert result.project_path.startswith("source/")
    saved = store.resolve_path(project.id, result.project_path).read_bytes()
    assert saved == b"A,B\n1,2\n"


def test_upload_spreadsheet_formula_is_rejected(tmp_path):
    store = ProjectStore(tmp_path / "projects")
    project = store.create("ingest")
    service = SourceIngestionService(store, resolver=_public_resolver)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"] = "=1+1"
    payload = io.BytesIO()
    wb.save(payload)

    with pytest.raises(UnsupportedSourceError):
        service.ingest_upload(
            project_id=project.id,
            filename="formula.xlsx",
            payload=payload.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )


def test_url_ingest_rejects_private_destination(tmp_path):
    store = ProjectStore(tmp_path / "projects")
    project = store.create("ingest")

    def private_resolver(_host: str, port: int):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]

    service = SourceIngestionService(store, resolver=private_resolver)
    with pytest.raises(UnsafeSourceURLError):
        service.ingest_url(project_id=project.id, url="https://example.com/standard.csv")


def test_url_ingest_revalidates_redirect_host(tmp_path):
    store = ProjectStore(tmp_path / "projects")
    project = store.create("ingest")

    def resolver(host: str, port: int):
        if host == "safe.example":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "safe.example":
            return httpx.Response(302, headers={"location": "https://internal.example/redirect.csv"})
        return httpx.Response(200, text="a,b\n1,2\n", headers={"content-type": "text/csv"})

    client_factory = lambda: httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    service = SourceIngestionService(store, resolver=resolver, client_factory=client_factory)

    with pytest.raises(UnsafeSourceURLError):
        service.ingest_url(project_id=project.id, url="https://safe.example/source.csv")


def test_url_ingest_succeeds_for_public_csv(tmp_path):
    store = ProjectStore(tmp_path / "projects")
    project = store.create("ingest")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"col1,col2\r\nx,y\r\n",
            headers={"content-type": "text/csv"},
        )

    client_factory = lambda: httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    service = SourceIngestionService(store, resolver=_public_resolver, client_factory=client_factory)

    result = service.ingest_url(project_id=project.id, url="https://example.com/std.csv")
    assert result.rows == 2
    assert result.columns == 2
    assert store.resolve_path(project.id, result.project_path).read_text(encoding="utf-8") == "col1,col2\nx,y\n"
