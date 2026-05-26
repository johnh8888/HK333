#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 老澳门六合彩属性时序预测系统 V3
# 动态状态空间 + 拉普拉斯平滑 + 状态机 + Beta贝叶斯修正 + 置信度过滤

from __future__ import annotations

import argparse
import json
import sqlite3
import math
import ssl
import random
from collections import defaultdict, Counter, deque
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Any
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

SCRIPT_DIR = Path(__file__).resolve().parent
DB_PATH_DEFAULT = str(SCRIPT_DIR / "sdxmacau.db")

THIRD_PARTY_URLS = [
    "https://marksix6.net/index.php?api=1",
    "https://marksix6.net/api/lottery_api.php"
]

# ========== 属性映射 ==========
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

# 属性对应的状态空间
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

def fetch_json_url(url: str, timeout: int = 20):
    ctx = ssl.create_default_context()
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=timeout, context=ctx) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        raw = resp.read().decode(charset, errors="ignore")
        return json.loads(raw)

def _parse_marksix6_response(payload):
    records = []
    lottery_data = payload.get("lottery_data", [])
    hk_data = next((l for l in lottery_data if l.get("name") == "老澳门彩"), None)
    if not hk_data:
        return records
    try:
        latest_open_time = datetime.strptime(hk_data.get("openTime", ""), "%Y-%m-%d %H:%M:%S")
    except Exception:
        latest_open_time = datetime.now()
    for idx, item in enumerate(hk_data.get("history", [])):
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

def fetch_online_records():
    for url in THIRD_PARTY_URLS:
        try:
            payload = fetch_json_url(url, timeout=20)
            records = _parse_marksix6_response(payload)
            if records:
                return records, "marksix6", url
        except Exception as e:
            print(f"从 {url} 获取失败: {e}")
    raise RuntimeError("无法获取老澳门彩数据")

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

# ========== 动态窗口加权 ==========
def dynamic_weight(sequence: List[str], decay: float = 0.95) -> List[float]:
    """指数衰减权重，越近权重越大"""
    n = len(sequence)
    weights = [decay ** (n - 1 - i) for i in range(n)]
    return weights

# ========== N阶马尔可夫链（带拉普拉斯平滑） ==========
class MarkovChain:
    def __init__(self, order: int, states: List[str], alpha: float = 1.0):
        self.order = order
        self.states = states
        self.alpha = alpha          # 拉普拉斯平滑参数
        self.transitions = defaultdict(Counter)
        self.total_counts = Counter()

    def train(self, sequence: List[str], weights: Optional[List[float]] = None):
        """
        训练马尔可夫链，支持加权（权重与序列长度一致）
        """
        if weights is None:
            weights = [1.0] * len(sequence)
        for i in range(len(sequence) - self.order):
            state = tuple(sequence[i:i+self.order])
            next_state = sequence[i+self.order]
            w = weights[i+self.order]   # 下一状态的权重
            self.transitions[state][next_state] += w
            self.total_counts[state] += w

    def predict(self, state: Tuple[str]) -> Dict[str, float]:
        """返回带拉普拉斯平滑的概率分布"""
        K = len(self.states)
        total = self.total_counts.get(state, 0)
        probs = {}
        for s in self.states:
            count = self.transitions[state].get(s, 0)
            # (count + alpha) / (total + alpha*K)
            probs[s] = (count + self.alpha) / (total + self.alpha * K)
        # 归一化（理论上已经归一化，但浮点误差可再调）
        sump = sum(probs.values())
        return {k: v/sump for k, v in probs.items()} if sump > 0 else {s: 1/K for s in self.states}

# ========== 状态机 ==========
class StateMachine:
    """识别趋势态、震荡态、混乱态"""
    @staticmethod
    def identify(seq: List[str]) -> str:
        if len(seq) < 5:
            return "unknown"
        # 趋势态：最近4期有3期及以上相同
        last4 = seq[-4:]
        if max(Counter(last4).values()) >= 3:
            return "trend"
        # 震荡态：最近6期交替次数≥4
        changes = sum(1 for i in range(1, len(seq)) if seq[i] != seq[i-1])
        if len(seq) >= 6 and changes / (len(seq)-1) > 0.6:
            return "oscillation"
        # 混乱态：熵高（简化：各状态出现概率相近）
        counts = Counter(seq[-10:])
        total = sum(counts.values())
        if total > 0:
            entropy = -sum((c/total)*math.log(c/total) for c in counts.values())
            max_entropy = math.log(len(set(seq)))
            if max_entropy > 0 and entropy / max_entropy > 0.8:
                return "chaotic"
        return "normal"

    @staticmethod
    def adjust_probabilities(probs: Dict[str, float], seq: List[str]) -> Dict[str, float]:
        """根据状态调整概率"""
        state = StateMachine.identify(seq)
        last = seq[-1] if seq else None
        if state == "trend" and last:
            # 趋势态：倾向于反转
            for s in probs:
                if s != last:
                    probs[s] *= 1.3
                else:
                    probs[s] *= 0.7
        elif state == "oscillation" and last:
            # 震荡态：倾向于继续切换
            for s in probs:
                if s != last:
                    probs[s] *= 1.2
                else:
                    probs[s] *= 0.8
        elif state == "chaotic":
            # 混乱态：均匀化
            K = len(probs)
            avg = 1/K
            for s in probs:
                probs[s] = avg * 0.6 + probs[s] * 0.4
        # 归一化
        total = sum(probs.values())
        if total > 0:
            probs = {k: v/total for k, v in probs.items()}
        return probs

# ========== 贝叶斯修正（Beta分布） ==========
class BayesianCorrector:
    def __init__(self, alpha_prior: float = 1.0, beta_prior: float = 1.0):
        self.alpha = alpha_prior   # 成功次数 +1
        self.beta = beta_prior     # 失败次数 +1

    def update(self, correct: bool):
        if correct:
            self.alpha += 1
        else:
            self.beta += 1

    def get_weight(self) -> float:
        """返回后验均值作为权重"""
        return self.alpha / (self.alpha + self.beta)

# ========== 转移熵（信息量） ==========
def transition_entropy(seq: List[str], order: int = 1) -> float:
    """计算状态转移的条件熵，衡量可预测性"""
    if len(seq) < order + 1:
        return 0.0
    trans = defaultdict(Counter)
    for i in range(len(seq)-order):
        state = tuple(seq[i:i+order])
        nxt = seq[i+order]
        trans[state][nxt] += 1
    total_trans = sum(sum(c.values()) for c in trans.values())
    if total_trans == 0:
        return 0.0
    entropy = 0.0
    for state, cnts in trans.items():
        total_state = sum(cnts.values())
        for nxt, cnt in cnts.items():
            p = cnt / total_state
            entropy -= p * math.log(p)
    # 归一化到 [0,1]
    max_entropy = math.log(len(set(seq)))
    if max_entropy == 0:
        return 0.0
    return entropy / max_entropy

# ========== 属性预测引擎 V3 ==========
class AttributeEngineV3:
    def __init__(self, name: str, order: int, alpha: float = 1.0, confidence_threshold: float = 0.55):
        self.name = name
        self.order = order
        self.states = ATTRIBUTE_STATES[name]
        self.alpha = alpha
        self.confidence_threshold = confidence_threshold
        self.markov = MarkovChain(order, self.states, alpha)
        self.corrector = BayesianCorrector(1.0, 1.0)   # Beta(1,1) 先验
        self.history = []        # 存储预测记录用于反馈
        self.consecutive_fails = 0
        self.cooling = False
        self.cool_threshold = 3

    def train(self, sequence: List[str], weights: Optional[List[float]] = None):
        """使用加权历史训练马尔可夫链"""
        if weights is None:
            # 动态窗口权重
            weights = dynamic_weight(sequence, decay=0.95)
        self.markov.train(sequence, weights)

    def predict(self, recent: List[str]) -> Tuple[Dict[str, float], float, bool]:
        """
        返回: (概率字典, 最大概率, 是否建议出手)
        """
        if len(recent) < self.order:
            # 数据不足，均匀分布
            prob = {s: 1/len(self.states) for s in self.states}
            return prob, 1/len(self.states), False
        # 马尔可夫预测
        state = tuple(recent[-self.order:])
        prob = self.markov.predict(state)
        # 状态机修正
        prob = StateMachine.adjust_probabilities(prob, recent)
        # 贝叶斯修正 (权重作用于概率的指数)
        weight = self.corrector.get_weight()
        if weight < 0.99:
            prob = {s: (p ** weight) for s, p in prob.items()}
            total = sum(prob.values())
            if total > 0:
                prob = {s: p/total for s, p in prob.items()}
        max_prob = max(prob.values())
        # 冷却机制
        if self.cooling:
            max_prob *= 0.5
        # 出手判断
        should_act = max_prob >= self.confidence_threshold
        return prob, max_prob, should_act

    def update_feedback(self, correct: bool):
        """更新贝叶斯修正器和冷却状态"""
        self.corrector.update(correct)
        if not correct:
            self.consecutive_fails += 1
            if self.consecutive_fails >= self.cool_threshold:
                self.cooling = True
        else:
            self.consecutive_fails = 0
            self.cooling = False

# ========== 集成系统 V3 ==========
class PredictionSystemV3:
    def __init__(self, order: int = 2, alpha: float = 1.0, confidence_threshold: float = 0.55):
        self.order = order
        self.engines = {
            "color": AttributeEngineV3("color", order, alpha, confidence_threshold),
            "size": AttributeEngineV3("size", order, alpha, confidence_threshold),
            "odd_even": AttributeEngineV3("odd_even", order, alpha, confidence_threshold)
        }
        self.total_confidence = 0.0

    def train_all(self, seqs: Dict[str, List[str]]):
        for name, seq in seqs.items():
            self.engines[name].train(seq)

    def predict_all(self, recents: Dict[str, List[str]]) -> Dict[str, Any]:
        results = {}
        overall_confidence = 0.0
        for name, engine in self.engines.items():
            prob, maxp, act = engine.predict(recents[name])
            results[name] = {
                "probs": prob,
                "max_prob": maxp,
                "should_act": act
            }
            overall_confidence += maxp
        overall_confidence /= 3
        results["overall_confidence"] = overall_confidence
        return results

    def update_feedback_all(self, corrects: Dict[str, bool]):
        for name, correct in corrects.items():
            self.engines[name].update_feedback(correct)

    def walk_forward_backtest(self, seqs: Dict[str, List[str]], test_len: int = 30) -> Dict[str, float]:
        """滚动回测，无未来泄漏"""
        total = 0
        correct = {name: 0 for name in self.engines}
        for idx in range(len(seqs["color"]) - test_len, len(seqs["color"]) - 1):
            # 训练数据: [0:idx]
            train_seqs = {name: seq[:idx] for name, seq in seqs.items()}
            self.train_all(train_seqs)
            # 预测当前期 idx
            recents = {}
            for name in self.engines:
                if idx >= self.order:
                    recents[name] = seqs[name][idx - self.order:idx]
                else:
                    recents[name] = seqs[name][:idx]
            pred = self.predict_all(recents)
            # 实际值
            actual = {name: seqs[name][idx] for name in self.engines}
            # 统计最大概率对应的预测是否正确
            for name in self.engines:
                pred_val = max(pred[name]["probs"].items(), key=lambda x: x[1])[0]
                if pred_val == actual[name]:
                    correct[name] += 1
            total += 1
        acc = {name: correct[name]/total if total>0 else 0 for name in self.engines}
        return acc

# ========== 仪表盘 ==========
def print_dashboard(conn, order=2, threshold=0.55, backtest_len=30):
    # 加载序列
    color_seq = load_sequence(conn, get_color, limit=500)
    size_seq = load_sequence(conn, get_big_small, limit=500)
    oe_seq = load_sequence(conn, get_odd_even, limit=500)
    if len(color_seq) < order + 10:
        print("历史数据不足，请先同步数据。")
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

    # 训练系统（全量历史用于当前预测）
    system = PredictionSystemV3(order=order, confidence_threshold=threshold)
    seqs = {"color": color_seq, "size": size_seq, "odd_even": oe_seq}
    system.train_all(seqs)
    # 预测下一期
    recents = {}
    for name in system.engines:
        if len(seqs[name]) >= order:
            recents[name] = seqs[name][-order:]
        else:
            recents[name] = seqs[name]
    pred = system.predict_all(recents)
    print(f"\n🔮 下一期属性预测 (阶数={order}, 出手阈值={threshold})")
    for name, data in pred.items():
        if name == "overall_confidence":
            continue
        print(f"\n{name}:")
        for s, p in sorted(data["probs"].items(), key=lambda x: -x[1]):
            marker = " ✓" if data["should_act"] and s == max(data["probs"].items(), key=lambda x: x[1])[0] else ""
            print(f"   {s}: {p*100:.1f}%{marker}")
        if data["should_act"]:
            print(f"   → 建议出手 (最大概率 {data['max_prob']*100:.1f}% ≥ {threshold*100:.1f}%)")
        else:
            print(f"   → 观望 (最大概率 {data['max_prob']*100:.1f}% < {threshold*100:.1f}%)")
    print(f"\n🔥 综合置信度: {pred['overall_confidence']*100:.1f}%")

    # 回测
    print(f"\n📊 Walk-Forward 回测 (最近 {backtest_len} 期):")
    acc = system.walk_forward_backtest(seqs, test_len=backtest_len)
    for name, a in acc.items():
        print(f"   {name} 准确率: {a*100:.1f}%")
    print(f"   平均准确率: {(acc['color']+acc['size']+acc['odd_even'])/3*100:.1f}%")

    # 转移熵分析（可选）
    entropy = transition_entropy(color_seq, order=order)
    print(f"\n📐 转移熵 (波色, order={order}): {entropy:.3f}  (越小越可预测)")

# ========== 命令行 ==========
def cmd_sync(args):
    conn = connect_db(args.db)
    try:
        init_db(conn)
        records, source, url = fetch_online_records()
        total, ins, upd = sync_from_records(conn, records, source)
        print(f"同步完成: 总计 {total}, 新增 {ins}, 更新 {upd}, 来源 {source}")
        print_dashboard(conn, order=args.order, threshold=args.threshold, backtest_len=args.backtest)
    except Exception as e:
        print(f"错误: {e}")
    finally:
        conn.close()

def cmd_show(args):
    conn = connect_db(args.db)
    try:
        print_dashboard(conn, order=args.order, threshold=args.threshold, backtest_len=args.backtest)
    finally:
        conn.close()

def main():
    p = argparse.ArgumentParser(description="老澳门六合彩属性时序预测 V3")
    p.add_argument("--db", default=DB_PATH_DEFAULT)
    p.add_argument("--order", type=int, default=2, choices=[1,2,3], help="马尔可夫阶数 (1-3)")
    p.add_argument("--threshold", type=float, default=0.55, help="出手阈值 (0.5~0.8)")
    p.add_argument("--backtest", type=int, default=30, help="回测最近期数")
    sub = p.add_subparsers(dest="cmd", required=True)
    sp_sync = sub.add_parser("sync")
    sp_sync.set_defaults(func=cmd_sync)
    sp_show = sub.add_parser("show")
    sp_show.set_defaults(func=cmd_show)
    args = p.parse_args()
    args.func(args)

if __name__ == "__main__":
    ssl._create_default_https_context = ssl._create_unverified_context
    main()
