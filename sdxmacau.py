#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 老澳门六合彩属性时序预测系统 V2 - 仅依赖标准库

from __future__ import annotations

import argparse
import json
import sqlite3
import math
from collections import defaultdict, Counter, deque
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

SCRIPT_DIR = Path(__file__).resolve().parent
DB_PATH_DEFAULT = str(SCRIPT_DIR / "macau.db")

# 数据源（只取老澳门彩）
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
        conn.execute("UPDATE draws SET draw_date=?, numbers_json=?, special_number=?, source=?, updated_at=? WHERE issue_no=?",
                     (record.draw_date, json.dumps(record.numbers), record.special_number, source, now, record.issue_no))
        return "updated"
    else:
        conn.execute("INSERT INTO draws VALUES (?,?,?,?,?,?,?)",
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
    """从数据库加载指定属性的历史序列（按时间升序）"""
    rows = conn.execute("SELECT special_number FROM draws ORDER BY draw_date ASC, issue_no ASC LIMIT ?", (limit,)).fetchall()
    return [attr_func(r["special_number"]) for r in rows]

# ========== N阶马尔可夫模型 ==========
class MarkovChain:
    def __init__(self, order: int = 2):
        self.order = order
        self.transitions = defaultdict(Counter)  # key: tuple(state), value: Counter(next_state)
        self.total_counts = Counter()  # 每个状态总出现次数

    def train(self, sequence: List[str]):
        """训练马尔可夫链"""
        for i in range(len(sequence) - self.order):
            state = tuple(sequence[i:i+self.order])
            next_state = sequence[i+self.order]
            self.transitions[state][next_state] += 1
            self.total_counts[state] += 1

    def predict(self, state: Tuple[str]) -> Dict[str, float]:
        """返回下一状态的概率分布"""
        if state not in self.transitions:
            return {}  # 无统计
        total = self.total_counts[state]
        if total == 0:
            return {}
        return {s: cnt/total for s, cnt in self.transitions[state].items()}

# ========== 周期统计与状态机 ==========
class CycleAnalyzer:
    @staticmethod
    def consecutive_count(seq: List[str]) -> int:
        """返回最后一项的连续出现次数（从末尾往前数）"""
        if not seq:
            return 0
        last = seq[-1]
        cnt = 1
        for i in range(len(seq)-2, -1, -1):
            if seq[i] == last:
                cnt += 1
            else:
                break
        return cnt

    @staticmethod
    def oscillation_score(seq: List[str]) -> float:
        """震荡评分：0-1，越高表示越可能震荡（频繁切换）"""
        if len(seq) < 5:
            return 0.0
        changes = sum(1 for i in range(1, len(seq)) if seq[i] != seq[i-1])
        return changes / (len(seq) - 1)

    @staticmethod
    def trend_factor(seq: List[str]) -> float:
        """趋势修正因子：连续相同越多，倾向于反转"""
        consec = CycleAnalyzer.consecutive_count(seq)
        if consec >= 4:
            return 0.3  # 强烈倾向反转
        if consec == 3:
            return 0.6
        if consec == 2:
            return 0.9
        return 1.0

    @staticmethod
    def oscillation_factor(seq: List[str]) -> float:
        """震荡修正：当交替频繁时，倾向延续震荡模式"""
        osc = CycleAnalyzer.oscillation_score(seq)
        if osc > 0.7:
            # 高震荡，下一期可能继续切换
            return 1.2
        if osc > 0.5:
            return 1.1
        return 1.0

# ========== 贝叶斯动态修正 ==========
class BayesianCorrector:
    def __init__(self, decay: float = 0.9, window: int = 10):
        self.decay = decay
        self.window = window
        self.history = deque(maxlen=window)  # 存储每期预测是否正确 (1/0)
        self.current_weight = 1.0

    def update(self, correct: bool):
        self.history.append(1 if correct else 0)
        # 计算最近准确率
        if len(self.history) >= 5:
            acc = sum(self.history) / len(self.history)
            # 权重 = 0.5 + acc*0.5，范围 [0.5, 1.0]
            self.current_weight = 0.5 + acc * 0.5
        else:
            self.current_weight = 1.0

    def get_weight(self) -> float:
        return self.current_weight

# ========== 属性预测引擎 ==========
class AttributeEngine:
    def __init__(self, order: int = 2, cool_threshold: int = 3):
        self.order = order
        self.cool_threshold = cool_threshold
        self.consecutive_fails = 0   # 连续失败次数
        self.cooling = False

    def train(self, sequence: List[str]):
        """训练马尔可夫链"""
        self.markov = MarkovChain(self.order)
        self.markov.train(sequence)

    def predict_proba(self, recent_states: List[str]) -> Dict[str, float]:
        """返回下一状态的概率分布，考虑周期修正和状态机"""
        if len(recent_states) < self.order:
            # 数据不足，返回均匀分布
            return {s: 1/3 for s in ["红","蓝","绿"]}  # 注意颜色替换

        # 1. 马尔可夫基础概率
        state = tuple(recent_states[-self.order:])
        markov_probs = self.markov.predict(state)
        if not markov_probs:
            return {s: 1/3 for s in ["红","蓝","绿"]}

        # 2. 周期修正
        consec = CycleAnalyzer.consecutive_count(recent_states)
        trend_factor = CycleAnalyzer.trend_factor(recent_states)
        osc_factor = CycleAnalyzer.oscillation_factor(recent_states)

        # 获取当前最后一项，用于反向倾向计算
        last = recent_states[-1]
        # 定义所有可能的状态
        all_states = list(markov_probs.keys())
        # 如果只有两个状态（大小只有大小），手动补全
        if "红" not in all_states and "蓝" not in all_states and "绿" not in all_states:
            all_states = ["红","蓝","绿"]

        # 修正：连续相同则提高反转概率
        adjusted = {}
        for s in all_states:
            base = markov_probs.get(s, 0)
            if s != last:
                # 反转倾向增强
                adjusted[s] = base * (1 + (1 - trend_factor) * 0.5)
            else:
                adjusted[s] = base * trend_factor
        # 归一化
        total = sum(adjusted.values())
        if total == 0:
            return {s: 1/len(all_states) for s in all_states}
        for s in adjusted:
            adjusted[s] /= total

        # 震荡修正：如果震荡评分高，提高切换概率（忽略连续）
        if osc_factor > 1.0:
            # 提高与最后一项不同的状态概率
            for s in adjusted:
                if s != last:
                    adjusted[s] *= osc_factor
            total = sum(adjusted.values())
            for s in adjusted:
                adjusted[s] /= total

        return adjusted

# ========== 系统集成（波色/大小/单双） ==========
class PredictionSystem:
    def __init__(self, order: int = 2):
        self.engines = {
            "color": AttributeEngine(order),
            "size": AttributeEngine(order),
            "odd_even": AttributeEngine(order)
        }
        self.correctors = {
            "color": BayesianCorrector(),
            "size": BayesianCorrector(),
            "odd_even": BayesianCorrector()
        }
        self.order = order

    def train_all(self, color_seq: List[str], size_seq: List[str], oe_seq: List[str]):
        for name, seq in [("color", color_seq), ("size", size_seq), ("odd_even", oe_seq)]:
            self.engines[name].train(seq)

    def predict(self, recent_color: List[str], recent_size: List[str], recent_oe: List[str]) -> Dict:
        """返回三个属性的概率分布及综合置信度"""
        results = {}
        confidences = {}
        for name, engine in self.engines.items():
            recent = {"color": recent_color, "size": recent_size, "odd_even": recent_oe}[name]
            proba = engine.predict_proba(recent)
            # 应用贝叶斯修正权重
            weight = self.correctors[name].get_weight()
            # 修正概率：原概率^weight 后归一化（使低权重分布更平坦）
            if weight < 0.99:
                adjusted = {k: (v ** weight) for k, v in proba.items()}
                total = sum(adjusted.values())
                if total > 0:
                    proba = {k: v/total for k, v in adjusted.items()}
            results[name] = proba
            # 置信度 = 最大概率 * 权重 * (1 - 冷却因子)
            maxp = max(proba.values())
            engine_obj = self.engines[name]
            if engine_obj.cooling:
                cool_factor = 0.5
            else:
                cool_factor = 1.0
            confidences[name] = maxp * weight * cool_factor

        # 综合置信度 = 三个置信度的平均值
        overall_conf = sum(confidences.values()) / 3
        return {
            "波色": results["color"],
            "大小": results["size"],
            "单双": results["odd_even"],
            "综合置信度": overall_conf,
            "各属性置信度": confidences
        }

    def update_feedback(self, attr: str, correct: bool):
        """更新贝叶斯修正器，并根据连续失败触发冷却"""
        self.correctors[attr].update(correct)
        engine = self.engines[attr]
        if not correct:
            engine.consecutive_fails += 1
            if engine.consecutive_fails >= engine.cool_threshold:
                engine.cooling = True
        else:
            engine.consecutive_fails = 0
            engine.cooling = False

# ========== 回测与展示 ==========
def backtest_system(conn, order=2, test_len=30):
    """滑动窗口回测，返回平均准确率"""
    color_seq = load_sequence(conn, get_color, limit=500)
    size_seq = load_sequence(conn, get_big_small, limit=500)
    oe_seq = load_sequence(conn, get_odd_even, limit=500)

    if len(color_seq) < order + test_len + 5:
        return None

    system = PredictionSystem(order)
    total_correct = {"color": 0, "size": 0, "odd_even": 0}
    total_count = 0

    # 从足够长的训练集开始
    train_len = len(color_seq) - test_len
    # 训练初始模型
    system.train_all(color_seq[:train_len], size_seq[:train_len], oe_seq[:train_len])

    for i in range(train_len, len(color_seq) - 1):
        # 用历史序列预测下一期
        recent_c = color_seq[max(0, i-order):i]
        recent_s = size_seq[max(0, i-order):i]
        recent_o = oe_seq[max(0, i-order):i]
        pred = system.predict(recent_c, recent_s, recent_o)
        actual_c = color_seq[i]
        actual_s = size_seq[i]
        actual_o = oe_seq[i]

        # 取最大概率的预测作为主预测
        pred_c = max(pred["波色"].items(), key=lambda x: x[1])[0]
        pred_s = max(pred["大小"].items(), key=lambda x: x[1])[0]
        pred_o = max(pred["单双"].items(), key=lambda x: x[1])[0]

        correct_c = (pred_c == actual_c)
        correct_s = (pred_s == actual_s)
        correct_o = (pred_o == actual_o)

        total_correct["color"] += correct_c
        total_correct["size"] += correct_s
        total_correct["odd_even"] += correct_o
        total_count += 1

        # 反馈更新系统
        system.update_feedback("color", correct_c)
        system.update_feedback("size", correct_s)
        system.update_feedback("odd_even", correct_o)

    acc = {k: v/total_count for k, v in total_correct.items()}
    return acc, total_count

def print_dashboard(conn, order=2, backtest_limit=30):
    # 加载序列
    color_seq = load_sequence(conn, get_color, limit=300)
    size_seq = load_sequence(conn, get_big_small, limit=300)
    oe_seq = load_sequence(conn, get_odd_even, limit=300)
    if len(color_seq) < order + 10:
        print("历史数据不足，请先同步数据。")
        return

    # 最新开奖
    latest = conn.execute("SELECT * FROM draws ORDER BY draw_date DESC, issue_no DESC LIMIT 1").fetchone()
    if latest:
        nums = " ".join(f"{n:02d}" for n in json.loads(latest["numbers_json"]))
        print(f"最新开奖: {latest['issue_no']} | {nums} + {latest['special_number']:02d}")
        # 属性展示
        attrs = {
            "色波": get_color(latest["special_number"]),
            "大小": get_big_small(latest["special_number"]),
            "单双": get_odd_even(latest["special_number"]),
        }
        print(f"特码属性: {attrs['单双']} {attrs['大小']} {attrs['色波']}")

    # 训练系统（使用全部历史）
    system = PredictionSystem(order)
    system.train_all(color_seq, size_seq, oe_seq)

    # 获取最近 order 个状态用于预测
    recent_c = color_seq[-order:] if len(color_seq) >= order else color_seq
    recent_s = size_seq[-order:] if len(size_seq) >= order else size_seq
    recent_o = oe_seq[-order:] if len(oe_seq) >= order else oe_seq

    pred = system.predict(recent_c, recent_s, recent_o)

    print(f"\n🔮 下一期属性概率预测 (基于 {order} 阶马尔可夫 + 周期修正 + 贝叶斯动态权重)")
    print(f"\n🎨 波色:")
    for c, prob in sorted(pred["波色"].items(), key=lambda x: -x[1]):
        print(f"   {c}: {prob*100:.1f}%")
    print(f"\n📏 大小:")
    for s, prob in sorted(pred["大小"].items(), key=lambda x: -x[1]):
        print(f"   {s}: {prob*100:.1f}%")
    print(f"\n🔢 单双:")
    for p, prob in sorted(pred["单双"].items(), key=lambda x: -x[1]):
        print(f"   {p}: {prob*100:.1f}%")
    print(f"\n🔥 综合置信度: {pred['综合置信度']*100:.1f}%")

    # 回测显示
    print(f"\n📊 滑动窗口回测 (最近 {backtest_limit} 期):")
    acc, total = backtest_system(conn, order, backtest_limit)
    if acc:
        print(f"   波色准确率: {acc['color']*100:.1f}%")
        print(f"   大小准确率: {acc['size']*100:.1f}%")
        print(f"   单双准确率: {acc['odd_even']*100:.1f}%")
        print(f"   平均准确率: {(acc['color']+acc['size']+acc['odd_even'])/3*100:.1f}%")
    else:
        print("   数据不足，无法回测。")

# ========== 命令行 ==========
def cmd_sync(args):
    conn = connect_db(args.db)
    try:
        init_db(conn)
        records, source, url = fetch_online_records()
        total, ins, upd = sync_from_records(conn, records, source)
        print(f"同步完成: 总计 {total}, 新增 {ins}, 更新 {upd}, 来源 {source}")
        print_dashboard(conn, order=args.order, backtest_limit=args.backtest)
    except Exception as e:
        print(f"错误: {e}")
    finally:
        conn.close()

def cmd_show(args):
    conn = connect_db(args.db)
    try:
        print_dashboard(conn, order=args.order, backtest_limit=args.backtest)
    finally:
        conn.close()

def main():
    p = argparse.ArgumentParser(description="老澳门六合彩属性时序预测 V2")
    p.add_argument("--db", default=DB_PATH_DEFAULT)
    p.add_argument("--order", type=int, default=2, choices=[1,2,3], help="马尔可夫阶数 (1-3)")
    p.add_argument("--backtest", type=int, default=30, help="回测最近期数")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp_sync = sub.add_parser("sync")
    sp_sync.set_defaults(func=cmd_sync)

    sp_show = sub.add_parser("show")
    sp_show.set_defaults(func=cmd_show)

    args = p.parse_args()
    args.func(args)

if __name__ == "__main__":
    # 解决SSL问题（GitHub Actions环境需要）
    import ssl
    ssl._create_default_https_context = ssl._create_unverified_context
    main()
