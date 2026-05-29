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

RED = {1,2,7,8,12,13,18,19,23,24,29,30,34,35,40,45,46}
BLUE = {3,4,9,10,14,15,20,25,26,31,36,37,41,42,47,48}
GREEN = {5,6,11,16,17,21,22,27,28,32,33,38,39,43,44,49}

def get_color(num):
    if num in RED: return "红"
    if num in BLUE: return "蓝"
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
    conn.execute("""CREATE TABLE IF NOT EXISTS draws(
        issue_no TEXT PRIMARY KEY, draw_date TEXT, numbers_json TEXT,
        special_number INTEGER, source TEXT, created_at TEXT, updated_at TEXT)""")
    conn.commit()

def fetch_json_url(url):
    try:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8", errors="ignore"))
    except:
        return None

def fetch_online_records(lottery_name):
    for url in THIRD_PARTY_URLS:
        payload = fetch_json_url(url)
        if not payload: continue
        target = next((x for x in payload.get("lottery_data", []) if x.get("name") == lottery_name), None)
        if not target: continue
        try:
            latest_time = datetime.strptime(target.get("openTime", ""), "%Y-%m-%d %H:%M:%S")
        except:
            latest_time = datetime.now()
        records = []
        for idx, item in enumerate(target.get("history", [])):
            try:
                parts = item.split("期：")
                if len(parts) != 2: continue
                issue_no = parts[0].strip()
                nums = [int(x.strip()) for x in parts[1].split(",")]
                if len(nums) != 7: continue
                draw_date = (latest_time - timedelta(days=idx)).strftime("%Y-%m-%d")
                records.append(DrawRecord(issue_no, draw_date, nums[:6], nums[6]))
            except:
                continue
        if records: return records, "marksix6"
    print("⚠️ 在线数据获取失败，使用本地数据")
    return [], "local"

def sync_from_records(conn, records, source):
    now = datetime.now(timezone.utc).isoformat()
    ins = upd = 0
    for r in records:
        exist = conn.execute("SELECT 1 FROM draws WHERE issue_no=?", (r.issue_no,)).fetchone()
        if exist:
            conn.execute("UPDATE draws SET draw_date=?,numbers_json=?,special_number=?,source=?,updated_at=? WHERE issue_no=?", 
                        (r.draw_date, json.dumps(r.numbers), r.special_number, source, now, r.issue_no))
            upd += 1
        else:
            conn.execute("INSERT INTO draws VALUES (?,?,?,?,?,?,?)", 
                        (r.issue_no, r.draw_date, json.dumps(r.numbers), r.special_number, source, now, now))
            ins += 1
    conn.commit()
    return len(records), ins, upd

def issue_to_int(issue_no):
    nums = re.sub(r"\D", "", issue_no)
    return int(nums) if nums else 0

def load_sequence(conn, attr_func):
    rows = conn.execute("SELECT issue_no, special_number FROM draws").fetchall()
    rows = sorted(rows, key=lambda r: issue_to_int(r["issue_no"]))
    return [attr_func(r["special_number"]) for r in rows]

# ==================== 基础函数（entropy, bayesian_prob, apply_temperature 等）================
def entropy(probs):
    e = 0
    for p in probs.values():
        p = max(p, 1e-12)
        e -= p * math.log(p)
    return e

def bayesian_prob(count, total, alpha, states):
    return (count + alpha) / (total + alpha * states)

def apply_temperature(probs, temp):
    logits = {k: math.log(max(v, 1e-12)) for k, v in probs.items()}
    scaled = {k: math.exp(v / temp) for k, v in logits.items()}
    total = sum(scaled.values())
    if total <= 0: return {k: 1/len(probs) for k in probs}
    result = {k: v/total for k,v in scaled.items()}
    for k in result: result[k] = min(max(result[k], 0.08), 0.68)
    s = sum(result.values())
    return {k: v/s for k,v in result.items()}

def rolling_temperature_search(probs_hist, actual_hist, window=60):
    if len(probs_hist) < 30: return 1.0
    best_temp, best_loss = 1.0, 999999
    for temp in np.arange(0.90, 1.25, 0.02):
        losses = [-math.log(max(apply_temperature(p, temp).get(a,1e-12),1e-12)) for p,a in zip(probs_hist[-window:], actual_hist[-window:])]
        loss = np.mean(losses)
        if loss < best_loss:
            best_loss = loss
            best_temp = temp
    return best_temp

class ConditionalMarkov:
    def __init__(self, states, alpha=1.5, decay=0.993, recent_periods=220):
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
        for age, i in enumerate(reversed(range(len(seq)))):
            self.global_counts[seq[i]] += self.decay ** age
        for age, i in enumerate(reversed(range(len(seq)-2))):
            a,b,c = seq[i],seq[i+1],seq[i+2]
            w = self.decay ** age
            self.transitions2[(a,b)][c] += w
            self.transitions1[b][c] += w

    def predict(self, recent):
        if len(recent) < 2: return {s:1/len(self.states) for s in self.states}
        a,b = recent[-2], recent[-1]
        trans2 = self.transitions2.get((a,b), Counter())
        trans1 = self.transitions1.get(b, Counter())
        total2,total1,totalg = sum(trans2.values()),sum(trans1.values()),sum(self.global_counts.values())
        w2 = 0.55 * min(total2/18,1)
        w1 = 0.30 * min(total1/12,1)
        wg = max(0.15, 1-w2-w1)
        probs = {}
        for s in self.states:
            p2 = bayesian_prob(trans2.get(s,0),total2,self.alpha,len(self.states))
            p1 = bayesian_prob(trans1.get(s,0),total1,self.alpha,len(self.states))
            pg = bayesian_prob(self.global_counts.get(s,0),totalg,self.alpha,len(self.states))
            probs[s] = w2*p2 + w1*p1 + wg*pg
        total = sum(probs.values())
        return {k:v/total for k,v in probs.items()}

class EnhancedColorMarkov(ConditionalMarkov):
    def predict(self, recent):
        base = super().predict(recent)
        if len(recent) >= 30:
            recent30 = Counter(recent[-30:])
            correction = {k: (len(recent30) - recent30.get(k,0)) / len(recent30) * 0.28 for k in base}
            for k in base:
                base[k] = base[k] * 0.72 + correction.get(k, 0) * 0.28
        total = sum(base.values())
        return {k: v/total for k,v in base.items()}

# KellyBankroll 类（简化版）
class KellyBankroll:
    def __init__(self, initial=10000):
        self.initial = initial
        self.current = initial
        self.history = []
        self.bet_count = self.win_count = 0
        self.loss_streak = self.cooldown = 0
        self.equity_curve = [initial]
        self.hard_stop = False

    def get_bet_size(self, p, odds_total, probs): return 0  # 简化，实战可后续开启
    def record_result(self, bet, won, odds): pass
    def get_stats(self): return 0, 0, 0

def main():
    parser = argparse.ArgumentParser(description="V15 波色优化版")
    parser.add_argument("--lottery", choices=["老澳门彩","香港彩","新澳门彩"], default="新澳门彩")
    parser.add_argument("--recent", type=int, default=220)
    parser.add_argument("--test", type=int, default=100)
    args = parser.parse_args()

    conn = connect_db(SCRIPT_DIR / DB_FILES[args.lottery])
    init_db(conn)

    try:
        records, source = fetch_online_records(args.lottery)
        total, ins, upd = sync_from_records(conn, records, source)
        print(f"{args.lottery} 同步完成 总计:{total} 新增:{ins} 更新:{upd}")

        color_seq = load_sequence(conn, get_color)
        size_seq = load_sequence(conn, get_big_small)
        odd_seq = load_sequence(conn, get_odd_even)

        # ====================== 最近10期预测记录 ======================
        print("\n" + "="*100)
        print("📊 【最近10期预测对错记录】")
        print("="*100)
        print(f"{'期号':<6} {'实际波色':<6} {'主推':<6} {'结果'}  {'实际大小':<6} {'预测':<6} {'结果'}  {'实际单双':<6} {'预测':<6} {'结果'}")

        correct_c = correct_s = correct_o = 0
        recent_start = max(0, len(color_seq) - 10)

        for t in range(recent_start, len(color_seq)):
            eng_c = EnhancedColorMarkov(["红","蓝","绿"], recent_periods=args.recent)
            eng_s = ConditionalMarkov(["大","小"], recent_periods=args.recent)
            eng_o = ConditionalMarkov(["单","双"], recent_periods=args.recent)

            eng_c.train(color_seq[:t])
            eng_s.train(size_seq[:t])
            eng_o.train(odd_seq[:t])

            pred_c = eng_c.predict(color_seq[max(0,t-40):t])
            pred_s = eng_s.predict(size_seq[max(0,t-30):t])
            pred_o = eng_o.predict(odd_seq[max(0,t-30):t])

            best_c = max(pred_c, key=pred_c.get)
            best_s = max(pred_s, key=pred_s.get)
            best_o = max(pred_o, key=pred_o.get)

            ac, as_, ao = color_seq[t], size_seq[t], odd_seq[t]

            c_ok = "✓" if best_c == ac else "✗"
            s_ok = "✓" if best_s == as_ else "✗"
            o_ok = "✓" if best_o == ao else "✗"

            if c_ok == "✓": correct_c += 1
            if s_ok == "✓": correct_s += 1
            if o_ok == "✓": correct_o += 1

            print(f"{t+1:<6} {ac:<6} {best_c:<6} {c_ok}     {as_:<6} {best_s:<6} {s_ok}     {ao:<6} {best_o:<6} {o_ok}")

        print("-"*100)
        print(f"最近10期准确率 → 波色: {correct_c/10*100:.1f}% | 大小: {correct_s/10*100:.1f}% | 单双: {correct_o/10*100:.1f}%")

        # ====================== 下一期实战预测 ======================
        print("\n" + "="*100)
        print(f"🎯 【V15 波色优化版 - 下一期实战预测 - {args.lottery}】")
        print("="*100)

        eng_c = EnhancedColorMarkov(["红","蓝","绿"], recent_periods=args.recent)
        eng_s = ConditionalMarkov(["大","小"], recent_periods=args.recent)
        eng_o = ConditionalMarkov(["单","双"], recent_periods=args.recent)

        eng_c.train(color_seq)
        eng_s.train(size_seq)
        eng_o.train(odd_seq)

        pred_c = eng_c.predict(color_seq[-40:])
        pred_s = eng_s.predict(size_seq[-30:])
        pred_o = eng_o.predict(odd_seq[-30:])

        calibrated_c = apply_temperature(pred_c, 1.0)
        sorted_c = sorted(calibrated_c.items(), key=lambda x: x[1], reverse=True)
        main_c, p_main = sorted_c[0]
        sec_c, p_sec = sorted_c[1]

        best_s = max(pred_s, key=pred_s.get)
        best_o = max(pred_o, key=pred_o.get)

        print(f"波色主推 → {main_c}波    概率: {p_main:.1%}")
        print(f"波色次推 → {sec_c}波    概率: {p_sec:.1%}")
        print(f"大小预测 → {best_s}      概率: {pred_s[best_s]:.1%}")
        print(f"单双预测 → {best_o}      概率: {pred_o[best_o]:.1%}")
        print("-"*100)
        print(f"🎯 综合推荐：【{main_c} + {best_s} + {best_o}】")
        print("="*100)

    except Exception as e:
        print(f"错误: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    main()