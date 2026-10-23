#!/usr/bin/env python3
"""Download direct PDFs with structural validation and resumable audit logs.

HTML/XML are saved separately as unverified content, never counted as full text.
Default resume retries unresolved records and handles new candidates. With
--retry-failed, only previously logged unresolved records are eligible.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from pdf_validation import PDFValidation, validate_pdf, validate_pdf_bytes


PAYWALL_MARKERS = [
    "institutional login", "subscribe to access", "purchase access",
    "access through your institution", "sign in to access", "login required",
    "shibboleth", "ezproxy", "redirecting", "temporarily unavailable",
]
LOG_FIELDS = [
    "record_id", "doi", "title", "final_path", "final_url", "download_status",
    "failure_reason", "access_route_used", "content_format", "attempt_count",
    "validation_evidence", "page_count", "payload_sha256",
]


def clean(value: object) -> str:
    return str(value or "").strip()


def normalize_doi(value: str) -> str:
    value = re.sub(r"^https?://(dx\.)?doi\.org/", "", clean(value), flags=re.I)
    return re.sub(r"^doi:\s*", "", value, flags=re.I).strip()


def record_id_for(row: dict[str, str]) -> str:
    supplied = clean(row.get("record_id")) or clean(row.get("id"))
    if supplied:
        return supplied
    identity = normalize_doi(clean(row.get("doi"))).casefold()
    if not identity:
        identity = "|".join(clean(row.get(key)).casefold() for key in ["title", "authors", "year"])
    return "rec_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]


def stable_stem(row: dict[str, str]) -> str:
    title = clean(row.get("title")) or "untitled"
    year = clean(row.get("year")) or "unknown"
    first = clean(row.get("authors")).split(";")[0].split(",")[0].strip()
    raw = f"{first}_{year}_{title[:80]}" if first else f"{year}_{title[:80]}"
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", raw).strip("_")
    return stem[:120] or hashlib.sha1(title.encode("utf-8")).hexdigest()[:12]


def file_stem(row: dict[str, str]) -> str:
    record_id = record_id_for(row)
    safe_id = re.sub(r"[^A-Za-z0-9._-]", "_", record_id).strip(".")[:70] or "record"
    if safe_id != record_id:
        safe_id += "_" + hashlib.sha256(record_id.encode("utf-8")).hexdigest()[:8]
    return f"{safe_id}_{stable_stem(row)}"


def classify_payload(payload: bytes, content_type: str, final_url: str = "") -> str:
    """Use bytes first; a URL suffix or PDF MIME type is not PDF evidence."""
    head = payload[:4096].lstrip().lower()
    if head.startswith(b"%pdf-"):
        return "pdf"
    if b"<html" in head or b"<!doctype html" in head:
        return "html"
    if head.startswith(b"<?xml"):
        return "xml"
    lowered_type = content_type.lower()
    if "html" in lowered_type:
        return "html"
    if "xml" in lowered_type:
        return "xml"
    return "binary"


def looks_paywalled(payload: bytes) -> bool:
    text = payload[:50000].decode("utf-8", errors="ignore").lower()
    return any(marker in text for marker in PAYWALL_MARKERS)


def candidate_urls(row: dict[str, str]) -> list[str]:
    urls: list[str] = []
    for key in ["pdf_url_candidate", "landing_page_url"]:
        value = clean(row.get(key))
        if value.startswith(("http://", "https://")):
            urls.append(value)
    raw_json = clean(row.get("candidate_urls_json"))
    if raw_json:
        try:
            parsed = json.loads(raw_json)
            if isinstance(parsed, list):
                for item in parsed:
                    value = clean(item.get("url") if isinstance(item, dict) else item)
                    if value.startswith(("http://", "https://")):
                        urls.append(value)
        except json.JSONDecodeError:
            pass
    doi = normalize_doi(clean(row.get("doi")))
    if doi:
        urls.append(f"https://doi.org/{doi}")
    return list(dict.fromkeys(urls))


def fetch(url: str, timeout: int) -> tuple[bytes, str, str]:
    request = urllib.request.Request(url, headers={
        "User-Agent": "literature-downloader/1.1 (+lawful-access-check)",
        "Accept": "application/pdf,text/html,application/xml,text/xml,*/*;q=0.8",
    })
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return response.read(), response.headers.get("Content-Type", ""), response.geturl()


def atomic_bytes(path: Path, payload: bytes, *, check_pdf: bool = False) -> PDFValidation | None:
    """Commit a staged file only after validation; preserve any previous file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name + ".", suffix=".part", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
        evidence = validate_pdf(temporary) if check_pdf else None
        if evidence is not None and not evidence.valid:
            raise ValueError(evidence.reason)
        temporary.replace(path)
        return evidence
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def write_log(path: Path, rows: list[dict[str, str]]) -> None:
    fields = list(LOG_FIELDS)
    for row in rows:
        fields.extend(key for key in row if key not in fields)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    try:
        with temporary.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def copy_alias(source: Path, alias: Path) -> None:
    if source.exists() and source.resolve() != alias.resolve():
        atomic_bytes(alias, source.read_bytes())


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def save_history(path: Path, previous: dict[str, str] | None, current: dict[str, str]) -> None:
    # Retain replaced outcomes, including legacy/custom columns. The primary
    # CSV remains one current outcome per record.
    rows = read_csv(path)
    moment = datetime.now(timezone.utc).isoformat()
    if previous is not None:
        rows.append({**previous, "history_event": "previous_result", "recorded_at_utc": moment})
    rows.append({**current, "history_event": "result", "recorded_at_utc": moment})
    write_log(path, rows)


def resolve_log_path(value: str, run_root: Path) -> Path | None:
    if not clean(value):
        return None
    path = Path(value).expanduser()
    return path if path.is_absolute() else run_root / path


def previous_pdf(previous: dict[str, str] | None, run_root: Path) -> tuple[Path | None, PDFValidation | None]:
    if previous is None or previous.get("download_status") != "success_pdf":
        return None, None
    path = resolve_log_path(previous.get("final_path", ""), run_root)
    if path is None:
        return None, PDFValidation(False, "missing_pdf_path")
    return path, validate_pdf(path)


def base_result(row: dict[str, str], previous: dict[str, str] | None) -> dict[str, str]:
    result = dict(previous or {})
    result.update({
        "record_id": record_id_for(row), "doi": clean(row.get("doi")), "title": clean(row.get("title")),
        "final_path": "", "final_url": "", "download_status": "metadata_only",
        "failure_reason": "no_candidate_url", "access_route_used": "direct_url_or_doi",
        "content_format": "", "validation_evidence": "", "page_count": "", "payload_sha256": "",
    })
    return result


def add_pdf_evidence(result: dict[str, str], path: Path, evidence: PDFValidation, route: str) -> None:
    result.update({
        "final_path": str(path), "download_status": "success_pdf", "failure_reason": "",
        "content_format": "pdf", "validation_evidence": evidence.reason,
        "page_count": str(evidence.page_count), "payload_sha256": evidence.sha256,
        "access_route_used": route,
    })


def download_record(row: dict[str, str], previous: dict[str, str] | None, args: argparse.Namespace,
                    run_root: Path) -> dict[str, str]:
    result = base_result(row, previous)
    try:
        old_attempts = max(0, int(clean((previous or {}).get("attempt_count")) or "0"))
    except ValueError:
        old_attempts = 0
    result["attempt_count"] = str(old_attempts)
    exclusion = clean(row.get("exclusion_reason_if_any"))
    if exclusion:
        result.update(download_status="excluded", failure_reason=exclusion, access_route_used="")
        return result

    stem = file_stem(row)
    output = run_root / "downloaded_fulltexts" / f"{stem}.pdf"
    alias = run_root / "已下载全文" / output.name
    # A valid deterministic file may predate a log or have survived a crash.
    for path in [alias, output]:
        if path.exists():
            evidence = validate_pdf(path)
            if evidence.valid:
                other = output if path == alias else alias
                atomic_bytes(other, path.read_bytes(), check_pdf=True)
                add_pdf_evidence(result, alias, evidence, "existing_validated_pdf")
                return result

    unverified: dict[str, str] | None = None
    for url in candidate_urls(row)[:args.max_attempts]:
        old_attempts += 1
        result["attempt_count"] = str(old_attempts)
        try:
            payload, content_type, resolved_url = fetch(url, args.timeout)
            kind = classify_payload(payload, content_type, resolved_url)
            result.update(final_url=resolved_url, content_format=kind)
            if kind == "pdf":
                evidence = validate_pdf_bytes(payload)
                if not evidence.valid:
                    result.update(download_status="failed", failure_reason=evidence.reason,
                                  validation_evidence=evidence.reason, payload_sha256=evidence.sha256)
                    continue
                atomic_bytes(output, payload, check_pdf=True)
                atomic_bytes(alias, payload, check_pdf=True)
                add_pdf_evidence(result, alias, evidence, "direct_url_or_doi")
                return result
            if kind in {"html", "xml"}:
                target = run_root / "unverified_content" / f"{stem}.{kind}"
                unverified_alias = run_root / "待核验内容" / target.name
                atomic_bytes(target, payload)
                atomic_bytes(unverified_alias, payload)
                reason = "paywall_or_login_detected" if looks_paywalled(payload) else f"{kind}_fulltext_not_verified"
                # Keep searching other URLs for a validated PDF.
                unverified = {
                    "download_status": f"saved_{kind}_unverified", "failure_reason": reason,
                    "final_path": str(unverified_alias), "final_url": resolved_url,
                    "content_format": kind, "validation_evidence": "content_saved_fulltext_unverified",
                    "payload_sha256": hashlib.sha256(payload).hexdigest(), "page_count": "",
                }
            else:
                result.update(download_status="failed", failure_reason="unsupported_or_non_pdf_payload",
                              validation_evidence="not_a_validated_pdf")
        except urllib.error.HTTPError as exc:
            code = int(getattr(exc, "code", 0))
            status = "inaccessible" if code in {401, 402, 403} else "broken_link" if code in {404, 410} else "metadata_only"
            result.update(download_status="rate_limited" if code == 429 else status,
                          failure_reason=f"http_{code}", final_url=url)
        except Exception as exc:  # noqa: BLE001
            result.update(download_status="failed", failure_reason=type(exc).__name__, final_url=url)
        finally:
            time.sleep(args.delay)
    if unverified is not None:
        result.update(unverified)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Download direct PDFs with structural validation; save HTML/XML as unverified.")
    parser.add_argument("--run-root", required=True)
    parser.add_argument("--retry-failed", action="store_true", help="Retry logged unresolved records only; leave new records untouched.")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--delay", type=float, default=0.5)
    parser.add_argument("--max-attempts", type=int, default=3)
    args = parser.parse_args(argv)
    if args.max_attempts < 1 or args.timeout < 1 or args.delay < 0:
        parser.error("max-attempts/timeout must be positive and delay must be nonnegative")

    run_root = Path(args.run_root).expanduser().resolve()
    log_path = run_root / "download_logs" / "download_log.csv"
    history_path = log_path.with_name("download_history.csv")
    existing = {record_id_for(row): row for row in read_csv(log_path)}
    candidate_path = run_root / "candidate_table.csv"
    if not candidate_path.is_file():
        parser.error("candidate_table.csv not found; use an existing prepared run directory")
    candidates = read_csv(candidate_path)
    candidate_ids = [record_id_for(row) for row in candidates]
    if len(candidate_ids) != len(set(candidate_ids)):
        parser.error("Duplicate record_id values; resolve duplicate candidates before downloading")
    seen: set[str] = set()
    processed_count = 0
    for row in candidates:
        record_id = record_id_for(row)
        if record_id in seen:
            continue
        seen.add(record_id)
        previous = existing.get(record_id)
        if args.retry_failed and previous is None:
            continue
        if previous is not None and previous.get("download_status") == "excluded" and clean(row.get("exclusion_reason_if_any")):
            continue
        _, evidence = previous_pdf(previous, run_root)
        if evidence is not None and evidence.valid and not clean(row.get("exclusion_reason_if_any")):
            continue
        current = download_record(row, previous, args, run_root)
        save_history(history_path, previous, current)
        existing[record_id] = current
        write_log(log_path, list(existing.values()))
        copy_alias(log_path, log_path.with_name("下载日志.csv"))
        copy_alias(log_path, run_root / "下载日志.csv")
        processed_count += 1

    write_log(log_path, list(existing.values()))
    copy_alias(log_path, log_path.with_name("下载日志.csv"))
    copy_alias(log_path, run_root / "下载日志.csv")
    print(f"Processed {processed_count} records; retained {len(existing)} current log rows at {log_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
