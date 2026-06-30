import io
import os

MAX_PHOTO_BYTES = int(os.environ.get("TIANWANG_MAX_PHOTO_BYTES", 1024 * 1024))


def compress_image_bytes(data: bytes, max_size: int = MAX_PHOTO_BYTES) -> tuple[bytes, dict]:
    from PIL import Image

    original_size = len(data)
    info = {
        "originalSize": original_size,
        "compressedSize": original_size,
        "quality": 100,
        "scale": 1.0,
        "compressed": False,
    }

    if original_size <= max_size:
        try:
            img = Image.open(io.BytesIO(data))
            if img.format == "JPEG" and img.mode in ("RGB", "L"):
                return data, info
        except Exception:
            return data, info

    img = Image.open(io.BytesIO(data))
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")

    quality = 85
    scale = 1.0
    best = None

    for _ in range(20):
        work = img
        if scale < 1.0:
            w, h = img.size
            work = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        work.save(buf, format="JPEG", quality=quality, optimize=True)
        out = buf.getvalue()
        best = out
        if len(out) <= max_size:
            info.update(
                {
                    "compressedSize": len(out),
                    "quality": quality,
                    "scale": round(scale, 2),
                    "compressed": True,
                }
            )
            return out, info
        if quality > 55:
            quality -= 8
        elif scale > 0.35:
            scale -= 0.08
            quality = 85
        else:
            break

    info.update(
        {
            "compressedSize": len(best or data),
            "quality": quality,
            "scale": round(scale, 2),
            "compressed": True,
            "warning": "已尽力压缩",
        }
    )
    return best or data, info
