# future-gadget-No829-RE

> *「この受信機が機能するなら、未来の私はすでに送っている」*

前身 [future-gadget-No829](../future-gadget-No829) の設計を根本から見直した改訂版。

---

## 変更点（Legacy → RE）

| 項目 | Legacy | RE |
|------|--------|-----|
| 異常判定 | A or B | ランク制 |
| ロト7 | 毎日生成 | 異常日のみ |
| ベースライン | 全日含む | **異常日除外** |
| チャンネル | 混合スコア | A・B 独立 |
| 通知 | σ≥2.0全て | SS/S/Aのみ |

---

## ランク定義

| ランク | 条件 |
|--------|------|
| **SS** | Channel A AND B、両方 σ≥3.0 |
| **S**  | Channel A AND B、どちらか σ≥3.0 |
| **A**  | Channel A AND B、両方 σ≥2.0 |
| **B**  | Channel A XOR B、σ≥3.0（片方のみ） |
| **C**  | Channel A XOR B、σ≥2.0（片方のみ） |
| **—**  | 異常なし（記録のみ） |

通知送信：SS / S / A のみ。ロト7生成：C 以上。

---

## チャンネル設計

```
Channel A — 量子チャンネル
  random.org（大気ノイズ）+ ANU QRNG（量子真空ゆらぎ）
  176248 キーフィルター後の統計的偏差

Channel B — 宇宙空間チャンネル
  NOAA DSCOVR L1（太陽と地球の重力均衡点 / 地球から150万km）
  Bz / 陽子密度 / 太陽風速度の複合偏差
  Bz 南向き（負値）は重み ×1.5
```

両チャンネルが独立しているため、同時異常の偶発確率は積算になる。

---

## ロト7生成ロジック

```
Channel A の filtered_mean
  + Channel B の Bz
  + 当日 UTC 日付文字列
  + 176248
  → HMAC-SHA256
  → σ合算値でオフセット付き抽出
  → 7数字（1-37）
```

異常の強度（σ値）が数字の選択ウィンドウをシフトする。  
静穏日と異常日では、同じ日付でも異なる数字が生成される。

---

## ベースライン設計

異常日（rank ≠ `-`）をベースライン計算から**除外**する。  
「シグナルが来た日」を「普通」の基準に混ぜない。

---

## Setup

1. リポジトリ作成
2. **Settings → Pages** → Source: `main` / root
3. **Actions** → Enable workflows
4. Actions → "Daily Signal Collection" → Run workflow（初回手動実行）
5. `data/results.json` が更新されたことを確認
6. Pages URL をスマートフォンで開き **INSTALL** → **ENABLE**

---

## File Structure

```
├── .github/workflows/daily.yml
├── data/results.json
├── collector.py
├── index.html
├── sw.js
├── manifest.json
├── icon-192.png
└── icon-512.png
```

---

*El Psy Kongroo.*
