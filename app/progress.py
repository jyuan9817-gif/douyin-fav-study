"""单条视频的进度条和预计剩余时间。

转文字这一步有真实进度（引擎每识别一段就上报一次）；其他步骤按以往实际耗时估算，
每处理完一条都会用实际耗时修正估算（指数平均），所以越用越准。
"""
import threading
import time

from . import config

STAGE_LABEL = {"fetching": "读取信息", "downloading": "下载", "transcribing": "转文字",
               "extracting": "AI 提炼", "deep": "精读"}
DEFAULT_TIMING = {"fetching": 6.0, "downloading": 8.0, "extracting": 20.0,
                  "rtf_mlx": 0.15, "rtf_faster": 0.6, "item": 60.0, "deep_call": 45.0}
ALPHA = 0.35  # 新数据的权重


def timing():
    t = dict(DEFAULT_TIMING)
    t.update(config.load().get("timing") or {})
    return t


def learn(key, value):
    t = timing()
    t[key] = round(t[key] * (1 - ALPHA) + value * ALPHA, 3)
    config.save({"timing": {k: v for k, v in t.items()}})


class Progress:
    def __init__(self):
        self._lock = threading.Lock()
        self.reset()

    def reset(self):
        with self._lock:
            self.item_id = None
            self.title = ""
            self.plan = []          # [(阶段, 预计秒数)]
            self.idx = -1
            self.stage_start = 0.0
            self.item_start = 0.0
            self.frac = None        # 当前阶段的真实进度 0~1（没有就按时间估算）
            self.duration = 0.0
            self.engine = "faster"
            self.is_note = False

    # ---------------------------------------------------------- 由处理线程调用
    def begin(self, it, has_transcript, engine):
        t = timing()
        dur = float(it.get("duration") or 0) or 90.0
        plan = []
        if not has_transcript:
            if it.get("source") != "local":
                plan += [("fetching", t["fetching"]), ("downloading", t["downloading"])]
            plan.append(("transcribing", dur * t[f"rtf_{engine}"]))
        plan.append(("extracting", t["extracting"]))
        with self._lock:
            self.item_id, self.title = it["id"], (it.get("title") or "")[:40]
            self.plan, self.idx, self.frac = plan, -1, None
            self.item_start = self.stage_start = time.time()
            self.duration, self.engine = dur, engine

    def set_title(self, title, duration=None):
        with self._lock:
            if title:
                self.title = title[:40]
            if duration:
                # 拿到真实时长后，重算转文字的预计时间
                self.duration = float(duration)
                t = timing()
                self.plan = [(n, self.duration * t[f"rtf_{self.engine}"]) if n == "transcribing" else (n, e)
                             for n, e in self.plan]

    def mark_note(self, n_images):
        """图文：没有视频时长，按图片张数估算下载和识别时间。"""
        with self._lock:
            self.is_note = True
            self.duration = 0.0
            est = {"downloading": 1.0 * n_images + 1, "transcribing": 1.5 * n_images + 1}
            self.plan = [(n, est.get(n, e)) for n, e in self.plan]

    def stage(self, name):
        now = time.time()
        with self._lock:
            self._finish_current(now)
            names = [n for n, _ in self.plan]
            if name in names:
                self.idx = names.index(name)
            else:
                self.plan.append((name, 10.0))
                self.idx = len(self.plan) - 1
            self.stage_start, self.frac = now, None

    def set_frac(self, f):
        with self._lock:
            self.frac = max(0.0, min(1.0, float(f)))

    def end(self, ok=True):
        now = time.time()
        with self._lock:
            if self.item_id is None:
                return
            self._finish_current(now)
            total = now - self.item_start
        if ok and total > 3:
            learn("item", total)
        self.reset()

    def _finish_current(self, now):
        if self.idx < 0 or self.idx >= len(self.plan):
            return
        name = self.plan[self.idx][0]
        spent = now - self.stage_start
        if spent < 0.5:
            return
        if self.is_note and name in ("downloading", "transcribing"):
            return  # 图文的耗时规律和视频不同，不拿来修正视频的估算
        try:
            if name in ("fetching", "downloading", "extracting"):
                learn(name, spent)
            elif name == "transcribing" and self.duration > 5:
                rtf = spent / self.duration
                if rtf < 3:  # 第一次要下载模型，会特别慢，不算进去
                    learn(f"rtf_{self.engine}", rtf)
        except Exception:
            pass

    # ---------------------------------------------------------- 给网页看
    def snapshot(self, queued_left=0):
        now = time.time()
        with self._lock:
            if self.item_id is None or self.idx < 0:
                avg = timing()["item"]
                return {"item_id": None, "batch_eta": round(queued_left * avg) if queued_left else 0}
            name, est = self.plan[self.idx]
            elapsed_stage = now - self.stage_start
            real = self.frac is not None and self.frac > 0.03
            if real:
                stage_frac = self.frac
                remain_stage = elapsed_stage * (1 - self.frac) / self.frac
            else:
                stage_frac = min(elapsed_stage / est, 0.95) if est > 0 else 0.5
                remain_stage = max(est - elapsed_stage, 2.0)
            future = sum(e for _, e in self.plan[self.idx + 1:])
            eta = remain_stage + future
            spent = now - self.item_start
            pct = spent / (spent + eta) if spent + eta > 0 else 0
            avg = timing()["item"]
            return {
                "item_id": self.item_id, "title": self.title, "stage": name,
                "stage_label": STAGE_LABEL.get(name, name),
                "step": self.idx + 1, "steps": len(self.plan), "is_note": self.is_note,
                "stage_pct": round(stage_frac * 100), "real": real,
                "pct": round(min(pct, 0.99) * 100), "eta": round(eta), "elapsed": round(spent),
                "batch_eta": round(eta + queued_left * avg),
            }
