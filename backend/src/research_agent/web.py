"""Same-origin static serving and browser security headers for the built frontend."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from starlette.staticfiles import StaticFiles

# Cloudflare Turnstile requires its loader in `script-src` and its widget iframe in
# `frame-src`. 'unsafe-inline' styles are required because MUI/Emotion inject <style>
# elements at runtime; the GitHub avatar host is the only remote image source allowed.
CSP = (
    "default-src 'self'; "
    "script-src 'self' https://challenges.cloudflare.com; "
    "frame-src https://challenges.cloudflare.com; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: https://avatars.githubusercontent.com; "
    "connect-src 'self'; "
    "font-src 'self'; "
    "object-src 'none'; "
    "base-uri 'none'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)
PERMISSIONS_POLICY = "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
# Server-rendered pages or documentation UIs that must not fall back to the SPA shell.
RESERVED_PREFIXES = ("/api/", "/auth/", "/assets/")
RESERVED_PATHS = frozenset({"/healthz", "/readyz", "/docs", "/redoc", "/openapi.json"})


def mount_frontend(app: FastAPI, static_dir: Path) -> None:
    """Serve Vite's build output, falling back to index.html for unknown paths.

    Register this last: the catch-all route would otherwise shadow API routes. Only the
    top level of the build directory is exposed as named files, and every resolved path is
    confirmed to stay inside that directory.
    """
    root = static_dir.resolve()
    app.mount("/assets", StaticFiles(directory=root / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def frontend(path: str):
        if path and (f"/{path}" in RESERVED_PATHS or f"/{path}".startswith(RESERVED_PREFIXES)):
            return JSONResponse(status_code=404, content={"detail": "Not Found"})
        index = root / "index.html"
        if path and "/" not in path and "\\" not in path:
            candidate = (root / path).resolve()
            if candidate.is_file() and candidate.is_relative_to(root):
                return FileResponse(candidate)
        return FileResponse(index)
