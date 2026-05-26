#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 老澳门六合彩属性时序预测系统 V4
# 动态统计 + 信息增益 + HMM + 概率校准 + 自适应冷却

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
from typing import Dict, List, Tuple, Optional, Any, Union
from urllib.request import Request, urlopen

SCRIPT_DIR = Path(__file__).resolve().parent
DB_PATH_DEFAULT = str(SCRIPT_DIR / "sdxmacau_v4.db")

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

# ========== 动态窗口权重 ==========
def dynamic_weights(n: int, decay: float = 0.95) -> List[float]:
    """返回长度为n的指数衰减权重，最后一个元素权重为1"""
    weights = [decay ** (n - 1 - i) for i in range(n)]
    return weights

# ========== 1. 动态统计趋势延续概率 ==========
class StreakStats:
    """统计连续相同状态的后续分布"""
    def __init__(self, states: List[str]):
        self.states = states
        self.streak_counts = defaultdict(lambda: defaultdict(Counter))  # streak_counts[state][length][next_state] = count

    def update(self, seq: List[str]):
        """从序列中提取连续段并统计"""
        if not seq:
            return
        i = 0
        while i < len(seq):
            j = i
            while j < len(seq) and seq[j] == seq[i]:
                j += 1
            length = j - i
            state = seq[i]
            # 记录该段的后续状态（如果存在）
            if j < len(seq):
                nxt = seq[j]
                self.streak_counts[state][length][nxt] += 1
            i = j

    def get_transition_probs(self, state: str, length: int) -> Dict[str, float]:
        """返回给定状态和连续长度下的后继概率（拉普拉斯平滑）"""
        K = len(self.states)
        alpha = 1.0
        total = sum(self.streak_counts[state][length].values())
        probs = {}
        for s in self.states:
            cnt = self.streak_counts[state][length].get(s, 0)
            probs[s] = (cnt + alpha) / (total + alpha * K)
        sump = sum(probs.values())
        return {s: p/sump for s, p in probs.items()} if sump > 0 else {s: 1/K for s in self.states}

# ========== 2. 数据驱动的状态机（KL散度+转移稳定度） ==========
class DataDrivenStateMachine:
    """基于滑动窗口的转移矩阵分析，自动识别趋势/震荡/混乱"""
    @staticmethod
    def compute_transition_matrix(seq: List[str], order: int = 1) -> np.ndarray:
        import numpy as np
        states = sorted(set(seq))
        state_to_idx = {s: i for i, s in enumerate(states)}
        K = len(states)
        mat = np.zeros((K, K))
        for i in range(len(seq)-order):
            cur = tuple(seq[i:i+order])
            nxt = seq[i+order]
            # 简化：只考虑order=1时的转移矩阵
            if order == 1:
                mat[state_to_idx[seq[i]], state_to_idx[nxt]] += 1
        # 归一化行
        row_sums = mat.sum(axis=1, keepdims=True)
        mat = np.divide(mat, row_sums, where=row_sums!=0)
        return mat, states

    @staticmethod
    def kl_divergence(p: np.ndarray, q: np.ndarray) -> float:
        """计算两个概率分布的KL散度"""
        p = np.clip(p, 1e-10, 1)
        q = np.clip(q, 1e-10, 1)
        return np.sum(p * np.log(p / q))

    @staticmethod
    def analyze_window(seq: List[str], window_size: int = 20) -> Dict[str, Any]:
        """分析最近window_size期的状态，返回状态标签和置信度"""
        if len(seq) < window_size:
            return {"state": "unknown", "confidence": 0.0}
        window = seq[-window_size:]
        mat, states = DataDrivenStateMachine.compute_transition_matrix(window, order=1)
        K = len(states)
        uniform = np.full(K, 1/K)
        # 计算每行与均匀分布的KL散度平均值
        kl_avg = 0.0
        for i in range(K):
            row = mat[i]
            if row.sum() > 0:
                kl_avg += DataDrivenStateMachine.kl_divergence(row, uniform)
        kl_avg /= K
        # 计算矩阵的奇异值（取最大奇异值反映确定性）
        u, s, vh = np.linalg.svd(mat)
        max_sv = s[0] if len(s) > 0 else 0.0
        # 根据kl_avg和max_sv判断状态
        if kl_avg > 0.5 and max_sv > 1.2:
            state = "trend"
            confidence = min(1.0, kl_avg)
        elif kl_avg < 0.2 and max_sv < 0.8:
            state = "chaotic"
            confidence = 1.0 - kl_avg
        else:
            state = "oscillation"
            confidence = 0.6
        return {"state": state, "confidence": confidence, "kl_avg": kl_avg, "max_sv": max_sv}

    @staticmethod
    def adjust_probs(probs: Dict[str, float], seq: List[str], window_size: int = 20) -> Dict[str, float]:
        """根据状态机分析结果调整概率"""
        analysis = DataDrivenStateMachine.analyze_window(seq, window_size)
        state = analysis["state"]
        last = seq[-1] if seq else None
        if state == "trend" and last:
            # 趋势态：提高反转概率（具体幅度依据confidence）
            factor = 1.0 + analysis["confidence"] * 0.5
            for s in probs:
                if s != last:
                    probs[s] *= factor
                else:
                    probs[s] /= factor
        elif state == "oscillation" and last:
            # 震荡态：提高切换概率
            factor = 1.0 + analysis["confidence"] * 0.3
            for s in probs:
                if s != last:
                    probs[s] *= factor
                else:
                    probs[s] /= factor
        elif state == "chaotic":
            # 混乱态：向均匀分布收缩
            K = len(probs)
            uniform = 1/K
            for s in probs:
                probs[s] = uniform * 0.7 + probs[s] * 0.3
        # 归一化
        total = sum(probs.values())
        if total > 0:
            probs = {k: v/total for k, v in probs.items()}
        return probs

# ========== 3. 信息增益计算 ==========
class InformationGain:
    @staticmethod
    def entropy(probs: List[float]) -> float:
        return -sum(p * math.log(p) for p in probs if p > 0)

    @staticmethod
    def from_sequence(seq: List[str], order: int = 1) -> float:
        """计算给定order的上下文对下一状态的IG"""
        if len(seq) < order + 1:
            return 0.0
        # 无条件分布
        unconditional = Counter(seq)
        total = len(seq)
        h_y = InformationGain.entropy([unconditional[s]/total for s in set(seq)])
        # 条件分布
        cond_entropy = 0.0
        contexts = defaultdict(list)
        for i in range(len(seq)-order):
            context = tuple(seq[i:i+order])
            nxt = seq[i+order]
            contexts[context].append(nxt)
        for context, nxt_list in contexts.items():
            cnt = Counter(nxt_list)
            p_context = len(nxt_list) / (len(seq)-order)
            probs = [cnt[s]/len(nxt_list) for s in set(seq)]
            cond_entropy += p_context * InformationGain.entropy(probs)
        ig = h_y - cond_entropy
        return max(0.0, ig)

# ========== 4. 在线概率校准 (滑动窗口 Isotonic) ==========
class ProbabilityCalibrator:
    def __init__(self, window_size: int = 100, n_bins: int = 10):
        self.window_size = window_size
        self.n_bins = n_bins
        self.preds = deque(maxlen=window_size)   # 预测概率
        self.outcomes = deque(maxlen=window_size) # 实际结果(0/1)

    def update(self, pred_prob: float, outcome: bool):
        self.preds.append(pred_prob)
        self.outcomes.append(1 if outcome else 0)

    def calibrate(self, prob: float) -> float:
        """使用当前窗口的保序回归进行校准（简化：分箱平均）"""
        if len(self.preds) < 10:
            return prob
        # 将 preds 分箱，计算每个箱的实际频率
        bins = [[] for _ in range(self.n_bins)]
        for p, o in zip(self.preds, self.outcomes):
            idx = min(int(p * self.n_bins), self.n_bins-1)
            bins[idx].append(o)
        bin_means = [np.mean(b) if b else 0.5 for b in bins]  # 使用numpy需导入，这里手动
        # 简化：手动求均值
        bin_means = []
        for b in bins:
            if b:
                bin_means.append(sum(b)/len(b))
            else:
                bin_means.append(0.5)
        idx = min(int(prob * self.n_bins), self.n_bins-1)
        calibrated = bin_means[idx]
        return calibrated

    def expected_calibration_error(self) -> float:
        """计算当前校准误差ECE"""
        if len(self.preds) < 10:
            return 0.0
        bins = [[] for _ in range(self.n_bins)]
        for p, o in zip(self.preds, self.outcomes):
            idx = min(int(p * self.n_bins), self.n_bins-1)
            bins[idx].append((p, o))
        ece = 0.0
        for bin_ in bins:
            if not bin_:
                continue
            acc = sum(o for _,o in bin_) / len(bin_)
            conf = sum(p for p,_ in bin_) / len(bin_)
            ece += abs(acc - conf) * (len(bin_)/len(self.preds))
        return ece

# ========== 5. 隐马尔可夫模型 (简化的离散HMM) ==========
class DiscreteHMM:
    """仅支持单个观测序列，Baum-Welch学习，用于预测下一观测"""
    def __init__(self, n_states: int, n_obs: int, states_list: List[str]):
        self.n_states = n_states
        self.n_obs = n_obs
        self.states_list = states_list
        self.obs_to_idx = {s:i for i,s in enumerate(states_list)}
        # 初始化参数
        self.pi = np.random.dirichlet(np.ones(n_states))
        self.A = np.random.dirichlet(np.ones(n_states), size=n_states)
        self.B = np.random.dirichlet(np.ones(n_obs), size=n_states)

    def train(self, obs_seq: List[str], max_iter: int = 50):
        import numpy as np
        obs_idx = [self.obs_to_idx[o] for o in obs_seq]
        T = len(obs_idx)
        for _ in range(max_iter):
            # forward
            alpha = np.zeros((T, self.n_states))
            alpha[0] = self.pi * self.B[:, obs_idx[0]]
            for t in range(1, T):
                alpha[t] = np.sum(alpha[t-1][:, None] * self.A * self.B[:, obs_idx[t]], axis=0)
            # backward
            beta = np.zeros((T, self.n_states))
            beta[-1] = 1
            for t in range(T-2, -1, -1):
                beta[t] = np.sum(self.A * self.B[:, obs_idx[t+1]] * beta[t+1], axis=1)
            # gamma, xi
            gamma = alpha * beta
            gamma /= gamma.sum(axis=1, keepdims=True)
            xi = np.zeros((T-1, self.n_states, self.n_states))
            for t in range(T-1):
                denom = np.sum(alpha[t][:, None] * self.A * self.B[:, obs_idx[t+1]] * beta[t+1])
                xi[t] = (alpha[t][:, None] * self.A * self.B[:, obs_idx[t+1]] * beta[t+1]) / denom
            # update
            self.pi = gamma[0]
            self.A = np.sum(xi, axis=0) / np.sum(gamma[:-1], axis=0)[:, None]
            self.B = np.zeros_like(self.B)
            for k in range(self.n_states):
                for t in range(T):
                    self.B[k, obs_idx[t]] += gamma[t, k]
            self.B /= self.B.sum(axis=1, keepdims=True)

    def predict_next_probs(self, obs_seq: List[str]) -> Dict[str, float]:
        import numpy as np
        obs_idx = [self.obs_to_idx[o] for o in obs_seq]
        T = len(obs_idx)
        alpha = np.zeros((T, self.n_states))
        alpha[0] = self.pi * self.B[:, obs_idx[0]]
        for t in range(1, T):
            alpha[t] = np.sum(alpha[t-1][:, None] * self.A * self.B[:, obs_idx[t]], axis=0)
        # 预测下一观测
        probs = np.sum(alpha[-1][:, None] * self.A * self.B, axis=0)
        probs = probs / probs.sum()
        return {self.states_list[i]: probs[i] for i in range(self.n_obs)}

# ========== 6. 元引擎 (Meta Engine) ==========
class MetaEngine:
    def __init__(self, min_ig: float = 0.05, max_ece: float = 0.1):
        self.min_ig = min_ig
        self.max_ece = max_ece

    def should_predict(self, ig: float, ece: float) -> Tuple[bool, str]:
        """返回 (是否出手, 原因)"""
        if ig < self.min_ig:
            return False, f"信息增益过低 ({ig:.3f} < {self.min_ig})"
        if ece > self.max_ece:
            return False, f"校准误差过大 ({ece:.3f} > {self.max_ece})"
        return True, "通过元检测"

# ========== 7. 集成预测引擎 ==========
class AttributeEngineV4:
    def __init__(self, name: str, order: int = 2, alpha_smooth: float = 1.0,
                 use_hmm: bool = True, hmm_states: int = 3):
        self.name = name
        self.order = order
        self.states = ATTRIBUTE_STATES[name]
        self.alpha = alpha_smooth
        self.use_hmm = use_hmm
        self.hmm = None
        if use_hmm:
            self.hmm = DiscreteHMM(hmm_states, len(self.states), self.states)
        # 马尔可夫链（拉普拉斯平滑）
        self.markov_counts = defaultdict(lambda: defaultdict(float))   # state -> next_state -> weight
        self.markov_total = defaultdict(float)
        # 趋势延续统计器
        self.streak_stats = StreakStats(self.states)
        # 校准器
        self.calibrator = ProbabilityCalibrator(window_size=100)
        # 历史记录（用于反馈）
        self.prediction_history = deque(maxlen=200)   # (prob_dist, actual)

    def train(self, seq: List[str]):
        """训练：马尔可夫计数 + 趋势统计 + HMM"""
        # 动态权重
        weights = dynamic_weights(len(seq))
        # 马尔可夫
        for i in range(len(seq) - self.order):
            state = tuple(seq[i:i+self.order])
            nxt = seq[i+self.order]
            w = weights[i+self.order]
            self.markov_counts[state][nxt] += w
            self.markov_total[state] += w
        # 趋势统计
        self.streak_stats.update(seq)
        # HMM
        if self.use_hmm and len(seq) > 50:
            self.hmm.train(seq)

    def predict_proba(self, recent: List[str]) -> Dict[str, float]:
        """返回校准后的概率分布"""
        K = len(self.states)
        # 1. 马尔可夫概率（带拉普拉斯平滑）
        if len(recent) >= self.order:
            state = tuple(recent[-self.order:])
            total = self.markov_total.get(state, 0)
            markov_probs = {}
            for s in self.states:
                cnt = self.markov_counts[state].get(s, 0)
                markov_probs[s] = (cnt + self.alpha) / (total + self.alpha * K)
            sum_m = sum(markov_probs.values())
            if sum_m > 0:
                markov_probs = {s: p/sum_m for s, p in markov_probs.items()}
            else:
                markov_probs = {s: 1/K for s in self.states}
        else:
            markov_probs = {s: 1/K for s in self.states}

        # 2. 趋势延续概率
        if len(recent) > 0:
            last = recent[-1]
            streak_len = 1
            for i in range(len(recent)-2, -1, -1):
                if recent[i] == last:
                    streak_len += 1
                else:
                    break
            streak_probs = self.streak_stats.get_transition_probs(last, streak_len)
        else:
            streak_probs = {s: 1/K for s in self.states}

        # 3. HMM 概率（如果启用）
        if self.use_hmm and self.hmm is not None and len(recent) > 10:
            hmm_probs = self.hmm.predict_next_probs(recent)
        else:
            hmm_probs = {s: 1/K for s in self.states}

        # 融合三种模型（平均）
        fused = {}
        for s in self.states:
            fused[s] = (markov_probs[s] + streak_probs[s] + hmm_probs[s]) / 3.0
        total = sum(fused.values())
        fused = {s: p/total for s, p in fused.items()}

        # 状态机调整（数据驱动）
        fused = DataDrivenStateMachine.adjust_probs(fused, recent, window_size=20)

        # 概率校准
        calibrated = {}
        for s, p in fused.items():
            cal = self.calibrator.calibrate(p)
            calibrated[s] = cal
        total = sum(calibrated.values())
        if total > 0:
            calibrated = {s: p/total for s, p in calibrated.items()}
        else:
            calibrated = {s: 1/K for s in self.states}

        return calibrated

    def update_feedback(self, predicted_probs: Dict[str, float], actual: str):
        """更新校准器，记录预测与实际"""
        # 取最大概率作为主预测（可改为置信度加权）
        max_state = max(predicted_probs.items(), key=lambda x: x[1])[0]
        is_correct = (max_state == actual)
        # 校准器更新
        max_prob = predicted_probs[max_state]
        self.calibrator.update(max_prob, is_correct)
        self.prediction_history.append((predicted_probs, actual))

    def get_calibration_error(self) -> float:
        return self.calibrator.expected_calibration_error()

# ========== 8. 系统集成 V4 ==========
class PredictionSystemV4:
    def __init__(self, order: int = 2, min_ig: float = 0.05, max_ece: float = 0.1):
        self.order = order
        self.engines = {
            "color": AttributeEngineV4("color", order),
            "size": AttributeEngineV4("size", order),
            "odd_even": AttributeEngineV4("odd_even", order)
        }
        self.meta = MetaEngine(min_ig, max_ece)
        self.igs = {}  # 存储各属性的信息增益

    def train_all(self, seqs: Dict[str, List[str]]):
        for name, seq in seqs.items():
            self.engines[name].train(seq)
            # 计算该属性的信息增益
            self.igs[name] = InformationGain.from_sequence(seq, order=self.order)

    def predict_all(self, recents: Dict[str, List[str]]) -> Dict[str, Any]:
        results = {}
        overall_confidence = 0.0
        for name, engine in self.engines.items():
            probs = engine.predict_proba(recents[name])
            results[name] = {
                "probs": probs,
                "max_prob": max(probs.values()),
                "best_state": max(probs.items(), key=lambda x: x[1])[0]
            }
            overall_confidence += results[name]["max_prob"]
        overall_confidence /= len(self.engines)
        # 元决策
        avg_ig = sum(self.igs.values()) / len(self.igs) if self.igs else 0
        avg_ece = sum(engine.get_calibration_error() for engine in self.engines.values()) / len(self.engines)
        should_act, reason = self.meta.should_predict(avg_ig, avg_ece)
        results["meta"] = {
            "should_act": should_act,
            "reason": reason,
            "avg_ig": avg_ig,
            "avg_ece": avg_ece,
            "overall_confidence": overall_confidence
        }
        return results

    def update_feedback_all(self, actuals: Dict[str, str], predicted_results: Dict[str, Any]):
        for name, engine in self.engines.items():
            probs = predicted_results[name]["probs"]
            engine.update_feedback(probs, actuals[name])

    def walk_forward_backtest(self, seqs: Dict[str, List[str]], test_len: int = 30) -> Dict[str, float]:
        """滚动回测，并返回每个属性的准确率"""
        total = 0
        correct = {name: 0 for name in self.engines}
        # 注意：这里为了简洁，每次重新训练整个历史序列，效率不高但正确。
        # 实际可优化为增量更新，但回测数据量小，OK。
        for idx in range(len(seqs["color"]) - test_len, len(seqs["color"]) - 1):
            train_seqs = {name: seq[:idx] for name, seq in seqs.items()}
            self.train_all(train_seqs)
            recents = {}
            for name in self.engines:
                if idx >= self.order:
                    recents[name] = seqs[name][idx-self.order:idx]
                else:
                    recents[name] = seqs[name][:idx]
            pred = self.predict_all(recents)
            actuals = {name: seqs[name][idx] for name in self.engines}
            for name in self.engines:
                if pred[name]["best_state"] == actuals[name]:
                    correct[name] += 1
            total += 1
        acc = {name: correct[name]/total if total>0 else 0 for name in self.engines}
        return acc

# ========== 辅助：导入 numpy 动态处理（仅用于 HMM 和 SVD） ==========
# 为避免依赖，在需要时导入 numpy，若没有则降级
try:
    import numpy as np
except ImportError:
    # 降级方案：使用纯 Python 实现简单版本，但会缺失部分功能，这里提示用户安装
    print("警告：未安装 numpy，部分高级功能（HMM、SVD）将不可用。请运行 pip install numpy")
    np = None

# ========== 仪表盘 ==========
def print_dashboard(conn, order=2, min_ig=0.05, max_ece=0.1, backtest_len=30):
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

    # 构建字典
    seqs = {"color": color_seq, "size": size_seq, "odd_even": oe_seq}
    system = PredictionSystemV4(order=order, min_ig=min_ig, max_ece=max_ece)
    system.train_all(seqs)

    # 预测下一期
    recents = {}
    for name in system.engines:
        if len(seqs[name]) >= order:
            recents[name] = seqs[name][-order:]
        else:
            recents[name] = seqs[name]
    pred = system.predict_all(recents)

    print(f"\n🔮 下一期属性预测 V4 (阶数={order})")
    for name, data in pred.items():
        if name == "meta":
            continue
        print(f"\n{name}:")
        for s, p in sorted(data["probs"].items(), key=lambda x: -x[1]):
            marker = " ✓" if s == data["best_state"] else ""
            print(f"   {s}: {p*100:.1f}%{marker}")
        print(f"   最佳: {data['best_state']} (置信度 {data['max_prob']*100:.1f}%)")
    # 元决策
    m = pred["meta"]
    print(f"\n🧠 元引擎决策: {'出手' if m['should_act'] else '观望'}")
    print(f"   原因: {m['reason']}")
    print(f"   平均信息增益: {m['avg_ig']:.4f}")
    print(f"   平均校准误差 ECE: {m['avg_ece']:.4f}")
    print(f"   综合置信度: {m['overall_confidence']*100:.1f}%")

    # 回测
    print(f"\n📊 Walk-Forward 回测 (最近 {backtest_len} 期):")
    acc = system.walk_forward_backtest(seqs, test_len=backtest_len)
    for name, a in acc.items():
        print(f"   {name} 准确率: {a*100:.1f}%")
    print(f"   平均准确率: {(acc['color']+acc['size']+acc['odd_even'])/3*100:.1f}%")

# ========== 命令行 ==========
def cmd_sync(args):
    conn = connect_db(args.db)
    try:
        init_db(conn)
        records, source, url = fetch_online_records()
        total, ins, upd = sync_from_records(conn, records, source)
        print(f"同步完成: 总计 {total}, 新增 {ins}, 更新 {upd}, 来源 {source}")
        print_dashboard(conn, order=args.order, min_ig=args.min_ig, max_ece=args.max_ece, backtest_len=args.backtest)
    except Exception as e:
        print(f"错误: {e}")
    finally:
        conn.close()

def cmd_show(args):
    conn = connect_db(args.db)
    try:
        print_dashboard(conn, order=args.order, min_ig=args.min_ig, max_ece=args.max_ece, backtest_len=args.backtest)
    finally:
        conn.close()

def main():
    p = argparse.ArgumentParser(description="老澳门六合彩属性时序预测 V4")
    p.add_argument("--db", default=DB_PATH_DEFAULT)
    p.add_argument("--order", type=int, default=2, help="马尔可夫阶数")
    p.add_argument("--min-ig", type=float, default=0.05, help="最小信息增益阈值")
    p.add_argument("--max-ece", type=float, default=0.1, help="最大校准误差阈值")
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
