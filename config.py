"""
To'lov qabul qilinadigan karta va mijozga ko'rsatiladigan platforma tugmalari.

MUHIM: Har bir platforma uchun 'url' maydoni o'sha ilova/sayt manzili.
Mijoz tugmani bosganda shu manzil ochiladi (agar ilova telefonda o'rnatilgan
bo'lsa, odatda avtomatik ilovani ochadi).
"""

CARD_NUMBER = "5614682210457448"
CARD_HOLDER = "AKHROLKHUJA RAKHMATKHUJAYEV"

PLATFORMS = [
    {"id": "payme", "name": "🔵 Payme", "url": "https://payme.uz"},
    {"id": "click", "name": "🟢 Click", "url": "https://my.click.uz"},
    {"id": "uzum", "name": "🟣 Uzum Bank", "url": "https://uzumbank.uz"},
]

# Narxi individual bo'lgan xizmatlar uchun (Konsultatsiya, Rahbarlar Kursi,
# Tizimlashtirish) mijoz ariza to'ldiradigan Google Form havolasi.
# Har bir mijoz uchun shaxsiy (chat_id oldindan to'ldirilgan) havola
# yaratish uchun asosiy (base) havola va "Telegram ID" savolining
# entry ID'si alohida saqlanadi.
GOOGLE_FORM_BASE_URL = (
    "https://docs.google.com/forms/d/e/"
    "1FAIpQLSdI3TiZtbVM6d_MowDap5YHG26pqb8KNRhlxnUBv7NxZQ9kVg/viewform"
)
GOOGLE_FORM_CHATID_ENTRY = "entry.351197641"

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
