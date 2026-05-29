#!/usr/bin/env python3
# -*- coding: utf-8 -*-

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

SEED = 42
np.random.seed(SEED)
random.seed(SEED)

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

def get_color(num):
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

def connect_db(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

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
    conn.commit()

def fetch_json_url(url):
    try:
        req = Request(
            url,
            headers={"User-Agent": "Mozilla/5.0"}
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

        for idx, item in enumerate(target.get("history", [])):
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

    print("⚠️ 在线数据获取失败，使用本地数据库现有数据")
    return [], "local"

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

def issue_to_int(issue_no):

    nums = re.sub(r"\D", "", issue_no)

    if nums == "":
        return 0

    return int(nums)

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

def entropy(probs):

    e = 0

    for p in probs.values():

        p = max(p, 1e-12)

        e -= p * math.log(p)

    return e

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
            max(result[k], 0.08),
            0.68
        )

    s = sum(result.values())

    return {
        k: v / s
        for k, v in result.items()
    }

def rolling_temperature_search(
    probs_hist,
    actual_hist,
    window=60
):

    if len(probs_hist) < 30:
        return 1.0

    probs_hist = probs_hist[-window:]
    actual_hist = actual_hist[-window:]

    best_temp = 1.0
    best_loss = 999999

    for temp in np.arange(0.90, 1.25, 0.02):

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

def detect_regime(seq):

    if len(seq) < 80:
        return "NORMAL"

    recent = seq[-40:]
    old = seq[-80:-40]

    rc = Counter(recent)
    oc = Counter(old)

    drift = 0

    for s in set(rc.keys()) | set(oc.keys()):

        r = rc[s] / len(recent)
        o = oc[s] / len(old)

        drift += abs(r - o)

    if drift > 0.28:
        return "VOLATILE"

    return "NORMAL"

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

        self.global_counts.clear()
        self.transitions1.clear()
        self.transitions2.clear()

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

        conf2 = min(total2 / 18, 1.0)
        conf1 = min(total1 / 12, 1.0)

        w2 = 0.55 * conf2
        w1 = 0.30 * conf1
        wg = max(0.15, 1.0 - w2 - w1)

        probs = {}

        for s in self.states:

            p2 = bayesian_prob(
                trans2.get(s,0),
                total2,
                self.alpha,
                len(self.states)
            )

            p1 = bayesian_prob(
                trans1.get(s,0),
                total1,
                self.alpha,
                len(self.states)
            )

            pg = bayesian_prob(
                self.global_counts.get(s,0),
                totalg,
                self.alpha,
                len(self.states)
            )

            probs[s] = (
                w2 * p2 +
                w1 * p1 +
                wg * pg
            )

        total = sum(probs.values())

        return {
            k: v / total
            for k, v in probs.items()
        }

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

        self.hard_stop = False

    def get_bet_size(
        self,
        p,
        odds_total,
        probs
    ):

        if self.hard_stop:
            return 0

        if self.current <= self.initial * 0.35:
            self.hard_stop = True
            return 0

        if self.cooldown > 0:
            self.cooldown -= 1
            return 0

        ent = entropy(probs)

        if ent > 1.02:
            return 0

        b = odds_total - 1
        q = 1 - p

        edge = (b * p) - q

        if edge <= 0.015:
            return 0

        f = edge / b

        f *= 0.10

        f = min(f, 0.025)

        bet = int(self.current * f)

        bet = min(
            bet,
            int(self.current * 0.04)
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
                self.cooldown = 10
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

        if downside_std <= 0:
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

        losses = max(losses, 1e-9)

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

def calc_logloss(probs_list, actuals):

    vals = []

    for probs, actual in zip(
        probs_list,
        actuals
    ):

        p = max(
            probs.get(actual, 1e-12),
            1e-12
        )

        vals.append(-math.log(p))

    return np.mean(vals)

def calc_brier(probs_list, actuals):

    total = 0

    for probs, actual in zip(
        probs_list,
        actuals
    ):

        row = 0

        for s, p in probs.items():

            y = 1 if s == actual else 0

            row += (p - y) ** 2

        total += row

    return total / len(probs_list)

def calc_ece(
    probs_list,
    actuals,
    bins=10
):

    confidences = []
    accuracies = []

    for probs, actual in zip(
        probs_list,
        actuals
    ):

        pred = max(
            probs,
            key=probs.get
        )

        confidences.append(
            probs[pred]
        )

        accuracies.append(
            1 if pred == actual else 0
        )

    confidences = np.array(confidences)
    accuracies = np.array(accuracies)

    edges = np.linspace(0,1,bins+1)

    ece = 0

    for i in range(bins):

        if i == bins - 1:
            mask = (
                (confidences >= edges[i])
                &
                (confidences <= edges[i+1])
            )
        else:
            mask = (
                (confidences >= edges[i])
                &
                (confidences < edges[i+1])
            )

        if np.sum(mask) == 0:
            continue

        avg_conf = np.mean(
            confidences[mask]
        )

        avg_acc = np.mean(
            accuracies[mask]
        )

        ece += (
            np.sum(mask)
            / len(confidences)
        ) * abs(avg_conf - avg_acc)

    return ece

def markov_bootstrap_baseline(
    actuals,
    trials=2000
):

    states = list(set(actuals))

    counts = Counter(actuals)

    probs = np.array([
        counts[s] / len(actuals)
        for s in states
    ])

    accs = []

    for _ in range(trials):

        preds = np.random.choice(
            states,
            size=len(actuals),
            p=probs
        )

        reals = np.random.choice(
            states,
            size=len(actuals),
            p=probs
        )

        accs.append(
            np.mean(preds == reals)
        )

    return np.mean(accs)

def get_color_odds(color):

    if color == "红":
        return 2.7

    return 2.8

def main():

    parser = argparse.ArgumentParser(
        description="V14 FIXED - 实战预测版"
    )

    parser.add_argument(
        "--lottery",
        choices=["老澳门彩","香港彩","新澳门彩"],
        default="新澳门彩"
    )

    parser.add_argument(
        "--recent",
        type=int,
        default=220
    )

    parser.add_argument(
        "--bankroll",
        type=int,
        default=10000
    )

    parser.add_argument(
        "--test",
        type=int,
        default=250
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
            len(color_seq) - 60
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

        for t in range(start, len(color_seq)):

            regime = detect_regime(
                color_seq[:t]
            )

            if regime == "VOLATILE":
                dynamic_recent = 120
            else:
                dynamic_recent = min(
                    args.recent,
                    max(
                        140,
                        int(
                            180 +
                            np.std(
                                np.arange(
                                    max(0,t-100),
                                    t
                                )
                            )
                        )
                    )
                )

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

            historical_probs.append(dict(pred_c))
            historical_actuals.append(actual_c)

            probs_history.append(calibrated)
            actual_history.append(actual_c)

            best_color = max(
                calibrated,
                key=calibrated.get
            )

            best_prob = calibrated[
                best_color
            ]

            odds = get_color_odds(
                best_color
            )

            bet = bank.get_bet_size(
                best_prob,
                odds,
                calibrated
            )

            won = (
                best_color == actual_c
            )

            bank.record_result(
                bet,
                won,
                odds
            )

            if best_color == actual_c:
                color_correct += 1

            if (
                max(pred_s,key=pred_s.get)
                == actual_s
            ):
                size_correct += 1

            if (
                max(pred_o,key=pred_o.get)
                == actual_o
            ):
                odd_correct += 1

        logloss = calc_logloss(
            probs_history,
            actual_history
        )

        brier = calc_brier(
            probs_history,
            actual_history
        )

        ece = calc_ece(
            probs_history,
            actual_history
        )

        mc = markov_bootstrap_baseline(
            actual_history
        )

        profit, roi, winrate = bank.get_stats()

        max_dd = bank.get_max_drawdown()

        pf = bank.get_profit_factor()

        sortino = bank.get_sortino()

        print("\n" + "="*100)

        print(
            f"V14 FIXED "
            f"真WalkForward ({test_len}期)"
        )

        print("-"*100)

        print(
            f"色波准确率 : "
            f"{color_correct/test_len*100:.2f}%"
        )

        print(
            f"大小准确率 : "
            f"{size_correct/test_len*100:.2f}%"
        )

        print(
            f"单双准确率 : "
            f"{odd_correct/test_len*100:.2f}%"
        )

        print("-"*100)

        print(
            f"LogLoss    : {logloss:.6f}"
        )

        print(
            f"BrierScore : {brier:.6f}"
        )

        print(
            f"ECE         : {ece:.6f}"
        )

        print(
            f"BootstrapMC : {mc*100:.2f}%"
        )

        print("-"*100)

        print(
            f"最终资金    : ¥{bank.current:.2f}"
        )

        print(
            f"总盈亏      : ¥{profit:.2f}"
        )

        print(
            f"ROI         : {roi:.2f}%"
        )

        print(
            f"下注次数    : {bank.bet_count}"
        )

        print(
            f"真实胜率    : {winrate:.2f}%"
        )

        print("-"*100)

        print(
            f"MaxDrawdown : {max_dd:.2f}%"
        )

        print(
            f"ProfitFactor: {pf:.4f}"
        )

        print(
            f"SortinoRatio: {sortino:.4f}"
        )

        print("="*100)

        # ====================== 新增：最近10期预测对错 ======================
        print("\n" + "="*90)
        print("📊 【最近10期预测对错记录】")
        print("="*90)
        print(f"{'期号':<6} {'实际波色':<6} {'主推波色':<8} {'结果'}   {'实际大小':<6} {'预测大小':<8} {'结果'}   {'实际单双':<6} {'预测单双':<8} {'结果'}")

        correct_c10 = correct_s10 = correct_o10 = 0
        recent_start = max(0, len(color_seq) - 10)

        for t in range(recent_start, len(color_seq)):
            eng_c = ConditionalMarkov(["红","蓝","绿"], recent_periods=args.recent)
            eng_s = ConditionalMarkov(["大","小"], recent_periods=args.recent)
            eng_o = ConditionalMarkov(["单","双"], recent_periods=args.recent)

            eng_c.train(color_seq[:t])
            eng_s.train(size_seq[:t])
            eng_o.train(odd_seq[:t])

            pred_c = eng_c.predict(color_seq[max(0,t-30):t])
            pred_s = eng_s.predict(size_seq[max(0,t-30):t])
            pred_o = eng_o.predict(odd_seq[max(0,t-30):t])

            best_c = max(pred_c, key=pred_c.get)
            best_s = max(pred_s, key=pred_s.get)
            best_o = max(pred_o, key=pred_o.get)

            actual_c = color_seq[t]
            actual_s = size_seq[t]
            actual_o = odd_seq[t]

            c_ok = "✓" if best_c == actual_c else "✗"
            s_ok = "✓" if best_s == actual_s else "✗"
            o_ok = "✓" if best_o == actual_o else "✗"

            if c_ok == "✓": correct_c10 += 1
            if s_ok == "✓": correct_s10 += 1
            if o_ok == "✓": correct_o10 += 1

            print(f"{t+1:<6} {actual_c:<6} {best_c:<8} {c_ok}      {actual_s:<6} {best_s:<8} {s_ok}      {actual_o:<6} {best_o:<8} {o_ok}")

        print("-"*90)
        print(f"最近10期准确率 → 波色: {correct_c10/10*100:.1f}% | 大小: {correct_s10/10*100:.1f}% | 单双: {correct_o10/10*100:.1f}%")

        # ====================== 新增：下一期实战预测 ======================
        print("\n" + "="*90)
        print(f"🎯 【下一期实战预测 - {args.lottery}】")
        print("="*90)

        eng_c = ConditionalMarkov(["红","蓝","绿"], recent_periods=args.recent)
        eng_s = ConditionalMarkov(["大","小"], recent_periods=args.recent)
        eng_o = ConditionalMarkov(["单","双"], recent_periods=args.recent)

        eng_c.train(color_seq)
        eng_s.train(size_seq)
        eng_o.train(odd_seq)

        pred_c = eng_c.predict(color_seq[-30:])
        pred_s = eng_s.predict(size_seq[-30:])
        pred_o = eng_o.predict(odd_seq[-30:])

        temp = rolling_temperature_search(historical_probs, historical_actuals) if historical_probs else 1.0
        calibrated_c = apply_temperature(pred_c, temp)

        sorted_c = sorted(calibrated_c.items(), key=lambda x: x[1], reverse=True)
        main_c, p_main = sorted_c[0]
        sec_c, p_sec = sorted_c[1] if len(sorted_c) > 1 else (list(calibrated_c.keys())[0], 0.0)

        best_s = max(pred_s, key=pred_s.get)
        best_o = max(pred_o, key=pred_o.get)

        print(f"波色主推 → {main_c}波    概率: {p_main:.1%}")
        print(f"波色次推 → {sec_c}波    概率: {p_sec:.1%}")
        print(f"大小预测 → {best_s}      概率: {pred_s[best_s]:.1%}")
        print(f"单双预测 → {best_o}      概率: {pred_o[best_o]:.1%}")
        print("-"*90)
        print(f"🎯 综合推荐：【{main_c} + {best_s} + {best_o}】")
        print("="*90)

    except Exception as e:
        print(f"错误: {e}")

    finally:
        conn.close()

if __name__ == "__main__":
    main()