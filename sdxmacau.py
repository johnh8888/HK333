#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 三彩种属性预测 (老澳门 / 香港 / 新澳门)
# 同时同步数据、训练模型、输出预测

from __future__ import annotations

import argparse
import json
import sqlite3
import math
import ssl
import sys
import time
from collections import defaultdict, Counter, deque
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Any
from urllib.request import Request, urlopen

try:
    import numpy as np
except ImportError:
    print("错误：需要安装 numpy。请运行: pip install numpy")
    sys.exit(1)

# ========== 配置 ==========
SCRIPT_DIR = Path(__file__).resolve().parent

# 数据库文件名映射
DB_FILES = {
    "老澳门彩": "old_macau.db",
    "香港彩": "hk_macau.db",
    "新澳门彩": "xin_macau.db"
}

THIRD_PARTY_URLS = [
    "https://marksix6.net/index.php?api=1",
    "https://marksix6.net/api/lottery_api.php"
]

# 波色 / 属性映射（三彩种通用）
def get_color(num: int) -> str:
    RED = {1,2,7,8,12,13,18,19,23,24,29,30,34,35,40,45,46}
    BLUE = {3,4,9,10,14,15,20,25,26,31,36,37,41,42,47,48}
    GREEN = {5,6,11,16,17,21,22,27,28,32,33,38,39,43,44,49}
    if num in RED: return "红"
    if num in BLUE: return "蓝"
    return "绿"

def get_big_small(num: int) -> str:
    return "大" if num >= 25 else "小"

def get_odd_even(num: int) -> str:
    return "单" if num % 2 else "双"

ATTRIBUTE_STATES = {
    "color": ["红", "蓝", "绿"],
    "size": ["大", "小"],
    "odd_even": ["单", "双"]
}

# ========== 数据层 ==========
@dataclass
class DrawRecord:
    issue_no: str
    draw_date: str
    numbers: List[int]
    special_number: int

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()

def connect_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def init_db(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS draws (
            issue_no TEXT PRIMARY KEY,
            draw_date TEXT NOT NULL,
            numbers_json TEXT NOT NULL,
            special_number INTEGER NOT NULL,
            source TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.commit()

def fetch_json_url(url: str, timeout: int = 30, retries: int = 2):
    ctx = ssl.create_default_context()
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    for attempt in range(retries + 1):
        try:
            with urlopen(req, timeout=timeout, context=ctx) as resp:
                charset = resp.headers.get_content_charset() or "utf-8"
                raw = resp.read().decode(charset, errors="ignore")
                return json.loads(raw)
        except Exception as e:
            if attempt < retries:
                time.sleep(1)
                continue
            raise e
    raise RuntimeError(f"无法获取 {url}")

def parse_response(payload, lottery_name: str):
    """从 API 返回中提取指定彩种的历史记录"""
    records = []
    lottery_data = payload.get("lottery_data", [])
    target = next((l for l in lottery_data if l.get("name") == lottery_name), None)
    if not target:
        return records
    try:
        latest_open_time = datetime.strptime(target.get("openTime", ""), "%Y-%m-%d %H:%M:%S")
    except Exception:
        latest_open_time = datetime.now()
    for idx, item in enumerate(target.get("history", [])):
        try:
            parts = item.split("期：")
            if len(parts) != 2: continue
            issue_no = parts[0].strip()
            nums = [int(n.strip()) for n in parts[1].split(",")]
            if len(nums) != 7: continue
            draw_date = (latest_open_time - timedelta(days=idx)).strftime("%Y-%m-%d")
            records.append(DrawRecord(issue_no, draw_date, nums[:6], nums[6]))
        except Exception:
            continue
    return records

def fetch_online_records(lottery_name: str):
    for url in THIRD_PARTY_URLS:
        try:
            payload = fetch_json_url(url, timeout=30, retries=2)
            records = parse_response(payload, lottery_name)
            if records:
                return records, "marksix6", url
        except Exception as e:
            print(f"从 {url} 获取 {lottery_name} 数据失败: {e}")
    raise RuntimeError(f"无法获取 {lottery_name} 数据")

def upsert_draw(conn, record, source):
    now = utc_now()
    if conn.execute("SELECT 1 FROM draws WHERE issue_no=?", (record.issue_no,)).fetchone():
        conn.execute("""UPDATE draws SET draw_date=?, numbers_json=?, special_number=?, source=?, updated_at=?
                        WHERE issue_no=?""",
                     (record.draw_date, json.dumps(record.numbers), record.special_number, source, now, record.issue_no))
        return "updated"
    else:
        conn.execute("""INSERT INTO draws VALUES (?,?,?,?,?,?,?)""",
                     (record.issue_no, record.draw_date, json.dumps(record.numbers), record.special_number, source, now, now))
        return "inserted"

def sync_from_records(conn, records, source):
    ins = upd = 0
    for r in records:
        res = upsert_draw(conn, r, source)
        if res == "inserted": ins += 1
        else: upd += 1
    conn.commit()
    return len(records), ins, upd

def load_sequence(conn, attr_func, limit: int = 500) -> List[str]:
    rows = conn.execute("SELECT special_number FROM draws ORDER BY draw_date ASC, issue_no ASC LIMIT ?", (limit,)).fetchall()
    return [attr_func(r["special_number"]) for r in rows]

def dynamic_weights(n: int, decay: float = 0.95) -> List[float]:
    return [decay ** (n - 1 - i) for i in range(n)]

# ========== 三个核心模型 ==========
class MarkovN:
    def __init__(self, order: int, states: List[str], alpha: float = 1.0):
        self.order = order
        self.states = states
        self.alpha = alpha
        self.counts = defaultdict(Counter)
        self.total = defaultdict(int)

    def train(self, seq: List[str], weights: List[float]):
        for i in range(len(seq) - self.order):
            state = tuple(seq[i:i+self.order])
            nxt = seq[i+self.order]
            w = weights[i+self.order]
            self.counts[state][nxt] += w
            self.total[state] += w

    def predict(self, context: Tuple[str]) -> Dict[str, float]:
        K = len(self.states)
        total = self.total.get(context, 0)
        probs = {}
        for s in self.states:
            cnt = self.counts[context].get(s, 0)
            probs[s] = (cnt + self.alpha) / (total + self.alpha * K)
        sum_p = sum(probs.values())
        return {s: p/sum_p for s, p in probs.items()} if sum_p>0 else {s:1/K for s in self.states}

class StreakBias:
    def __init__(self, states: List[str], alpha: float = 1.0):
        self.states = states
        self.alpha = alpha
        self.streak_counts = defaultdict(lambda: defaultdict(Counter))

    def update(self, seq: List[str]):
        i = 0
        while i < len(seq):
            j = i
            while j < len(seq) and seq[j] == seq[i]:
                j += 1
            length = j - i
            state = seq[i]
            if j < len(seq):
                nxt = seq[j]
                self.streak_counts[state][length][nxt] += 1
            i = j

    def predict(self, last: str, streak_len: int) -> Dict[str, float]:
        K = len(self.states)
        total = sum(self.streak_counts[last][streak_len].values())
        probs = {}
        for s in self.states:
            cnt = self.streak_counts[last][streak_len].get(s, 0)
            probs[s] = (cnt + self.alpha) / (total + self.alpha * K)
        sum_p = sum(probs.values())
        return {s: p/sum_p for s, p in probs.items()} if sum_p>0 else {s:1/K for s in self.states}

class FrequencyPrior:
    def __init__(self, states: List[str]):
        self.states = states
        self.probs = {s: 1/len(states) for s in states}

    def train(self, seq: List[str]):
        cnt = Counter(seq)
        total = len(seq)
        if total > 0:
            self.probs = {s: cnt[s]/total for s in self.states}
        sum_p = sum(self.probs.values())
        self.probs = {s: p/sum_p for s, p in self.probs.items()}

    def predict(self) -> Dict[str, float]:
        return self.probs.copy()

# ========== 贝叶斯集成（基于对数似然的Dirichlet权重） ==========
class BayesianEnsemble:
    def __init__(self, model_names: List[str], alpha_prior: float = 1.0, temperature: float = 1.0):
        self.models = model_names
        self.alpha_prior = alpha_prior
        self.temperature = temperature
        self.log_likelihood = {m: 0.0 for m in model_names}
        self.counts = {m: 0 for m in model_names}

    def update(self, model_probs: Dict[str, Dict[str, float]], actual: str):
        for m in self.models:
            prob = model_probs[m].get(actual, 1e-10)
            self.log_likelihood[m] += math.log(prob)
            self.counts[m] += 1

    def get_weights(self) -> Dict[str, float]:
        weights = {}
        for m in self.models:
            if self.counts[m] > 0:
                avg_ll = self.log_likelihood[m] / self.counts[m]
                weights[m] = math.exp(avg_ll / self.temperature)
            else:
                weights[m] = 1.0
        total = sum(weights.values()) + self.alpha_prior * len(self.models)
        return {m: (weights[m] + self.alpha_prior) / total for m in self.models}

# ========== 属性预测引擎 ==========
class AttributeEngine:
    def __init__(self, name: str, order: int = 3, alpha: float = 1.0, temperature: float = 1.0):
        self.name = name
        self.states = ATTRIBUTE_STATES[name]
        self.order = order
        self.markov = MarkovN(order, self.states, alpha)
        self.streak = StreakBias(self.states, alpha)
        self.freq = FrequencyPrior(self.states)
        self.ensemble = BayesianEnsemble(["markov", "streak", "freq"], alpha_prior=1.0, temperature=temperature)

    def train(self, seq: List[str]):
        weights = dynamic_weights(len(seq))
        self.markov.train(seq, weights)
        self.streak.update(seq)
        self.freq.train(seq)

    def predict_proba(self, recent: List[str]) -> Tuple[Dict[str, float], Dict[str, float]]:
        # 获取上下文和连续长度
        if len(recent) >= self.order:
            context = tuple(recent[-self.order:])
        else:
            context = tuple(recent) if recent else tuple()
        markov_probs = self.markov.predict(context) if context else {s:1/len(self.states) for s in self.states}
        if recent:
            last = recent[-1]
            streak_len = 1
            for i in range(len(recent)-2, -1, -1):
                if recent[i] == last:
                    streak_len += 1
                else:
                    break
        else:
            last = None
            streak_len = 1
        streak_probs = self.streak.predict(last, streak_len) if last else {s:1/len(self.states) for s in self.states}
        freq_probs = self.freq.predict()
        model_probs = {"markov": markov_probs, "streak": streak_probs, "freq": freq_probs}
        weights = self.ensemble.get_weights()
        fused = {}
        for s in self.states:
            fused[s] = weights["markov"] * markov_probs[s] + \
                       weights["streak"] * streak_probs[s] + \
                       weights["freq"] * freq_probs[s]
        total = sum(fused.values())
        fused = {s: p/total for s, p in fused.items()}
        return fused, model_probs

    def update_feedback(self, model_probs: Dict[str, Dict[str, float]], actual: str):
        self.ensemble.update(model_probs, actual)

# ========== 系统集成 ==========
class PredictionSystem:
    def __init__(self, order: int = 3, min_confidence: float = 0.55, temperature: float = 1.0):
        self.order = order
        self.min_confidence = min_confidence
        self.temperature = temperature
        self.engines = {
            "color": AttributeEngine("color", order, temperature=temperature),
            "size": AttributeEngine("size", order, temperature=temperature),
            "odd_even": AttributeEngine("odd_even", order, temperature=temperature)
        }

    def train_all(self, seqs: Dict[str, List[str]]):
        for name, seq in seqs.items():
            self.engines[name].train(seq)

    def predict_all(self, recents: Dict[str, List[str]]) -> Dict[str, Any]:
        results = {}
        for name, engine in self.engines.items():
            probs, model_probs = engine.predict_proba(recents[name])
            results[name] = {
                "probs": probs,
                "model_probs": model_probs,
                "max_prob": max(probs.values()),
                "best_state": max(probs.items(), key=lambda x: x[1])[0],
                "second_state": sorted(probs.items(), key=lambda x: -x[1])[1][0] if len(probs)>=2 else None
            }
        avg_max_prob = np.mean([results[name]["max_prob"] for name in self.engines])
        should_act = avg_max_prob >= self.min_confidence
        results["meta"] = {"should_act": should_act, "reason": f"avg_max_prob={avg_max_prob:.3f}"}
        return results

    def update_feedback_all(self, actuals: Dict[str, str], predictions: Dict[str, Any]):
        for name, engine in self.engines.items():
            engine.update_feedback(predictions[name]["model_probs"], actuals[name])

    def walk_forward_backtest(self, seqs: Dict[str, List[str]], test_len: int = 30) -> Tuple[Dict[str, float], Dict[str, float], float]:
        total = 0
        correct = {name: 0 for name in self.engines}
        logloss_sum = {name: 0.0 for name in self.engines}
        color_second_correct = 0
        min_len = self.order + 10
        for idx in range(min_len, len(seqs["color"]) - 1):
            if idx < len(seqs["color"]) - test_len:
                continue
            system = PredictionSystem(order=self.order, min_confidence=self.min_confidence, temperature=self.temperature)
            train_seqs = {name: seq[:idx] for name, seq in seqs.items()}
            system.train_all(train_seqs)
            recents = {name: seqs[name][idx-self.order:idx] if idx>=self.order else seqs[name][:idx] for name in self.engines}
            pred = system.predict_all(recents)
            actuals = {name: seqs[name][idx] for name in self.engines}
            if pred["meta"]["should_act"]:
                for name in self.engines:
                    prob = pred[name]["probs"].get(actuals[name], 1e-10)
                    logloss_sum[name] += -math.log(prob)
                    if pred[name]["best_state"] == actuals[name]:
                        correct[name] += 1
                if pred["color"]["best_state"] == actuals["color"] or pred["color"]["second_state"] == actuals["color"]:
                    color_second_correct += 1
                total += 1
        if total == 0:
            return {name: 0.0 for name in self.engines}, {name: 0.0 for name in self.engines}, 0.0
        acc = {name: correct[name]/total for name in self.engines}
        avg_logloss = {name: logloss_sum[name]/total for name in self.engines}
        return acc, avg_logloss, color_second_correct/total

# ========== 处理单个彩种 ==========
def process_lottery(lottery_name: str, db_path: str, order: int, min_conf: float,
                    temperature: float, backtest_len: int, skip_sync: bool = False):
    print(f"\n{'='*60}\n处理彩种: {lottery_name}\n{'='*60}")
    conn = connect_db(db_path)
    try:
        if not skip_sync:
            init_db(conn)
            records, source, url = fetch_online_records(lottery_name)
            total, ins, upd = sync_from_records(conn, records, source)
            print(f"同步完成: 总计 {total}, 新增 {ins}, 更新 {upd}, 来源 {source} ({url})")
        else:
            print("跳过同步，仅使用已有数据")

        # 加载序列
        seqs = {
            "color": load_sequence(conn, get_color, limit=500),
            "size": load_sequence(conn, get_big_small, limit=500),
            "odd_even": load_sequence(conn, get_odd_even, limit=500)
        }
        if len(seqs["color"]) < order + 10:
            print("历史数据不足，跳过预测")
            return

        # 最新开奖
        latest = conn.execute("SELECT * FROM draws ORDER BY draw_date DESC, issue_no DESC LIMIT 1").fetchone()
        if latest:
            nums = " ".join(f"{n:02d}" for n in json.loads(latest["numbers_json"]))
            print(f"最新开奖: {latest['issue_no']} | {nums} + {latest['special_number']:02d}")
            attrs = {
                "色波": get_color(latest["special_number"]),
                "大小": get_big_small(latest["special_number"]),
                "单双": get_odd_even(latest["special_number"]),
            }
            print(f"特码属性: {attrs['单双']} {attrs['大小']} {attrs['色波']}")

        # 训练并预测
        system = PredictionSystem(order=order, min_confidence=min_conf, temperature=temperature)
        system.train_all(seqs)
        recents = {name: seqs[name][-order:] for name in seqs}
        pred = system.predict_all(recents)

        print(f"\n🔮 下一期属性预测 (阶数={order}, 温度={temperature})")
        for name, data in pred.items():
            if name == "meta":
                continue
            print(f"\n{name}:")
            for s, p in sorted(data["probs"].items(), key=lambda x: -x[1]):
                marker = " ✓" if s == data["best_state"] else ""
                print(f"   {s}: {p*100:.1f}%{marker}")
        meta = pred["meta"]
        print(f"\n🧠 元决策: {'出手' if meta['should_act'] else '观望'}")
        print(f"   原因: {meta['reason']}")

        # 回测
        print(f"\n📊 无泄漏 Walk-Forward 回测 (最近 {backtest_len} 期):")
        acc, logloss, color_second_acc = system.walk_forward_backtest(seqs, test_len=backtest_len)
        for name in acc:
            print(f"   {name} 准确率: {acc[name]*100:.1f}%  平均LogLoss: {logloss[name]:.4f}")
        print(f"   波色二中一准确率: {color_second_acc*100:.1f}%")
        if any(acc.values()):
            print(f"   平均准确率: {np.mean(list(acc.values()))*100:.1f}%")
            print(f"   平均LogLoss: {np.mean(list(logloss.values())):.4f} (越小越好)")
        else:
            print("   未出手，无数据")
    except Exception as e:
        print(f"处理 {lottery_name} 时出错: {e}")
    finally:
        conn.close()

# ========== 命令行 ==========
def cmd_run(args):
    # 确定要处理的彩种列表
    if args.lottery:
        lotteries = [args.lottery]
    else:
        lotteries = ["老澳门彩", "香港彩", "新澳门彩"]
    for lottery in lotteries:
        db_path = str(SCRIPT_DIR / DB_FILES[lottery])
        process_lottery(lottery, db_path, args.order, args.min_conf, args.temp,
                        args.backtest, skip_sync=args.skip_sync)

def main():
    p = argparse.ArgumentParser(description="三彩种属性预测 (老澳门/香港/新澳门)")
    p.add_argument("--lottery", choices=["老澳门彩", "香港彩", "新澳门彩"],
                   help="指定单个彩种，不指定则处理全部三个")
    p.add_argument("--order", type=int, default=3, help="马尔可夫阶数 (默认3)")
    p.add_argument("--min-conf", type=float, default=0.55, help="出手最小平均概率阈值")
    p.add_argument("--temp", type=float, default=1.0, help="模型融合温度 (默认1.0)")
    p.add_argument("--backtest", type=int, default=30, help="回测最近期数 (默认30)")
    p.add_argument("--skip-sync", action="store_true", help="跳过数据同步，仅使用已有数据库")
    p.add_argument("--db-dir", default=str(SCRIPT_DIR), help="数据库存放目录")
    args = p.parse_args()
    cmd_run(args)

if __name__ == "__main__":
    main()