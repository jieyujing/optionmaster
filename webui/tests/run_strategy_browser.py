"""Temporary isolated loopback v4 ASGI + Chromium test; never touches served dist."""
import os
from pathlib import Path
import secrets
import socket
import subprocess
from tempfile import mkdtemp
from threading import Thread
from time import monotonic, sleep

import uvicorn

from webui.strategy_preview import create_strategy_preview_app
from webui.tests.run_browser import browser_executable

ROOT = Path(__file__).resolve().parents[2]
UI = ROOT / "webui/test-results/d12/strategy-ui"


def main() -> None:
    executable = browser_executable()
    token = secrets.token_urlsafe(32)
    app = create_strategy_preview_app(token, UI)

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(32)
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, access_log=False,
        proxy_headers=False, ws="none", log_level="warning", limit_concurrency=16,
        lifespan="off", timeout_keep_alive=5, timeout_graceful_shutdown=5))
    thread = Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
    thread.start()
    try:
        deadline = monotonic() + 10
        while not server.started and thread.is_alive() and monotonic() < deadline:
            sleep(.05)
        if not server.started:
            raise RuntimeError("Temporary test listener failed")
        env = {**os.environ, "OPTIONMASTER_TEST_ORIGIN": f"http://127.0.0.1:{port}",
               "OPTIONMASTER_TEST_TOKEN": token, "OPTIONMASTER_BROWSER_EXECUTABLE": str(executable)}
        allowed = ["strategy-archive-browser.mjs", "browser.mjs", "chain-browser.mjs", "catalog-browser.mjs"]
        only = os.environ.get("OPTIONMASTER_TEST_SCRIPT")
        if only is not None and only not in allowed:
            raise ValueError("Unknown browser test script")
        scripts = [only] if only else allowed if os.environ.get("OPTIONMASTER_TEST_LEGACY") == "1" else allowed[:1]
        for script in scripts:
            evidence = Path(mkdtemp(prefix=script.removesuffix(".mjs") + "-", dir=UI.parent))
            result = subprocess.run(["node", "tests/" + script], cwd=ROOT / "webui",
                env={**env, "OPTIONMASTER_BROWSER_EVIDENCE": str(evidence)},
                capture_output=True, text=True, timeout=120)
            print(script, result.stdout.replace(token, "[redacted]"))
            if result.returncode:
                print(result.stderr.replace(token, "[redacted]")[-5000:])
                raise SystemExit(result.returncode)
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        if thread.is_alive():
            server.force_exit = True
            thread.join(timeout=3)
        listener.close()
        assert not thread.is_alive()
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(1)
            assert probe.connect_ex(("127.0.0.1", port)) != 0
        print("LOOPBACK_CLEANUP_PASS")


if __name__ == "__main__":
    main()
