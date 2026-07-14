# TEMPORAL SIGNAL RECEIVER 176248 — REMIX

> 将来、過去へ情報を送る技術が成立するなら、未来の私はこの鍵を知っている。

固定キー **176248** が現在の公開情報から、対照キーより継続的に強いパターンを取り出せるかを記録する、GitHub Pages向けの公開実験です。

## このリミックスで変えたこと

旧版は異常日をベースラインから除外したため、異常判定が自己固定化し、ほぼ毎日信号になる問題がありました。本版はベースライン方式を廃止し、毎日まったく同じ公開データを次の128鍵で比較します。

- 本命キー: `176248`
- 固定された対照キー: 127個
- 合計: 128鍵

176248の総合順位が8位以内のときだけ信号を確定します。偶然だけなら、信号率の期待値は `8 / 128 = 6.25%` です。

## 受信プロトコル

### Channel A — 公開エントロピー / 公開台帳

- random.org
- ANU QRNG
- drand
- Bitcoin最新ブロック

4ソース中2つ以上が取得できた場合に正常とします。

### Channel B — 太陽風

- NOAA DSCOVR magnetic field
- NOAA DSCOVR plasma

少なくとも1ソースを取得できた場合に正常とします。

### 検出

各鍵は、HMAC-SHA256で公開データ内の256位置をチャンネルごとに指定します。指定位置の下位2ビットが鍵固有の期待値と一致した数を二項分布のz値へ変換し、Channel AとBを合成して128鍵を順位付けします。

| Rank | 固定条件 |
|---|---|
| SS | 総合1位、かつ両チャンネルとも7位以内 |
| S | 総合1位 |
| A | 総合2位 |
| B | 総合3〜4位 |
| C | 総合5〜8位 |
| — | 9〜128位、または収集品質が低下 |

### 7数の復元

信号日に限り、176248が指定する観測位置を7レーン×37候補へ割り当てます。候補ごとの一致票が多い数字を各レーンから選び、重複を除いて1〜37の7数字を確定します。

金曜日の信号は `FRIDAY LOCK` として表示します。

## 重要な実験ルール

1. 鍵、対照群、閾値、抽出規則は観測後に変更しない。
2. 変更する場合は `PROTOCOL_VERSION` を更新し、別実験として `data/results.json` を空にして再開する。
3. 当選結果を見た後に、数字や信号日を選び直さない。
4. 日次結果はGitHub Actionsがコミットし、コミット時刻を事前確定の証拠とする。
5. 旧版125日分は `data/legacy-results.json` に退避している。

## GitHub Pagesへの差し替え

1. ZIPを展開し、リポジトリのルートへ全ファイルを上書きします。
2. GitHubの `Settings → Pages` で、`Deploy from a branch`、`main`、`/(root)` を選びます。
3. `Actions` を有効にします。
4. `Actions → Daily 176248 Signal Collection → Run workflow` を一度手動実行します。
5. `data/results.json` に最初のレコードが追加されたことを確認します。

日次実行は `00:30 UTC = 09:30 JST` に設定されています。GitHub側の都合で開始が遅れる場合がありますが、レコードの日付はJSTで保存されます。

## 異常検知メール

GitHub Pagesは静的サイトのため、メールは日次収集を実行するGitHub Actionsから送信します。収集品質が `OK` で、ランクが `SS / S / A / B / C` のいずれかになった日に、設定した宛先へ1回だけ送信します。

標準設定はGmail SMTPです。送信用Googleアカウントで2段階認証を有効にしてアプリパスワードを発行し、リポジトリの `Settings → Secrets and variables → Actions → New repository secret` に次の3件を登録してください。

| Secret名 | 内容 |
|---|---|
| `SMTP_USERNAME` | 送信に使うGmailアドレス |
| `SMTP_PASSWORD` | Googleで発行した16文字のアプリパスワード |
| `ALERT_EMAIL_TO` | 異常検知メールの受信アドレス |

受信アドレスは公開コードへ直書きせずSecretに保存するため、公開リポジトリでもページ閲覧者には表示されません。通常のGoogleアカウントのパスワードは登録しないでください。

設定確認は `Actions → Daily Signal Collection → Run workflow` を開き、`異常検知メールのテスト送信を行う` にチェックを入れて実行します。チェックなしの通常実行では、信号がない日はメールを送信しません。

公開ページへのリンクもメールに載せる場合は、同じ画面の `Variables` に `PAGES_URL` を登録します。

既存のntfy通知も併用できます。`NTFY_TOPIC` をSecretとして登録してください。未設定でも収集とメール通知は動作します。

ブラウザの `LOCAL ALERT` は、ページまたはPWAが起動してデータを確認した時点でローカル通知します。バックグラウンドの真のPush通知ではありません。

## ローカル検証

```bash
python test_protocol.py
python collector.py
python -m http.server 8000
```

ブラウザで `http://localhost:8000` を開きます。`collector.py` は実際の公開データへアクセスし、その日のレコードを1回だけ追加します。

## ファイル

```text
.github/workflows/daily.yml  日次収集とコミット
collector.py                固定プロトコル本体
protocol.json               公開された鍵・対照群・閾値
index.html                  GitHub Pages画面
data/results.json           新実験の記録
data/notification-state.json メールの重複送信防止状態
 data/legacy-results.json   旧RE版125日分
manifest.json / sw.js       PWA
```

この装置が未来通信を証明するとは限りません。証明できるのは、176248が事前に固定された対照群より強かったかどうかだけです。

*El Psy Kongroo.*
