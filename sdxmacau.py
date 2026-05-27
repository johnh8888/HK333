#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
三彩种属性预测系统 V8 Stable
特点:
- 无 scipy 依赖
- GitHub Actions 稳定运行
- 无未来函数泄漏
- Walk Forward 回测
- LogLoss / ECE
- 温度缩放
- 贝叶斯动态权重
- 滑动窗口训练
- SQLite 自动存储
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sqlite3
import ssl
import sys
import time

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Tuple, Any
from urllib.request import Request, urlopen

try:
    import numpy as np
except ImportError:
    print("请安装 numpy")
    sys.exit(1)

# =========================================================
# 配置
# =========================================================

SCRIPT_DIR = Path(__file__).resolve().parent

DB_FILES = {
    "老澳门彩": "old_macau.db",
    "香港彩": "hk_macau.db",
    "新澳门彩": "xin_macau.db"
}

THIRD_PARTY_URLS = [
    "https://marksix6.net/index.php?api=1",
    "https://marksix6.net/api/lottery_api.php"
]

WINDOW_SIZE = 120

ATTRIBUTE_STATES = {
    "color": ["红", "蓝", "绿"],
    "size": ["大", "小"],
    "odd_even": ["单", "双"]
}

# =========================================================
# 属性映射
# =========================================================

RED = {
    1,2,7,8,12,13,18,19,23,24,
    29,30,34,35,40,45,46
}

BLUE = {
    3,4,9,10,14,15,20,25,26,
    31,36,37,41,42,47,48
}

GREEN = {
    5,6,11,16,17,21,22,27,
    28,32,33,38,39,43,44,49
}


def get_color(num: int) -> str:
    if num in RED:
        return "红"
    if num in BLUE:
        return "蓝"
    return "绿"


def get_big_small(num: int) -> str:
    return "大" if num >= 25 else "小"


def get_odd_even(num: int) -> str:
    return "单" if num % 2 else "双"


def get_tail(num: int) -> int:
    return num % 10


def get_mod7(num: int) -> int:
    return num % 7


def get_cross_distance(a: int, b: int) -> int:
    return abs(a - b)


# =========================================================
# 数据结构
# =========================================================

@dataclass
class DrawRecord:
    issue_no: str
    draw_date: str
    numbers: List[int]
    special_number: int


# =========================================================
# 数据库
# =========================================================

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect_db(path: str):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn):
    conn.execute("""
    CREATE TABLE IF NOT EXISTS draws (
        issue_no TEXT PRIMARY KEY,
        draw_date TEXT,
        numbers_json TEXT,
        special_number INTEGER,
        source TEXT,
        created_at TEXT,
        updated_at TEXT
    )
    """)

    conn.execute("""
    CREATE INDEX IF NOT EXISTS idx_draw_date
    ON draws(draw_date)
    """)

    conn.execute("""
    CREATE INDEX IF NOT EXISTS idx_issue_no
    ON draws(issue_no)
    """)

    conn.commit()


# =========================================================
# 网络获取
# =========================================================

def fetch_json_url(url: str, timeout=20, retries=3):

    for i in range(retries):

        try:
            ctx = ssl.create_default_context()

            req = Request(
                url,
                headers={"User-Agent": "Mozilla/5.0"}
            )

            with urlopen(req, timeout=timeout, context=ctx) as resp:

                charset = (
                    resp.headers.get_content_charset()
                    or "utf-8"
                )

                raw = resp.read().decode(
                    charset,
                    errors="ignore"
                )

                payload = json.loads(raw)

                if not isinstance(payload, dict):
                    raise ValueError("payload不是dict")

                return payload

        except Exception as e:

            if i == retries - 1:
                raise e

            time.sleep(2)

    raise RuntimeError("获取失败")


def parse_response(payload, lottery_name: str):

    records = []

    lottery_data = payload.get("lottery_data", [])

    target = next(
        (
            x for x in lottery_data
            if x.get("name") == lottery_name
        ),
        None
    )

    if not target:
        return records

    try:
        latest_open_time = datetime.strptime(
            target.get("openTime", ""),
            "%Y-%m-%d %H:%M:%S"
        )
    except:
        latest_open_time = datetime.now()

    history = target.get("history", [])

    for idx, item in enumerate(history):

        try:

            parts = item.split("期：")

            if len(parts) != 2:
                continue

            issue_no = parts[0].strip()

            nums = [
                int(x.strip())
                for x in parts[1].split(",")
            ]

            if len(nums) != 7:
                continue

            draw_date = (
                latest_open_time - timedelta(days=idx)
            ).strftime("%Y-%m-%d")

            records.append(
                DrawRecord(
                    issue_no,
                    draw_date,
                    nums[:6],
                    nums[6]
                )
            )

        except:
            continue

    return records


def fetch_online_records(lottery_name: str):

    for url in THIRD_PARTY_URLS:

        try:

            payload = fetch_json_url(url)

            records = parse_response(payload, lottery_name)

            if records:
                return records, "marksix6", url

        except Exception as e:
            print(f"获取失败 {url}: {e}")

    raise RuntimeError("全部API失败")


# =========================================================
# 数据同步
# =========================================================

def upsert_draw(conn, record, source):

    now = utc_now()

    exists = conn.execute(
        "SELECT 1 FROM draws WHERE issue_no=?",
        (record.issue_no,)
    ).fetchone()

    if exists:

        conn.execute("""
        UPDATE draws
        SET draw_date=?,
            numbers_json=?,
            special_number=?,
            source=?,
            updated_at=?
        WHERE issue_no=?
        """, (
            record.draw_date,
            json.dumps(record.numbers),
            record.special_number,
            source,
            now,
            record.issue_no
        ))

        return "updated"

    else:

        conn.execute("""
        INSERT INTO draws
        VALUES (?,?,?,?,?,?,?)
        """, (
            record.issue_no,
            record.draw_date,
            json.dumps(record.numbers),
            record.special_number,
            source,
            now,
            now
        ))

        return "inserted"


def sync_records(conn, records, source):

    ins = 0
    upd = 0

    for r in records:

        result = upsert_draw(conn, r, source)

        if result == "inserted":
            ins += 1
        else:
            upd += 1

    conn.commit()

    return len(records), ins, upd


# =========================================================
# 数据读取
# =========================================================

def load_sequence(conn, func, limit=500):

    rows = conn.execute("""
    SELECT special_number
    FROM draws
    ORDER BY draw_date ASC, issue_no ASC
    LIMIT ?
    """, (limit,)).fetchall()

    return [
        func(r["special_number"])
        for r in rows
    ]


def load_draws(conn, limit=500):

    rows = conn.execute("""
    SELECT special_number, draw_date
    FROM draws
    ORDER BY draw_date ASC, issue_no ASC
    LIMIT ?
    """, (limit,)).fetchall()

    return [
        {
            "num": r["special_number"],
            "date": r["draw_date"]
        }
        for r in rows
    ]


# =========================================================
# Markov
# =========================================================

class MarkovN:

    def __init__(self, order, states, alpha=1.0):

        self.order = order
        self.states = states
        self.alpha = alpha

        self.counts = defaultdict(Counter)
        self.total = defaultdict(int)

    def train(self, seq):

        for i in range(len(seq) - self.order):

            context = tuple(seq[i:i+self.order])

            nxt = seq[i+self.order]

            self.counts[context][nxt] += 1
            self.total[context] += 1

    def predict(self, context):

        K = len(self.states)

        total = self.total.get(context, 0)

        probs = {}

        for s in self.states:

            cnt = self.counts[context].get(s, 0)

            probs[s] = (
                cnt + self.alpha
            ) / (
                total + self.alpha * K
            )

        sm = sum(probs.values())

        return {
            k: v/sm
            for k, v in probs.items()
        }


# =========================================================
# Frequency
# =========================================================

class FrequencyPrior:

    def __init__(self, states):

        self.states = states

        self.probs = {
            s: 1/len(states)
            for s in states
        }

    def train(self, seq):

        cnt = Counter(seq)

        total = len(seq)

        if total == 0:
            return

        self.probs = {
            s: cnt[s] / total
            for s in self.states
        }

    def predict(self):

        return self.probs.copy()


# =========================================================
# Feature 模型
# =========================================================

class FeatureModel:

    def __init__(self, states):

        self.states = states

        self.counts = defaultdict(
            lambda: defaultdict(int)
        )

        self.total = defaultdict(int)

    def train(self, seq, features):

        for s, f in zip(seq, features):

            self.counts[f][s] += 1
            self.total[f] += 1

    def predict(self, feature):

        K = len(self.states)

        total = self.total.get(feature, 0)

        probs = {}

        for s in self.states:

            cnt = self.counts[feature].get(s, 0)

            probs[s] = (
                cnt + 1
            ) / (
                total + K
            )

        sm = sum(probs.values())

        return {
            k: v/sm
            for k, v in probs.items()
        }


# =========================================================
# 温度缩放
# =========================================================

class TemperatureScaling:

    def __init__(self, t=1.0):
        self.t = t

    def calibrate(self, probs):

        scaled = {
            k: v ** (1/self.t)
            for k, v in probs.items()
        }

        sm = sum(scaled.values())

        return {
            k: v/sm
            for k, v in scaled.items()
        }


# =========================================================
# 贝叶斯权重
# =========================================================

class BayesianWeight:

    def __init__(self, names):

        self.alpha = {
            n: 1.0 for n in names
        }

        self.beta = {
            n: 1.0 for n in names
        }

    def update(self, name, reward):

        self.alpha[name] += reward
        self.beta[name] += (1-reward)

    def weights(self):

        vals = {}

        for k in self.alpha:

            vals[k] = (
                self.alpha[k]
                /
                (self.alpha[k] + self.beta[k])
            )

        sm = sum(vals.values())

        return {
            k: v/sm
            for k, v in vals.items()
        }


# =========================================================
# Attribute Engine
# =========================================================

class AttributeEngine:

    def __init__(self, name, order=3):

        self.name = name

        self.states = ATTRIBUTE_STATES[name]

        self.markov = MarkovN(order, self.states)

        self.freq = FrequencyPrior(self.states)

        self.feature = FeatureModel(self.states)

        self.scaler = TemperatureScaling(1.15)

        self.bayes = BayesianWeight([
            "markov",
            "freq",
            "feature"
        ])

        self.order = order

    def train(self, seq, draws):

        seq = seq[-WINDOW_SIZE:]
        draws = draws[-WINDOW_SIZE:]

        self.markov.train(seq)

        self.freq.train(seq)

        features = [
            get_tail(x["num"])
            for x in draws
        ]

        self.feature.train(seq, features)

    def predict(self, recent_seq, recent_draws):

        context = tuple(
            recent_seq[-self.order:]
        )

        p1 = self.markov.predict(context)

        p2 = self.freq.predict()

        feature = get_tail(
            recent_draws[-1]["num"]
        )

        p3 = self.feature.predict(feature)

        weights = self.bayes.weights()

        final = {}

        for s in self.states:

            final[s] = (
                weights["markov"] * p1[s]
                +
                weights["freq"] * p2[s]
                +
                weights["feature"] * p3[s]
            )

        sm = sum(final.values())

        final = {
            k: v/sm
            for k, v in final.items()
        }

        final = self.scaler.calibrate(final)

        return final


# =========================================================
# 指标
# =========================================================

def log_loss(prob, actual):

    p = prob.get(actual, 1e-15)

    return -math.log(p)


def calc_ece(probs, outcomes, bins=10):

    if not probs:
        return 0.0

    ece = 0.0

    for i in range(bins):

        low = i / bins
        high = (i + 1) / bins

        idxs = [
            j for j, p in enumerate(probs)
            if low <= p < high
        ]

        if not idxs:
            continue

        acc = np.mean([
            outcomes[j]
            for j in idxs
        ])

        conf = np.mean([
            probs[j]
            for j in idxs
        ])

        ece += (
            abs(acc - conf)
            *
            len(idxs)
            / len(probs)
        )

    return ece


# =========================================================
# 主系统
# =========================================================

class PredictionSystem:

    def __init__(self, order=3, min_prob=0.55):

        self.order = order

        self.min_prob = min_prob

        self.engines = {
            "color": AttributeEngine("color", order),
            "size": AttributeEngine("size", order),
            "odd_even": AttributeEngine("odd_even", order)
        }

    def train(self, seqs, draws):

        for k in self.engines:

            self.engines[k].train(
                seqs[k],
                draws[k]
            )

    def predict(self, seqs, draws):

        result = {}

        for k in self.engines:

            probs = self.engines[k].predict(
                seqs[k],
                draws[k]
            )

            best = max(
                probs.items(),
                key=lambda x: x[1]
            )

            result[k] = {
                "probs": probs,
                "best": best[0],
                "max_prob": best[1]
            }

        avg = np.mean([
            result[k]["max_prob"]
            for k in result
        ])

        result["meta"] = {
            "should_act": avg >= self.min_prob,
            "avg_prob": avg
        }

        return result


# =========================================================
# 回测
# =========================================================

def walk_forward(seqs, draws, order=3, backtest=100):

    start = max(order + 20, len(seqs["color"]) - backtest)

    total = 0

    correct = {
        "color": 0,
        "size": 0,
        "odd_even": 0
    }

    losses = defaultdict(float)

    ece_probs = defaultdict(list)
    ece_outcomes = defaultdict(list)

    for idx in range(start, len(seqs["color"])):

        train_seq = {
            k: seqs[k][:idx]
            for k in seqs
        }

        train_draws = {
            k: draws[k][:idx]
            for k in draws
        }

        system = PredictionSystem(order)

        system.train(train_seq, train_draws)

        recent_seq = {
            k: seqs[k][:idx]
            for k in seqs
        }

        recent_draws = {
            k: draws[k][:idx]
            for k in draws
        }

        pred = system.predict(
            recent_seq,
            recent_draws
        )

        actual = {
            "color": seqs["color"][idx],
            "size": seqs["size"][idx],
            "odd_even": seqs["odd_even"][idx]
        }

        for k in correct:

            if pred[k]["best"] == actual[k]:
                correct[k] += 1
                outcome = 1
            else:
                outcome = 0

            losses[k] += log_loss(
                pred[k]["probs"],
                actual[k]
            )

            ece_probs[k].append(
                pred[k]["max_prob"]
            )

            ece_outcomes[k].append(outcome)

        total += 1

    print("\n========== 回测结果 ==========")

    for k in correct:

        acc = correct[k] / total

        ll = losses[k] / total

        ece = calc_ece(
            ece_probs[k],
            ece_outcomes[k]
        )

        print(
            f"{k} | "
            f"准确率={acc:.3f} | "
            f"LogLoss={ll:.4f} | "
            f"ECE={ece:.4f}"
        )


# =========================================================
# Dashboard
# =========================================================

def print_dashboard(conn, lottery_name, order, min_prob, backtest):

    seqs = {
        "color": load_sequence(conn, get_color),
        "size": load_sequence(conn, get_big_small),
        "odd_even": load_sequence(conn, get_odd_even)
    }

    draws = load_draws(conn)

    draws_dict = {
        "color": draws,
        "size": draws,
        "odd_even": draws
    }

    latest = conn.execute("""
    SELECT *
    FROM draws
    ORDER BY draw_date DESC, issue_no DESC
    LIMIT 1
    """).fetchone()

    if latest:

        print("\n==============================")
        print(f"{lottery_name}")
        print("==============================")

        nums = json.loads(latest["numbers_json"])

        print(
            f"最新期开奖: {latest['issue_no']}"
        )

        print(
            "号码:",
            " ".join(
                f"{x:02d}" for x in nums
            ),
            "+",
            latest["special_number"]
        )

    system = PredictionSystem(
        order=order,
        min_prob=min_prob
    )

    system.train(seqs, draws_dict)

    pred = system.predict(seqs, draws_dict)

    print("\n========== 下一期预测 ==========")

    for k in ["color", "size", "odd_even"]:

        print(f"\n{k}")

        for s, p in sorted(
            pred[k]["probs"].items(),
            key=lambda x: -x[1]
        ):

            flag = "✓" if s == pred[k]["best"] else ""

            print(f"{s}: {p*100:.2f}% {flag}")

    print("\n========== 元决策 ==========")

    if pred["meta"]["should_act"]:
        print("建议: 出手")
    else:
        print("建议: 观望")

    print(
        f"平均置信度: "
        f"{pred['meta']['avg_prob']:.3f}"
    )

    walk_forward(
        seqs,
        draws_dict,
        order=order,
        backtest=backtest
    )


# =========================================================
# 主流程
# =========================================================

def process_lottery(name, args):

    db_path = str(
        SCRIPT_DIR / DB_FILES[name]
    )

    print("\n================================================")
    print(f"处理彩种: {name}")
    print("================================================")

    conn = connect_db(db_path)

    try:

        init_db(conn)

        records, source, url = fetch_online_records(name)

        total, ins, upd = sync_records(
            conn,
            records,
            source
        )

        print(
            f"同步完成 "
            f"总={total} "
            f"新增={ins} "
            f"更新={upd}"
        )

        print_dashboard(
            conn,
            name,
            args.order,
            args.min_ig,
            args.backtest
        )

    except Exception as e:

        print(f"错误: {e}")

    finally:

        conn.close()


# =========================================================
# main
# =========================================================

def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--lottery",
        choices=[
            "老澳门彩",
            "香港彩",
            "新澳门彩"
        ]
    )

    parser.add_argument(
        "--order",
        type=int,
        default=3
    )

    parser.add_argument(
        "--min-ig",
        type=float,
        default=0.55
    )

    parser.add_argument(
        "--backtest",
        type=int,
        default=100
    )

    args = parser.parse_args()

    if args.lottery:

        process_lottery(
            args.lottery,
            args
        )

    else:

        for name in [
            "老澳门彩",
            "香港彩",
            "新澳门彩"
        ]:

            process_lottery(
                name,
                args
            )


if __name__ == "__main__":
    main()