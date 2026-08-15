"""
Botda taklif qilinadigan xizmatlar ro'yxati.
Yangi xizmat qo'shish, narxni yoki rejani o'zgartirish uchun shu faylni tahrirlang.

Ikki xil xizmat turi bor:

1) type = "payment"
   Mijoz to'g'ridan-to'g'ri shu yerda to'laydi (karta raqami ko'rsatiladi).
   "plans" ro'yxatida bitta yoki bir nechta to'lov varianti bo'lishi mumkin
   (masalan: to'liq / 6 oyga bo'lib / 12 oyga bo'lib).

2) type = "inquiry"
   Narx individual, soatlik yoki diagnostikaga bog'liq bo'lgani uchun to'lov
   ekrani ko'rsatilmaydi. Mijoz "Ariza qoldirish" tugmasini bosadi, so'rovi
   to'g'ridan-to'g'ri adminга (sizga) yuboriladi.
"""

SERVICES = {
    "seminar": {
        "name": "📚 Offline seminarga yozilish",
        "title": "Offline seminarga yozilish",
        "type": "payment",
        "plans": [
            {"id": "full", "label": "💯 To'liq (99,000 so'm)", "amount": 99_000},
        ],
        "location": {
            "latitude": 41.374064,
            "longitude": 69.271688,
            "address": "7-mavze, 20B, Yunusobod dahasi, Yunusobod tumani, Toshkent",
        },
    },
    "shogirtlik": {
        "name": "🚀 Biznes Shogirtlik dasturi",
        "title": "Biznes Shogirtlik dasturi (2 yil: 6 oy nazariy + 1.5 yil amaliyot)",
        "type": "payment",
        "plans": [
            {"id": "full", "label": "💯 To'liq (15,000,000 so'm)", "amount": 15_000_000},
            {
                "id": "plan6",
                "label": "🔢 6 oyga bo'lib (2,500,000 so'm/oy)",
                "amount": 2_500_000,
                "months": 6,
            },
        ],
    },
    "shogirtlik_online": {
        "name": "💻 Biznes Shogirtlik dasturi (onlayn kurs)",
        "title": "Biznes Shogirtlik dasturi (onlayn kurs)",
        "type": "payment",
        "plans": [
            {"id": "full", "label": "💯 To'liq (12,000,000 so'm)", "amount": 12_000_000},
            {
                "id": "plan6",
                "label": "🔢 6 oyga bo'lib (2,000,000 so'm/oy)",
                "amount": 2_000_000,
                "months": 6,
            },
        ],
    },
    "konsultatsiya": {
        "name": "💼 Biznes Konsultatsiya",
        "title": "Biznes Konsultatsiya",
        "type": "inquiry",
        "note": "Narx: $500 / soat. Ariza qoldiring — tez orada bog'lanamiz.",
    },
    "rahbarlar_kursi": {
        "name": "🎯 Rahbarlar Kursi",
        "title": "Rahbarlar Kursi (10 hafta)",
        "type": "inquiry",
        "note": "Narx individual belgilanadi. Ariza qoldiring — tez orada bog'lanamiz.",
    },
    "tizimlashtirish": {
        "name": "🧩 Bizneslarni Tizimlashtirish",
        "title": "Bizneslarni Tizimlashtirish",
        "type": "inquiry",
        "note": (
            "Narx diagnostika natijasiga qarab belgilanadi. "
            "Ariza qoldiring — tez orada bog'lanamiz."
        ),
    },
}


def get_service(service_id: str):
    return SERVICES.get(service_id)


def get_plan(service_id: str, plan_id: str):
    service = get_service(service_id)
    if not service:
        return None
    for plan in service.get("plans", []):
        if plan["id"] == plan_id:
            return plan
    return None
