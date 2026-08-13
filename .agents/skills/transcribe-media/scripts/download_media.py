#!/usr/bin/env python3
"""Download a remote audio or video file into a new transcription run."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import ipaddress
import json
import mimetypes
import re
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_MAX_BYTES = 4 * 1024 * 1024 * 1024
READ_SIZE = 1024 * 1024
SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


def fail(message: str) -> None:
    raise SystemExit(message)


def display_url(url: str) -> str:
    """Return a URL safe for manifests and console output."""
    parsed = urllib.parse.urlsplit(url)
    hostname = parsed.hostname or ""
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    netloc = hostname
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    return urllib.parse.urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))


def validate_url(url: str, *, allow_private_hosts: bool = False) -> None:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        fail("Only http:// and https:// media URLs are supported")
    if not parsed.hostname:
        fail("Media URL must include a hostname")
    if parsed.username is not None or parsed.password is not None:
        fail("Credentials in media URLs are not supported")
    if allow_private_hosts:
        return

    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith(".localhost"):
        fail("Private or local media hosts require --allow-private-hosts")
    try:
        addresses = {
            item[4][0]
            for item in socket.getaddrinfo(
                hostname,
                parsed.port or (443 if parsed.scheme == "https" else 80),
                type=socket.SOCK_STREAM,
            )
        }
    except socket.gaierror as exc:
        fail(f"Unable to resolve media host {hostname}: {exc}")
    if not addresses:
        fail(f"Unable to resolve media host {hostname}")
    for address in addresses:
        if not ipaddress.ip_address(address).is_global:
            fail("Private or local media hosts require --allow-private-hosts")


class ValidatingRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, *, allow_private_hosts: bool) -> None:
        super().__init__()
        self.allow_private_hosts = allow_private_hosts

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        validate_url(newurl, allow_private_hosts=self.allow_private_hosts)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def filename_from_response(final_url: str, headers: Any) -> str:
    disposition_name = headers.get_filename() if hasattr(headers, "get_filename") else None
    candidate = disposition_name or Path(urllib.parse.unquote(urllib.parse.urlsplit(final_url).path)).name
    candidate = SAFE_FILENAME.sub("-", Path(candidate).name).strip(".-")
    if not candidate:
        candidate = "source-media"
    if not Path(candidate).suffix:
        content_type = (headers.get_content_type() if hasattr(headers, "get_content_type") else "")
        extension = mimetypes.guess_extension(content_type or "") or ""
        candidate += extension
    return candidate


def download_media(
    url: str,
    output_dir: Path,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    timeout_seconds: float = 30.0,
    allow_private_hosts: bool = False,
) -> dict[str, Any]:
    validate_url(url, allow_private_hosts=allow_private_hosts)
    if max_bytes <= 0:
        fail("max-bytes must be positive")
    if timeout_seconds <= 0:
        fail("timeout-seconds must be positive")

    output_dir = output_dir.expanduser().resolve()
    manifest_path = output_dir / "download.json"
    if manifest_path.exists():
        fail(f"Download manifest already exists; choose a new directory: {manifest_path}")
    output_dir.mkdir(parents=True, exist_ok=True)

    opener = urllib.request.build_opener(
        ValidatingRedirectHandler(allow_private_hosts=allow_private_hosts)
    )
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "podcast-whisper-mlx/1.0",
            "Accept": "audio/*,video/*,application/octet-stream;q=0.9,*/*;q=0.1",
        },
    )
    temporary_path: Path | None = None
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            final_url = response.geturl()
            validate_url(final_url, allow_private_hosts=allow_private_hosts)
            declared_size = response.headers.get("Content-Length")
            if declared_size:
                try:
                    if int(declared_size) > max_bytes:
                        fail(f"Remote media exceeds max-bytes ({declared_size} > {max_bytes})")
                except ValueError:
                    pass

            filename = filename_from_response(final_url, response.headers)
            destination = output_dir / filename
            temporary_path = destination.with_name(f".{destination.name}.part")
            if destination.exists() or temporary_path.exists():
                fail(f"Download target already exists; choose a new directory: {destination}")

            digest = hashlib.sha256()
            byte_count = 0
            with temporary_path.open("xb") as handle:
                while True:
                    block = response.read(READ_SIZE)
                    if not block:
                        break
                    byte_count += len(block)
                    if byte_count > max_bytes:
                        fail(f"Remote media exceeds max-bytes ({max_bytes})")
                    handle.write(block)
                    digest.update(block)
            if byte_count == 0:
                fail("Remote media response was empty")
            temporary_path.replace(destination)
            temporary_path = None

            payload = {
                "schema_version": 1,
                "downloaded_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "source_url": display_url(url),
                "resolved_url": display_url(final_url),
                "url_query_redacted": bool(
                    urllib.parse.urlsplit(url).query or urllib.parse.urlsplit(final_url).query
                ),
                "path": str(destination),
                "filename": destination.name,
                "content_type": response.headers.get("Content-Type"),
                "content_length": byte_count,
                "sha256": digest.hexdigest(),
                "etag": response.headers.get("ETag"),
                "last_modified": response.headers.get("Last-Modified"),
            }
            manifest_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            return payload
    except urllib.error.HTTPError as exc:
        fail(f"Media download failed with HTTP {exc.code}: {display_url(exc.geturl())}")
    except urllib.error.URLError as exc:
        fail(f"Media download failed: {exc.reason}")
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download one remote audio or video file and write download.json."
    )
    parser.add_argument("url")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument(
        "--allow-private-hosts",
        action="store_true",
        help="Allow localhost or private network sources only when explicitly authorized.",
    )
    args = parser.parse_args()
    payload = download_media(
        args.url,
        args.output_dir,
        max_bytes=args.max_bytes,
        timeout_seconds=args.timeout_seconds,
        allow_private_hosts=args.allow_private_hosts,
    )
    print(
        json.dumps(
            {
                "path": payload["path"],
                "content_type": payload["content_type"],
                "content_length": payload["content_length"],
                "sha256": payload["sha256"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
