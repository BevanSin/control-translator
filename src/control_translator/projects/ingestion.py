"""Project-scoped ingestion of uploaded/remote CSV and spreadsheet sources."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime, time
import hashlib
import io
import ipaddress
import os
from pathlib import Path
from pathlib import PurePosixPath
import socket
import tempfile
import time as monotonic_time
from typing import Callable
from urllib.parse import urljoin, urlsplit
import zipfile

try:  # pragma: no cover - exercised in tests
    import httpx
except ModuleNotFoundError:  # pragma: no cover
    httpx = None  # type: ignore[assignment]

from .store import ProjectStore

try:  # pragma: no cover - exercised in tests through the happy-path API flow
    import openpyxl
except ModuleNotFoundError:  # pragma: no cover
    openpyxl = None  # type: ignore[assignment]

MAX_SOURCE_BYTES = 2 * 1024 * 1024
MAX_ROWS = 5000
MAX_COLUMNS = 200
MAX_CELL_CHARS = 4000
DEFAULT_REQUEST_TIMEOUT_SECONDS = 10
MAX_REDIRECTS = 3
MAX_WORKBOOK_ARCHIVE_MEMBERS = 2048
MAX_WORKBOOK_MEMBER_UNCOMPRESSED_BYTES = 8 * 1024 * 1024
MAX_WORKBOOK_TOTAL_UNCOMPRESSED_BYTES = 24 * 1024 * 1024
MAX_WORKBOOK_COMPRESSION_RATIO = 200
MAX_NORMALIZED_BYTES = MAX_SOURCE_BYTES

_CSV_EXTENSIONS = frozenset({".csv"})
_SPREADSHEET_EXTENSIONS = frozenset({".xlsx", ".xlsm"})
_ALL_EXTENSIONS = _CSV_EXTENSIONS | _SPREADSHEET_EXTENSIONS
_FORMULA_PREFIXES = ("=", "+", "-", "@")
_CSV_MIME_TYPES = frozenset({
    "text/csv",
    "application/csv",
    "application/vnd.ms-excel",
    "text/plain",
})
_XLS_MIME_TYPES = frozenset({
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/octet-stream",
})


class SourceIngestionError(Exception):
    """Base failure for source ingestion."""


class UnsupportedSourceError(SourceIngestionError):
    """Source extension/MIME/type is unsupported."""


class SourceTooLargeError(SourceIngestionError):
    """Source body exceeded configured size limits."""


class MalformedSourceError(SourceIngestionError):
    """Source structure is malformed or violates strict bounds."""


class UnsafeSourceURLError(SourceIngestionError):
    """The URL or one of its redirects resolved to an unsafe destination."""


@dataclass(frozen=True)
class IngestedSource:
    source_id: str
    filename: str
    content_type: str
    size_bytes: int
    rows: int
    columns: int
    sha256: str
    project_path: str


class SourceIngestionService:
    """Validate and normalize user-provided standards into project-local source files."""

    def __init__(
        self,
        project_store: ProjectStore,
        *,
        resolver: Callable[[str, int], list[tuple]] | None = None,
        client_factory: Callable[[], object] | None = None,
        monotonic: Callable[[], float] | None = None,
    ):
        self._project_store = project_store
        self._resolver = resolver or socket.getaddrinfo
        self._client_factory = client_factory
        self._monotonic = monotonic or monotonic_time.monotonic

    def ingest_upload(
        self,
        *,
        project_id: str,
        filename: str,
        payload: bytes,
        content_type: str | None,
    ) -> IngestedSource:
        self._enforce_size_limit(payload)
        extension = self._validated_extension(filename)
        self._validate_declared_content_type(extension, content_type)
        normalized_csv, rows, columns = self._normalize_payload(extension, payload)
        return self._persist(project_id=project_id, normalized_csv=normalized_csv, rows=rows, columns=columns)

    def ingest_url(
        self,
        *,
        project_id: str,
        url: str,
        timeout_seconds: int = DEFAULT_REQUEST_TIMEOUT_SECONDS,
    ) -> IngestedSource:
        payload, extension = self._download(url=url, timeout_seconds=timeout_seconds)
        self._enforce_size_limit(payload)
        normalized_csv, rows, columns = self._normalize_payload(extension, payload)
        return self._persist(project_id=project_id, normalized_csv=normalized_csv, rows=rows, columns=columns)

    def _download(self, *, url: str, timeout_seconds: int) -> tuple[bytes, str]:
        if httpx is None:
            raise UnsupportedSourceError("URL ingestion support is unavailable.")
        redirects = 0
        current = url
        deadline = self._monotonic() + timeout_seconds
        client_factory = self._client_factory or (lambda: httpx.Client(follow_redirects=False))

        with client_factory() as client:
            while True:
                parts, address = self._validated_url_destination(current)
                timeout = self._remaining_timeout(deadline)
                pinned_url = self._pinned_request_url(parts, address)
                host_header = parts.hostname if parts.port in (None, 443) else f"{parts.hostname}:{parts.port}"
                try:
                    with client.stream(
                        "GET",
                        pinned_url,
                        timeout=httpx.Timeout(timeout, connect=timeout, read=timeout, write=timeout),
                        headers={"Host": host_header},
                        extensions={"sni_hostname": parts.hostname},
                    ) as response:
                        status = response.status_code
                        if status in (301, 302, 303, 307, 308):
                            location = response.headers.get("location")
                            if not location:
                                raise MalformedSourceError("URL response is malformed.")
                            redirects += 1
                            if redirects > MAX_REDIRECTS:
                                raise UnsafeSourceURLError("URL redirect chain is unsafe.")
                            current = urljoin(current, location)
                            continue
                        if status < 200 or status >= 300:
                            raise SourceIngestionError("URL source could not be retrieved.")
                        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                        ext = self._validated_extension(parts.path)
                        self._validate_declared_content_type(ext, content_type or None)
                        data = self._read_stream(response, deadline=deadline)
                        return data, ext
                except httpx.TimeoutException as exc:
                    raise SourceIngestionError("URL source could not be retrieved.") from exc
                except httpx.HTTPError as exc:
                    raise SourceIngestionError("URL source could not be retrieved.") from exc

    def _read_stream(self, response: object, *, deadline: float) -> bytes:
        content_length = response.headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > MAX_SOURCE_BYTES:
                    raise SourceTooLargeError("Source exceeds allowed size.")
            except ValueError as exc:
                raise MalformedSourceError("Source payload is malformed.") from exc

        buffer = bytearray()
        for chunk in response.iter_bytes():
            self._remaining_timeout(deadline)
            buffer.extend(chunk)
            if len(buffer) > MAX_SOURCE_BYTES:
                raise SourceTooLargeError("Source exceeds allowed size.")
        return bytes(buffer)

    def _validated_url_destination(self, url: str):
        parts = urlsplit(url)
        if parts.scheme.lower() != "https":
            raise UnsafeSourceURLError("Only HTTPS URLs are permitted.")
        if parts.username or parts.password:
            raise UnsafeSourceURLError("Credentialed URLs are not permitted.")
        if not parts.hostname:
            raise UnsafeSourceURLError("URL host is required.")
        port = parts.port or 443
        return parts, self._resolve_public_ip(parts.hostname, port)

    def _resolve_public_ip(self, hostname: str, port: int) -> str:
        try:
            results = self._resolver(hostname, port)
        except OSError as exc:
            raise UnsafeSourceURLError("URL host cannot be resolved.") from exc
        if not results:
            raise UnsafeSourceURLError("URL host cannot be resolved.")
        selected: str | None = None
        for entry in results:
            sockaddr = entry[4]
            if not sockaddr:
                raise UnsafeSourceURLError("URL host cannot be resolved.")
            address = sockaddr[0]
            self._validate_ip_address(address)
            if selected is None:
                selected = address
        if selected is None:
            raise UnsafeSourceURLError("URL host cannot be resolved.")
        return selected

    @staticmethod
    def _pinned_request_url(parts, address: str) -> str:
        host = f"[{address}]" if ":" in address else address
        port = parts.port or 443
        netloc = host if port == 443 else f"{host}:{port}"
        path = parts.path or "/"
        if parts.query:
            path = f"{path}?{parts.query}"
        return f"https://{netloc}{path}"

    def _remaining_timeout(self, deadline: float) -> float:
        remaining = deadline - self._monotonic()
        if remaining <= 0:
            raise SourceIngestionError("URL source could not be retrieved.")
        return remaining

    @staticmethod
    def _validate_ip_address(raw_address: str) -> None:
        try:
            parsed = ipaddress.ip_address(raw_address)
        except ValueError as exc:
            raise UnsafeSourceURLError("URL host cannot be resolved.") from exc

        if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped is not None:
            parsed = parsed.ipv4_mapped

        if (parsed.is_private or parsed.is_loopback or parsed.is_link_local or parsed.is_multicast
                or parsed.is_reserved or parsed.is_unspecified):
            raise UnsafeSourceURLError("URL destination is not permitted.")

    @staticmethod
    def _validated_extension(filename_or_path: str) -> str:
        extension = Path(filename_or_path).suffix.lower()
        if extension not in _ALL_EXTENSIONS:
            raise UnsupportedSourceError("Source type is not supported.")
        if extension == ".xlsm":
            raise UnsupportedSourceError("Macro-enabled spreadsheets are not permitted.")
        return extension

    @staticmethod
    def _validate_declared_content_type(extension: str, content_type: str | None) -> None:
        if content_type is None or not content_type.strip():
            return
        canonical = content_type.strip().lower()
        allowed = _CSV_MIME_TYPES if extension in _CSV_EXTENSIONS else _XLS_MIME_TYPES
        if canonical not in allowed:
            raise UnsupportedSourceError("Source media type is not supported.")

    @staticmethod
    def _enforce_size_limit(payload: bytes) -> None:
        if len(payload) > MAX_SOURCE_BYTES:
            raise SourceTooLargeError("Source exceeds allowed size.")

    def _normalize_payload(self, extension: str, payload: bytes) -> tuple[bytes, int, int]:
        if extension in _CSV_EXTENSIONS:
            return self._normalize_csv(payload)
        return self._normalize_workbook(payload)

    def _normalize_csv(self, payload: bytes) -> tuple[bytes, int, int]:
        try:
            text = payload.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise MalformedSourceError("CSV source is malformed.") from exc
        if "\x00" in text:
            raise MalformedSourceError("CSV source is malformed.")

        try:
            rows, columns = self._parse_csv_rows(csv.reader(io.StringIO(text, newline="")))
        except csv.Error as exc:
            raise MalformedSourceError("CSV source is malformed.") from exc
        return self._render_csv(rows), len(rows), columns

    def _normalize_workbook(self, payload: bytes) -> tuple[bytes, int, int]:
        if openpyxl is None:
            raise UnsupportedSourceError("Spreadsheet support is unavailable.")
        if not payload.startswith(b"PK"):
            raise MalformedSourceError("Spreadsheet source is malformed.")
        self._validate_workbook_archive(payload)
        try:
            workbook = openpyxl.load_workbook(
                io.BytesIO(payload), read_only=True, data_only=False, keep_links=False,
            )
        except Exception as exc:  # noqa: BLE001 - normalize parser/library failures
            raise MalformedSourceError("Spreadsheet source is malformed.") from exc

        if getattr(workbook, "vba_archive", None):
            raise UnsupportedSourceError("Macro-enabled spreadsheets are not permitted.")
        if getattr(workbook, "_external_links", None):
            raise UnsupportedSourceError("Spreadsheets with external links are not permitted.")

        sheet = workbook.worksheets[0] if workbook.worksheets else None
        if sheet is None:
            raise MalformedSourceError("Spreadsheet source is malformed.")

        parsed_rows: list[list[str]] = []
        max_columns = 0
        for row_index, row in enumerate(sheet.iter_rows(values_only=False), start=1):
            if row_index > MAX_ROWS:
                raise SourceTooLargeError("Source exceeds allowed row limits.")
            rendered_row: list[str] = []
            for col_index, cell in enumerate(row, start=1):
                if col_index > MAX_COLUMNS:
                    raise SourceTooLargeError("Source exceeds allowed column limits.")
                if getattr(cell, "data_type", None) == "f":
                    raise UnsupportedSourceError("Spreadsheet formulas are not permitted.")
                value = cell.value
                rendered = self._normalize_cell(value)
                self._validate_cell(rendered)
                rendered_row.append(rendered)
            max_columns = max(max_columns, len(rendered_row))
            parsed_rows.append(rendered_row)

        if not parsed_rows:
            raise MalformedSourceError("Spreadsheet source is malformed.")
        return self._render_csv(parsed_rows), len(parsed_rows), max_columns

    @staticmethod
    def _validate_workbook_archive(payload: bytes) -> None:
        try:
            archive = zipfile.ZipFile(io.BytesIO(payload))
        except zipfile.BadZipFile as exc:
            raise MalformedSourceError("Spreadsheet source is malformed.") from exc
        with archive:
            members = archive.infolist()
            if not members:
                raise MalformedSourceError("Spreadsheet source is malformed.")
            if len(members) > MAX_WORKBOOK_ARCHIVE_MEMBERS:
                raise SourceTooLargeError("Source exceeds allowed size.")
            total_uncompressed = 0
            for member in members:
                if member.flag_bits & 0x1:
                    raise UnsupportedSourceError("Encrypted spreadsheets are not permitted.")
                member_path = member.filename.replace("\\", "/")
                parts = PurePosixPath(member_path).parts
                if member_path.startswith("/") or any(part in ("", "..") for part in parts) or any(":" in p for p in parts):
                    raise MalformedSourceError("Spreadsheet source is malformed.")
                if member.file_size > MAX_WORKBOOK_MEMBER_UNCOMPRESSED_BYTES:
                    raise SourceTooLargeError("Source exceeds allowed size.")
                total_uncompressed += member.file_size
                if total_uncompressed > MAX_WORKBOOK_TOTAL_UNCOMPRESSED_BYTES:
                    raise SourceTooLargeError("Source exceeds allowed size.")
                if member.file_size > 0:
                    compressed = member.compress_size
                    if compressed <= 0:
                        raise SourceTooLargeError("Source exceeds allowed size.")
                    if (member.file_size / compressed) > MAX_WORKBOOK_COMPRESSION_RATIO:
                        raise SourceTooLargeError("Source exceeds allowed size.")

    def _parse_csv_rows(self, rows_iterable) -> tuple[list[list[str]], int]:
        rows: list[list[str]] = []
        max_columns = 0
        for row_index, row in enumerate(rows_iterable, start=1):
            if row_index > MAX_ROWS:
                raise SourceTooLargeError("Source exceeds allowed row limits.")
            if len(row) > MAX_COLUMNS:
                raise SourceTooLargeError("Source exceeds allowed column limits.")
            normalized_row: list[str] = []
            for value in row:
                self._validate_cell(value)
                normalized_row.append(value)
            rows.append(normalized_row)
            max_columns = max(max_columns, len(normalized_row))
        if not rows:
            raise MalformedSourceError("CSV source is empty.")
        return rows, max_columns

    @staticmethod
    def _normalize_cell(value: object) -> str:
        if value is None:
            return ""
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, date):
            return value.isoformat()
        if isinstance(value, time):
            return value.isoformat()
        return str(value)

    @staticmethod
    def _validate_cell(value: str) -> None:
        stripped = SourceIngestionService._strip_leading_control_whitespace(value)
        if stripped.startswith(_FORMULA_PREFIXES):
            raise UnsupportedSourceError("Spreadsheet formulas are not permitted.")
        if len(value) > MAX_CELL_CHARS:
            raise SourceTooLargeError("Source exceeds allowed cell limits.")

    @staticmethod
    def _strip_leading_control_whitespace(value: str) -> str:
        index = 0
        while index < len(value):
            character = value[index]
            if character.isspace() or ord(character) <= 0x1F:
                index += 1
                continue
            break
        return value[index:]

    @staticmethod
    def _render_csv(rows: list[list[str]]) -> bytes:
        bytes_buffer = io.BytesIO()
        text_buffer = io.TextIOWrapper(bytes_buffer, encoding="utf-8", newline="", write_through=True)
        writer = csv.writer(text_buffer, lineterminator="\n")
        for row in rows:
            writer.writerow(row)
            if bytes_buffer.tell() > MAX_NORMALIZED_BYTES:
                raise SourceTooLargeError("Source exceeds allowed size.")
        text_buffer.flush()
        return bytes_buffer.getvalue()

    def _persist(
        self,
        *,
        project_id: str,
        normalized_csv: bytes,
        rows: int,
        columns: int,
        content_type: str = "text/csv",
    ) -> IngestedSource:
        self._enforce_size_limit(normalized_csv)
        digest = hashlib.sha256(normalized_csv).hexdigest()
        filename = f"{digest[:16]}.csv"
        target = self._project_store.resolve_path(project_id, Path("source") / filename)
        target.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write(target, normalized_csv)
        return IngestedSource(
            source_id=digest[:16],
            filename=filename,
            content_type=content_type,
            size_bytes=len(normalized_csv),
            rows=rows,
            columns=columns,
            sha256=digest,
            project_path=f"source/{filename}",
        )

    @staticmethod
    def _atomic_write(path: Path, payload: bytes) -> None:
        fd, temporary = tempfile.mkstemp(prefix=".source-", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as file:
                file.write(payload)
                file.flush()
                os.fsync(file.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
