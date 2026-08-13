"""
To'lov qabul qilinadigan karta va mijozga ko'rsatiladigan platforma tugmalari.

MUHIM: Har bir platforma uchun 'url' maydoni o'sha ilova/sayt manzili.
Mijoz tugmani bosganda shu manzil ochiladi (agar ilova telefonda o'rnatilgan
bo'lsa, odatda avtomatik ilovani ochadi).
"""

CARD_NUMBER = "5614 6821 2358 7752"
CARD_HOLDER = "AKHROLKHUJA RAKHMATKHUJAYEV"

PLATFORMS = [
    {"id": "payme", "name": "🔵 Payme", "url": "https://payme.uz"},
    {"id": "click", "name": "🟢 Click", "url": "https://my.click.uz"},
    {"id": "uzum", "name": "🟣 Uzum Bank", "url": "https://uzumbank.uz"},
]

# Narxi individual bo'lgan xizmatlar uchun (Konsultatsiya, Rahbarlar Kursi,
# Tizimlashtirish) mijoz ariza to'ldiradigan Google Form havolasi.
GOOGLE_FORM_URL = "https://forms.gle/RY4ux2hNEWC2nPqa9"

# Mijozdan so'raladigan hududlar ro'yxati (lead intake bosqichida).
REGIONS = [
    "Toshkent shahri",
    "Toshkent viloyati",
    "Andijon",
    "Farg'ona",
    "Namangan",
    "Samarqand",
    "Buxoro",
    "Navoiy",
    "Qashqadaryo",
    "Surxondaryo",
    "Jizzax",
    "Sirdaryo",
    "Xorazm",
    "Qoraqalpog'iston",
]
