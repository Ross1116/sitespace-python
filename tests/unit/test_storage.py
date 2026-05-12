import pytest

from app.core.config import settings
from app.utils.storage import LocalStorage


def test_worker_reads_existing_local_file_before_remote_fetch(monkeypatch, tmp_path):
    storage = LocalStorage()
    file_path = tmp_path / "upload.pdf"
    file_path.write_bytes(b"pdf-bytes")
    monkeypatch.setattr(settings, "SERVICE_ROLE", "worker")
    monkeypatch.setattr(settings, "WEB_INTERNAL_URL", "/app/uploads")
    monkeypatch.setattr(
        storage,
        "_read_remote",
        lambda _path: (_ for _ in ()).throw(AssertionError("remote fetch should not run")),
    )

    assert storage.read(str(file_path)) == b"pdf-bytes"


def test_remote_fetch_url_adds_http_scheme_for_private_host(monkeypatch):
    storage = LocalStorage()
    monkeypatch.setattr(settings, "WEB_INTERNAL_URL", "web.railway.internal:8080/")

    assert storage._remote_fetch_url() == "http://web.railway.internal:8080/internal/files/fetch"


def test_remote_fetch_url_rejects_filesystem_path(monkeypatch):
    storage = LocalStorage()
    monkeypatch.setattr(settings, "WEB_INTERNAL_URL", "/app/uploads")

    with pytest.raises(ValueError, match="WEB_INTERNAL_URL"):
        storage._remote_fetch_url()
