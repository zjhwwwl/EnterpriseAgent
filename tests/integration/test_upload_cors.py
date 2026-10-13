"""Real ASGI middleware/form parsing: readable errors without business body delivery."""

from typing import Annotated

import pytest
from fastapi import File, Form, UploadFile
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient

from enterprise_agent.application import create_app
from enterprise_agent.config import Settings

pytestmark = pytest.mark.integration
ORIGIN = "http://localhost:5173"
UPLOAD_PATH = "/api/v1/knowledge/documents"
BOUNDARY = "enterprise-agent-upload-test"


@pytest.fixture
def upload_probe(tmp_path):
    app = create_app(
        Settings(
            _env_file=None,
            app_env="test",
            data_dir=tmp_path,
            cors_origins=[ORIGIN],
            rag_upload_max_bytes=1024,
        ),
        configure_logs=False,
    )
    # Keep the application's real middleware stack. A probe at the exact
    # protected path isolates Form/UploadFile routing from DB and model work.
    app.router.routes.clear()
    received = []

    @app.post(UPLOAD_PATH)
    async def record_endpoint(file: Annotated[UploadFile, File()], title: Annotated[str, Form()]):
        content = await file.read()
        received.append(content)
        await file.close()
        return JSONResponse({"received_bytes": len(content)}, status_code=201)

    return app, received


def multipart_parts():
    prefix = (
        f'--{BOUNDARY}\r\nContent-Disposition: form-data; name="title"\r\n\r\n'
        "模拟知识文档\r\n"
        f'--{BOUNDARY}\r\nContent-Disposition: form-data; name="file"; '
        'filename="policy.txt"\r\nContent-Type: text/plain\r\n\r\n'
    ).encode()
    suffix = f"\r\n--{BOUNDARY}--\r\n".encode()
    return prefix, suffix


def assert_upload_rejected(response, origin):
    assert response.status_code == 413
    assert response.json()["request_id"] == response.headers["X-Request-ID"]
    assert response.json()["error"]["code"]
    if origin == ORIGIN:
        assert response.headers["Access-Control-Allow-Origin"] == ORIGIN
        assert "X-Request-ID" in response.headers["Access-Control-Expose-Headers"]
        assert "Origin" in response.headers["Vary"]
    else:
        assert "Access-Control-Allow-Origin" not in response.headers


@pytest.mark.parametrize("origin", [ORIGIN, "https://untrusted.invalid"])
async def test_declared_oversize_is_rejected_before_body_read_and_preserves_cors(
    upload_probe, origin
):
    app, received = upload_probe
    produced = []

    async def untouched_body():
        produced.append("body")
        yield b"this request body must not be consumed"

    async with AsyncClient(transport=ASGITransport(app), base_url="http://testserver") as client:
        response = await client.post(
            UPLOAD_PATH,
            headers={
                "Origin": origin,
                "Content-Type": f"multipart/form-data; boundary={BOUNDARY}",
                "Content-Length": str(1024 + 64 * 1024 + 1),
                "X-Request-ID": "upload-declared-limit",
            },
            content=untouched_body(),
        )
    assert_upload_rejected(response, origin)
    assert produced == []
    assert received == []


@pytest.mark.parametrize("origin", [ORIGIN, "https://untrusted.invalid"])
async def test_streamed_oversize_stops_parsing_without_delivering_body_to_route(
    upload_probe, origin
):
    app, received = upload_probe
    produced = []
    prefix, suffix = multipart_parts()

    async def oversized_body():
        for label, content in [
            ("metadata", prefix),
            ("first", b"x" * 40000),
            ("oversized", b"x" * 40000),
            ("closing", suffix),
        ]:
            produced.append(label)
            yield content

    async with AsyncClient(transport=ASGITransport(app), base_url="http://testserver") as client:
        request = client.build_request(
            "POST",
            UPLOAD_PATH,
            headers={
                "Origin": origin,
                "Content-Type": f"multipart/form-data; boundary={BOUNDARY}",
                "X-Request-ID": "upload-streamed-limit",
            },
            content=oversized_body(),
        )
        assert "Content-Length" not in request.headers
        response = await client.send(request)
    assert_upload_rejected(response, origin)
    assert produced == ["metadata", "first", "oversized"]
    assert received == []


async def test_under_limit_control_proves_the_probe_route_receives_parsed_file(upload_probe):
    app, received = upload_probe
    prefix, suffix = multipart_parts()
    content = "可追溯的模拟政策".encode()
    async with AsyncClient(transport=ASGITransport(app), base_url="http://testserver") as client:
        response = await client.post(
            UPLOAD_PATH,
            headers={"Origin": ORIGIN, "Content-Type": f"multipart/form-data; boundary={BOUNDARY}"},
            content=prefix + content + suffix,
        )
    assert response.status_code == 201
    assert response.json() == {"received_bytes": len(content)}
    assert received == [content]
    assert response.headers["Access-Control-Allow-Origin"] == ORIGIN
