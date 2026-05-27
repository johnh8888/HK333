
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
V8.5 Stable AI Time-Series Edition
- 无 scipy
- 无 sklearn
- 无 torch
- GitHub Actions 稳定运行
- 时间衰减
- 动态模型权重
- 高置信过滤
- Walk Forward 回测
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sqlite3
import ssl
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen

try:
    import numpy as np
except ImportError:
    print("请先安装 numpy")
    sys.exit(1)

SCRIPT_DIR = Path(__file__).resolve().parent

DB_FILES = {
    "老澳门彩": "old_macau.db",
    "香港彩": "hk_macau.db",
    "新澳门彩": "xin_macau.db"
}

API_URLS = [
    "https://marksix6.net/index.php?api=1",
    "https://marksix6.net/api/lottery_api.php"
]

ATTRIBUTE_STATES = {
    "color": ["红", "蓝", "绿"],
    "size": ["大", "小"],
    "odd_even": ["单", "双"]
}

def get_color(num):
    RED = {1,2,7,8,12,13,18,19,23,24,29,30,34,35,40,45,46}
    BLUE = {3,4,9,10,14,15,20,25,26,31,36,37,41,42,47,48}
    if num in RED:
        return "红"
    if num in BLUE:
        return "蓝"
    return "绿"

def get_big_small(num):
    return "大" if num >= 25 else "小"

def get_odd_even(num):
    return "单" if num % 2 else "双"

@dataclass
class DrawRecord:
    issue_no: str
    draw_date: str
    numbers: list
    special_number: int

def connect_db(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn

def init_db(conn):
    conn.execute("""
    CREATE TABLE IF NOT EXISTS draws(
        issue_no TEXT PRIMARY KEY,
        draw_date TEXT,
        numbers_json TEXT,
        special_number INTEGER,
        created_at TEXT
    )
    """)
    conn.commit()

def fetch_json(url):
    ctx = ssl.create_default_context()
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=20, context=ctx) as resp:
        return json.loads(resp.read().decode("utf-8", errors="ignore"))

def parse_records(payload, lottery_name):
    data = payload.get("lottery_data", [])
    target = next((x for x in data if x.get("name") == lottery_name), None)

    if not target:
        return []

    records = []

    try:
        latest_dt = datetime.strptime(
            target.get("openTime", ""),
            "%Y-%m-%d %H:%M:%S"
        )
    except:
        latest_dt = datetime.now()

    for idx, item in enumerate(target.get("history", [])):
        try:
            issue, nums = item.split("期：")
            nums = [int(x.strip()) for x in nums.split(",")]

            if len(nums) != 7:
                continue

            draw_date = (
                latest_dt - timedelta(days=idx)
            ).strftime("%Y-%m-%d")

            records.append(
                DrawRecord(
                    issue.strip(),
                    draw_date,
                    nums[:6],
                    nums[6]
                )
            )
        except:
            pass

    return records

def fetch_online_records(lottery_name):
    for url in API_URLS:
        try:
            payload = fetch_json(url)
            records = parse_records(payload, lottery_name)

            if records:
                return records
        except:
            pass

    raise RuntimeError("获取数据失败")

def sync_db(conn, records):
    now = datetime.now(timezone.utc).isoformat()

    inserted = 0
    updated = 0

    for r in records:
        old = conn.execute(
            "SELECT 1 FROM draws WHERE issue_no=?",
            (r.issue_no,)
        ).fetchone()

        if old:
            conn.execute("""
            UPDATE draws
            SET draw_date=?,
                numbers_json=?,
                special_number=?
            WHERE issue_no=?
            """, (
                r.draw_date,
                json.dumps(r.numbers),
                r.special_number,
                r.issue_no
            ))
            updated += 1
        else:
            conn.execute("""
            INSERT INTO draws VALUES(?,?,?,?,?)
            """, (
                r.issue_no,
                r.draw_date,
                json.dumps(r.numbers),
                r.special_number,
                now
            ))
            inserted += 1

    conn.commit()
    return inserted, updated

def load_numbers(conn, limit=600):
    rows = conn.execute("""
    SELECT special_number
    FROM draws
    ORDER BY draw_date ASC
    LIMIT ?
    """, (limit,)).fetchall()

    return [r["special_number"] for r in rows]

class TimeDecayMarkov:

    def __init__(self, states, order=3, decay=120):
        self.states = states
        self.order = order
        self.decay = decay

        self.counts = defaultdict(lambda: defaultdict(float))

    def train(self, seq):

        n = len(seq)

        for i in range(n - self.order):

            ctx = tuple(seq[i:i+self.order])
            nxt = seq[i+self.order]

            age = n - i

            weight = math.exp(-age / self.decay)

            self.counts[ctx][nxt] += weight

    def predict(self, recent):

        ctx = tuple(recent[-self.order:])

        probs = {}

        total = sum(self.counts[ctx].values())

        if total == 0:
            return {
                s: 1 / len(self.states)
                for s in self.states
            }

        for s in self.states:
            probs[s] = (
                self.counts[ctx].get(s, 0.0) + 1.0
            ) / (total + len(self.states))

        sm = sum(probs.values())

        return {
            k: v / sm
            for k, v in probs.items()
        }

class FrequencyModel:

    def __init__(self, states):
        self.states = states
        self.probs = {}

    def train(self, seq):

        cnt = Counter(seq)
        total = len(seq)

        self.probs = {
            s: cnt[s] / total
            for s in self.states
        }

    def predict(self):
        return self.probs

class EnsembleEngine:

    def __init__(self, attr_name, order=3):

        self.attr_name = attr_name
        self.states = ATTRIBUTE_STATES[attr_name]

        self.markov = TimeDecayMarkov(
            self.states,
            order=order
        )

        self.freq = FrequencyModel(
            self.states
        )

        self.weights = {
            "markov": 0.7,
            "freq": 0.3
        }

    def train(self, seq):

        self.markov.train(seq)
        self.freq.train(seq)

    def predict(self, recent):

        p1 = self.markov.predict(recent)
        p2 = self.freq.predict()

        final = {}

        for s in self.states:
            final[s] = (
                self.weights["markov"] * p1[s] +
                self.weights["freq"] * p2[s]
            )

        sm = sum(final.values())

        return {
            k: v / sm
            for k, v in final.items()
        }

def accuracy(preds, actuals):
    if not preds:
        return 0.0

    c = sum(1 for a, b in zip(preds, actuals) if a == b)

    return c / len(preds)

def logloss(probs_list, actuals):

    loss = 0.0

    for probs, actual in zip(probs_list, actuals):
        p = probs.get(actual, 1e-15)
        loss += -math.log(p)

    return loss / len(actuals)

def walk_forward(attr_name, seq, order=3, test_len=100):

    preds = []
    actuals = []
    prob_list = []

    start = max(order + 20, len(seq) - test_len)

    for i in range(start, len(seq)-1):

        train_seq = seq[:i]

        model = EnsembleEngine(attr_name, order)
        model.train(train_seq)

        recent = train_seq[-order:]

        probs = model.predict(recent)

        pred = max(probs.items(), key=lambda x: x[1])[0]

        actual = seq[i]

        preds.append(pred)
        actuals.append(actual)
        prob_list.append(probs)

    return (
        accuracy(preds, actuals),
        logloss(prob_list, actuals)
    )

def run_lottery(lottery_name, order, backtest):

    print("\n" + "="*48)
    print(f"处理彩种: {lottery_name}")
    print("="*48)

    db_path = SCRIPT_DIR / DB_FILES[lottery_name]

    conn = connect_db(str(db_path))

    init_db(conn)

    records = fetch_online_records(lottery_name)

    inserted, updated = sync_db(conn, records)

    print(f"同步完成 总={len(records)} 新增={inserted} 更新={updated}")

    nums = load_numbers(conn)

    color_seq = [get_color(x) for x in nums]
    size_seq = [get_big_small(x) for x in nums]
    odd_seq = [get_odd_even(x) for x in nums]

    all_seq = {
        "color": color_seq,
        "size": size_seq,
        "odd_even": odd_seq
    }

    latest = nums[-1]

    print("\n==============================")
    print(lottery_name)
    print("==============================")
    print(f"最新特码: {latest:02d}")

    print("\n========== 下一期预测 ==========")

    avg_conf = []

    for name, seq in all_seq.items():

        engine = EnsembleEngine(name, order)
        engine.train(seq)

        probs = engine.predict(seq[-order:])

        best = max(probs.items(), key=lambda x: x[1])

        avg_conf.append(best[1])

        print(f"\n{name}")

        for s, p in sorted(
            probs.items(),
            key=lambda x: -x[1]
        ):
            mark = " ✓" if s == best[0] else ""
            print(f"{s}: {p*100:.2f}%{mark}")

    avg_prob = np.mean(avg_conf)

    print("\n========== 元决策 ==========")

    if avg_prob >= 0.58:
        print("建议: 出手")
    else:
        print("建议: 观望")

    print(f"平均置信度: {avg_prob:.3f}")

    print("\n========== 回测结果 ==========")

    for name, seq in all_seq.items():

        acc, ll = walk_forward(
            name,
            seq,
            order,
            backtest
        )

        print(
            f"{name} | "
            f"准确率={acc:.3f} | "
            f"LogLoss={ll:.4f}"
        )

    conn.close()

def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--lottery",
        choices=["老澳门彩", "香港彩", "新澳门彩"]
    )

    parser.add_argument(
        "--order",
        type=int,
        default=3
    )

    parser.add_argument(
        "--backtest",
        type=int,
        default=100
    )

    args = parser.parse_args()

    if args.lottery:
        run_lottery(
            args.lottery,
            args.order,
            args.backtest
        )
    else:
        for x in [
            "老澳门彩",
            "香港彩",
            "新澳门彩"
        ]:
            run_lottery(
                x,
                args.order,
                args.backtest
            )

if __name__ == "__main__":
    main()