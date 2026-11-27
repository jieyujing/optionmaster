"""D04: concrete narrow HTTPS transport for the D03 read repository.

An operator must supply an authorized direct TLS endpoint, credentials and
expected server display name. This module has no default endpoint, secrets,
auto-discovery, retries, driver dependency, or production activation. One
instance is for one materialization, with one HTTP session and connection.
Server-side limits remain mandatory; socket timeouts plus monotonic checks
are not proof of an absolute DNS/HTTP-header deadline or server cancellation.
"""
from base64 import b64encode
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from http.client import HTTPSConnection, HTTPResponse
import json
from math import isfinite
import re
import ssl
from struct import pack, unpack
from threading import Lock
from time import monotonic
from urllib.parse import urlencode
from uuid import uuid4

from vnpy_optionmaster.research_history_repository import (
    Row,
    _FUTURES_SQL,
    _OPTION_SQL,
    _PROFILE_CEILINGS,
    _PROFILE_EXACT,
    _PROFILE_SQL,
    _TERMS_SQL,
)


class ClickHouseReadError(RuntimeError):
    """Sanitized transport refusal; never carries credentials or server body."""


_MAX_BODY = 2 * 1024 * 1024
_MAX_CALLS = 25
_REQUEST_SECONDS = 20.0
_QUERY_SECONDS = 5.0
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_DATE_TIME_TYPE = "DateTime64(3, 'Asia/Shanghai')"


@dataclass(frozen=True)
class _Field:
    name: str
    kind: str = "text"

    @property
    def source_type(self) -> str | None:
        return {
            "bits": "Float64",
            "nullable_bits": "Nullable(Float64)",
            "epoch_ms": _DATE_TIME_TYPE,
        }.get(self.kind)

    @property
    def wire_types(self) -> tuple[str, ...]:
        if self.kind == "text":
            return ("String", "LowCardinality(String)")
        if self.kind == "date":
            return ("Date",)
        if self.kind == "nullable_bits":
            return ("Nullable(String)",)
        return ("String",)


@dataclass(frozen=True)
class _Query:
    sql: str
    fields: tuple[_Field, ...]
    parameter_names: frozenset[str]
    max_rows: int


def _market_query(
    logical: str, table: str, fields: tuple[_Field, ...], params: tuple[str, ...],
) -> _Query:
    # Only called on the four trusted D03 constants at module load. The FROM,
    # WHERE, typed parameters and LIMIT are unchanged. Qualify source columns
    # to avoid ClickHouse's SELECT alias substitution in type guards.
    expressions: list[str] = []
    type_guards: list[_Field] = []
    guards: list[str] = []
    for field in fields:
        column = f"{table}.{field.name}"
        if field.kind in ("bits", "nullable_bits"):
            expression = f"toString(reinterpretAsUInt64({column}))"
        elif field.kind == "epoch_ms":
            expression = f"toString(toUnixTimestamp64Milli({column}))"
        else:
            expression = column
        expressions.append(f"{expression} AS {field.name}")
        if field.source_type is not None:
            tag = f"__type_{field.name}"
            guards.append(f"toTypeName({column}) AS {tag}")
            type_guards.append(_Field(tag))
    tail = logical.split("\nFROM ", 1)[1]
    sql = "SELECT " + ", ".join(expressions + guards) + "\nFROM " + tail
    return _Query(sql, fields + tuple(type_guards), frozenset(params), 2)


_QUERIES = {
    _PROFILE_SQL: _Query(_PROFILE_SQL, (_Field("name"), _Field("value")), frozenset(), 10),
    _TERMS_SQL: _market_query(_TERMS_SQL, "option_contract_terms_daily", (
        _Field("symbol"), _Field("exchange"), _Field("trade_date", "date"),
        _Field("portfolio_code"), _Field("chain_id"), _Field("underlying_symbol"),
        _Field("expiry_date", "date"), _Field("option_type"), _Field("exercise_style"),
        _Field("strike", "nullable_bits"), _Field("contract_size", "nullable_bits"),
        _Field("contract_version"), _Field("source"), _Field("provenance"),
        _Field("ingested_at", "epoch_ms"),
    ), ("portfolio_code", "trade_date", "symbol")),
    _OPTION_SQL: _market_query(_OPTION_SQL, "option_daily", (
        _Field("symbol"), _Field("exchange"), _Field("trade_date", "date"),
        _Field("close", "nullable_bits"), _Field("settlement", "nullable_bits"),
        _Field("source"), _Field("ingested_at", "epoch_ms"),
    ), ("symbol", "trade_date")),
    _FUTURES_SQL: _market_query(_FUTURES_SQL, "futures_daily", (
        _Field("symbol"), _Field("exchange"), _Field("trade_date", "date"),
        _Field("product_symbol"), _Field("close", "bits"), _Field("settlement", "nullable_bits"),
        _Field("price_source"), _Field("settlement_source"), _Field("ingested_at", "epoch_ms"),
    ), ("product_symbol", "trade_date", "symbol")),
}


def _refuse(reason: str) -> ClickHouseReadError:
    return ClickHouseReadError(reason)


def _exact_date(value: str) -> bool:
    try:
        return len(value) == 10 and date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _bound_params(spec: _Query, parameters: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(parameters, Mapping) or set(parameters) != spec.parameter_names:
        raise _refuse("INVALID_QUERY_PARAMETERS")
    result: dict[str, str] = {}
    for key, value in parameters.items():
        # ClickHouse HTTP parameters use the Escaped format before typed
        # substitution. Reject backslashes and control/whitespace entirely.
        if (
            not isinstance(value, str) or not 0 < len(value) <= 512
            or not value.isprintable() or any(c.isspace() for c in value)
            or chr(92) in value
            or (key == "trade_date" and not _exact_date(value))
        ):
            raise _refuse("INVALID_QUERY_PARAMETERS")
        result[f"param_{key}"] = value
    return result


def _safe_profile(rows: tuple[Row, ...]) -> bool:
    settings: dict[str, str] = {}
    for row in rows:
        name, value = row.get("name"), row.get("value")
        if not isinstance(name, str) or not isinstance(value, str) or name in settings:
            return False
        settings[name] = value
    if set(settings) != set(_PROFILE_EXACT) | set(_PROFILE_CEILINGS):
        return False
    if any(settings[name] != value for name, value in _PROFILE_EXACT.items()):
        return False
    for name, cap in _PROFILE_CEILINGS.items():
        try:
            raw = settings[name]
            if len(raw) > 32:
                return False
            value_dec = Decimal(raw)
            if not value_dec.is_finite() or not 0 < value_dec <= cap:
                return False
            if name != "max_execution_time" and value_dec != value_dec.to_integral_value():
                return False
        except (ValueError, InvalidOperation):
            return False
    return True


def _no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _refuse("INVALID_JSON_RESPONSE")
        result[key] = value
    return result


def _no_json_constant(value: str) -> object:
    raise _refuse("INVALID_JSON_RESPONSE")


def _decode(field: _Field, value: object) -> object:
    if value is None and field.kind == "nullable_bits":
        return None
    if not isinstance(value, str):
        raise _refuse("WIRE_VALUE_MISMATCH")
    if field.kind == "text":
        return value
    if field.kind == "date":
        if not _exact_date(value):
            raise _refuse("WIRE_VALUE_MISMATCH")
        return value
    if field.kind in ("bits", "nullable_bits"):
        if not re.fullmatch(r"[0-9]{1,20}", value):
            raise _refuse("WIRE_VALUE_MISMATCH")
        bits = int(value)
        if bits >= 2**64:
            raise _refuse("WIRE_VALUE_MISMATCH")
        # Preserve IEEE values exactly, even NaN or infinity; D01/D02 reject
        # nonfinite marks rather than mistaking them for missing settlement.
        decoded: float = unpack("<d", pack("<Q", bits))[0]
        return decoded
    if field.kind == "epoch_ms":
        if not re.fullmatch(r"-?[0-9]{1,19}", value):
            raise _refuse("WIRE_VALUE_MISMATCH")
        try:
            return _EPOCH + timedelta(milliseconds=int(value))
        except OverflowError:
            raise _refuse("WIRE_VALUE_MISMATCH") from None
    raise _refuse("WIRE_SCHEMA_MISMATCH")


def _parse_json(body: bytes, spec: _Query) -> tuple[Row, ...]:
    document = json.loads(
        body.decode("utf-8"), object_pairs_hook=_no_duplicates, parse_constant=_no_json_constant,
    )
    if not isinstance(document, dict) or set(document) - {"meta", "data", "rows", "statistics", "rows_before_limit_at_least"}:
        raise _refuse("INVALID_JSON_RESPONSE")
    meta, rows, count = document.get("meta"), document.get("data"), document.get("rows")
    if (
        not isinstance(meta, list) or len(meta) != len(spec.fields)
        or not isinstance(rows, list) or type(count) is not int
        or count != len(rows) or len(rows) > spec.max_rows
    ):
        raise _refuse("INVALID_JSON_RESPONSE")
    for column, field in zip(meta, spec.fields, strict=True):
        if (
            not isinstance(column, dict) or set(column) != {"name", "type"}
            or column["name"] != field.name or column["type"] not in field.wire_types
        ):
            raise _refuse("WIRE_SCHEMA_MISMATCH")
    names = {f.name for f in spec.fields}
    result: list[Row] = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != names:
            raise _refuse("WIRE_SCHEMA_MISMATCH")
        decoded: dict[str, object] = {}
        for field in spec.fields:
            if field.name.startswith("__type_"):
                continue
            if field.source_type is not None and row[f"__type_{field.name}"] != field.source_type:
                raise _refuse("WIRE_SCHEMA_MISMATCH")
            decoded[field.name] = _decode(field, row[field.name])
        result.append(decoded)
    return tuple(result)


def _header(response: HTTPResponse, key: str) -> str | None:
    values = [value for name, value in response.getheaders() if name.lower() == key.lower()]
    if len(values) > 1:
        raise _refuse("INVALID_HTTP_RESPONSE")
    return values[0] if values else None


class ClickHouseHttpSelect:
    """Single-materialization concrete ReadOnlySelect transport.

    Intended for trusted composition code only, not an untrusted public API.
    Use one new instance for each HistoricalRepository.materialize call and
    close it even if the profile or D02 rejects. TLS validation plus an
    expected server name and HTTP session detect obvious endpoint drift;
    they do not certify a multi-table business release or proxy topology.
    """

    def __init__(
        self, *, host: str, port: int, username: str, password: str,
        expected_server_name: str, tls_context: ssl.SSLContext | None = None,
    ) -> None:
        # Deliberately narrow direct hostname/IPv4 contract. No URL, path,
        # proxy, userinfo, embedded query or insecure transport configuration.
        if (
            not isinstance(host, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}", host)
            or type(port) is not int or not 1 <= port <= 65535
            or not isinstance(username, str) or not username or ":" in username
            or not isinstance(password, str) or not password
            or not username.isprintable() or not password.isprintable()
            or not isinstance(expected_server_name, str) or not expected_server_name
            or not expected_server_name.isprintable()
        ):
            raise _refuse("INVALID_ENDPOINT_CONFIG")
        context = tls_context if tls_context is not None else ssl.create_default_context()
        if not context.check_hostname or context.verify_mode != ssl.CERT_REQUIRED:
            raise _refuse("TLS_VERIFICATION_REQUIRED")
        self._context = context
        self._host, self._port = host, port
        self._server_name = expected_server_name
        self._auth = "Basic " + b64encode(f"{username}:{password}".encode()).decode("ascii")
        self._session = uuid4().hex
        self._connection: HTTPSConnection | None = None
        self._deadline: float | None = None
        self._calls = 0
        self._profile_seen = False
        self._admitted = False
        self._closed = False
        self._lock = Lock()

    def __enter__(self) -> "ClickHouseHttpSelect":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def _abort(self) -> None:
        self._closed = True
        self._admitted = False
        connection, self._connection = self._connection, None
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass

    def close(self) -> None:
        with self._lock:
            self._abort()

    @staticmethod
    def _remaining(deadline: float) -> float:
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise _refuse("QUERY_BUDGET_EXCEEDED")
        return remaining

    def _set_timeout(self, deadline: float) -> HTTPSConnection:
        connection = self._connection
        if connection is None or connection.sock is None:
            raise _refuse("SESSION_CONNECTION_LOST")
        connection.sock.settimeout(self._remaining(deadline))
        return connection

    def _read_body(self, response: HTTPResponse, deadline: float) -> bytes:
        if (
            response.status != 200 or response.will_close
            or _header(response, "X-ClickHouse-Server-Display-Name") != self._server_name
            or _header(response, "X-ClickHouse-Format") != "JSON"
            or _header(response, "X-ClickHouse-Exception-Code") not in (None, "0")
            or _header(response, "Content-Encoding") not in (None, "identity")
        ):
            raise _refuse("INVALID_HTTP_RESPONSE")
        content_type = _header(response, "Content-Type")
        if content_type is None or content_type.split(";", 1)[0].strip().lower() != "application/json":
            raise _refuse("INVALID_HTTP_RESPONSE")
        content_length = _header(response, "Content-Length")
        transfer_encoding = _header(response, "Transfer-Encoding")
        expected: int | None = None
        if content_length is not None:
            if response.chunked or transfer_encoding is not None or not re.fullmatch(r"[0-9]{1,10}", content_length):
                raise _refuse("INVALID_HTTP_RESPONSE")
            expected = int(content_length)
            if expected > _MAX_BODY:
                raise _refuse("RESPONSE_BUDGET_EXCEEDED")
        elif not response.chunked or transfer_encoding != "chunked":
            raise _refuse("INVALID_HTTP_RESPONSE")
        chunks: list[bytes] = []
        size = 0
        while True:
            self._set_timeout(deadline)
            chunk = response.read1(min(65536, _MAX_BODY + 1 - size))
            self._remaining(deadline)
            if not chunk:
                break
            size += len(chunk)
            if size > _MAX_BODY:
                raise _refuse("RESPONSE_BUDGET_EXCEEDED")
            chunks.append(chunk)
        if expected is not None and size != expected:
            raise _refuse("INCOMPLETE_HTTP_RESPONSE")
        return b"".join(chunks)

    def select(
        self, sql: str, parameters: Mapping[str, str], *, timeout_seconds: float,
    ) -> tuple[Row, ...]:
        if not self._lock.acquire(blocking=False):
            raise _refuse("CONCURRENT_QUERY_FORBIDDEN")
        try:
            return self._select(sql, parameters, timeout_seconds)
        finally:
            self._lock.release()

    def _select(self, sql: str, parameters: Mapping[str, str], timeout_seconds: float) -> tuple[Row, ...]:
        if self._closed:
            raise _refuse("SESSION_CLOSED")
        spec = _QUERIES.get(sql) if isinstance(sql, str) else None
        if spec is None:
            raise _refuse("QUERY_NOT_ALLOWED")
        if (
            isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
            or not 0 < timeout_seconds <= _QUERY_SECONDS or not isfinite(timeout_seconds)
        ):
            raise _refuse("INVALID_QUERY_TIMEOUT")
        params = _bound_params(spec, parameters)
        if sql == _PROFILE_SQL:
            if self._profile_seen:
                raise _refuse("SESSION_ALREADY_USED")
        elif not self._admitted:
            raise _refuse("UNSAFE_READ_PROFILE")
        if self._calls >= _MAX_CALLS:
            self._abort()
            raise _refuse("QUERY_BUDGET_EXCEEDED")

        started = monotonic()
        if self._deadline is None:
            self._deadline = started + _REQUEST_SECONDS
        deadline = min(self._deadline, started + timeout_seconds)
        try:
            if self._connection is None:
                # A supplied SSLContext is mutable; recheck at actual connect.
                if not self._context.check_hostname or self._context.verify_mode != ssl.CERT_REQUIRED:
                    raise _refuse("TLS_VERIFICATION_REQUIRED")
                # Exactly one explicit initial connect; no silent reconnect.
                self._connection = HTTPSConnection(
                    self._host, port=self._port, context=self._context,
                    timeout=self._remaining(deadline),
                )
                self._connection.connect()
            connection = self._set_timeout(deadline)
            url_params = {"session_id": self._session, **params}
            if self._calls:
                url_params["session_check"] = "1"
            path = "/?" + urlencode(url_params)
            self._calls += 1
            connection.request("POST", path, body=(spec.sql + "\nFORMAT JSON").encode("utf-8"), headers={
                "Authorization": self._auth, "Content-Type": "text/plain; charset=utf-8",
                "Accept-Encoding": "identity", "Connection": "keep-alive",
            })
            self._set_timeout(deadline)
            response = connection.getresponse()
            try:
                raw = self._read_body(response, deadline)
                rows = _parse_json(raw, spec)
                self._remaining(deadline)
            finally:
                response.close()
            if sql == _PROFILE_SQL:
                self._profile_seen = True
                self._admitted = _safe_profile(rows)
            return rows
        except ClickHouseReadError:
            self._abort()
            raise
        except Exception:
            self._abort()
            raise _refuse("QUERY_TRANSPORT_FAILED") from None
