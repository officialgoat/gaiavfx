import hashlib
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from gaiavfx.setup.download import download

DATA = b"small portable model fixture\n" * 200
SHA = hashlib.sha256(DATA).hexdigest()


@pytest.fixture
def server():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(dict(self.headers))
            offset = int(self.headers.get("Range", "bytes=0-").split("=")[1].split("-")[0])
            if self.path == "/ignore-range":
                offset = 0
            self.send_response(206 if offset else 200)
            self.send_header("Content-Length", str(len(DATA) - offset))
            if offset:
                self.send_header("Content-Range", f"bytes {offset}-{len(DATA) - 1}/{len(DATA)}")
            self.end_headers()
            self.wfile.write(DATA[offset:])

        def log_message(self, *_args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}", requests
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join()


def test_full_download_and_skip(server, tmp_path):
    url, requests = server
    target = tmp_path / "nested/model.bin"
    download(url, target, size=len(DATA), sha256=SHA)
    assert target.read_bytes() == DATA
    assert not target.with_suffix(".bin.part").exists()
    download(url, target, size=len(DATA), sha256=SHA, verify=True)
    assert len(requests) == 1


@pytest.mark.parametrize("endpoint", ["/", "/ignore-range"])
def test_resume_or_restart_when_server_ignores_range(server, tmp_path, endpoint):
    url, requests = server
    target = tmp_path / "model.bin"
    target.with_suffix(".bin.part").write_bytes(DATA[:127])
    download(url + endpoint, target, size=len(DATA), sha256=SHA)
    assert requests[0]["Range"] == "bytes=127-"
    assert target.read_bytes() == DATA


def test_sha_mismatch_discards_partial_and_preserves_existing(server, tmp_path):
    url, _ = server
    target = tmp_path / "model.bin"
    target.write_bytes(b"old file")
    with pytest.raises(ValueError, match="mismatch"):
        download(url, target, size=len(DATA), sha256="0" * 64)
    assert not target.with_suffix(".bin.part").exists()
    assert target.read_bytes() == b"old file"


def test_verify_repairs_corrupted_same_size_file(server, tmp_path):
    url, _ = server
    target = tmp_path / "model.bin"
    target.write_bytes(b"x" * len(DATA))
    download(url, target, size=len(DATA), sha256=SHA, verify=True)
    assert target.read_bytes() == DATA


def test_complete_partial_promotes_without_network(tmp_path):
    target = tmp_path / "model.bin"
    target.with_suffix(".bin.part").write_bytes(DATA)
    download("http://127.0.0.1:1", target, size=len(DATA), sha256=SHA)
    assert target.read_bytes() == DATA


def test_hf_token_only_for_huggingface_and_not_redirects(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "test-token-not-a-secret")
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.host == "huggingface.co":
            return httpx.Response(302, headers={"Location": "https://cdn.example/file"})
        return httpx.Response(200, content=DATA)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        download(
            "https://huggingface.co/repo/resolve/pin/file",
            tmp_path / "model",
            size=len(DATA),
            sha256=SHA,
            client=client,
        )
    assert requests[0].headers["Authorization"] == "Bearer test-token-not-a-secret"
    assert "authorization" not in requests[1].headers


def test_bad_content_range_does_not_append(tmp_path):
    target = tmp_path / "model"
    partial = tmp_path / "model.part"
    partial.write_bytes(DATA[:10])
    with (
        httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    206,
                    headers={"Content-Range": f"bytes 0-{len(DATA) - 1}/{len(DATA)}"},
                    content=DATA,
                )
            )
        ) as client,
        pytest.raises(ValueError, match="Invalid resume"),
    ):
        download("https://example.test/file", target, size=len(DATA), sha256=SHA, client=client)
    assert partial.read_bytes() == DATA[:10]
