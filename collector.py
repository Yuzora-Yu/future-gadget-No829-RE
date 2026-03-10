#!/usr/bin/env python3
"""
Temporal Signal Receiver RE — collector.py
future-gadget-No829-RE

Analysis key : 176248 (embedded)
Ntfy topic   : tsr-3ede456f7bc9b1a5 (derived from key)

Rank logic:
  SS : Channel A AND B, both σ≥3.0
  S  : Channel A AND B, one  σ≥3.0
  A  : Channel A AND B, both σ≥2.0
  B  : Channel A XOR B,      σ≥3.0  (single channel only)
  C  : Channel A XOR B,      σ≥2.0  (single channel only)
  -  : no anomaly  → no lotto, no notify

Notify : SS / S / A only
Lotto7 : C and above, derived from A+B+date+key
Baseline: anomaly days excluded
"""

import json
import hashlib
import hmac as hmac_mod
import random
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ── Constants ────────────────────────────────────────────────────────────────
_KEY        = "176248"
_NTFY_TOPIC = "tsr-3ede456f7bc9b1a5"
RESULTS     = Path("data/results.json")
BL_WINDOW   = 14      # baseline window (anomaly-free days)
THR_LO      = 2.0     # lower threshold
THR_HI      = 3.0     # upper threshold

# ── Word pool (key-seeded) ───────────────────────────────────────────────────
_seed = int(hashlib.sha256(_KEY.encode()).hexdigest(), 16) % (2**32)
_rng  = random.Random(_seed)
_WORDS = [
    "WAIT","MOVE","NORTH","SOUTH","EAST","WEST","STILL","READY",
    "SOON","DEEP","HIGH","DARK","LIGHT","OPEN","CLOSE","SAFE",
    "ALERT","CALM","HOLD","TURN","RISE","FALL","CARRY","LEAVE",
    "RETURN","WATCH","LISTEN","TRUST","DOUBT","SEEK","FIND","LOSE",
    "BEGIN","END","CYCLE","GATE","BRIDGE","ROOT","WAVE","ECHO",
    "PAUSE","SHIFT","MARK","FOLD","CROSS","BIND","TRACE","SPLIT",
]
WORD_POOL = _WORDS[:]
_rng.shuffle(WORD_POOL)

WORD_JA = {
    "WAIT":"待機せよ","MOVE":"移動せよ","NORTH":"北","SOUTH":"南",
    "EAST":"東","WEST":"西","STILL":"静止","READY":"準備完了",
    "SOON":"間もなく","DEEP":"深部","HIGH":"上昇","DARK":"暗転",
    "LIGHT":"光","OPEN":"開放","CLOSE":"閉鎖","SAFE":"安全",
    "ALERT":"警戒","CALM":"静穏","HOLD":"保留","TURN":"転換点",
    "RISE":"上昇せよ","FALL":"下降","CARRY":"継続せよ","LEAVE":"離脱せよ",
    "RETURN":"帰還せよ","WATCH":"監視せよ","LISTEN":"受信中","TRUST":"信頼せよ",
    "DOUBT":"疑念","SEEK":"探索せよ","FIND":"発見","LOSE":"喪失",
    "BEGIN":"開始","END":"終端","CYCLE":"周期","GATE":"ゲート",
    "BRIDGE":"架橋","ROOT":"原点","WAVE":"波動","ECHO":"残響",
    "PAUSE":"一時停止","SHIFT":"シフト","MARK":"記録せよ","FOLD":"収束",
    "CROSS":"交差点","BIND":"束縛","TRACE":"追跡せよ","SPLIT":"分岐",
}


# ── Fetchers ─────────────────────────────────────────────────────────────────
def fetch_random_org(count=512):
    url = (f"https://www.random.org/integers/"
           f"?num={count}&min=0&max=255&col=1&base=10&format=plain&rnd=new")
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return [int(l.strip()) for l in r.read().decode().strip().split("\n") if l.strip()]
    except Exception as e:
        print(f"[WARN] random.org: {e}")
        return []


def fetch_anu_qrng(count=512):
    url = f"https://qrng.anu.edu.au/API/jsonI.php?length={count}&type=uint8"
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            d = json.loads(r.read().decode())
            if d.get("success"):
                return d["data"]
    except Exception as e:
        print(f"[WARN] ANU QRNG: {e}")
    return []


def fetch_solar_wind():
    result = {"bz": None, "density": None, "speed": None, "temp": None}
    try:
        url = "https://services.swpc.noaa.gov/products/solar-wind/mag-1-day.json"
        with urllib.request.urlopen(url, timeout=15) as r:
            rows = json.loads(r.read().decode())
            bz_vals = []
            for row in rows[1:][-60:]:
                try:
                    bz_vals.append(float(row[3]))
                except (ValueError, IndexError):
                    pass
            if bz_vals:
                result["bz"] = round(sum(bz_vals) / len(bz_vals), 4)
                print(f"[Solar] Bz={result['bz']:.3f} nT ({len(bz_vals)} samples)")
    except Exception as e:
        print(f"[WARN] Solar mag: {e}")

    try:
        url = "https://services.swpc.noaa.gov/products/solar-wind/plasma-1-day.json"
        with urllib.request.urlopen(url, timeout=15) as r:
            rows = json.loads(r.read().decode())
            dens, speeds, temps = [], [], []
            for row in rows[1:][-60:]:
                try:
                    dens.append(float(row[1]))
                    speeds.append(float(row[2]))
                    temps.append(float(row[3]))
                except (ValueError, IndexError):
                    pass
            if dens:
                result["density"] = round(sum(dens)   / len(dens),   4)
                result["speed"]   = round(sum(speeds) / len(speeds), 2)
                result["temp"]    = round(sum(temps)  / len(temps),  1)
                print(f"[Solar] density={result['density']:.3f}  speed={result['speed']:.1f}")
    except Exception as e:
        print(f"[WARN] Solar plasma: {e}")

    return result


def fetch_btc_hash():
    try:
        with urllib.request.urlopen("https://blockchain.info/latestblock", timeout=10) as r:
            return json.loads(r.read().decode()).get("hash", "")
    except Exception as e:
        print(f"[WARN] BTC: {e}")
        return ""


# ── Signal processing ─────────────────────────────────────────────────────────
def key_filter(data: list) -> list:
    if not data:
        return []
    kb = _KEY.encode()
    return [v for i, v in enumerate(data)
            if int(hmac_mod.new(kb, f"{i}:{v}".encode(), hashlib.sha256).hexdigest()[0], 16) < 8]


def stats(values):
    if len(values) < 2:
        return 0.0, 1.0
    mean = sum(values) / len(values)
    std  = (sum((x - mean)**2 for x in values) / len(values)) ** 0.5
    return mean, max(std, 0.001)


def sigma_against_baseline(score: float, history: list, field: str) -> float:
    """Compute σ using only non-anomaly days for baseline."""
    quiet = [d[field] for d in history
             if d.get(field) is not None and not d.get("anomaly", False)]
    quiet = quiet[-BL_WINDOW:]
    if len(quiet) < 3:
        return 0.0
    mean, std = stats(quiet)
    return round(abs(score - mean) / std, 4)


def solar_composite(sw: dict, history: list) -> float:
    """Weighted composite σ for solar wind channel."""
    scores = []
    if sw["bz"] is not None:
        s = sigma_against_baseline(sw["bz"], history, "solar_bz")
        scores.append(s * (1.5 if sw["bz"] < 0 else 1.0))
    if sw["density"] is not None:
        scores.append(sigma_against_baseline(sw["density"], history, "solar_density"))
    if sw["speed"] is not None:
        scores.append(sigma_against_baseline(sw["speed"], history, "solar_speed"))
    return round(sum(scores) / len(scores), 4) if scores else 0.0


# ── Rank logic ────────────────────────────────────────────────────────────────
def compute_rank(sigma_a: float, sigma_b: float) -> str:
    """
    SS : A AND B, both ≥3.0
    S  : A AND B, one  ≥3.0
    A  : A AND B, both ≥2.0
    B  : single channel ≥3.0
    C  : single channel ≥2.0
    -  : none
    """
    a_lo = sigma_a >= THR_LO
    a_hi = sigma_a >= THR_HI
    b_lo = sigma_b >= THR_LO
    b_hi = sigma_b >= THR_HI
    both_lo = a_lo and b_lo

    if both_lo and a_hi and b_hi:
        return "SS"
    if both_lo and (a_hi or b_hi):
        return "S"
    if both_lo:
        return "A"
    if (a_hi and not b_lo) or (b_hi and not a_lo):
        return "B"
    if (a_lo and not b_lo) or (b_lo and not a_lo):
        return "C"
    return "-"


# ── Lotto7 ───────────────────────────────────────────────────────────────────
def make_lotto(sigma_a: float, sigma_b: float,
               filtered_mean: float, solar_bz, date_str: str) -> list:
    """
    Derive 7 unique numbers (1-37) from both channels + date + key.
    Solar Bz and RNG filtered_mean both contribute to the entropy.
    """
    bz_str = f"{solar_bz:.4f}" if solar_bz is not None else "none"
    composite = (
        f"{date_str}:{_KEY}:"
        f"a={filtered_mean:.4f}:sa={sigma_a:.4f}:"
        f"bz={bz_str}:sb={sigma_b:.4f}"
    )
    h = hmac_mod.new(_KEY.encode(), composite.encode(), hashlib.sha256).hexdigest()
    h2 = h + hashlib.sha256(h.encode()).hexdigest()

    # offset by combined sigma (higher anomaly → different window)
    offset = int(min((sigma_a + sigma_b) * 2, 12))
    nums, cursor = set(), offset
    while len(nums) < 7 and cursor < len(h2) - 1:
        nums.add((int(h2[cursor:cursor+2], 16) + offset) % 37 + 1)
        cursor += 2
    return sorted(nums)


# ── Word signal ───────────────────────────────────────────────────────────────
def make_word(rank: str, date_str: str) -> tuple:
    h = hashlib.sha256(f"{date_str}:{rank}:{_KEY}".encode()).hexdigest()
    word_en = WORD_POOL[int(h[:8], 16) % len(WORD_POOL)]
    return word_en, WORD_JA.get(word_en, word_en)


# ── Notification ──────────────────────────────────────────────────────────────
def notify(date_str, rank, sigma_a, sigma_b, word_en, word_ja, lotto):
    nums = " ".join(f"{n:02d}" for n in lotto)
    rank_label = {"SS": "🔴 SS", "S": "🟠 S", "A": "🟡 A"}.get(rank, rank)
    msg = (f"{rank_label} [{date_str}]\n"
           f"A={sigma_a:.3f}  B={sigma_b:.3f}\n"
           f"シグナル: {word_ja}（{word_en}）\n"
           f"LOTTO: {nums}")
    try:
        req = urllib.request.Request(
            f"https://ntfy.sh/{_NTFY_TOPIC}",
            data=msg.encode("utf-8"),
            headers={
                "Title": f"[{rank}] {word_ja} — シグナル検出",
                "Priority": "high" if rank in ("SS", "S") else "default",
                "Tags": "signal_strength_bars",
            },
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=10):
            print(f"[NOTIFY] {rank} sent.")
    except Exception as e:
        print(f"[WARN] ntfy: {e}")


# ── Persistence ───────────────────────────────────────────────────────────────
def load():
    if RESULTS.exists():
        with open(RESULTS) as f:
            return json.load(f)
    return []


def save(data):
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    with open(RESULTS, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ── Main ──────────────────────────────────────────────────────────────────────
def run():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    print(f"[RE] {today}")

    history = load()
    if any(d["date"] == today for d in history):
        print("[RE] Already collected today.")
        return

    # ── Fetch ──
    rand_data = fetch_random_org(512)
    anu_data  = fetch_anu_qrng(512)
    solar     = fetch_solar_wind()
    btc_hash  = fetch_btc_hash()

    # ── Channel A: Quantum RNG ──
    combined = rand_data + anu_data
    filtered = key_filter(combined) if combined else []
    fm = round(sum(filtered) / len(filtered), 4) if filtered else 0.0
    sigma_a = sigma_against_baseline(fm, history, "filtered_mean") if filtered else 0.0

    # ── Channel B: Solar wind ──
    sigma_b = solar_composite(solar, history)

    # ── Rank ──
    rank     = compute_rank(sigma_a, sigma_b)
    anomaly  = rank != "-"

    # ── Signal outputs (only when rank ≥ C) ──
    lotto              = make_lotto(sigma_a, sigma_b, fm, solar["bz"], today) if anomaly else []
    word_en, word_ja   = make_word(rank, today) if anomaly else ("", "")

    entry = {
        "date":          today,
        # Channel A
        "filtered_mean": fm,
        "sample_size":   len(combined),
        "filtered_size": len(filtered),
        "sigma_a":       sigma_a,
        # Channel B
        "solar_bz":      solar["bz"],
        "solar_density": solar["density"],
        "solar_speed":   solar["speed"],
        "solar_temp":    solar["temp"],
        "sigma_b":       sigma_b,
        # Result
        "rank":          rank,
        "anomaly":       anomaly,
        "word":          word_en,
        "word_ja":       word_ja,
        "lotto":         lotto,
        "sources":       sum([bool(rand_data), bool(anu_data),
                              solar["bz"] is not None, bool(btc_hash)]),
    }

    history.append(entry)
    save(history)

    print(f"[A] mean={fm}  σ={sigma_a:.4f}")
    print(f"[B] bz={solar['bz']}  σ={sigma_b:.4f}")
    print(f"[RANK] {rank}  anomaly={anomaly}")
    if anomaly:
        print(f"[WORD] {word_en} / {word_ja}")
        print(f"[LOTTO] {lotto}")

    if rank in ("SS", "S", "A"):
        notify(today, rank, sigma_a, sigma_b, word_en, word_ja, lotto)


if __name__ == "__main__":
    run()
