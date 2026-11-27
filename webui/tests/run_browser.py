"""Bounded temporary loopback ASGI + Chromium acceptance; cleanup in finally.

Run .venv/bin/python -m webui.tests.run_browser from the repository root.
Requires existing local Chromium cache or explicitly configured executable.
No browser download, external network or production credentials.
"""
from argparse import ArgumentParser
from hashlib import sha256
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
from threading import Thread
from time import monotonic, sleep

import uvicorn

from webui.serve_local import ROOT, create_local_app


def browser_executable() -> Path:
    override = os.environ.get("OPTIONMASTER_BROWSER_EXECUTABLE")
    if override:
        path = Path(override).resolve()
        if not path.is_file():
            raise RuntimeError("Configured browser executable missing")
        return path
    cache = Path.home() / "Library/Caches/ms-playwright"
    paths = list(cache.glob("chromium_headless_shell-*/chrome-headless-shell-*/chrome-headless-shell"))
    if not paths:
        raise RuntimeError("Local Chromium cache unavailable; no automatic download")
    return max(paths, key=lambda path: int(path.parent.parent.name.rsplit("-", 1)[1]))


def main() -> None:
    parser = ArgumentParser()
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--chain", action="store_true", help="Run D08 expanded-chain browser acceptance")
    modes.add_argument("--catalog", action="store_true", help="Run D09 multi-date catalog browser acceptance")
    parser.add_argument("--evidence-dir", help="New project-local D09 evidence directory (catalog mode only)")
    args = parser.parse_args()
    if args.evidence_dir and not args.catalog:
        parser.error("--evidence-dir requires --catalog")
    evidence = ROOT / ("docs/evidence/m1-d09/browser" if args.catalog else
                       "docs/evidence/m1-d08/cu2211-20220907" if args.chain else "docs/evidence/m1-d07")
    if args.catalog:
        base = (ROOT / "docs/evidence/m1-d09").resolve()
        if args.evidence_dir:
            evidence = (ROOT / args.evidence_dir).resolve()
        if base not in evidence.resolve().parents:
            parser.error("D09 evidence directory must be inside docs/evidence/m1-d09")
        if evidence.exists() and any(evidence.iterdir()):
            parser.error("Preserve existing browser evidence; choose a new --evidence-dir")
    evidence.mkdir(parents=True, exist_ok=True)
    executable = browser_executable()
    token = secrets.token_urlsafe(32)
    app = create_local_app(token)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(64)
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(
        app, host="127.0.0.1", port=port, access_log=False, proxy_headers=False,
        ws="none", log_level="warning", timeout_keep_alive=5,
        limit_concurrency=16, lifespan="off", timeout_graceful_shutdown=5,
    ))
    thread = Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
    thread.start()
    env = {
        **os.environ,
        "OPTIONMASTER_TEST_ORIGIN": f"http://127.0.0.1:{port}",
        "OPTIONMASTER_TEST_TOKEN": token,
        "OPTIONMASTER_BROWSER_EXECUTABLE": str(executable),
        "OPTIONMASTER_BROWSER_EVIDENCE": str(evidence),
    }
    try:
        deadline = monotonic() + 10
        while not server.started and thread.is_alive() and monotonic() < deadline:
            sleep(0.05)
        if not server.started:
            raise RuntimeError("Temporary loopback service failed to start")
        result = subprocess.run(
            ["node", "tests/catalog-browser.mjs" if args.catalog else "tests/chain-browser.mjs" if args.chain else "tests/browser.mjs"],
            cwd=ROOT / "webui", env=env,
            capture_output=True, text=True, timeout=180 if args.catalog else 150 if args.chain else 100,
        )
        # Never emit a test bearer even if a diagnostic unexpectedly includes it.
        print(result.stdout.replace(token, "[redacted]"))
        if result.returncode:
            print(result.stderr.replace(token, "[redacted]")[-5000:])
            raise SystemExit(result.returncode)
        summary = json.loads((evidence / "browser_result.json").read_bytes())
        assert summary["status"] == "PASS"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        if thread.is_alive():
            server.force_exit = True
            thread.join(timeout=3)
        listener.close()
        assert not thread.is_alive(), "Temporary listener cleanup failed"
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(1)
            assert probe.connect_ex(("127.0.0.1", port)) != 0, "Loopback port still accepts connections"
        print("LOOPBACK_CLEANUP_PASS")
    names = ("browser_result.json", "desktop.png", "mobile.png")
    if args.catalog:
        names += ("actual_catalog_api_result.json",)
    elif args.chain:
        names += ("actual_chain_api_result.json",)
    for name in names:
        path = evidence / name
        print(name, path.stat().st_size, sha256(path.read_bytes()).hexdigest())


if __name__ == "__main__":
    main()
