#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# =========================================================
# 三彩种属性预测 V15-QUANT STABLE - 双推增强版
#
# 最终稳定完整版
# 新增：色波主推 + 次推模式 + 双推回测命中
# =========================================================

from __future__ import annotations

import argparse
import json
import math
import random
import re
import sqlite3

from collections import defaultdict, Counter
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np

from urllib.request import Request, urlopen

# =========================================================
# 固定随机种子
# =========================================================

SEED = 42

np.random.seed(SEED)
random.seed(SEED)

# =========================================================
# 基础
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

# =========================================================
# 属性
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
    5,6,11,16,17,21,22,27,28,
    32,33,38,39,43,44,49
}

# =========================================================

def get_color(num):
    if num in RED:
        return "红"
    if num in BLUE:
        return "蓝"
    return "绿"

# =========================================================

def get_big_small(num):
    return "大" if num >= 25 else "小"

# =========================================================

def get_odd_even(num):
    return "单" if num % 2 else "双"

# =========================================================
# 数据结构
# =========================================================

@dataclass
class DrawRecord:
    issue_no: str
    draw_date: str
    numbers: list
    special_number: int

# =========================================================
# DB
# =========================================================

def connect_db(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

# =========================================================

def init_db(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS draws(
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
        CREATE INDEX IF NOT EXISTS idx_issue
        ON draws(issue_no)
    """)

    conn.commit()

# =========================================================
# 网络
# =========================================================

def fetch_json_url(url):
    try:
        req = Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0"
            }
        )
        with urlopen(req, timeout=20) as resp:
            return json.loads(
                resp.read().decode(
                    "utf-8",
                    errors="ignore"
                )
            )
    except:
        return None

# =========================================================
# 数据获取
# =========================================================

def fetch_online_records(lottery_name):
    for url in THIRD_PARTY_URLS:
        payload = fetch_json_url(url)
        if not payload:
            continue

        lottery_data = payload.get(
            "lottery_data",
            []
        )

        target = next(
            (
                x for x in lottery_data
                if x.get("name") == lottery_name
            ),
            None
        )

        if not target:
            continue

        try:
            latest_time = datetime.strptime(
                target.get("openTime", ""),
                "%Y-%m-%d %H:%M:%S"
            )
        except:
            latest_time = datetime.now()

        records = []

        for idx, item in enumerate(
            target.get("history", [])
        ):
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
                    latest_time - timedelta(days=idx)
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

        if records:
            return records, "marksix6"

    raise RuntimeError("无法获取数据")

# =========================================================
# 同步
# =========================================================

def sync_from_records(conn, records, source):
    now = datetime.now(
        timezone.utc
    ).isoformat()

    ins = 0
    upd = 0

    for r in records:
        exist = conn.execute(
            "SELECT 1 FROM draws WHERE issue_no=?",
            (r.issue_no,)
        ).fetchone()

        if exist:
            conn.execute("""
                UPDATE draws
                SET draw_date=?,
                    numbers_json=?,
                    special_number=?,
                    source=?,
                    updated_at=?
                WHERE issue_no=?
            """, (
                r.draw_date,
                json.dumps(r.numbers),
                r.special_number,
                source,
                now,
                r.issue_no
            ))
            upd += 1
        else:
            conn.execute("""
                INSERT INTO draws
                VALUES (?,?,?,?,?,?,?)
            """, (
                r.issue_no,
                r.draw_date,
                json.dumps(r.numbers),
                r.special_number,
                source,
                now,
                now
            ))
            ins += 1

    conn.commit()
    return len(records), ins, upd

# =========================================================
# issue_no 修复
# =========================================================

def issue_to_int(issue_no):
    nums = re.sub(r"\D", "", issue_no)
    if nums == "":
        return 0
    return int(nums)

# =========================================================
# 加载序列
# =========================================================

def load_sequence(conn, attr_func):
    rows = conn.execute("""
        SELECT issue_no, special_number
        FROM draws
    """).fetchall()

    rows = sorted(
        rows,
        key=lambda r: issue_to_int(
            r["issue_no"]
        )
    )

    return [
        attr_func(r["special_number"])
        for r in rows
    ]

# =========================================================
# Entropy
# =========================================================

def entropy(probs):
    e = 0
    for p in probs.values():
        p = max(p, 1e-12)
        e -= p * math.log(p)
    return e

# =========================================================
# Bayesian smoothing
# =========================================================

def bayesian_prob(
    count,
    total,
    alpha,
    states
):
    return (
        count + alpha
    ) / (
        total + alpha * states
    )

# =========================================================
# 温度校准
# =========================================================

def apply_temperature(probs, temp):
    logits = {}
    for k, v in probs.items():
        v = max(v, 1e-12)
        logits[k] = math.log(v)

    scaled = {}
    for k, v in logits.items():
        scaled[k] = math.exp(v / temp)

    total = sum(scaled.values())
    if total <= 0:
        return {
            k: 1 / len(probs)
            for k in probs
        }

    result = {
        k: v / total
        for k, v in scaled.items()
    }

    for k in result:
        result[k] = min(
            max(result[k], 0.07),
            0.72
        )

    s = sum(result.values())
    return {
        k: v / s
        for k, v in result.items()
    }

# =========================================================
# Rolling calibration
# =========================================================

def rolling_temperature_search(
    probs_hist,
    actual_hist,
    window=80
):
    if len(probs_hist) < 25:
        return 1.0

    probs_hist = probs_hist[-window:]
    actual_hist = actual_hist[-window:]

    best_temp = 1.0
    best_loss = 999999

    for temp in np.arange(0.85, 1.31, 0.02):
        losses = []
        for probs, actual in zip(
            probs_hist,
            actual_hist
        ):
            calibrated = apply_temperature(
                probs,
                temp
            )
            p = max(
                calibrated.get(actual, 1e-12),
                1e-12
            )
            losses.append(
                -math.log(p)
            )
        loss = np.mean(losses)

        if loss < best_loss:
            best_loss = loss
            best_temp = temp

    return best_temp

# =========================================================
# Regime Detection
# =========================================================

def detect_regime(seq):
    if len(seq) < 60:
        return "NORMAL"

    recent = seq[-30:]
    old = seq[-60:-30]

    recent_counts = Counter(recent)
    old_counts = Counter(old)

    drift = 0
    states = set(
        list(recent_counts.keys())
        +
        list(old_counts.keys())
    )

    for s in states:
        r = recent_counts[s] / len(recent)
        o = old_counts[s] / len(old)
        drift += abs(r - o)

    if drift > 0.35:
        return "VOLATILE"
    return "NORMAL"

# =========================================================
# 真 Conditional Markov
# =========================================================

class ConditionalMarkov:
    def __init__(
        self,
        states,
        alpha=1.5,
        decay=0.993,
        recent_periods=220
    ):
        self.states = states
        self.alpha = alpha
        self.decay = decay
        self.recent_periods = recent_periods
        self.global_counts = Counter()
        self.transitions1 = defaultdict(Counter)
        self.transitions2 = defaultdict(Counter)

    def train(self, seq):
        seq = seq[-self.recent_periods:]
        for age, i in enumerate(
            reversed(range(len(seq)))
        ):
            s = seq[i]
            w = self.decay ** age
            self.global_counts[s] += w

        for age, i in enumerate(
            reversed(range(len(seq)-2))
        ):
            a = seq[i]
            b = seq[i+1]
            c = seq[i+2]
            w = self.decay ** age
            self.transitions2[(a,b)][c] += w
            self.transitions1[b][c] += w

    def predict(self, recent):
        if len(recent) < 2:
            return {
                s: 1 / len(self.states)
                for s in self.states
            }

        a = recent[-2]
        b = recent[-1]

        trans2 = self.transitions2.get(
            (a,b),
            Counter()
        )
        trans1 = self.transitions1.get(
            b,
            Counter()
        )

        total2 = sum(trans2.values())
        total1 = sum(trans1.values())
        totalg = sum(self.global_counts.values())

        if total2 >= 12:
            base = trans2
            total = total2
        elif total1 >= 8:
            base = trans1
            total = total1
        else:
            base = self.global_counts
            total = totalg

        probs = {}
        for s in self.states:
            probs[s] = bayesian_prob(
                base.get(s,0),
                total,
                self.alpha,
                len(self.states)
            )

        total_p = sum(probs.values())
        return {
            k: v / total_p
            for k, v in probs.items()
        }

# =========================================================
# Kelly
# =========================================================

class KellyBankroll:
    def __init__(self, initial=10000):
        self.initial = initial
        self.current = initial
        self.history = []
        self.bet_count = 0
        self.win_count = 0
        self.loss_streak = 0
        self.cooldown = 0
        self.equity_curve = [initial]

    def get_bet_size(
        self,
        p,
        odds_total,
        probs
    ):
        if self.cooldown > 0:
            self.cooldown -= 1
            return 0

        ent = entropy(probs)
        if ent > 0.96:
            return 0

        b = odds_total - 1
        q = 1 - p
        edge = (b * p) - q

        if edge <= 0.012:
            return 0

        f = min(0.125, edge / b, 0.03)

        if self.current < 500:
            return 0

        bet = int(self.current * f)
        bet = min(
            bet,
            int(self.current * 0.05)
        )

        if bet < 20:
            return 0

        return bet

    def record_result(
        self,
        bet,
        won,
        odds_total
    ):
        if bet <= 0:
            return

        self.bet_count += 1

        if won:
            self.win_count += 1
            self.loss_streak = 0
            profit = bet * (
                odds_total - 1
            )
        else:
            self.loss_streak += 1
            profit = -bet
            if self.loss_streak >= 6:
                self.cooldown = 12
                self.loss_streak = 0

        self.current += profit
        self.history.append(profit)
        self.equity_curve.append(
            self.current
        )

    def get_max_drawdown(self):
        peak = self.equity_curve[0]
        max_dd = 0
        for x in self.equity_curve:
            peak = max(peak, x)
            dd = (peak - x) / peak
            max_dd = max(max_dd, dd)
        return max_dd * 100

    def get_sortino(self):
        if len(self.history) < 2:
            return 0
        arr = np.array(self.history)
        mean = np.mean(arr)
        downside = arr[arr < 0]
        if len(downside) == 0:
            return 0
        downside_std = np.std(downside)
        if downside_std <= 1e-6:
            return 0
        return (
            mean / downside_std
        ) * math.sqrt(len(arr))

    def get_profit_factor(self):
        gains = sum(
            x for x in self.history
            if x > 0
        )
        losses = abs(sum(
            x for x in self.history
            if x < 0
        ))
        if losses <= 0:
            return gains
        return gains / losses

    def get_stats(self):
        roi = (
            (self.current / self.initial) - 1
        ) * 100
        profit = sum(self.history)
        winrate = 0
        if self.bet_count > 0:
            winrate = (
                self.win_count
                / self.bet_count
            ) * 100
        return profit, roi, winrate

# =========================================================
# MAIN
# =========================================================

def main():
    parser = argparse.ArgumentParser(
        description="V15-QUANT STABLE 双推增强版"
    )

    parser.add_argument(
        "--lottery",
        choices=["老澳门彩","香港彩","新澳门彩"],
        default="香港彩"
    )

    parser.add_argument(
        "--recent",
        type=int,
        default=240
    )

    parser.add_argument(
        "--bankroll",
        type=int,
        default=10000
    )

    parser.add_argument(
        "--test",
        type=int,
        default=10
    )

    args = parser.parse_args()

    conn = connect_db(
        SCRIPT_DIR / DB_FILES[args.lottery]
    )

    init_db(conn)

    try:
        records, source = fetch_online_records(
            args.lottery
        )

        total, ins, upd = sync_from_records(
            conn,
            records,
            source
        )

        print(
            f"{args.lottery} 同步完成 "
            f"总计:{total} 新增:{ins} 更新:{upd}"
        )

        color_seq = load_sequence(
            conn,
            get_color
        )

        size_seq = load_sequence(
            conn,
            get_big_small
        )

        odd_seq = load_sequence(
            conn,
            get_odd_even
        )

        test_len = min(
            args.test,
            len(color_seq) - 40
        )

        start = len(color_seq) - test_len

        bank = KellyBankroll(
            initial=args.bankroll
        )

        probs_history = []
        actual_history = []
        historical_probs = []
        historical_actuals = []

        color_correct = 0
        size_correct = 0
        odd_correct = 0

        # =================================================
        # WalkForward
        # =================================================

        for t in range(start, len(color_seq)):

            regime = detect_regime(
                color_seq[:t]
            )

            if regime == "VOLATILE":
                dynamic_recent = 120
                entropy_limit = 0.90
            else:
                dynamic_recent = 240
                entropy_limit = 0.96

            eng_c = ConditionalMarkov(
                ["红","蓝","绿"],
                recent_periods=dynamic_recent
            )

            eng_s = ConditionalMarkov(
                ["大","小"],
                recent_periods=dynamic_recent
            )

            eng_o = ConditionalMarkov(
                ["单","双"],
                recent_periods=dynamic_recent
            )

            eng_c.train(color_seq[:t])
            eng_s.train(size_seq[:t])
            eng_o.train(odd_seq[:t])

            pred_c = eng_c.predict(
                color_seq[max(0,t-30):t]
            )

            pred_s = eng_s.predict(
                size_seq[max(0,t-30):t]
            )

            pred_o = eng_o.predict(
                odd_seq[max(0,t-30):t]
            )

            actual_c = color_seq[t]
            actual_s = size_seq[t]
            actual_o = odd_seq[t]

            temp = rolling_temperature_search(
                historical_probs,
                historical_actuals
            )

            calibrated = apply_temperature(
                pred_c,
                temp
            )

            historical_probs.append(pred_c)
            historical_actuals.append(actual_c)

            probs_history.append(calibrated)
            actual_history.append(actual_c)

            # ==================== 色波双推 ====================
            sorted_color = sorted(
                calibrated.items(),
                key=lambda x: x[1],
                reverse=True
            )
            top2_colors = [sorted_color[0][0], sorted_color[1][0]]
            best_color = sorted_color[0][0]

            won_color = actual_c in top2_colors
            if won_color:
                color_correct += 1

            odds = 2.7 if best_color == "红" else 2.8

            if entropy(calibrated) > entropy_limit:
                bet = 0
            else:
                bet = bank.get_bet_size(
                    calibrated[best_color],
                    odds,
                    calibrated
                )

            bank.record_result(
                bet,
                won_color,
                odds
            )

            # 大小 & 单双
            if max(pred_s, key=pred_s.get) == actual_s:
                size_correct += 1
            if max(pred_o, key=pred_o.get) == actual_o:
                odd_correct += 1

        # =================================================
        # Metrics
        # =================================================

        profit, roi, winrate = bank.get_stats()

        print("\n" + "="*100)
        print(f"V15-QUANT STABLE 双推增强版 WalkForward ({test_len}期)")
        print("-"*100)
        print(f"色波准确率(双推) : {color_correct/test_len*100:.2f}%")
        print(f"大小准确率(单推) : {size_correct/test_len*100:.2f}%")
        print(f"单双准确率(单推) : {odd_correct/test_len*100:.2f}%")
        print("-"*100)
        print(f"最终资金    : ¥{bank.current:.2f}")
        print(f"总盈亏      : ¥{profit:.2f}")
        print(f"ROI         : {roi:.2f}%")
        print(f"下注次数    : {bank.bet_count}")
        print(f"真实胜率    : {winrate:.2f}%")
        print(f"MaxDrawdown : {bank.get_max_drawdown():.2f}%")
        print(f"ProfitFactor: {bank.get_profit_factor():.4f}")
        print(f"SortinoRatio: {bank.get_sortino():.4f}")

        # =================================================
        # 下期预测
        # =================================================

        temp = rolling_temperature_search(
            historical_probs,
            historical_actuals
        )

        # 色波
        future_color_engine = ConditionalMarkov(
            ["红","蓝","绿"],
            recent_periods=args.recent
        )
        future_color_engine.train(color_seq)
        future_color_probs = apply_temperature(
            future_color_engine.predict(
                color_seq[-30:]
            ),
            temp
        )

        # 大小
        future_size_engine = ConditionalMarkov(
            ["大","小"],
            recent_periods=args.recent
        )
        future_size_engine.train(size_seq)
        future_size_probs = apply_temperature(
            future_size_engine.predict(
                size_seq[-30:]
            ),
            temp
        )

        # 单双
        future_odd_engine = ConditionalMarkov(
            ["单","双"],
            recent_periods=args.recent
        )
        future_odd_engine.train(odd_seq)
        future_odd_probs = apply_temperature(
            future_odd_engine.predict(
                odd_seq[-30:]
            ),
            temp
        )

        print("\n" + "="*100)
        print("下期预测")
        print("-"*100)

        print("【色波】")
        sorted_color = sorted(
            future_color_probs.items(),
            key=lambda x: x[1],
            reverse=True
        )
        for i, (k, v) in enumerate(sorted_color):
            tag = "【主推】" if i == 0 else "【次推】" if i == 1 else "       "
            print(f"{tag} {k} : {v*100:.2f}%")

        print(f"\n推荐组合：{sorted_color[0][0]}（主推） + {sorted_color[1][0]}（次推）")
        print(f"双推覆盖置信 : {(sorted_color[0][1] + sorted_color[1][1])*100:.2f}%")

        print("\n【大小】")
        for k, v in sorted(future_size_probs.items(), key=lambda x: x[1], reverse=True):
            print(f"{k} : {v*100:.2f}%")

        print("\n【单双】")
        for k, v in sorted(future_odd_probs.items(), key=lambda x: x[1], reverse=True):
            print(f"{k} : {v*100:.2f}%")

        print("="*100)

    except Exception as e:
        print(f"错误: {e}")
    finally:
        conn.close()

# =========================================================

if __name__ == "__main__":
    main()