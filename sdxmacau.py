#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 老澳门六合彩属性时序预测系统 V5
# 无泄漏回测 + 动态模型融合 + 市场状态识别

from __future__ import annotations

import argparse
import json
import sqlite3
import math
import ssl
import sys
from collections import defaultdict, Counter, deque
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Any
from urllib.request import Request, urlopen

# 依赖检查
try:
    import numpy as np
except ImportError:
    print("错误：需要安装 numpy。请运行: pip install numpy")
    sys.exit(1)

# ========== 配置 ==========
SCRIPT_DIR = Path(__file__).resolve().parent
DB_PATH_DEFAULT = str(SCRIPT_DIR / "sdxmacau_v5.db")

THIRD_PARTY_URLS = [
    "https://marksix6.net/index.php?api=1",
    "https://marksix6.net/api/lottery_api.php"
]

ATTRIBUTE_STATES = {
    "color": ["红", "蓝", "绿"],
    "size": ["大", "小"],
    "odd_even": ["单", "双"]
}

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

# ========== 数据层（与之前相同） ==========
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

def dynamic_weights(n: int, decay: float = 0.95) -> List[float]:
    return [decay ** (n - 1 - i) for i in range(n)]

# ========== 工具函数 ==========
def log_loss(p: float, y: int) -> float:
    """p 是预测为正类的概率，y ∈ {0,1}"""
    p = np.clip(p, 1e-15, 1-1e-15)
    return - (y * math.log(p) + (1-y) * math.log(1-p))

def brier_score(p: float, y: int) -> float:
    return (p - y) ** 2

# ========== 1. 动态统计趋势延续概率 (同上) ==========
class StreakStats:
    def __init__(self, states: List[str]):
        self.states = states
        self.streak_counts = defaultdict(lambda: defaultdict(Counter))

    def update(self, seq: List[str]):
        if not seq:
            return
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

    def get_transition_probs(self, state: str, length: int) -> Dict[str, float]:
        K = len(self.states)
        alpha = 1.0
        total = sum(self.streak_counts[state][length].values())
        probs = {}
        for s in self.states:
            cnt = self.streak_counts[state][length].get(s, 0)
            probs[s] = (cnt + alpha) / (total + alpha * K)
        total_p = sum(probs.values())
        return {s: p/total_p for s, p in probs.items()} if total_p>0 else {s:1/K for s in self.states}

# ========== 2. 信息增益计算与归一化 ==========
def entropy(probs: List[float]) -> float:
    return -sum(p * math.log(p) for p in probs if p > 0)

def normalized_information_gain(seq: List[str], order: int = 1) -> float:
    """返回归一化信息增益 (IG / H(Y))"""
    if len(seq) < order + 1:
        return 0.0
    # 无条件分布 H(Y)
    unconditional = Counter(seq)
    total = len(seq)
    h_y = entropy([unconditional[s]/total for s in set(seq)])
    if h_y == 0:
        return 0.0
    # 条件熵 H(Y|X)
    contexts = defaultdict(list)
    for i in range(len(seq)-order):
        context = tuple(seq[i:i+order])
        nxt = seq[i+order]
        contexts[context].append(nxt)
    cond_entropy = 0.0
    for context, nxt_list in contexts.items():
        cnt = Counter(nxt_list)
        p_context = len(nxt_list) / (len(seq)-order)
        probs = [cnt[s]/len(nxt_list) for s in set(seq)]
        cond_entropy += p_context * entropy(probs)
    ig = h_y - cond_entropy
    return ig / h_y   # 归一化

# ========== 3. HMM 带正则化和 Early Stopping ==========
class DiscreteHMM:
    def __init__(self, n_states: int, n_obs: int, states_list: List[str],
                 reg_factor: float = 0.05, early_stop_eps: float = 1e-4):
        self.n_states = n_states
        self.n_obs = n_obs
        self.states_list = states_list
        self.obs_to_idx = {s:i for i,s in enumerate(states_list)}
        self.reg_factor = reg_factor   # transition regularization
        self.early_stop_eps = early_stop_eps
        # 随机初始化参数
        self.pi = np.random.dirichlet(np.ones(n_states))
        self.A = np.random.dirichlet(np.ones(n_states), size=n_states)
        self.B = np.random.dirichlet(np.ones(n_obs), size=n_states)

    def train(self, obs_seq: List[str], max_iter: int = 100):
        obs_idx = [self.obs_to_idx[o] for o in obs_seq]
        T = len(obs_idx)
        prev_log_lik = -np.inf
        for it in range(max_iter):
            # forward
            alpha = np.zeros((T, self.n_states))
            alpha[0] = self.pi * self.B[:, obs_idx[0]]
            for t in range(1, T):
                alpha[t] = np.sum(alpha[t-1][:, None] * self.A * self.B[:, obs_idx[t]], axis=0)
            log_lik = np.log(np.sum(alpha[-1]))
            # early stopping
            if it > 0 and abs(log_lik - prev_log_lik) < self.early_stop_eps:
                break
            prev_log_lik = log_lik
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
            # update parameters
            self.pi = gamma[0]
            self.A = np.sum(xi, axis=0) / np.sum(gamma[:-1], axis=0)[:, None]
            # 正则化：与均匀分布平滑
            uniform = np.ones_like(self.A) / self.n_states
            self.A = (1 - self.reg_factor) * self.A + self.reg_factor * uniform
            # 重新归一化行
            self.A = self.A / self.A.sum(axis=1, keepdims=True)
            # B 矩阵更新
            self.B = np.zeros_like(self.B)
            for k in range(self.n_states):
                for t in range(T):
                    self.B[k, obs_idx[t]] += gamma[t, k]
            self.B /= self.B.sum(axis=1, keepdims=True)

    def predict_next_probs(self, obs_seq: List[str]) -> Dict[str, float]:
        obs_idx = [self.obs_to_idx[o] for o in obs_seq]
        T = len(obs_idx)
        alpha = np.zeros((T, self.n_states))
        alpha[0] = self.pi * self.B[:, obs_idx[0]]
        for t in range(1, T):
            alpha[t] = np.sum(alpha[t-1][:, None] * self.A * self.B[:, obs_idx[t]], axis=0)
        probs = np.sum(alpha[-1][:, None] * self.A * self.B, axis=0)
        probs = probs / probs.sum()
        return {self.states_list[i]: probs[i] for i in range(self.n_obs)}

# ========== 4. 概率校准（保序回归 + 大窗口） ==========
class ProbabilityCalibrator:
    def __init__(self, window_size: int = 300):
        self.window_size = window_size
        self.preds = deque(maxlen=window_size)
        self.outcomes = deque(maxlen=window_size)

    def update(self, pred_prob: float, outcome: bool):
        self.preds.append(pred_prob)
        self.outcomes.append(1 if outcome else 0)

    def _isotonic_regression(self, x, y):
        """简单的 PAV 算法实现保序回归，返回单调非降函数值（对x去重后的映射）"""
        # 将数据按 x 排序
        pairs = sorted(zip(x, y))
        xs = [p[0] for p in pairs]
        ys = [p[1] for p in pairs]
        # PAV
        blocks = []
        for i in range(len(xs)):
            blocks.append([xs[i], ys[i], 1])  # [x_sum, y_sum, count]
            while len(blocks) > 1 and blocks[-2][1]/blocks[-2][2] > blocks[-1][1]/blocks[-1][2]:
                prev = blocks.pop()
                cur = blocks[-1]
                cur[0] += prev[0]
                cur[1] += prev[1]
                cur[2] += prev[2]
        # 生成单调序列
        fitted = []
        for block in blocks:
            mean = block[1] / block[2]
            fitted.extend([mean] * block[2])
        return fitted

    def calibrate(self, prob: float) -> float:
        if len(self.preds) < 10:
            return prob
        # 使用最近的数据进行保序回归
        preds_arr = list(self.preds)
        outcomes_arr = list(self.outcomes)
        # 降采样或直接用全部（窗口内最多300）
        fitted = self._isotonic_regression(preds_arr, outcomes_arr)
        # 为每个预测值找到最近的校准值（简单方式：按值排序插值）
        # 这里我们用分箱平均的简化，但更准确：对预测值排序后，映射到 fitted 值
        # 由于 isotonic 输出与输入顺序一致，我们可以构建一个插值函数
        sorted_idx = np.argsort(preds_arr)
        sorted_preds = [preds_arr[i] for i in sorted_idx]
        sorted_fitted = [fitted[i] for i in sorted_idx]
        # 使用 numpy 的 interp
        cal = np.interp(prob, sorted_preds, sorted_fitted)
        return float(cal)

    def expected_calibration_error(self, n_bins: int = 10) -> float:
        if len(self.preds) < 10:
            return 0.0
        bins = [[] for _ in range(n_bins)]
        for p, o in zip(self.preds, self.outcomes):
            idx = min(int(p * n_bins), n_bins-1)
            bins[idx].append((p, o))
        ece = 0.0
        for bin_ in bins:
            if not bin_:
                continue
            acc = sum(o for _,o in bin_) / len(bin_)
            conf = sum(p for p,_ in bin_) / len(bin_)
            ece += abs(acc - conf) * (len(bin_)/len(self.preds))
        return ece

# ========== 5. 动态阈值状态机 ==========
class DynamicStateMachine:
    def __init__(self, window: int = 30, n_std: float = 2.0):
        self.window = window
        self.n_std = n_std
        self.history_kl = deque(maxlen=window)
        self.history_sv = deque(maxlen=window)

    def analyze(self, seq: List[str]) -> Dict[str, Any]:
        if len(seq) < 10:
            return {"state": "unknown", "confidence": 0.0}
        # 计算转移矩阵
        states = sorted(set(seq))
        if len(states) < 2:
            return {"state": "trend", "confidence": 1.0}
        state_to_idx = {s:i for i,s in enumerate(states)}
        K = len(states)
        mat = np.zeros((K, K))
        for i in range(len(seq)-1):
            cur = state_to_idx[seq[i]]
            nxt = state_to_idx[seq[i+1]]
            mat[cur, nxt] += 1
        row_sums = mat.sum(axis=1, keepdims=True)
        mat = np.divide(mat, row_sums, where=row_sums!=0)
        # 计算 KL 散度（与均匀分布）
        uniform = np.full(K, 1/K)
        kl_avg = 0.0
        for i in range(K):
            row = mat[i]
            if row.sum() > 0:
                kl = np.sum(row * np.log(row / uniform))
                kl_avg += kl
        kl_avg /= K
        # 奇异值
        u, s, vh = np.linalg.svd(mat)
        max_sv = s[0] if len(s)>0 else 0.0
        # 动态阈值
        self.history_kl.append(kl_avg)
        self.history_sv.append(max_sv)
        if len(self.history_kl) >= 5:
            kl_mean = np.mean(self.history_kl)
            kl_std = np.std(self.history_kl)
            sv_mean = np.mean(self.history_sv)
            sv_std = np.std(self.history_sv)
            kl_thresh_high = kl_mean + self.n_std * kl_std
            sv_thresh_low = sv_mean - self.n_std * sv_std
            if kl_avg > kl_thresh_high and max_sv > sv_thresh_low:
                state = "trend"
                confidence = min(1.0, (kl_avg - kl_mean)/(kl_std+1e-6))
            elif kl_avg < kl_mean - self.n_std * kl_std and max_sv < sv_mean - self.n_std * sv_std:
                state = "chaotic"
                confidence = min(1.0, (kl_mean - kl_avg)/(kl_std+1e-6))
            else:
                state = "oscillation"
                confidence = 0.6
        else:
            # 初始时使用保守阈值
            if kl_avg > 0.5 and max_sv > 1.2:
                state = "trend"
                confidence = 0.7
            elif kl_avg < 0.2 and max_sv < 0.8:
                state = "chaotic"
                confidence = 0.7
            else:
                state = "oscillation"
                confidence = 0.5
        return {"state": state, "confidence": confidence, "kl_avg": kl_avg, "max_sv": max_sv}

    def adjust_probs(self, probs: Dict[str, float], seq: List[str]) -> Dict[str, float]:
        analysis = self.analyze(seq)
        state = analysis["state"]
        conf = analysis["confidence"]
        last = seq[-1] if seq else None
        if state == "trend" and last:
            factor = 1.0 + conf * 0.5
            for s in probs:
                if s != last:
                    probs[s] *= factor
                else:
                    probs[s] /= factor
        elif state == "oscillation" and last:
            factor = 1.0 + conf * 0.3
            for s in probs:
                if s != last:
                    probs[s] *= factor
                else:
                    probs[s] /= factor
        elif state == "chaotic":
            K = len(probs)
            uniform = 1/K
            for s in probs:
                probs[s] = uniform * 0.7 + probs[s] * 0.3
        total = sum(probs.values())
        if total > 0:
            probs = {s: v/total for s, v in probs.items()}
        return probs

# ========== 6. 市场状态检测 (Regime Detection) ==========
def hurst_exponent(ts: List[float], max_lag: int = 20) -> float:
    """计算 Hurst 指数，粗略估计趋势性"""
    n = len(ts)
    if n < max_lag+1:
        return 0.5
    lags = range(2, min(max_lag, n//2))
    tau = []
    for lag in lags:
        diff = [ts[i+lag] - ts[i] for i in range(n-lag)]
        var = np.var(diff)
        tau.append(var)
    if len(tau) < 2:
        return 0.5
    m = np.polyfit(np.log(lags), np.log(tau), 1)
    hurst = m[0] / 2.0
    return hurst

def runs_test(seq: List[str]) -> float:
    """游程检验，返回p值近似，判断是否随机"""
    n = len(seq)
    if n < 10:
        return 0.5
    # 转换为二元序列（0/1）
    states = sorted(set(seq))
    if len(states) != 2:
        return 0.5
    binary = [0 if s == states[0] else 1 for s in seq]
    n1 = sum(binary)
    n2 = n - n1
    runs = 1
    for i in range(1, n):
        if binary[i] != binary[i-1]:
            runs += 1
    mean_runs = 1 + (2 * n1 * n2) / n
    var_runs = (2 * n1 * n2 * (2 * n1 * n2 - n)) / (n**2 * (n-1))
    if var_runs <= 0:
        return 0.5
    z = (runs - mean_runs) / math.sqrt(var_runs)
    p_value = 2 * (1 - 0.5 * (1 + math.erf(abs(z) / math.sqrt(2))))
    return p_value

def ljung_box_test(seq: List[float], max_lag: int = 10) -> float:
    """Ljung-Box Q 统计量，返回 p-value，检验自相关性"""
    n = len(seq)
    if n < max_lag + 2:
        return 0.5
    series = np.array(seq)
    acf = np.correlate(series - series.mean(), series - series.mean(), mode='full')[-n:]
    acf = acf / acf[0]
    q = 0
    for k in range(1, max_lag+1):
        q += acf[k]**2 / (n - k)
    q = n * (n+2) * q
    p_value = 1 - 0.5 * (1 + math.erf(q / math.sqrt(2)))  # 近似
    return p_value

class RegimeDetector:
    def __init__(self, hurst_low: float = 0.45, hurst_high: float = 0.55,
                 runs_p_thresh: float = 0.05, lb_p_thresh: float = 0.05):
        self.hurst_low = hurst_low
        self.hurst_high = hurst_high
        self.runs_p_thresh = runs_p_thresh
        self.lb_p_thresh = lb_p_thresh

    def detect(self, seq: List[str], value_func: callable) -> Dict[str, Any]:
        """value_func: 将状态映射为数值（例如 红->0, 蓝->1, 绿->2）"""
        if len(seq) < 30:
            return {"predictable": False, "reason": "数据不足"}
        # 转换为数值序列
        vals = [value_func(s) for s in seq]
        hurst = hurst_exponent(vals, max_lag=min(20, len(seq)//4))
        runs_p = runs_test(seq)
        lb_p = ljung_box_test(vals, max_lag=min(10, len(seq)//10))
        predictable = (hurst < self.hurst_low or hurst > self.hurst_high) or runs_p < self.runs_p_thresh or lb_p < self.lb_p_thresh
        reason = []
        if hurst < self.hurst_low:
            reason.append(f"Hurst={hurst:.3f}<{self.hurst_low} (均值回归)")
        elif hurst > self.hurst_high:
            reason.append(f"Hurst={hurst:.3f}>{self.hurst_high} (趋势)")
        if runs_p < self.runs_p_thresh:
            reason.append(f"游程检验 p={runs_p:.4f}<{self.runs_p_thresh}")
        if lb_p < self.lb_p_thresh:
            reason.append(f"Ljung-Box p={lb_p:.4f}<{self.lb_p_thresh}")
        return {
            "predictable": predictable,
            "reason": "; ".join(reason) if reason else "随机游走",
            "hurst": hurst,
            "runs_p": runs_p,
            "lb_p": lb_p
        }

# ========== 7. 动态模型加权 ==========
class ModelWeightManager:
    def __init__(self, models: List[str], loss_window: int = 50):
        self.models = models
        self.loss_window = loss_window
        self.losses = {m: deque(maxlen=loss_window) for m in models}
        self.weights = {m: 1.0/len(models) for m in models}

    def update_loss(self, model: str, loss: float):
        self.losses[model].append(loss)
        # 更新权重 (softmax of negative recent average loss)
        recent_loss = np.mean(self.losses[model]) if self.losses[model] else 0.0
        # 使用指数加权移动平均
        exp_losses = {}
        for m in self.models:
            mean_loss = np.mean(self.losses[m]) if self.losses[m] else 0.0
            exp_losses[m] = math.exp(-mean_loss)
        total = sum(exp_losses.values())
        if total > 0:
            self.weights = {m: exp_losses[m]/total for m in self.models}

    def get_weights(self) -> Dict[str, float]:
        return self.weights

# ========== 8. 异常状态过滤 ==========
class AnomalyFilter:
    def __init__(self, consecutive_extreme_threshold: int = 3, bias_threshold: float = 0.8):
        self.consecutive_extreme_threshold = consecutive_extreme_threshold
        self.bias_threshold = bias_threshold
        self.extreme_streak = 0

    def check(self, probs: Dict[str, float]) -> bool:
        """返回是否应该暂停预测"""
        maxp = max(probs.values())
        # 极度偏态（某一状态概率过高）
        if maxp > self.bias_threshold:
            self.extreme_streak += 1
        else:
            self.extreme_streak = 0
        if self.extreme_streak >= self.consecutive_extreme_threshold:
            return True
        # 其他异常检测可以扩展
        return False

# ========== 9. 集成引擎 V5 ==========
class AttributeEngineV5:
    def __init__(self, name: str, order: int = 2, alpha_smooth: float = 1.0,
                 use_hmm: bool = True, hmm_states: int = 3, reg_factor: float = 0.05):
        self.name = name
        self.order = order
        self.states = ATTRIBUTE_STATES[name]
        self.alpha = alpha_smooth
        self.use_hmm = use_hmm
        self.hmm = None
        if use_hmm:
            self.hmm = DiscreteHMM(hmm_states, len(self.states), self.states, reg_factor=reg_factor)
        # 马尔可夫模型
        self.markov_counts = defaultdict(lambda: defaultdict(float))
        self.markov_total = defaultdict(float)
        self.streak_stats = StreakStats(self.states)
        self.calibrator = ProbabilityCalibrator(window_size=300)
        self.state_machine = DynamicStateMachine()
        self.regime_detector = RegimeDetector()
        self.model_weight_mgr = ModelWeightManager(["markov", "streak", "hmm"], loss_window=50)
        self.anomaly_filter = AnomalyFilter()
        # 历史记录用于计算损失
        self.recent_preds = deque(maxlen=100)   # (model_name, prob_dict, actual_state)

    def train(self, seq: List[str]):
        weights = dynamic_weights(len(seq))
        # 马尔可夫
        for i in range(len(seq) - self.order):
            state = tuple(seq[i:i+self.order])
            nxt = seq[i+self.order]
            w = weights[i+self.order]
            self.markov_counts[state][nxt] += w
            self.markov_total[state] += w
        self.streak_stats.update(seq)
        if self.use_hmm and len(seq) > 50:
            self.hmm.train(seq)

    def _markov_probs(self, recent: List[str]) -> Dict[str, float]:
        K = len(self.states)
        if len(recent) >= self.order:
            state = tuple(recent[-self.order:])
            total = self.markov_total.get(state, 0)
            probs = {}
            for s in self.states:
                cnt = self.markov_counts[state].get(s, 0)
                probs[s] = (cnt + self.alpha) / (total + self.alpha * K)
            sum_p = sum(probs.values())
            if sum_p > 0:
                return {s: p/sum_p for s, p in probs.items()}
        return {s: 1/K for s in self.states}

    def _streak_probs(self, recent: List[str]) -> Dict[str, float]:
        if not recent:
            return {s: 1/len(self.states) for s in self.states}
        last = recent[-1]
        streak_len = 1
        for i in range(len(recent)-2, -1, -1):
            if recent[i] == last:
                streak_len += 1
            else:
                break
        return self.streak_stats.get_transition_probs(last, streak_len)

    def _hmm_probs(self, recent: List[str]) -> Dict[str, float]:
        if self.use_hmm and self.hmm and len(recent) > 10:
            return self.hmm.predict_next_probs(recent)
        else:
            return {s: 1/len(self.states) for s in self.states}

    def predict_proba(self, recent: List[str]) -> Tuple[Dict[str, float], Dict[str, float]]:
        """返回 (融合概率, 各模型概率)"""
        # 各模型概率
        markov_p = self._markov_probs(recent)
        streak_p = self._streak_probs(recent)
        hmm_p = self._hmm_probs(recent)
        model_probs = {"markov": markov_p, "streak": streak_p, "hmm": hmm_p}
        # 动态权重
        weights = self.model_weight_mgr.get_weights()
        fused = {}
        for s in self.states:
            fused[s] = weights["markov"] * markov_p.get(s,0) + \
                       weights["streak"] * streak_p.get(s,0) + \
                       weights["hmm"] * hmm_p.get(s,0)
        total = sum(fused.values())
        if total > 0:
            fused = {s: p/total for s, p in fused.items()}
        # 状态机调整
        fused = self.state_machine.adjust_probs(fused, recent)
        # 校准
        calibrated = {}
        for s, p in fused.items():
            cal = self.calibrator.calibrate(p)
            calibrated[s] = cal
        total = sum(calibrated.values())
        if total > 0:
            calibrated = {s: p/total for s, p in calibrated.items()}
        else:
            calibrated = fused
        return calibrated, model_probs

    def update_feedback(self, predicted_probs: Dict[str, float], actual: str):
        """更新校准器、模型权重等，但不使用未来信息"""
        # 计算每个模型的损失
        # 需要知道每个模型对这一期的预测概率
        # 由于我们没有存储每个模型单独的预测，可以在 predict_proba 时保存历史
        # 简化：我们只更新校准器和损失（使用融合概率的单一损失来调整权重？）
        # 正确做法：在调用 predict_proba 时返回各模型概率并存储
        # 这里留一个接口，期望外部传入各模型概率
        # 为避免复杂，我们只更新校准器，模型权重将在外部调用时提供各模型概率
        # 实际使用时，在系统层面记录模型概率并更新权重管理器
        max_state = max(predicted_probs.items(), key=lambda x: x[1])[0]
        is_correct = (max_state == actual)
        max_prob = predicted_probs[max_state]
        self.calibrator.update(max_prob, is_correct)

    def update_model_losses(self, model_probs: Dict[str, Dict[str, float]], actual: str):
        """传入各模型在这一期的预测概率字典，更新损失"""
        for model_name, probs in model_probs.items():
            # 计算对数损失：取实际状态的负对数概率
            prob_actual = probs.get(actual, 1e-10)
            loss = -math.log(prob_actual)
            self.model_weight_mgr.update_loss(model_name, loss)

    def get_calibration_error(self) -> float:
        return self.calibrator.expected_calibration_error()

    def check_anomaly(self, probs: Dict[str, float]) -> bool:
        return self.anomaly_filter.check(probs)

    def detect_regime(self, seq: List[str]) -> Dict[str, Any]:
        # 将状态映射为数值（简单按顺序）
        value_map = {s: i for i, s in enumerate(self.states)}
        def mapper(s): return value_map[s]
        return self.regime_detector.detect(seq, mapper)

# ========== 10. 系统集成 V5 ==========
class PredictionSystemV5:
    def __init__(self, order: int = 2, min_norm_ig: float = 0.1, max_ece: float = 0.1):
        self.order = order
        self.min_norm_ig = min_norm_ig
        self.max_ece = max_ece
        self.engines = {
            "color": AttributeEngineV5("color", order),
            "size": AttributeEngineV5("size", order),
            "odd_even": AttributeEngineV5("odd_even", order)
        }
        self.norm_igs = {}

    def train_all(self, seqs: Dict[str, List[str]]):
        for name, seq in seqs.items():
            self.engines[name].train(seq)
            self.norm_igs[name] = normalized_information_gain(seq, order=self.order)

    def predict_all(self, recents: Dict[str, List[str]]) -> Dict[str, Any]:
        results = {}
        for name, engine in self.engines.items():
            probs, model_probs = engine.predict_proba(recents[name])
            results[name] = {
                "probs": probs,
                "model_probs": model_probs,
                "max_prob": max(probs.values()),
                "best_state": max(probs.items(), key=lambda x: x[1])[0]
            }
        # 检查异常和可预测性
        skip = False
        reasons = []
        for name, engine in self.engines.items():
            regime = engine.detect_regime(recents[name])
            if not regime["predictable"]:
                skip = True
                reasons.append(f"{name}: {regime['reason']}")
            if engine.check_anomaly(results[name]["probs"]):
                skip = True
                reasons.append(f"{name}: 异常偏态")
        if skip:
            results["meta"] = {
                "should_act": False,
                "reason": "; ".join(reasons),
                "avg_norm_ig": np.mean(list(self.norm_igs.values())),
                "avg_ece": np.mean([e.get_calibration_error() for e in self.engines.values()])
            }
            return results
        # 元决策：信息增益和校准误差
        avg_norm_ig = np.mean(list(self.norm_igs.values()))
        avg_ece = np.mean([e.get_calibration_error() for e in self.engines.values()])
        should_act = (avg_norm_ig >= self.min_norm_ig and avg_ece <= self.max_ece)
        reason = f"norm_IG={avg_norm_ig:.3f}, ECE={avg_ece:.3f}"
        results["meta"] = {
            "should_act": should_act,
            "reason": reason,
            "avg_norm_ig": avg_norm_ig,
            "avg_ece": avg_ece
        }
        return results

    def update_feedback_all(self, actuals: Dict[str, str], predictions: Dict[str, Any]):
        """传入本期实际结果和预测结果（包含各模型概率）用于在线学习"""
        for name, engine in self.engines.items():
            probs = predictions[name]["probs"]
            model_probs = predictions[name]["model_probs"]
            engine.update_feedback(probs, actuals[name])
            engine.update_model_losses(model_probs, actuals[name])

    def walk_forward_backtest(self, seqs: Dict[str, List[str]], test_len: int = 30) -> Dict[str, float]:
        """完全无泄漏回测：每一步重新初始化系统"""
        total = 0
        correct = {name: 0 for name in self.engines}
        # 从最早的可预测点开始
        min_len = self.order + 10
        for idx in range(min_len, len(seqs["color"]) - 1):
            if idx < len(seqs["color"]) - test_len:
                continue   # 只测试最后 test_len 期
            # 重新创建系统（完全独立）
            system = PredictionSystemV5(order=self.order, min_norm_ig=self.min_norm_ig, max_ece=self.max_ece)
            train_seqs = {name: seq[:idx] for name, seq in seqs.items()}
            system.train_all(train_seqs)
            recents = {}
            for name in self.engines:
                if idx >= self.order:
                    recents[name] = seqs[name][idx-self.order:idx]
                else:
                    recents[name] = seqs[name][:idx]
            pred = system.predict_all(recents)
            actuals = {name: seqs[name][idx] for name in self.engines}
            should_act = pred["meta"]["should_act"]
            if should_act:
                for name in self.engines:
                    if pred[name]["best_state"] == actuals[name]:
                        correct[name] += 1
                total += 1
        # 如果没有出手次数，准确率无意义
        if total == 0:
            return {name: 0.0 for name in self.engines}
        return {name: correct[name]/total for name in self.engines}

# ========== 仪表盘 ==========
def print_dashboard(conn, order=2, min_norm_ig=0.1, max_ece=0.1, backtest_len=30):
    seqs = {
        "color": load_sequence(conn, get_color, limit=500),
        "size": load_sequence(conn, get_big_small, limit=500),
        "odd_even": load_sequence(conn, get_odd_even, limit=500)
    }
    if len(seqs["color"]) < order + 10:
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

    # 训练完整系统用于下一期预测
    system = PredictionSystemV5(order=order, min_norm_ig=min_norm_ig, max_ece=max_ece)
    system.train_all(seqs)
    recents = {name: seq[-order:] for name, seq in seqs.items()}
    pred = system.predict_all(recents)

    print(f"\n🔮 下一期属性预测 V5 (阶数={order})")
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
    print(f"   平均归一化信息增益: {meta['avg_norm_ig']:.4f}")
    print(f"   平均校准误差 ECE: {meta['avg_ece']:.4f}")

    # 回测
    print(f"\n📊 无泄漏 Walk-Forward 回测 (最近 {backtest_len} 期):")
    acc = system.walk_forward_backtest(seqs, test_len=backtest_len)
    for name, a in acc.items():
        print(f"   {name} 准确率: {a*100:.1f}%")
    if any(acc.values()):
        print(f"   平均准确率: {np.mean(list(acc.values()))*100:.1f}%")
    else:
        print("   未出手，无准确率数据")

# ========== 命令行 ==========
def cmd_sync(args):
    conn = connect_db(args.db)
    try:
        init_db(conn)
        records, source, url = fetch_online_records()
        total, ins, upd = sync_from_records(conn, records, source)
        print(f"同步完成: 总计 {total}, 新增 {ins}, 更新 {upd}, 来源 {source}")
        print_dashboard(conn, order=args.order, min_norm_ig=args.min_ig, max_ece=args.max_ece, backtest_len=args.backtest)
    except Exception as e:
        print(f"错误: {e}")
    finally:
        conn.close()

def cmd_show(args):
    conn = connect_db(args.db)
    try:
        print_dashboard(conn, order=args.order, min_norm_ig=args.min_ig, max_ece=args.max_ece, backtest_len=args.backtest)
    finally:
        conn.close()

def main():
    p = argparse.ArgumentParser(description="老澳门六合彩属性时序预测 V5")
    p.add_argument("--db", default=DB_PATH_DEFAULT)
    p.add_argument("--order", type=int, default=2, help="马尔可夫阶数")
    p.add_argument("--min-ig", type=float, default=0.1, help="最小归一化信息增益阈值")
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
