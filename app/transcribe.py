"""本地语音转文字。音频不出你的电脑。

两个引擎：
- mlx-whisper：苹果芯片（M1/M2/M3/M4）Mac 专用，用 GPU 加速，比 faster-whisper 快好几倍
- faster-whisper：Windows / Intel Mac / Linux 通用

音频统一用 PyAV（faster-whisper 自带）解码成 16kHz 单声道，所以不需要另外装 ffmpeg。
"""
import os
import platform
import threading

from .config import MODELS_DIR

_model = None
_model_key = None
_lock = threading.Lock()
PROMPT = "以下是普通话的句子，使用简体中文和标点符号。"
SR = 16000


class NoSpeech(Exception):
    """视频里没有可用的人声（纯音乐、静音画面等）。"""


def is_apple_silicon():
    return platform.system() == "Darwin" and platform.machine() == "arm64"


def mlx_available():
    if not is_apple_silicon():
        return False
    try:
        import mlx_whisper  # noqa: F401
        return True
    except Exception:
        return False


def pick_engine(pref="auto"):
    if pref == "faster":
        return "faster"
    if pref == "mlx":
        return "mlx" if mlx_available() else "faster"
    return "mlx" if mlx_available() else "faster"


def engine_status():
    return {"apple_silicon": is_apple_silicon(), "mlx_available": mlx_available()}


def has_audio(path) -> bool:
    import av
    try:
        with av.open(str(path)) as c:
            return any(s.type == "audio" for s in c.streams)
    except Exception:
        return False


def load_audio(path, limit_sec=None):
    """把音视频解码成 16kHz 单声道 float32 数组。

    自己用 PyAV 解码，不用 faster_whisper.decode_audio——新版 PyAV 去掉了它用的
    metadata_errors 参数，会报 "open() got an unexpected keyword argument"。
    """
    import av
    import numpy as np

    max_samples = int(limit_sec * SR) if limit_sec else None
    chunks, total = [], 0
    try:
        container = av.open(str(path))
    except Exception as e:
        raise NoSpeech(f"打不开这个音视频文件：{e}")
    with container:
        stream = next((s for s in container.streams if s.type == "audio"), None)
        if stream is None:
            raise NoSpeech("视频没有声音")
        resampler = av.AudioResampler(format="s16", layout="mono", rate=SR)

        def take(frames):
            nonlocal total
            if frames is None:
                return
            if not isinstance(frames, (list, tuple)):
                frames = [frames]
            for f in frames:
                arr = f.to_ndarray().reshape(-1)
                chunks.append(arr)
                total += arr.shape[0]

        try:
            for frame in container.decode(stream):
                frame.pts = None  # 避免重采样器因时间戳不连续报错
                take(resampler.resample(frame))
                if max_samples and total >= max_samples:
                    break
            take(resampler.resample(None))  # 冲出缓冲区里剩下的样本
        except Exception as e:
            if not chunks:
                raise NoSpeech(f"音频解码失败：{e}")
            # 文件尾部损坏时，已解码的部分照样能用
    if not chunks:
        raise NoSpeech("视频没有声音")
    audio = np.concatenate(chunks).astype(np.float32) / 32768.0
    if max_samples:
        audio = audio[:max_samples]
    return audio


def _setup_hf(use_mirror):
    os.environ.setdefault("HF_HOME", str(MODELS_DIR / "hf"))
    if use_mirror:
        os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")


def _faster_model(size, log):
    global _model, _model_key
    with _lock:
        key = ("faster", size)
        if _model is not None and _model_key == key:
            return _model
        from faster_whisper import WhisperModel
        log(f"加载语音识别模型 faster-whisper {size}（第一次会自动下载，small 约 480MB）……")
        _model = WhisperModel(size, device="cpu", compute_type="int8", download_root=str(MODELS_DIR))
        _model_key = key
        log("语音识别模型已就绪。")
        return _model


def _run_faster(audio, size, log, should_stop, on_progress):
    model = _faster_model(size, log)
    total = len(audio) / SR
    segments, info = model.transcribe(audio, language="zh", beam_size=5, vad_filter=True,
                                      initial_prompt=PROMPT)
    segs = []
    for s in segments:  # 生成器：边识别边返回，正好用来报进度
        if should_stop():
            break
        segs.append({"start": s.start, "end": s.end, "text": s.text})
        if total:
            on_progress(min(s.end / total, 1.0))
    return segs


_mlx_loaded = set()


class _TqdmHook:
    """mlx-whisper 内部用 tqdm 显示进度，换成这个类就能拿到进度。"""
    callback = None

    def __init__(self, total=None, **kw):
        self.total, self.n = total or 0, 0

    def update(self, n=1):
        self.n += n
        if self.total and _TqdmHook.callback:
            _TqdmHook.callback(min(self.n / self.total, 1.0))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def close(self):
        pass


def _hook_mlx_progress(on_progress):
    import sys
    import types
    mod = sys.modules.get("mlx_whisper.transcribe")
    if mod is None or not hasattr(mod, "tqdm"):
        return False
    _TqdmHook.callback = on_progress
    cur = mod.tqdm
    if isinstance(cur, types.ModuleType) or isinstance(cur, types.SimpleNamespace):
        mod.tqdm = types.SimpleNamespace(tqdm=_TqdmHook)   # 写法是 `import tqdm` → tqdm.tqdm(...)
    else:
        mod.tqdm = _TqdmHook                               # 写法是 `from tqdm import tqdm`
    return True


def _run_mlx(audio, repo, log, on_progress):
    import mlx_whisper
    hooked = False
    try:
        hooked = _hook_mlx_progress(on_progress)
    except Exception:
        pass
    if repo not in _mlx_loaded:
        log(f"加载语音识别模型 {repo.split('/')[-1]}（Mac 加速版，第一次会自动下载约 1.6GB，请耐心等几分钟）……")
    with _lock:  # MLX 同一时间只跑一个
        r = mlx_whisper.transcribe(audio, path_or_hf_repo=repo, language="zh", initial_prompt=PROMPT,
                                   condition_on_previous_text=False,
                                   verbose=False if hooked else None)  # False 时内部会走 tqdm 进度
    if repo not in _mlx_loaded:
        _mlx_loaded.add(repo)
        log("语音识别模型已就绪。")
    return [{"start": s["start"], "end": s["end"], "text": s["text"]} for s in r.get("segments", [])]


def transcribe(path, *, engine="auto", mlx_model="mlx-community/whisper-large-v3-turbo", size="small",
               use_mirror=False, max_minutes=15, log=print, should_stop=lambda: False,
               on_progress=lambda f: None):
    """返回 {"text", "segments", "duration", "engine"}。"""
    _setup_hf(use_mirror)
    audio = load_audio(path, max_minutes * 60 if max_minutes else None)
    duration = len(audio) / SR
    if duration < 1:
        raise NoSpeech("视频没有声音")
    eng = pick_engine(engine)
    if eng == "mlx":
        try:
            segs = _run_mlx(audio, mlx_model, log, on_progress)
        except Exception as e:
            log(f"Mac 加速引擎出错（{str(e)[:120]}），改用通用引擎重试……")
            eng = "faster"
    if eng == "faster":
        segs = _run_faster(audio, size, log, should_stop, on_progress)

    segs = [{"start": round(s["start"], 1), "end": round(s["end"], 1), "text": s["text"].strip()}
            for s in segs if s["text"].strip()]
    segs = _drop_repeats(segs)
    text = "".join(_join(segs))
    # 少于 15 个字基本就是纯音乐/只有一两句歌词，不值得提炼
    if len(text.replace(" ", "")) < 15:
        raise NoSpeech("视频里几乎没有说话内容（可能是纯音乐或无对白）")
    return {"text": text, "segments": segs, "duration": duration, "engine": eng}


def _drop_repeats(segs):
    """Whisper 遇到背景音乐偶尔会把同一句重复很多遍，连续重复超过 2 次的去掉。"""
    out, run = [], 0
    for s in segs:
        if out and s["text"] == out[-1]["text"]:
            run += 1
            if run >= 2:
                continue
        else:
            run = 0
        out.append(s)
    return out


def _join(segs):
    for s in segs:
        t = s["text"]
        yield t if t[-1] in "，。！？；：,.!?;:" else t + "，"


# ---------------------------------------------------------------- 字幕

def parse_subtitle(raw: str) -> str:
    """把 WebVTT / SRT / 抖音 JSON 字幕变成纯文字。"""
    import json
    import re
    raw = (raw or "").strip()
    if not raw:
        return ""
    if raw[0] in "[{":
        try:
            data = json.loads(raw)
            items = data if isinstance(data, list) else (
                data.get("utterances") or data.get("captions") or data.get("body") or data.get("subtitles") or [])
            lines = [str(x.get("text") or x.get("content") or "").strip() for x in items if isinstance(x, dict)]
            return _clean_lines(lines)
        except Exception:
            pass
    lines = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.upper().startswith(("WEBVTT", "NOTE", "STYLE", "REGION")) or line.isdigit() or "-->" in line:
            continue
        line = re.sub(r"<[^>]+>", "", line)
        lines.append(line)
    return _clean_lines(lines)


def _clean_lines(lines):
    out = []
    for t in lines:
        t = t.strip()
        if t and (not out or out[-1] != t):
            out.append(t)
    return "".join(t if t[-1] in "，。！？；：,.!?;:" else t + "，" for t in out)


def _ts(t):
    """'00:01:02.500' / '01:02,5' → 秒"""
    t = t.strip().replace(",", ".")
    parts = t.split(":")
    try:
        sec = 0.0
        for x in parts:
            sec = sec * 60 + float(x)
        return sec
    except ValueError:
        return None


def subtitle_segments(raw: str):
    """从 WebVTT / SRT 里取出带时间戳的分段；JSON 字幕尽量取 start/end 字段。"""
    import json
    import re
    raw = (raw or "").strip()
    segs = []
    if raw[:1] in "[{":
        try:
            data = json.loads(raw)
            items = data if isinstance(data, list) else (
                data.get("utterances") or data.get("captions") or data.get("body") or data.get("subtitles") or [])
            for x in items:
                if not isinstance(x, dict):
                    continue
                st = x.get("start_time", x.get("start", x.get("from")))
                en = x.get("end_time", x.get("end", x.get("to")))
                tx = str(x.get("text") or x.get("content") or "").strip()
                if tx and isinstance(st, (int, float)):
                    div = 1000 if st > 10000 else 1  # 毫秒 → 秒
                    segs.append({"start": st / div, "end": (en or st) / div, "text": tx})
            return segs
        except Exception:
            return []
    cur = None
    for line in raw.splitlines():
        line = line.strip()
        if "-->" in line:
            a, b = line.split("-->", 1)
            s0, e0 = _ts(a), _ts(b.split()[0] if b.split() else b)
            cur = {"start": s0 or 0.0, "end": e0 or s0 or 0.0, "text": ""} if s0 is not None else None
            if cur:
                segs.append(cur)
        elif cur is not None and line and not line.isdigit():
            cur["text"] += re.sub(r"<[^>]+>", "", line)
        elif not line:
            cur = None
    return [x for x in segs if x["text"]]
