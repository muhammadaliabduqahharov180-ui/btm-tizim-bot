"""
Mavzu bo'yicha video moslashtirish mantig'i.

Saqlash endi PostgreSQL orqali (db.py) amalga oshadi — bu fayl endi faqat
matndan mavzuni aniqlash mantig'ini o'z ichiga oladi, hech qanday faylga
yozmaydi/o'qimaydi.

Yangi mavzu qo'shish uchun TOPIC_KEYWORDS ro'yxatiga qator qo'shing — teg
nomi admin botga video yuborayotganda ishlatadigan caption bilan bir xil
bo'lishi kerak.
"""

TOPIC_KEYWORDS = {
    "marketing": ["marketing", "smm", "reklama", "kontent"],
    "sotuv": ["sotuv", "sotish", "savdo"],
    "moliya": ["moliya", "moliyaviy", "budjet", "byudjet"],
    "hr": ["hr", "kadrlar", "xodim"],
    "boshqaruv": ["boshqaruv", "menejment"],
    "tizimlashtirish": ["tizimlashtirish", "tizim"],
    "shogirtlik": ["shogirtlik", "shogird", "akademiya"],
    "konsultatsiya": ["konsultatsiya", "maslahat"],
    "rahbarlar_kursi": ["rahbar", "rahbarlik"],
}


def find_video_for_text(text: str, library: dict) -> str | None:
    """Matndagi kalit so'zlarga qarab, berilgan kutubxonadan mos videoning
    file_id'sini qaytaradi. `library` — {"tag": ["file_id", ...], ...}."""
    if not text or not library:
        return None
    lowered = text.lower()
    for topic, keywords in TOPIC_KEYWORDS.items():
        if any(keyword in lowered for keyword in keywords):
            videos = library.get(topic)
            if videos:
                return videos[0]
    return None
