"""Explicit, one-shot ClickHouse HTTP transport for D12 historical research.

No default target, credentials, account creation or activation. Caller supplies
the already authorized exact query plan and protected credential callback.
The actual provider is not wired to any running service. POSIX main-thread
only so an absolute per-request deadline can be enforced without worker leaks.
"""
from collections.abc import Callable, Mapping
from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
from http.client import HTTPConnection, HTTPSConnection
from ipaddress import IPv4Address, IPv4Network
import base64
import json
import os
from pathlib import Path
import re
import signal
import ssl
from threading import current_thread, main_thread
from time import monotonic
from typing import Any
from urllib.parse import urlencode
from uuid import uuid4

from vnpy_optionmaster.research_historical_provider import (
    CAPS, PROFILE_SQL, CATALOG_SQL, TERMS_SQL, OPTION_SQL, FUTURES_SQL,
    HistoricalQuery, HistoricalReadReceipt,
)
from vnpy_optionmaster.research_strategy_payoff import StrategyUnavailable

_TABLES = {CATALOG_SQL: "option_contract_terms_daily", TERMS_SQL: "option_contract_terms_daily",
           OPTION_SQL: "option_daily", FUTURES_SQL: "futures_daily"}
_KEYS = {CATALOG_SQL: {"portfolio", "start", "end"}, TERMS_SQL: {"day", "portfolio", "symbols"},
         OPTION_SQL: {"day", "symbols"}, FUTURES_SQL: {"day", "product", "symbol"}}
# Exact T04/T06-observed projections. New discovery projection uses only
# those same physical columns plus UInt64 count(); no dynamic schema names.
_META = {
    PROFILE_SQL: tuple((x, "String") for x in ("server_name", "reader", "database", "name", "value")),
    CATALOG_SQL: (("trade_date", "Date"), ("chain_id", "String"),
                  ("underlying_symbol", "String"), ("physical_rows", "UInt64")),
    TERMS_SQL: (
        ("symbol", "String"), ("exchange", "LowCardinality(String)"), ("trade_date", "Date"),
        ("portfolio_code", "String"), ("chain_id", "String"), ("underlying_symbol", "String"),
        ("expiry_date", "Date"), ("option_type", "LowCardinality(String)"),
        ("exercise_style", "LowCardinality(String)"), ("strike", "Nullable(Float64)"),
        ("contract_size", "Nullable(Float64)"), ("contract_version", "String"),
        ("source", "LowCardinality(String)"), ("provenance", "LowCardinality(String)"),
        ("ingested_at", "DateTime64(3, 'Asia/Shanghai')"),
    ),
    OPTION_SQL: (
        ("symbol", "String"), ("exchange", "LowCardinality(String)"), ("trade_date", "Date"),
        ("close", "Nullable(Float64)"), ("settlement", "Nullable(Float64)"),
        ("source", "LowCardinality(String)"), ("ingested_at", "DateTime64(3, 'Asia/Shanghai')"),
    ),
    FUTURES_SQL: (
        ("symbol", "String"), ("exchange", "LowCardinality(String)"), ("trade_date", "Date"),
        ("product_symbol", "String"), ("close", "Float64"), ("settlement", "Nullable(Float64)"),
        ("price_source", "LowCardinality(String)"), ("settlement_source", "LowCardinality(String)"),
        ("ingested_at", "DateTime64(3, 'Asia/Shanghai')"),
    ),
}
_PLAN_META = (("database", "String"), ("table", "String"), ("parts", "UInt64"),
              ("rows", "UInt64"), ("marks", "UInt64"))

_LIMIT = 1048576
_PRIVATE_NETWORKS = tuple(IPv4Network(x) for x in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


def _guard(condition: bool) -> None:
    if not condition:
        raise StrategyUnavailable("SOURCE_VERSION_UNVERIFIED")


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha(data: bytes) -> str:
    return sha256(data).hexdigest()


def _json(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False) + "\n").encode()


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _persist(path: Path, data: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    _fsync_dir(path.parent)


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        _guard(key not in result)
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise StrategyUnavailable("SOURCE_VERSION_UNVERIFIED")


def _uint(value: object) -> int:
    if type(value) is int:
        n = int(value)
    elif isinstance(value, str) and value.isascii() and value.isdecimal() and len(value) <= 20:
        n = int(value)
    else:
        raise StrategyUnavailable("SOURCE_VERSION_UNVERIFIED")
    _guard(n >= 0)
    return n


def _alarm(*_: object) -> None:
    raise TimeoutError("historical read deadline")


class HistoricalHttpTransport:
    """A fixed-plan transport; one attempt per approved stage, no redispatch.

    HTTPS uses certificate verification. Plain HTTP requires an explicit
    private IPv4 LAN opt-in (the existing research diagnostic boundary);
    it does NOT provide TLS identity or production security.
    """
    def __init__(self, *, host: str, port: int, account: str, server: str,
                 password: Callable[[], str], approved_plan: tuple[HistoricalQuery, ...],
                 authorization_id: str, evidence_dir: Path, allow_plaintext_lan: bool = False) -> None:
        _guard(re.fullmatch(r"[A-Za-z0-9.-]{1,253}", host) is not None
               and type(port) is int and 1 <= port <= 65535
               and re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", account) is not None
               and re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", server) is not None
               and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", authorization_id) is not None)
        if allow_plaintext_lan:
            address = IPv4Address(host)
            _guard(any(address in network for network in _PRIVATE_NETWORKS))
        _guard(3 <= len(approved_plan) <= 64 and len(approved_plan) % 2 == 1
               and approved_plan[0] == HistoricalQuery(PROFILE_SQL, ()))
        for i in range(1, len(approved_plan), 2):
            plan, read = approved_plan[i:i+2]
            _guard(read.sql in _TABLES and plan.sql == "EXPLAIN ESTIMATE " + read.sql
                   and plan.parameters == read.parameters
                   and len(read.parameters) == len(_KEYS[read.sql])
                   and {k for k, _ in read.parameters} == _KEYS[read.sql])
        self.host, self.port, self.account, self.server = host, port, account, server
        self.target = f"{host}:{port}/quantfeed"
        self._password = password
        self._plan = approved_plan
        self._authorization_id = authorization_id
        self._root = evidence_dir
        self._http = allow_plaintext_lan
        self._index = 0
        self._stopped = False
        self._started = False
        self._terminal_written = False
        self._begin = 0.0

    @property
    def attempts(self) -> int:
        return self._index

    def _append(self, row: dict[str, object]) -> None:
        with (self._root / "requests.jsonl").open("ab") as handle:
            handle.write(_json(row))
            handle.flush()
            os.fsync(handle.fileno())
        _fsync_dir(self._root)

    def _start(self) -> None:
        # Exclusive durable authorization fence. Unknown process outcome is not
        # permission to rerun the plan or to delete this directory.
        self._root.mkdir(mode=0o700, parents=False, exist_ok=False)
        _fsync_dir(self._root.parent)
        _persist(self._root / "authorization.json", _json({
            "authorization_id": self._authorization_id, "target": self.target,
            "account": self.account, "server": self.server, "plan": [asdict(x) for x in self._plan],
            "max_requests": len(self._plan), "caps": CAPS, "deadline_seconds": 8,
            "batch_seconds": 120, "automatic_retries": 0,
            "transport": "EXPLICIT_LAN_HTTP" if self._http else "VERIFIED_TLS",
        }))
        self._append({"event": "reservation", "at": _utc(), "max_requests": len(self._plan)})
        self._started = True
        self._begin = monotonic()

    def seal(self) -> None:
        """Close an interrupted consumer batch without dispatching any more SQL."""
        self._stopped = True
        if self._started and not self._terminal_written:
            self._append({"event": "batch_terminal", "at": _utc(), "attempts": self._index,
                          "status": "BLOCKED", "automatic_retries": 0})
            self._terminal_written = True

    def select(self, sql: str, parameters: Mapping[str, str], *,
               timeout_seconds: float) -> HistoricalReadReceipt:
        _guard(not self._stopped and self._index < len(self._plan)
               and HistoricalQuery(sql, tuple(sorted(parameters.items()))) == self._plan[self._index]
               and current_thread() is main_thread() and hasattr(signal, "setitimer")
               and signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)
               and 0 < timeout_seconds <= 8)
        try:
            if not self._started:
                self._start()
            _guard(monotonic() - self._begin <= 120 - timeout_seconds)
            secret = self._password()
            _guard(isinstance(secret, str) and bool(secret) and "\r" not in secret and "\n" not in secret)
            auth = "Basic " + base64.b64encode((self.account + ":" + secret).encode()).decode("ascii")
            del secret
        except Exception:
            self._stopped = True
            raise StrategyUnavailable("SOURCE_INCOMPLETE") from None
        qid = "optionmaster-history-" + uuid4().hex
        try:
            self._append({"event": "attempt", "query_id": qid, "at": _utc(), "sequence": self._index + 1,
                          "sql": sql, "parameters": dict(parameters)})
        except Exception:
            self._stopped = True
            raise StrategyUnavailable("SOURCE_INCOMPLETE") from None
        self._index += 1
        began = monotonic()
        rec: dict[str, object] = {"event": "result", "query_id": qid, "status": "BLOCKED",
                                 "server_outcome": "UNKNOWN"}
        conn: HTTPConnection | None = None
        previous = signal.getsignal(signal.SIGALRM)
        try:
            signal.signal(signal.SIGALRM, _alarm)
            signal.setitimer(signal.ITIMER_REAL, timeout_seconds)
            if self._http:
                conn = HTTPConnection(self.host, self.port, timeout=timeout_seconds)
            else:
                conn = HTTPSConnection(self.host, self.port, timeout=timeout_seconds,
                                       context=ssl.create_default_context())
            url = "/?" + urlencode({"database": "quantfeed", "query_id": qid, "wait_end_of_query": "1",
                                     **{"param_" + k: v for k, v in parameters.items()}})
            conn.request("POST", url, body=sql.encode(), headers={
                "Authorization": auth, "Content-Type": "text/plain; charset=utf-8",
                "Accept-Encoding": "identity", "Connection": "close"})
            response = conn.getresponse()
            rec["http_status"] = response.status
            declared = response.getheader("Content-Length")
            _guard(declared is None or _uint(declared) <= _LIMIT)
            _guard(response.getheader("Content-Encoding") in (None, "", "identity"))
            raw = response.read(_LIMIT + 1)
            rec.update(server_outcome="HTTP_RESPONSE_RECEIVED", response_bytes=len(raw), response_sha256=_sha(raw))
            _guard(0 < len(raw) <= _LIMIT and (declared is None or _uint(declared) == len(raw))
                   and response.status == 200 and not response.getheader("X-ClickHouse-Exception-Code")
                   and response.getheader("X-ClickHouse-Server-Display-Name") == self.server)
            body = json.loads(raw, object_pairs_hook=_unique, parse_constant=_reject_constant)
            _guard(isinstance(body, dict) and isinstance(body.get("data"), list) and isinstance(body.get("statistics"), dict))
            rows, stats = body["data"], body["statistics"]
            expected_meta = _PLAN_META if sql.startswith("EXPLAIN ESTIMATE ") else _META[sql]
            _guard(body.get("meta") == [{"name": name, "type": kind} for name, kind in expected_meta]
                   and all(isinstance(row, dict) and set(row) == {name for name, _ in expected_meta}
                           for row in rows))
            _guard(type(body.get("rows")) is int and body["rows"] == len(rows)
                   and len(rows) <= 64 and _uint(body.get("rows_before_limit_at_least", len(rows))) == len(rows)
                   and all(isinstance(row, dict) for row in rows))
            scan_rows, scan_bytes = _uint(stats["rows_read"]), _uint(stats["bytes_read"])
            _guard(scan_rows <= 100000 and scan_bytes <= 16777216
                   and type(stats["elapsed"]) in (int, float) and 0 <= stats["elapsed"] <= 5)
            if sql == PROFILE_SQL:
                _guard(len(rows) == len(CAPS)
                       and all(r["server_name"] == self.server and r["reader"] == self.account
                               and r["database"] == "quantfeed" for r in rows)
                       and {r["name"]: str(r["value"]) for r in rows} == CAPS)
            elif sql.startswith("EXPLAIN ESTIMATE "):
                table = _TABLES[sql.removeprefix("EXPLAIN ESTIMATE ")]
                _guard(len(rows) == 1 and rows[0]["database"] == "quantfeed" and rows[0]["table"] == table
                       and _uint(rows[0]["rows"]) <= 100000)
                _uint(rows[0]["parts"])
                _uint(rows[0]["marks"])
            else:
                _guard(len(rows) < (2 if sql == FUTURES_SQL else 64))
            elapsed = monotonic() - began
            _guard(elapsed <= timeout_seconds)
            received = _utc()
            # Persist complete raw bytes, never the Authorization header.
            artifact = f"{self._index:02d}.body"
            _persist(self._root / artifact, raw)
            rec.update(status="PASS", artifact=artifact, returned_rows=len(rows),
                       scan_rows=scan_rows, scan_bytes=scan_bytes, elapsed_seconds=elapsed, observed_at=received)
            return HistoricalReadReceipt(target=self.target, server=self.server, account=self.account,
                query_id=qid, observed_at=received, rows=tuple(rows), scan_rows=scan_rows, scan_bytes=scan_bytes,
                response_bytes=len(raw), elapsed_seconds=elapsed, body_sha256=_sha(raw))
        except Exception as exc:
            self._stopped = True
            rec["reason"] = type(exc).__name__
            raise StrategyUnavailable("SOURCE_INCOMPLETE") from None
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous)
            if conn is not None:
                conn.close()
            try:
                self._append(rec)
                if self._stopped or self._index == len(self._plan):
                    self._append({"event": "batch_terminal", "at": _utc(), "attempts": self._index,
                                  "status": "BLOCKED" if self._stopped else "OBSERVED", "automatic_retries": 0})
                    self._terminal_written = True
            except Exception:
                self._stopped = True
                raise StrategyUnavailable("SOURCE_INCOMPLETE") from None
