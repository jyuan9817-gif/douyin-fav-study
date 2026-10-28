"""图片文字识别（本地运行，图片不上传）。

- Mac：用苹果系统自带的「实况文本」同款识别引擎（Vision 框架，通过 ocrmac 调用），中文很准，离线免费。
- Windows / 其他：用 RapidOCR（开源，ONNX 模型，第一次运行会自带模型，不需要联网下载）。
"""
import platform
import threading

_engine = None
_lock = threading.Lock()


class OCRUnavailable(Exception):
    pass


def backend():
    if platform.system() == "Darwin":
        try:
            import ocrmac  # noqa: F401
            return "vision"
        except Exception:
            pass
    try:
        import rapidocr_onnxruntime  # noqa: F401
        return "rapidocr"
    except Exception:
        pass
    return None


def status():
    b = backend()
    return {"backend": b, "label": {"vision": "苹果系统文字识别", "rapidocr": "RapidOCR"}.get(b, "未安装")}


def _vision(path):
    from ocrmac import ocrmac
    res = ocrmac.OCR(str(path), language_preference=["zh-Hans", "zh-Hant", "en-US"],
                     recognition_level="accurate").recognize()
    # res: [(文字, 置信度, (x, y, w, h))]，坐标原点在左下角 → 按从上到下、从左到右排
    rows = [(t, b) for t, c, b in res if t and t.strip() and c >= 0.3]
    rows.sort(key=lambda r: (-round((r[1][1] + r[1][3]) * 40), r[1][0]))
    return [t.strip() for t, _ in rows]


def _rapid(path):
    global _engine
    from rapidocr_onnxruntime import RapidOCR
    if _engine is None:
        _engine = RapidOCR()
    result, _ = _engine(str(path))
    lines = []
    for box, text, score in result or []:
        if text and text.strip() and float(score) >= 0.5:
            y = min(p[1] for p in box)
            x = min(p[0] for p in box)
            lines.append((y, x, text.strip()))
    lines.sort(key=lambda r: (round(r[0] / 12), r[1]))
    return [t for _, _, t in lines]


def recognize(path) -> str:
    """识别一张图片，返回按阅读顺序拼好的文字。"""
    b = backend()
    if b is None:
        raise OCRUnavailable("没装文字识别组件，请重新运行一次安装脚本")
    with _lock:
        lines = _vision(path) if b == "vision" else _rapid(path)
    return _merge(lines)


def _merge(lines):
    """把识别出的一行行文字拼起来：中文行之间直接相连，句末标点处换行。"""
    out = ""
    for ln in lines:
        if not out:
            out = ln
        elif out[-1] in "。！？!?：:；;" or len(ln) <= 6:
            out += "\n" + ln
        else:
            sep = " " if (out[-1].isascii() and ln[0].isascii()) else ""
            out += sep + ln
    return out.strip()
