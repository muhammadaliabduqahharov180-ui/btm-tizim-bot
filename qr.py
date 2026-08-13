"""QR-kod yaratish uchun yordamchi modul."""

import io
import qrcode


def generate_qr_png(data: str) -> bytes:
    """Berilgan matn/havolani QR-kod PNG rasmi (bayt ko'rinishida) qilib qaytaradi."""
    img = qrcode.make(data)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
