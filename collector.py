#!/usr/bin/env python3
"""
TEMPORAL SIGNAL RECEIVER / REMIX

A falsifiable detector for one private, fixed analysis anchor.
The target key is compared with 127 pre-committed placebo keys every day.
Only rare target ranks produce a signal and a seven-number decode.

No third-party packages are required.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import smtplib
import ssl
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Iterable

PROTOCOL_VERSION = "176248-remix-v1"
TARGET_KEY = "176248"
CONTROL_COUNT = 127
DETECT_SAMPLES = 256
DECODE_SAMPLES_PER_SYMBOL = 16
RESULTS = Path("data/results.json")
NOTIFICATION_STATE = Path("data/notification-state.json")
PROTOCOL_FILE = Path("protocol.json")
JST = timezone(timedelta(hours=9))
USER_AGENT = "TemporalSignalReceiver/remix-v1 (+GitHub Actions)"


# ---------------------------------------------------------------------------
# Network helpers
# ---------------------------------------------------------------------------

def request_bytes(url: str, timeout: int = 12) -> bytes:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json,text/plain,*/*"},
    )
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return response.read()
        except Exception as exc:  # network failures must not abort the daily record
            last_error = exc
            if attempt == 0:
                time.sleep(0.8)
    raise RuntimeError(f"fetch failed: {url}: {last_error}")


def parse_json(raw: bytes) -> Any:
    return json.loads(raw.decode("utf-8"))


def compact_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


@dataclass
class SourceResult:
    name: str
    ok: bool
    packet: bytes
    detail: dict[str, Any]
    error: str = ""

    def public_status(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "bytes": len(self.packet),
            "detail": self.detail,
            "error": self.error[:180],
        }


def safe_source(name: str, loader) -> SourceResult:
    try:
        packet, detail = loader()
        return SourceResult(name=name, ok=bool(packet), packet=packet, detail=detail)
    except Exception as exc:
        print(f"[WARN] {name}: {exc}")
        return SourceResult(name=name, ok=False, packet=b"", detail={}, error=str(exc))


# ---------------------------------------------------------------------------
# Public-data sources
# ---------------------------------------------------------------------------

def load_random_org() -> tuple[bytes, dict[str, Any]]:
    count = 384
    url = (
        "https://www.random.org/integers/"
        f"?num={count}&min=0&max=255&col=1&base=10&format=plain&rnd=new"
    )
    values = [int(x) for x in request_bytes(url).decode("ascii").split()]
    values = [x for x in values if 0 <= x <= 255]
    if len(values) < 64:
        raise ValueError("insufficient random.org values")
    return bytes(values), {"samples": len(values)}


def load_anu_qrng() -> tuple[bytes, dict[str, Any]]:
    count = 384
    url = f"https://qrng.anu.edu.au/API/jsonI.php?length={count}&type=uint8"
    payload = parse_json(request_bytes(url))
    values = payload.get("data", []) if payload.get("success") else []
    values = [int(x) for x in values if 0 <= int(x) <= 255]
    if len(values) < 64:
        raise ValueError("insufficient ANU QRNG values")
    return bytes(values), {"samples": len(values)}


def load_drand() -> tuple[bytes, dict[str, Any]]:
    errors: list[str] = []
    for endpoint in (
        "https://api.drand.sh/public/latest",
        "https://api2.drand.sh/public/latest",
        "https://drand.cloudflare.com/public/latest",
    ):
        try:
            payload = parse_json(request_bytes(endpoint))
            randomness = str(payload.get("randomness", ""))
            if len(randomness) < 32:
                raise ValueError("missing drand randomness")
            packet = bytes.fromhex(randomness)
            return packet, {
                "round": payload.get("round"),
                "randomness": randomness,
                "relay": endpoint.split("/")[2],
            }
        except Exception as exc:
            errors.append(str(exc))
    raise RuntimeError("; ".join(errors))


def load_bitcoin() -> tuple[bytes, dict[str, Any]]:
    try:
        payload = parse_json(request_bytes("https://blockchain.info/latestblock"))
        block_hash = str(payload.get("hash", ""))
        height = payload.get("height")
        provider = "blockchain.info"
    except Exception:
        block_hash = request_bytes("https://blockstream.info/api/blocks/tip/hash").decode("ascii").strip()
        height = None
        provider = "blockstream.info"
    if len(block_hash) < 32:
        raise ValueError("missing latest block hash")
    try:
        packet = bytes.fromhex(block_hash)
    except ValueError:
        packet = block_hash.encode("ascii", "ignore")
    return packet, {"height": height, "hash": block_hash, "provider": provider}


NOAA_MAG_URLS = (
    "https://services.swpc.noaa.gov/json/rtsw/rtsw_mag_1m.json",
    "https://noaa-swpc-pds.s3.amazonaws.com/json/rtsw/rtsw_mag_1m.json",
)
NOAA_WIND_URLS = (
    "https://services.swpc.noaa.gov/json/rtsw/rtsw_wind_1m.json",
    "https://noaa-swpc-pds.s3.amazonaws.com/json/rtsw/rtsw_wind_1m.json",
)


def request_json_fallback(urls: Iterable[str]) -> tuple[Any, str]:
    errors: list[str] = []
    for url in urls:
        try:
            return parse_json(request_bytes(url, timeout=18)), url
        except Exception as exc:
            errors.append(str(exc))
    raise RuntimeError("; ".join(errors))


def _last_valid_objects(rows: Any, count: int = 90) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    clean: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("time_tag"):
            continue
        if any(value not in (None, "") for key, value in row.items() if key != "time_tag"):
            clean.append(row)
    active = [
        row for row in clean
        if row.get("active") in (True, 1, "true", "True", "1")
    ]
    usable = active if len(active) >= 10 else clean
    usable.sort(key=lambda row: str(row.get("time_tag", "")))
    return usable[-count:]


def _float_values(rows: Iterable[dict[str, Any]], key: str) -> list[float]:
    values: list[float] = []
    for row in rows:
        try:
            value = row.get(key)
            if value not in (None, ""):
                values.append(float(value))
        except (ValueError, TypeError):
            pass
    return values


def load_noaa_mag() -> tuple[bytes, dict[str, Any]]:
    rows, endpoint = request_json_fallback(NOAA_MAG_URLS)
    selected = _last_valid_objects(rows)
    if len(selected) < 10:
        raise ValueError("insufficient NOAA RTSW magnetic-field rows")
    bz_values = _float_values(selected, "bz_gsm")
    detail = {
        "rows": len(selected),
        "bz_mean": round(sum(bz_values) / len(bz_values), 4) if bz_values else None,
        "last_time": selected[-1].get("time_tag"),
        "source": selected[-1].get("source"),
        "endpoint": endpoint.split("/")[2],
    }
    return compact_json(selected), detail


def load_noaa_plasma() -> tuple[bytes, dict[str, Any]]:
    rows, endpoint = request_json_fallback(NOAA_WIND_URLS)
    selected = _last_valid_objects(rows)
    if len(selected) < 10:
        raise ValueError("insufficient NOAA RTSW solar-wind rows")
    density = _float_values(selected, "proton_density")
    speed = _float_values(selected, "proton_speed")
    detail = {
        "rows": len(selected),
        "density_mean": round(sum(density) / len(density), 4) if density else None,
        "speed_mean": round(sum(speed) / len(speed), 2) if speed else None,
        "last_time": selected[-1].get("time_tag"),
        "source": selected[-1].get("source"),
        "endpoint": endpoint.split("/")[2],
    }
    return compact_json(selected), detail


def collect_sources() -> tuple[list[SourceResult], list[SourceResult]]:
    # Fetch independent sources concurrently so one slow endpoint does not
    # multiply the collector runtime. The returned order remains fixed.
    channel_a_specs = [
        ("random.org", load_random_org),
        ("ANU QRNG", load_anu_qrng),
        ("drand", load_drand),
        ("Bitcoin latest block", load_bitcoin),
    ]
    channel_b_specs = [
        ("NOAA RTSW magnetic field", load_noaa_mag),
        ("NOAA RTSW solar wind", load_noaa_plasma),
    ]
    specs = channel_a_specs + channel_b_specs
    with ThreadPoolExecutor(max_workers=len(specs)) as executor:
        futures = [executor.submit(safe_source, name, loader) for name, loader in specs]
        resolved = [future.result() for future in futures]
    return resolved[: len(channel_a_specs)], resolved[len(channel_a_specs) :]


def build_channel_packet(label: str, day: str, sources: Iterable[SourceResult]) -> bytes:
    parts = [f"{PROTOCOL_VERSION}|{label}|{day}".encode("utf-8")]
    for source in sources:
        if not source.ok:
            continue
        parts.extend(
            [
                b"\x1e",
                source.name.encode("utf-8"),
                b"\x1f",
                len(source.packet).to_bytes(8, "big"),
                source.packet,
            ]
        )
    return b"".join(parts)


# ---------------------------------------------------------------------------
# Fixed placebo keys
# ---------------------------------------------------------------------------

def generate_control_keys() -> list[str]:
    seed = b"FG829|176248|PLACEBO-CONTROLS|v1"
    keys: list[str] = []
    counter = 0
    while len(keys) < CONTROL_COUNT:
        digest = hashlib.sha256(seed + counter.to_bytes(4, "big")).digest()
        candidate = str(100000 + int.from_bytes(digest[:8], "big") % 900000)
        if candidate != TARGET_KEY and candidate not in keys:
            keys.append(candidate)
        counter += 1
    return keys


CONTROL_KEYS = generate_control_keys()
ALL_KEYS = [TARGET_KEY, *CONTROL_KEYS]


# ---------------------------------------------------------------------------
# Key-specific detector
# ---------------------------------------------------------------------------

def keyed_digest(key: str, message: bytes) -> bytes:
    return hmac.new(key.encode("ascii"), message, hashlib.sha256).digest()


def channel_score(packet: bytes, key: str, channel: str) -> dict[str, Any]:
    if len(packet) < 32:
        return {"matches": 0, "samples": 0, "z": 0.0}

    matches = 0
    for index in range(DETECT_SAMPLES):
        digest = keyed_digest(
            key, f"{PROTOCOL_VERSION}|detect|{channel}|{index}".encode("utf-8")
        )
        position = int.from_bytes(digest[:8], "big") % len(packet)
        expected = digest[8] & 0b11
        if packet[position] & 0b11 == expected:
            matches += 1

    probability = 0.25
    expected_count = DETECT_SAMPLES * probability
    std = math.sqrt(DETECT_SAMPLES * probability * (1.0 - probability))
    z_value = (matches - expected_count) / std
    return {"matches": matches, "samples": DETECT_SAMPLES, "z": round(z_value, 6)}


def combined_score(score_a: dict[str, Any], score_b: dict[str, Any]) -> float:
    zs = [s["z"] for s in (score_a, score_b) if s["samples"]]
    if not zs:
        return 0.0
    return round(sum(zs) / math.sqrt(len(zs)), 6)


def rank_map(values: dict[str, float]) -> dict[str, int]:
    ordered = sorted(values.items(), key=lambda item: (-item[1], item[0]))
    return {key: rank for rank, (key, _) in enumerate(ordered, start=1)}


def compute_detection(packet_a: bytes, packet_b: bytes) -> dict[str, Any]:
    per_key: dict[str, dict[str, Any]] = {}
    for key in ALL_KEYS:
        score_a = channel_score(packet_a, key, "A")
        score_b = channel_score(packet_b, key, "B")
        per_key[key] = {
            "a": score_a,
            "b": score_b,
            "combined": combined_score(score_a, score_b),
        }

    ranks_a = rank_map({key: value["a"]["z"] for key, value in per_key.items()})
    ranks_b = rank_map({key: value["b"]["z"] for key, value in per_key.items()})
    ranks_combined = rank_map({key: value["combined"] for key, value in per_key.items()})

    target = per_key[TARGET_KEY]
    total = len(ALL_KEYS)
    combined_rank = ranks_combined[TARGET_KEY]
    percentile = 100.0 * (total - combined_rank) / (total - 1)

    top_controls = sorted(
        (
            {
                "key_hash": hashlib.sha256(key.encode("ascii")).hexdigest()[:10],
                "score": value["combined"],
                "rank": ranks_combined[key],
            }
            for key, value in per_key.items()
            if key != TARGET_KEY
        ),
        key=lambda item: item["rank"],
    )[:5]

    return {
        "target": {
            "score_a": target["a"]["z"],
            "score_b": target["b"]["z"],
            "score_combined": target["combined"],
            "matches_a": target["a"]["matches"],
            "matches_b": target["b"]["matches"],
            "rank_a": ranks_a[TARGET_KEY],
            "rank_b": ranks_b[TARGET_KEY],
            "rank_combined": combined_rank,
            "percentile": round(percentile, 3),
        },
        "control_count": CONTROL_COUNT,
        "top_controls": top_controls,
    }


def signal_rank(target: dict[str, Any], healthy_channels: int) -> str:
    # A signal requires both independent channels. This prevents a single-source
    # outage from manufacturing a high rank.
    if healthy_channels < 2:
        return "-"

    rank = int(target["rank_combined"])
    rank_a = int(target["rank_a"])
    rank_b = int(target["rank_b"])

    if rank == 1 and rank_a <= 7 and rank_b <= 7:
        return "SS"
    if rank == 1:
        return "S"
    if rank == 2:
        return "A"
    if rank <= 4:
        return "B"
    if rank <= 8:
        return "C"
    return "-"


# ---------------------------------------------------------------------------
# Seven-symbol decoder
# ---------------------------------------------------------------------------

def symbol_alignment(packet_a: bytes, packet_b: bytes, lane: int, number: int) -> tuple[int, str]:
    packets = [packet_a, packet_b]
    matches = 0
    transcript = bytearray()
    for sample in range(DECODE_SAMPLES_PER_SYMBOL):
        digest = keyed_digest(
            TARGET_KEY,
            f"{PROTOCOL_VERSION}|decode|{lane}|{number}|{sample}".encode("utf-8"),
        )
        channel_index = digest[0] & 1
        packet = packets[channel_index]
        position = int.from_bytes(digest[1:9], "big") % len(packet)
        value = packet[position]
        expected = digest[9] & 0b111
        transcript.extend((channel_index, value, expected))
        if value & 0b111 == expected:
            matches += 1
    tie = hashlib.sha256(bytes(transcript)).hexdigest()
    return matches, tie


def decode_numbers(packet_a: bytes, packet_b: bytes) -> tuple[list[int], list[dict[str, Any]]]:
    chosen: list[int] = []
    evidence: list[dict[str, Any]] = []

    for lane in range(7):
        candidates: list[tuple[int, str, int]] = []
        for number in range(1, 38):
            matches, tie = symbol_alignment(packet_a, packet_b, lane, number)
            candidates.append((matches, tie, number))
        candidates.sort(key=lambda item: (-item[0], item[1], item[2]))

        selected = next(item for item in candidates if item[2] not in chosen)
        chosen.append(selected[2])
        runner_up = next(item for item in candidates if item[2] not in chosen)
        evidence.append(
            {
                "lane": lane + 1,
                "number": selected[2],
                "matches": selected[0],
                "margin": selected[0] - runner_up[0],
            }
        )

    return sorted(chosen), evidence


# ---------------------------------------------------------------------------
# Persistence / notification
# ---------------------------------------------------------------------------

def load_results() -> list[dict[str, Any]]:
    if not RESULTS.exists():
        return []
    value = json.loads(RESULTS.read_text(encoding="utf-8"))
    return value if isinstance(value, list) else []


def save_results(results: list[dict[str, Any]]) -> None:
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    temp = RESULTS.with_suffix(".json.tmp")
    temp.write_text(
        json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temp.replace(RESULTS)


def load_notification_state() -> dict[str, Any]:
    if not NOTIFICATION_STATE.exists():
        return {}
    try:
        value = json.loads(NOTIFICATION_STATE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def save_notification_state(state: dict[str, Any]) -> None:
    NOTIFICATION_STATE.parent.mkdir(parents=True, exist_ok=True)
    temp = NOTIFICATION_STATE.with_suffix(".json.tmp")
    temp.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temp.replace(NOTIFICATION_STATE)


def send_notification(entry: dict[str, Any]) -> None:
    topic = os.getenv("NTFY_TOPIC", "").strip()
    if not topic:
        return
    rank = entry["rank"]
    if rank not in {"SS", "S", "A"} and not entry.get("friday_lock"):
        return
    numbers = " ".join(f"{n:02d}" for n in entry.get("numbers", [])) or "NO DECODE"
    target = entry["detection"]["target"]
    body = (
        f"{entry['date']} JST\n"
        f"RANK {rank} · target rank {target['rank_combined']}/128\n"
        f"A={target['score_a']:.3f} B={target['score_b']:.3f}\n"
        f"NUM7 {numbers}"
    )
    request = urllib.request.Request(
        f"https://ntfy.sh/{topic}",
        data=body.encode("utf-8"),
        headers={
            "Title": f"[{rank}] TEMPORAL SIGNAL",
            "Tags": "satellite,signal_strength",
            "Priority": "high" if rank in {"SS", "S"} else "default",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=12):
            print("[NOTIFY] sent")
    except Exception as exc:
        print(f"[WARN] notification failed: {exc}")


def build_email_message(entry: dict[str, Any], sender: str, recipient: str) -> EmailMessage:
    rank = entry["rank"]
    target = entry["detection"]["target"]
    numbers = " ".join(f"{number:02d}" for number in entry.get("numbers", []))
    numbers = numbers or "NO DECODE"
    friday_text = "あり" if entry.get("friday_lock") else "なし"

    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = f"[{rank}] 異常信号を検知しました ({entry['date']} JST)"

    lines = [
        "TEMPORAL SIGNAL RECEIVER が異常信号を検知しました。",
        "",
        f"日付: {entry['date']} JST",
        f"ランク: {rank}",
        f"総合順位: {target['rank_combined']} / 128",
        f"Channel A: {target['score_a']:.3f}",
        f"Channel B: {target['score_b']:.3f}",
        f"復元された7数: {numbers}",
        f"FRIDAY LOCK: {friday_text}",
    ]
    pages_url = os.getenv("PAGES_URL", "").strip()
    if pages_url:
        lines.extend(["", f"公開ページ: {pages_url}"])
    lines.extend(["", "このメールはGitHub Actionsから自動送信されています。"])
    message.set_content("\n".join(lines))
    return message


def smtp_settings() -> tuple[str, int, str, str, str, str] | None:
    host = os.getenv("SMTP_HOST", "smtp.gmail.com").strip()
    username = os.getenv("SMTP_USERNAME", "").strip()
    password = os.getenv("SMTP_PASSWORD", "")
    recipient = os.getenv("ALERT_EMAIL_TO", "").strip()
    sender = os.getenv("ALERT_EMAIL_FROM", username).strip()
    try:
        port = int(os.getenv("SMTP_PORT", "465"))
    except ValueError:
        port = 465

    if not all((host, username, password, sender, recipient)):
        print("[EMAIL] skipped: SMTP secrets are not configured")
        return None
    return host, port, username, password, sender, recipient


def deliver_email(message: EmailMessage, settings: tuple[str, int, str, str, str, str]) -> bool:
    host, port, username, password, _sender, _recipient = settings

    context = ssl.create_default_context()
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=20, context=context) as smtp:
                smtp.login(username, password)
                smtp.send_message(message)
        else:
            with smtplib.SMTP(host, port, timeout=20) as smtp:
                smtp.ehlo()
                smtp.starttls(context=context)
                smtp.ehlo()
                smtp.login(username, password)
                smtp.send_message(message)
        print("[EMAIL] sent")
        return True
    except Exception as exc:
        print(f"[WARN] email notification failed: {exc}")
        return False


def send_email_notification(entry: dict[str, Any]) -> bool:
    """Send an email for every healthy signal rank (SS/S/A/B/C)."""
    if entry.get("quality") != "OK" or not entry.get("signal"):
        return False
    settings = smtp_settings()
    if settings is None:
        return False
    _host, _port, _username, _password, sender, recipient = settings
    return deliver_email(build_email_message(entry, sender, recipient), settings)


def send_test_email() -> bool:
    settings = smtp_settings()
    if settings is None:
        return False
    _host, _port, _username, _password, sender, recipient = settings
    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = "[TEST] 異常検知メール設定確認"
    message.set_content(
        "GitHub Actionsからテストメールを送信しました。\n"
        "このメールを受信できれば、異常検知時の通知設定は有効です。"
    )
    return deliver_email(message, settings)


def send_email_if_due(entry: dict[str, Any]) -> None:
    """Retry failed mail on later workflow runs without sending duplicates."""
    if entry.get("quality") != "OK" or not entry.get("signal"):
        return
    state = load_notification_state()
    if state.get("email_last_sent_date") == entry.get("date"):
        print("[EMAIL] already sent for this signal date")
        return
    if send_email_notification(entry):
        state.update(
            {
                "email_last_sent_date": entry.get("date"),
                "email_last_sent_rank": entry.get("rank"),
                "email_last_sent_at_jst": datetime.now(JST).isoformat(timespec="seconds"),
            }
        )
        save_notification_state(state)


def write_protocol_file() -> None:
    protocol = {
        "protocol_version": PROTOCOL_VERSION,
        "target_key": TARGET_KEY,
        "control_count": CONTROL_COUNT,
        "control_keys": CONTROL_KEYS,
        "detection_samples_per_channel": DETECT_SAMPLES,
        "decode_samples_per_symbol": DECODE_SAMPLES_PER_SYMBOL,
        "rank_rules": {
            "SS": "combined rank 1 and each channel rank <= 7",
            "S": "combined rank 1",
            "A": "combined rank 2",
            "B": "combined rank 3-4",
            "C": "combined rank 5-8",
            "-": "combined rank 9-128 or degraded collection",
        },
        "expected_signal_rate_under_null": "8/128 = 6.25% when both channels are healthy",
        "generated_at": "pre-committed by source code; control keys never rotate",
    }
    PROTOCOL_FILE.write_text(
        json.dumps(protocol, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def observation_strength(entry: dict[str, Any]) -> tuple[int, int, int]:
    sources = [
        *(entry.get("sources", {}).get("channel_a", []) or []),
        *(entry.get("sources", {}).get("channel_b", []) or []),
    ]
    online = sum(bool(source.get("ok")) for source in sources if isinstance(source, dict))
    packet_bytes = int(entry.get("packets", {}).get("a_bytes", 0) or 0) + int(
        entry.get("packets", {}).get("b_bytes", 0) or 0
    )
    return int(entry.get("healthy_channels", 0) or 0), online, packet_bytes


def run() -> int:
    now_jst = datetime.now(JST)
    day = now_jst.strftime("%Y-%m-%d")
    collected_at = now_jst.isoformat(timespec="seconds")
    print(f"[receiver] collecting {day} JST")

    write_protocol_file()
    results = load_results()
    existing_index = next(
        (index for index, item in enumerate(results) if item.get("date") == day), None
    )
    existing = results[existing_index] if existing_index is not None else None
    if existing and existing.get("quality") == "OK":
        print("[receiver] an OK record already exists for this JST date")
        send_email_if_due(existing)
        return 0

    channel_a_sources, channel_b_sources = collect_sources()
    packet_a = build_channel_packet("A", day, channel_a_sources)
    packet_b = build_channel_packet("B", day, channel_b_sources)

    healthy_a = sum(source.ok for source in channel_a_sources) >= 2 and len(packet_a) >= 96
    healthy_b = sum(source.ok for source in channel_b_sources) >= 1 and len(packet_b) >= 256
    healthy_channels = int(healthy_a) + int(healthy_b)

    detection = compute_detection(packet_a, packet_b)
    rank = signal_rank(detection["target"], healthy_channels)
    numbers: list[int] = []
    decode_evidence: list[dict[str, Any]] = []
    if rank != "-" and healthy_channels == 2:
        numbers, decode_evidence = decode_numbers(packet_a, packet_b)

    entry = {
        "date": day,
        "collected_at_jst": collected_at,
        "protocol_version": PROTOCOL_VERSION,
        "target_key": TARGET_KEY,
        "quality": "OK" if healthy_channels == 2 else "DEGRADED",
        "healthy_channels": healthy_channels,
        "rank": rank,
        "signal": rank != "-",
        "friday_lock": rank != "-" and now_jst.weekday() == 4,
        "numbers": numbers,
        "decode_evidence": decode_evidence,
        "detection": detection,
        "packets": {
            "a_sha256": hashlib.sha256(packet_a).hexdigest(),
            "b_sha256": hashlib.sha256(packet_b).hexdigest(),
            "a_bytes": len(packet_a),
            "b_bytes": len(packet_b),
        },
        "sources": {
            "channel_a": [source.public_status() for source in channel_a_sources],
            "channel_b": [source.public_status() for source in channel_b_sources],
        },
    }

    saved_entry = entry
    changed = False
    if existing_index is None:
        results.append(entry)
        changed = True
    elif observation_strength(entry) > observation_strength(existing):
        entry["supersedes_degraded_collected_at_jst"] = existing.get("collected_at_jst")
        results[existing_index] = entry
        changed = True
        print("[receiver] replaced the same-day degraded record with a stronger observation")
    else:
        saved_entry = existing
        print("[receiver] kept the stronger existing degraded record")

    if changed:
        save_results(results)
        send_notification(entry)
        send_email_if_due(entry)

    print(
        f"[RESULT] quality={saved_entry['quality']} rank={saved_entry['rank']} "
        f"target={saved_entry['detection']['target']['rank_combined']}/128 "
        f"numbers={saved_entry.get('numbers', [])}"
    )
    return 0 if saved_entry.get("quality") == "OK" else 2


if __name__ == "__main__":
    sys.exit(run())
