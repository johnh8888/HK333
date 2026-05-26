#!/usr/bin/env python3
# -- coding: utf-8 --

from future import annotations

import argparse
import json
import sqlite3
import ssl
import math
import random
import sys

from pathlib import Path
from datetime import datetime, timedelta
from urllib.request import Request, urlopen
from collections import Counter, defaultdict

import numpy as np

try:
    from lightgbm import LGBMClassifier
    HAS_LGBM = True
except:
    HAS_LGBM = False

from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss
from sklearn.preprocessing import LabelEncoder

SCRIPT_DIR = Path(file).resolve().parent

DB_FILES = {
    "老澳门彩": "old_macau.db",
    "香港彩": "hk_macau.db",
    "新澳门彩": "xin_macau.db"
}

URLS = [
    "https://marksix6.net/index.php?api=1",
]

ATTR_STATES = {
    "color": ["红", "蓝", "绿"],
    "size": ["大", "小"],
    "odd_even": ["单", "双"]
}

# =========================
# 属性映射
# =========================

RED = {1,2,7,8,12,13,18,19,23,24,29,30,34,35,40,45,46}
BLUE = {3,4,9,10,14,15,20,25,26,31,36,37,41,42,47,48}

def get_color(n):
    if n in RED:
        return "红"
    if n in BLUE:
        return "蓝"
    return "绿"

def get_size(n):
    return "大" if n >= 25 else "小"

def get_odd_even(n):
    return "单" if n % 2 else "双"

# =========================
# DB
# =========================

def connect_db(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn

def init_db(conn):
    conn.execute("""
    CREATE TABLE IF NOT EXISTS draws(
        issue_no TEXT PRIMARY KEY,
        draw_date TEXT,
        special_number INTEGER
    )
    """)
    conn.commit()

# =========================
# 抓取
# =========================

def fetch_json(url):

    ctx = ssl.create_default_context()

    req = Request(
        url,
        headers={"User-Agent":"Mozilla/5.0"}
    )

    with urlopen(req, context=ctx, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))

def fetch_records(name):

    for url in URLS:

        try:
            payload = fetch_json(url)

            lottery_data = payload.get("lottery_data", [])

            target = next(
                (x for x in lottery_data if x["name"] == name),
                None
            )

            if not target:
                continue

            records = []

            latest = datetime.strptime(
                target["openTime"],
                "%Y-%m-%d %H:%M:%S"
            )

            for idx, item in enumerate(target["history"]):

                issue, nums = item.split("期：")

                arr = [int(x) for x in nums.split(",")]

                special = arr[-1]

                dt = latest - timedelta(days=idx)

                records.append({
                    "issue": issue.strip(),
                    "date": dt.strftime("%Y-%m-%d"),
                    "special": special
                })

            return records

        except Exception as e:
            print("抓取失败:", e)

    return []

# =========================
# 同步
# =========================

def sync(conn, records):

    ins = 0

    for r in records:

        cur = conn.execute(
            "SELECT 1 FROM draws WHERE issue_no=?",
            (r["issue"],)
        ).fetchone()

        if cur:
            continue

        conn.execute(
            "INSERT INTO draws VALUES(?,?,?)",
            (
                r["issue"],
                r["date"],
                r["special"]
            )
        )

        ins += 1

    conn.commit()

    return ins

# =========================
# 加载
# =========================

def load_draws(conn):

    rows = conn.execute("""
    SELECT *
    FROM draws
    ORDER BY draw_date ASC
    """).fetchall()

    arr = []

    for r in rows:

        n = r["special_number"]

        arr.append({
            "num": n,
            "color": get_color(n),
            "size": get_size(n),
            "odd_even": get_odd_even(n)
        })

    return arr

# =========================
# 特征工程
# =========================

def make_feature(draws, idx):

    prev = draws[idx-1]["num"]

    return [
        prev,
        prev % 10,
        prev % 7,
        int(prev >= 25),
        prev % 2,
    ]

# =========================
# Rolling Window
# =========================

WINDOWS = [30,50,100,200]

# =========================
# ML Engine
# =========================

class EnsembleEngine:

    def init(self, attr):

        self.attr = attr

        self.states = ATTR_STATES[attr]

        self.encoder = LabelEncoder()

        self.encoder.fit(self.states)

        self.models = []

        self.meta_model = LogisticRegression()

    def build_dataset(self, draws, window):

        X = []
        y = []

        start = max(1, len(draws)-window)

        for i in range(start, len(draws)):

            feat = make_feature(draws, i)

            target = draws[i][self.attr]

            X.append(feat)

            y.append(target)

        return np.array(X), np.array(y)

    def train(self, draws):

        base_outputs = []

        final_y = None

        for w in WINDOWS:

            X, y = self.build_dataset(draws, w)

            if len(X) < 20:
                continue

            if HAS_LGBM:

                model = LGBMClassifier(
                    n_estimators=200,
                    learning_rate=0.03,
                    max_depth=5
                )

            else:

                model = RandomForestClassifier(
                    n_estimators=200,
                    max_depth=6
                )

            model.fit(X, y)

            self.models.append(model)

            probs = model.predict_proba(X)

            base_outputs.append(probs)

            final_y = y

        if not base_outputs:
            return

        stack_X = np.hstack(base_outputs)

        y_enc = self.encoder.transform(final_y)

        self.meta_model.fit(stack_X, y_enc)

    def predict(self, draws):

        feat = np.array([
            make_feature(draws, len(draws)-1)
        ])

        probs_all = []

        for m in self.models:

            p = m.predict_proba(feat)[0]

            probs_all.extend(p)

        probs_all = np.array(probs_all).reshape(1,-1)

        final_prob = self.meta_model.predict_proba(probs_all)[0]

        result = {}

        for i,s in enumerate(self.encoder.classes_):
            result[s] = float(final_prob[i])

        total = sum(result.values())

        return {
            k:v/total
            for k,v in result.items()
        }

# =========================
# 回测
# =========================

def backtest(draws, attr):

    correct = 0

    total = 0

    losses = []

    start = 120

    for i in range(start, len(draws)-1):

        train = draws[:i]

        test = draws[i]

        eng = EnsembleEngine(attr)

        eng.train(train)

        probs = eng.predict(train)

        pred = max(probs, key=probs.get)

        actual = test[attr]

        if pred == actual:
            correct += 1

        losses.append(
            log_loss(
                [[1 if s==actual else 0 for s in eng.states]],
                [[probs[s] for s in eng.states]]
            )
        )

        total += 1

    if total == 0:
        return 0,0

    return correct/total, np.mean(losses)

# =========================
# 主流程
# =========================

def process(name):

    print("="60)
    print(name)
    print("="60)

    db = SCRIPT_DIR / DB_FILES[name]

    conn = connect_db(str(db))

    init_db(conn)

    records = fetch_records(name)

    ins = sync(conn, records)

    print("新增:", ins)

    draws = load_draws(conn)

    if len(draws) < 150:
        print("数据不足")
        return

    latest = draws[-1]

    print(
        "最新特码:",
        latest["num"],
        latest["color"],
        latest["size"],
        latest["odd_even"]
    )

    for attr in ["color","size","odd_even"]:

        print()
        print("预测:", attr)

        eng = EnsembleEngine(attr)

        eng.train(draws)

        probs = eng.predict(draws)

        for k,v in sorted(
            probs.items(),
            key=lambda x:-x[1]
        ):
            print(f"{k}: {v100:.2f}%")

        acc, ll = backtest(draws, attr)

        print(
            f"回测准确率: {acc100:.2f}%"
        )

        print(
            f"LogLoss: {ll:.4f}"
        )

# =========================
# main
# =========================

def main():

    p = argparse.ArgumentParser()

    p.add_argument(
        "--lottery",
        choices=["老澳门彩","香港彩","新澳门彩"]
    )

    args = p.parse_args()

    if args.lottery:

        process(args.lottery)

    else:

        for x in DB_FILES:
            process(x)

if name == "main":
    main()