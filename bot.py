import asyncio
import logging
import os
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
    BufferedInputFile,
)
from dotenv import load_dotenv
from anthropic import AsyncAnthropic
from openai import OpenAI

import db
import sheets
from qr import generate_qr_png
from services import SERVICES, get_service, get_plan
from config import CARD_NUMBER, CARD_HOLDER, PLATFORMS, GOOGLE_FORM_URL, REGIONS
from ai_prompt import AI_SYSTEM_CONTEXT
from video_library import find_video_for_text

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")  # sizning shaxsiy Telegram chat ID'ingiz
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
CLAUDE_MODEL = "claude-sonnet-5"
GROQ_MODEL = "openai/gpt-oss-20b"

TASHKENT_TZ = ZoneInfo("Asia/Tashkent")
WELCOME_VIDEO_NOTE_TAG = "welcome_note"
SEMINAR_REMINDER_DAY_TAG = "seminar_reminder_day"
SEMINAR_REMINDER_HOURS_TAG = "seminar_reminder_hours"
SEMINAR_FOLLOWUP_VIDEO_TAG = "seminar_followup_video"
PAYMENT_APPROVED_VIDEO_TAG = "payment_approved_note"
PAYMENT_REJECTED_VIDEO_TAG = "payment_rejected_note"
RECEIPT_RECEIVED_VIDEO_TAG = "receipt_received_note"
INSTRUCTION_VIDEO_TAG = "instruction_video"

# Admin dumaloq video yuborganda caption'da yozadigan qisqa so'z -> ichki teg
VIDEO_NOTE_TAG_MAP = {
    "welcome": WELCOME_VIDEO_NOTE_TAG,
    "reminder_day": SEMINAR_REMINDER_DAY_TAG,
    "reminder_hours": SEMINAR_REMINDER_HOURS_TAG,
    "followup": SEMINAR_FOLLOWUP_VIDEO_TAG,
    "payment_approved": PAYMENT_APPROVED_VIDEO_TAG,
    "payment_rejected": PAYMENT_REJECTED_VIDEO_TAG,
    "receipt_received": RECEIPT_RECEIVED_VIDEO_TAG,
    "instruction": INSTRUCTION_VIDEO_TAG,
}

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# Agar ANTHROPIC_API_KEY bo'lsa — Claude ishlatiladi (tavsiya etiladi, sifatliroq).
# Aks holda, GROQ_API_KEY bo'lsa — Groq ishlatiladi (arzonroq/bepul, sifat pastroq).
claude_client = (
    AsyncAnthropic(api_key=ANTHROPIC_API_KEY) if ANTHROPIC_API_KEY else None
)
groq_client = (
    OpenAI(api_key=GROQ_API_KEY, base_url="https://api.groq.com/openai/v1")
    if GROQ_API_KEY
    else None
)

# Video kutubxonasining tezkor xotiradagi nusxasi (DB'dan ishga tushganda
# to'ldiriladi, admin yangi video qo'shsa yangilanadi). Har bir xabarda DB'ga
# murojaat qilmaslik uchun kerak.
VIDEO_CACHE: dict = {}

# Botning o'z username'i — QR-kod ichidagi deep-link (t.me/<username>?start=...)
# yaratish uchun kerak. main() da bot.get_me() orqali to'ldiriladi.
BOT_USERNAME: str = ""


async def ask_ai(chat_id: int, user_text: str) -> str:
    """Claude (afzal) yoki Groq (agar Claude kaliti yo'q bo'lsa) orqali javob oladi."""
    if not claude_client and not groq_client:
        return (
            "Kechirasiz, hozircha AI yordamchi ulanmagan. "
            "Iltimos, /start bosib xizmatlarimiz bilan tanishing."
        )

    history = await db.get_ai_history(chat_id)
    is_first_message = len(history) == 0
    history.append({"role": "user", "content": user_text})

    lead = await db.get_lead(chat_id)
    known_bits = []
    if lead.get("name"):
        known_bits.append(f"ism = \"{lead['name']}\"")
    if lead.get("phone"):
        known_bits.append(f"telefon = \"{lead['phone']}\"")
    if lead.get("region"):
        known_bits.append(f"hudud = \"{lead['region']}\"")
    if lead.get("mode"):
        known_bits.append(f"format = \"{'onlayn' if lead['mode'] == 'online' else 'oflayn'}\"")

    if known_bits:
        lead_context = (
            "MIJOZ HAQIDA MA'LUM MA'LUMOT (bazadan): " + ", ".join(known_bits) + ".\n"
            "QAT'IY QOIDA: quyidagi algoritmda yoki savollarda \"ismingiz nima\" yoki "
            "\"telefon raqamingiz\" kabi savol bo'lsa ham, bu ma'lumotlar YUQORIDA "
            "ALLAQACHON BERILGAN — ularni IKKINCHI MARTA SO'RASH TAQIQLANADI. Kerak "
            "bo'lsa shu ismdan foydalan, telefon allaqachon bazada bor deb hisobla. "
            "Faqat hali noma'lum bo'lgan narsalarni (shahar, faoliyat turi, daromad, "
            "qaysi xizmatga qiziqishi va h.k.) so'ra."
        )
    else:
        lead_context = (
            "MIJOZ HAQIDA MA'LUM MA'LUMOT: hozircha yo'q. Kerak bo'lganda ism va "
            "telefon raqamini so'rashing mumkin."
        )

    if is_first_message:
        turn_instruction = (
            "Bu mijoz bilan birinchi murojaat — javobingni salomlashish bilan boshla."
        )
    else:
        turn_instruction = (
            "Bu davom etayotgan suhbat — ASLO 'Assalomu alaykum', 'Salom' yoki "
            "boshqa salomlashish so'zi bilan boshlama. To'g'ridan-to'g'ri mijozning "
            "oxirgi xabariga javob ber."
        )

    system_message = (
        AI_SYSTEM_CONTEXT.replace("{{LEAD_CONTEXT}}", lead_context)
        + "\n\n" + turn_instruction
    )

    try:
        if claude_client:
            response = await claude_client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=1024,
                system=system_message,
                messages=history,
            )
            answer = response.content[0].text
        else:
            response = groq_client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[{"role": "system", "content": system_message}] + history,
                max_tokens=350,
            )
            answer = response.choices[0].message.content

        history.append({"role": "assistant", "content": answer})
        await db.set_ai_history(chat_id, history)
        return answer
    except Exception as e:
        logging.error(f"AI xatosi: {e}")
        if "429" in str(e) or "rate_limit" in str(e).lower():
            return (
                "Kechirasiz, hozir so'rovlar biroz ko'payib ketdi va AI vaqtinchalik "
                "band. Iltimos, bir necha soniyadan keyin qayta yozing yoki savolingizni "
                "shu yerga qoldiring — tez orada javob beramiz. 🙏"
            )
        return (
            "Kechirasiz, hozir javob bera olmayapman. "
            "Iltimos, birozdan keyin qayta urinib ko'ring."
        )


class LeadForm(StatesGroup):
    name = State()
    phone = State()


class PaymentForm(StatesGroup):
    full_name = State()


class AdminVideoUpload(StatesGroup):
    waiting_note = State()


MENU_SERVICES = "🎓 Xizmatlar"
MENU_PAYMENTS = "💳 To'lovlar"
MENU_CONTACT = "☎️ Biz bilan bog'lanish"
MENU_LOCATION = "📍 Manzil"
MENU_GUIDE = "📖 Qo'llanma"
MENU_RESTART = "🔄 Qaytadan boshlash"
MENU_BACK = "⬅️ Orqaga"


def build_services_menu_keyboard(payment_only: bool = False) -> ReplyKeyboardMarkup:
    """Xizmatlar ro'yxatini pastdagi menyu (reply keyboard) sifatida quradi."""
    services = [
        s for s in SERVICES.values() if not payment_only or s["type"] == "payment"
    ]
    rows = []
    row = []
    for service in services:
        row.append(KeyboardButton(text=service["name"]))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([KeyboardButton(text=MENU_BACK)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def build_plans_menu_keyboard(service: dict) -> ReplyKeyboardMarkup:
    """Bitta xizmatning to'lov rejalarini pastdagi menyu sifatida quradi."""
    rows = [[KeyboardButton(text=plan["label"])] for plan in service.get("plans", [])]
    rows.append([KeyboardButton(text=MENU_BACK)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def build_main_menu_keyboard() -> ReplyKeyboardMarkup:
    """Doim pastda turadigan asosiy menyu — istalgan vaqt ko'rinib turadi."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=MENU_SERVICES), KeyboardButton(text=MENU_PAYMENTS)],
            [KeyboardButton(text=MENU_CONTACT), KeyboardButton(text=MENU_LOCATION)],
            [KeyboardButton(text=MENU_GUIDE), KeyboardButton(text=MENU_RESTART)],
        ],
        resize_keyboard=True,
    )


async def get_menu_keyboard_for(chat_id: int) -> ReplyKeyboardMarkup:
    """Har doim to'liq asosiy menyuni qaytaradi."""
    return build_main_menu_keyboard()


# Xizmat nomi -> xizmat id, va reja label -> (xizmat id, reja id) — matndan
# tez topish uchun tayyorlab qo'yamiz (SERVICES o'zgarsa, bular ham yangilanadi).
SERVICE_NAMES = {service["name"]: sid for sid, service in SERVICES.items()}
PLAN_LABELS = {
    plan["label"]: (sid, plan["id"])
    for sid, service in SERVICES.items()
    for plan in service.get("plans", [])
}


@dp.message(CommandStart(deep_link=True))
async def handle_start_deep_link(message: Message, state: FSMContext, command: CommandObject):
    """
    /start ga payload (masalan checkin_<token>) qo'shib kelinganda ishlaydi —
    hozircha faqat QR-kod orqali "kelganini belgilash" uchun ishlatiladi.
    Bunday holatda oddiy lead-yig'ish oqimi (ism/telefon) ISHGA TUSHMAYDI.
    """
    payload = command.args or ""
    if payload.startswith("checkin_"):
        await handle_qr_checkin(message, payload[len("checkin_"):])
        return
    # Tanilmagan payload — oddiy /start sifatida davom ettiramiz
    await handle_start(message, state)


async def handle_qr_checkin(message: Message, token: str):
    """QR-kod skanerlanib, bot /start checkin_<token> bilan ochilganda ishlaydi.
    Faqat admin skanerlaganda ishlaydi (mijoz o'zi bossa, ma'no bermaydi)."""
    if not _is_admin(message.chat.id):
        await message.answer(
            "Bu havola faqat tadbir ma'muri uchun mo'ljallangan."
        )
        return

    result = await db.checkin_by_token(token)

    if result is None:
        await message.answer("⚠️ Noto'g'ri yoki topilmagan QR-kod.")
        return

    if result["already"]:
        checked_at = result.get("checked_in_at")
        when = checked_at.strftime("%d.%m.%Y %H:%M") if checked_at else "—"
        await message.answer(
            f"⚠️ <b>{result['name']}</b> allaqachon ro'yxatdan o'tgan ({when})."
        , parse_mode="HTML")
        return

    await message.answer(
        f"✅ <b>{result['name']}</b> muvaffaqiyatli ro'yxatdan o'tkazildi!\n"
        f"📞 {result.get('phone', '—')}\n"
        f"🎓 {result.get('title', '—')}",
        parse_mode="HTML",
    )

    sheets.append_checkin_row([
        datetime.now(TASHKENT_TZ).strftime("%d.%m.%Y %H:%M"),
        result["name"],
        result.get("phone", "—"),
        result.get("title", "—"),
        result.get("chat_id", "—"),
    ])


@dp.message(CommandStart())
async def handle_start(message: Message, state: FSMContext):
    """Mijoz botga kirganda — avval ism so'raladi (lead yig'ish boshlanadi)."""
    await state.clear()
    await state.set_state(LeadForm.name)

    # Ishonch hosil qilish uchun dumaloq (video note) xush kelibsiz videosi bo'lsa yuboramiz
    welcome_notes = VIDEO_CACHE.get(WELCOME_VIDEO_NOTE_TAG)
    if welcome_notes:
        try:
            await bot.send_video_note(
                message.chat.id, welcome_notes[0], protect_content=True
            )
        except Exception as e:
            logging.error(f"Xush kelibsiz videosini yuborishda xato: {e}")

    # Botdan qanday foydalanish haqida qo'llanma video (bor bo'lsa)
    await _send_seminar_video(message.chat.id, INSTRUCTION_VIDEO_TAG)

    await message.answer(
        "Assalomu alaykum! 👋\n"
        "Bu BTM | TIZIM to'lov botiga xush kelibsiz.\n\n"
        "Ismingizni kiriting:",
        reply_markup=ReplyKeyboardRemove(),
    )


def _is_admin(chat_id) -> bool:
    return bool(ADMIN_CHAT_ID) and str(chat_id) == str(ADMIN_CHAT_ID)


@dp.message(Command("stats"))
async def handle_stats(message: Message):
    """Faqat admin uchun: umumiy va davriy statistika (leadlar, to'lovlar)."""
    if not _is_admin(message.chat.id):
        return

    stats = await db.get_stats()
    if not stats:
        await message.answer("Statistika mavjud emas (baza ulanmagan bo'lishi mumkin).")
        return

    text = (
        "📊 <b>Statistika</b>\n\n"
        f"👥 <b>Leadlar</b>\n"
        f"  • Jami: {stats['leads_total']}\n"
        f"  • Bugun: {stats['leads_today']}\n"
        f"  • Shu oy: {stats['leads_month']}\n\n"
        f"💳 <b>Tasdiqlangan to'lovlar</b>\n"
        f"  • Jami: {stats['payments_total_count']} ta — {stats['payments_total_sum']:,} so'm\n"
        f"  • Bugun: {stats['payments_today_count']} ta — {stats['payments_today_sum']:,} so'm\n"
        f"  • Shu oy: {stats['payments_month_count']} ta — {stats['payments_month_sum']:,} so'm\n\n"
        f"❌ Rad etilgan to'lovlar: {stats['payments_rejected_count']} ta"
    )
    await message.answer(text, parse_mode="HTML")


@dp.message(Command("leads"))
async def handle_leads(message: Message, command: CommandObject):
    """
    Faqat admin uchun: so'nggi leadlar ro'yxati.
    /leads — oxirgi 10 tasi
    /leads Ali — ism/telefon/F.I.Sh bo'yicha qidiradi
    """
    if not _is_admin(message.chat.id):
        return

    search = (command.args or "").strip() or None
    total = await db.count_all_leads(search=search)
    leads = await db.list_leads(limit=10, search=search)

    if not leads:
        await message.answer(
            f"\"{search}\" bo'yicha hech narsa topilmadi." if search else "Hozircha leadlar yo'q."
        )
        return

    header = f"📋 <b>Leadlar</b> (jami: {total})"
    if search:
        header += f" — qidiruv: \"{search}\""
    lines = [header, ""]

    for lead in leads:
        created = lead["created_at"].strftime("%d.%m.%Y %H:%M") if lead.get("created_at") else "—"
        mode_label = "Onlayn" if lead.get("mode") == "online" else ("Oflayn" if lead.get("mode") else "—")
        lines.append(
            f"• <b>{lead.get('name') or '—'}</b> | {lead.get('phone') or '—'}\n"
            f"  F.I.Sh: {lead.get('full_name') or '—'} | {created}\n"
            f"  Hudud: {lead.get('region') or '—'} | Format: {mode_label}\n"
            f"  Chat ID: <code>{lead['chat_id']}</code>"
        )

    lines.append("")
    lines.append(
        "Qidirish uchun: <code>/leads Ali</code> yoki <code>/leads +998...</code>"
        if not search
        else "Boshqa qidiruv: <code>/leads &lt;ism yoki telefon&gt;</code>"
    )

    await message.answer("\n".join(lines), parse_mode="HTML")


ADMIN_VIDEO_COMMANDS = {
    "setwelcome": WELCOME_VIDEO_NOTE_TAG,
    "setreminderday": SEMINAR_REMINDER_DAY_TAG,
    "setreminderhours": SEMINAR_REMINDER_HOURS_TAG,
    "setfollowupvideo": SEMINAR_FOLLOWUP_VIDEO_TAG,
    "setpaymentapproved": PAYMENT_APPROVED_VIDEO_TAG,
    "setpaymentrejected": PAYMENT_REJECTED_VIDEO_TAG,
    "setreceiptvideo": RECEIPT_RECEIVED_VIDEO_TAG,
    "setinstructionvideo": INSTRUCTION_VIDEO_TAG,
}


@dp.message(Command(*ADMIN_VIDEO_COMMANDS.keys()))
async def handle_admin_video_tag_command(
    message: Message, command: CommandObject, state: FSMContext
):
    """
    Admin uchun: dumaloq video qaysi turkumga tegishli ekanini belgilaydi
    (video note'larda caption bo'lmagani uchun buyruq orqali qilinadi).
    Bu handler ATAYLAB yuqorida, FSM (ism/telefon/to'liq ism) matn
    handlerlaridan OLDIN turadi — aks holda agar admin biror bosqichda
    "qolib qolgan" bo'lsa, buyruq o'sha maydonga yozilib, jim yutilib
    ketishi mumkin edi.
    /setwelcome         — /start dagi xush kelibsiz videosi
    /setreminderday     — seminardan 1 kun oldingi eslatma
    /setreminderhours   — seminar kunidagi eslatma (bir necha soat oldin)
    /setfollowupvideo   — seminardan keyingi kuni taassurot so'rash videosi
    /setpaymentapproved — to'lov TASDIQLANGANDA yuboriladigan video
    /setpaymentrejected — to'lovda MUAMMO bo'lganda (rad etilganda) yuboriladigan video
    /setreceiptvideo    — mijoz CHEK (skrinshot) yuborganda darhol yuboriladigan video
    /setinstructionvideo — botdan qanday foydalanish haqida qo'llanma video
    """
    if not _is_admin(message.chat.id):
        return

    tag = ADMIN_VIDEO_COMMANDS[command.command]
    await state.set_state(AdminVideoUpload.waiting_note)
    await state.update_data(video_tag=tag)
    await message.answer("Endi shu turkum uchun DUMALOQ videoni yuboring 🎥")


@dp.message(LeadForm.name, F.text, ~F.text.startswith("/"))
async def handle_lead_name(message: Message, state: FSMContext):
    """Ism qabul qilinadi, keyin telefon raqami so'raladi."""
    name = message.text.strip() if message.text else ""

    if not name:
        await message.answer("Iltimos, ismingizni matn ko'rinishida yuboring.")
        return

    await db.upsert_lead(message.chat.id, name=name)
    await state.set_state(LeadForm.phone)

    phone_keyboard = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Raqamni yuborish", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await message.answer(
        f"Rahmat, {name}! 🙏\n\n"
        f"Endi telefon raqamingizni yuboring (tugmani bosing yoki qo'lda kiriting):",
        reply_markup=phone_keyboard,
    )


@dp.message(LeadForm.phone, F.contact)
async def handle_lead_phone_contact(message: Message, state: FSMContext):
    """Telefon raqami 'Raqamni yuborish' tugmasi orqali kelganda."""
    phone = message.contact.phone_number
    await save_lead_phone_and_continue(message, state, phone)


@dp.message(LeadForm.phone, F.text, ~F.text.startswith("/"))
async def handle_lead_phone_text(message: Message, state: FSMContext):
    """Telefon raqami qo'lda matn sifatida kiritilganda."""
    phone = message.text.strip()
    await save_lead_phone_and_continue(message, state, phone)


async def save_lead_phone_and_continue(message: Message, state: FSMContext, phone: str):
    chat_id = message.chat.id
    await db.upsert_lead(chat_id, phone=phone, username=message.from_user.username)
    await state.clear()

    lead = await db.get_lead(chat_id)
    name = lead.get("name", "")

    # AI suhbat tarixiga ism/telefonni "haqiqiy xabar" sifatida yozib qo'yamiz —
    # shunda AI keyinroq buni tarixdan ko'rib, qayta so'ramaydi.
    await db.set_ai_history(
        chat_id,
        [
            {"role": "user", "content": f"Ismim {name}, telefon raqamim {phone}."},
            {
                "role": "assistant",
                "content": f"Rahmat, {name}! Ismingiz va telefon raqamingizni yozib oldim.",
            },
        ],
    )

    # Adminga yangi lead haqida xabar beramiz
    if ADMIN_CHAT_ID:
        await bot.send_message(
            ADMIN_CHAT_ID,
            f"🆕 <b>Yangi lead</b>\n\n"
            f"Ism: {lead.get('name', '—')}\n"
            f"Telefon: {lead.get('phone', '—')}\n"
            f"Telegram: @{message.from_user.username or 'username yoq'}\n"
            f"Chat ID: {chat_id}",
            parse_mode="HTML",
        )

    await message.answer(
        "Rahmat! ✅\n\nQaysi hududdansiz?",
        reply_markup=build_region_keyboard(),
    )


def build_region_keyboard() -> InlineKeyboardMarkup:
    """Hududlar ro'yxatini 2 ustunli inline klaviatura sifatida quradi."""
    buttons = [
        InlineKeyboardButton(text=region, callback_data=f"region:{i}")
        for i, region in enumerate(REGIONS)
    ]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    return InlineKeyboardMarkup(inline_keyboard=rows)


@dp.callback_query(F.data.startswith("region:"))
async def handle_region_choice(callback: CallbackQuery):
    """Mijoz hududni tanlaganda — saqlaydi va onlayn/oflayn formatni so'raydi."""
    try:
        idx = int(callback.data.split(":", 1)[1])
        region = REGIONS[idx]
    except (ValueError, IndexError):
        await callback.answer()
        return

    chat_id = callback.message.chat.id
    await db.upsert_lead(chat_id, region=region)
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer(f"{region} ✅")

    mode_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="🟢 Onlayn", callback_data="mode:online"),
            InlineKeyboardButton(text="🔵 Oflayn", callback_data="mode:oflayn"),
        ]]
    )
    await callback.message.answer(
        "Xizmatlardan onlayn yoki oflayn foydalanishni afzal ko'rasiz?",
        reply_markup=mode_keyboard,
    )


@dp.callback_query(F.data.startswith("mode:"))
async def handle_mode_choice(callback: CallbackQuery):
    """Mijoz onlayn/oflayn formatni tanlaganda — saqlaydi, lead yig'ish yakunlanadi."""
    mode = callback.data.split(":", 1)[1]
    chat_id = callback.message.chat.id

    await db.upsert_lead(chat_id, mode=mode)
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("Saqlandi ✅")

    lead = await db.get_lead(chat_id)
    sheets.append_lead_row([
        datetime.now(TASHKENT_TZ).strftime("%d.%m.%Y %H:%M"),
        lead.get("name", "—"),
        lead.get("phone", "—"),
        lead.get("region", "—"),
        "Onlayn" if mode == "online" else "Oflayn",
        f"@{callback.from_user.username}" if callback.from_user.username else "—",
        chat_id,
    ])

    await callback.message.answer(
        "Rahmat! ✅\n\nPastdagi menyudan kerakli bo'limni tanlang 👇",
        reply_markup=await get_menu_keyboard_for(chat_id),
    )


@dp.message(F.text.in_(SERVICE_NAMES.keys()))
async def handle_service_text_choice(message: Message):
    """Mijoz pastdagi menyudan xizmat nomini tanlaganda ishlaydi."""
    service_id = SERVICE_NAMES[message.text]
    service = get_service(service_id)
    chat_id = message.chat.id

    if service["type"] == "inquiry":
        # Narx individual/soatlik — to'lov ekrani ko'rsatilmaydi, ariza adminga yuboriladi
        await send_inquiry_to_admin(chat_id, service)
        await message.answer(
            "Boshqa bo'lim kerak bo'lsa, pastdagi menyudan tanlang 👇",
            reply_markup=await get_menu_keyboard_for(chat_id),
        )
        return

    plans = service.get("plans", [])

    if len(plans) == 1:
        # Yagona to'lov varianti — to'g'ridan-to'g'ri to'lov ekraniga o'tamiz
        await show_payment_screen(chat_id, title=service["title"], amount=plans[0]["amount"])
        await bot.send_message(
            chat_id,
            "Boshqa bo'lim kerak bo'lsa, pastdagi menyudan tanlang 👇",
            reply_markup=await get_menu_keyboard_for(chat_id),
        )
        return

    # Bir nechta to'lov varianti bor — pastdagi menyudan tanlov beramiz
    await message.answer(
        f"🎓 {service['title']}\n\nTo'lov turini tanlang:",
        reply_markup=build_plans_menu_keyboard(service),
    )


async def send_inquiry_to_admin(chat_id: int, service: dict):
    """Narxi individual bo'lgan xizmatlar uchun ariza qabul qilib, adminga yuboradi."""
    lead = await db.get_lead(chat_id)

    if ADMIN_CHAT_ID:
        await bot.send_message(
            ADMIN_CHAT_ID,
            f"📩 <b>Yangi ariza — {service['title']}</b>\n\n"
            f"Ism: {lead.get('name', '—')}\n"
            f"Telefon: {lead.get('phone', '—')}\n"
            f"Chat ID: {chat_id}",
            parse_mode="HTML",
        )

    form_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="📝 Ariza to'ldirish", url=GOOGLE_FORM_URL)
        ]]
    )
    await bot.send_message(
        chat_id,
        f"✅ <b>{service['title']}</b>\n\n"
        f"{service['note']}\n\n"
        f"To'liq ariza qoldirish uchun quyidagi formani to'ldiring 👇",
        parse_mode="HTML",
        reply_markup=form_keyboard,
    )


@dp.message(F.text.in_(PLAN_LABELS.keys()))
async def handle_plan_text_choice(message: Message):
    """Mijoz pastdagi menyudan to'lov rejasini (masalan 6/12 oy) tanlaganda ishlaydi."""
    service_id, plan_id = PLAN_LABELS[message.text]
    service = get_service(service_id)
    plan = get_plan(service_id, plan_id)

    if plan.get("months"):
        title = f"{service['title']} ({plan['months']} oyga bo'lib, 1-oy to'lovi)"
    else:
        title = f"{service['title']} (to'liq to'lov)"

    await show_payment_screen(message.chat.id, title=title, amount=plan["amount"])
    await bot.send_message(
        message.chat.id,
        "Boshqa bo'lim kerak bo'lsa, pastdagi menyudan tanlang 👇",
        reply_markup=await get_menu_keyboard_for(message.chat.id),
    )


async def show_payment_screen(chat_id: int, title: str, amount: int):
    """Karta raqami va Payme/Click/Uzum havolalarini ko'rsatadi."""
    # Yangi xizmat tanlanganda avvalgi (tugallanmagan) to'lov holatini tozalaymiz
    await db.clear_pending(chat_id)
    await db.set_pending(chat_id, title=title, amount=amount)

    platform_buttons = [
        [InlineKeyboardButton(text=platform["name"], url=platform["url"])]
        for platform in PLATFORMS
    ]
    keyboard = InlineKeyboardMarkup(inline_keyboard=platform_buttons)

    await bot.send_message(
        chat_id,
        f"🧾 <b>{title}</b>\n"
        f"💰 Summa: {amount:,} so'm\n\n"
        f"💳 Karta raqami: <code>{CARD_NUMBER}</code>\n"
        f"👤 Karta egasi: {CARD_HOLDER}\n\n"
        f"➡️ Quyidagi tugmalardan birini bosing, so'ng ilova ichida:\n"
        f"1️⃣ \"Kartaga o'tkazish\" yoki \"Perevod na kartu\" bo'limini toping\n"
        f"2️⃣ Yuqoridagi karta raqamini kiriting\n"
        f"3️⃣ Summani kiritib, o'tkazmani tasdiqlang\n\n"
        f"✅ To'lov qilingach, <b>chek skrinshotini shu yerga yuboring</b>.",
        reply_markup=keyboard,
        parse_mode="HTML",
    )


@dp.message(F.text == MENU_SERVICES)
async def handle_menu_services(message: Message):
    await message.answer(
        "Xizmatni tanlang:",
        reply_markup=build_services_menu_keyboard(payment_only=False),
    )


@dp.message(F.text == MENU_PAYMENTS)
async def handle_menu_payments(message: Message):
    await message.answer(
        "💳 To'lov qilishingiz mumkin bo'lgan xizmatlar:",
        reply_markup=build_services_menu_keyboard(payment_only=True),
    )


@dp.message(F.text == MENU_BACK)
async def handle_menu_back(message: Message):
    await message.answer("Asosiy menyu 👇", reply_markup=await get_menu_keyboard_for(message.chat.id))


@dp.message(F.text == MENU_CONTACT)
async def handle_menu_contact(message: Message):
    chat_id = message.chat.id
    if ADMIN_CHAT_ID:
        lead = await db.get_lead(chat_id)
        customer = message.from_user
        await bot.send_message(
            ADMIN_CHAT_ID,
            f"☎️ <b>Bog'lanish so'rovi</b>\n\n"
            f"Ism: {lead.get('name', '—')}\n"
            f"Telefon: {lead.get('phone', '—')}\n"
            f"Telegram: @{customer.username or 'username yoq'}\n"
            f"Chat ID: {chat_id}",
            parse_mode="HTML",
        )
    await message.answer(
        "✅ So'rovingiz qabul qilindi, tez orada siz bilan bog'lanamiz.\n\n"
        "📞 Telefon: +998 98 001 00 08\n"
        "✈️ Telegram: @tizim008"
    )


@dp.message(F.text == MENU_LOCATION)
async def handle_menu_location(message: Message):
    location = SERVICES.get("seminar", {}).get("location")
    if location:
        await bot.send_location(
            message.chat.id,
            latitude=location["latitude"],
            longitude=location["longitude"],
        )
        await message.answer(f"📍 Manzil: {location['address']}")
    else:
        await message.answer("Manzil ma'lumoti hozircha kiritilmagan.")


@dp.message(F.text == MENU_GUIDE)
async def handle_menu_guide(message: Message):
    """Botdan qanday foydalanish haqida qo'llanma videoni yuboradi."""
    videos = VIDEO_CACHE.get(INSTRUCTION_VIDEO_TAG)
    if videos:
        try:
            await bot.send_video(message.chat.id, videos[0], protect_content=True)
        except Exception as e:
            logging.error(f"Qo'llanma videosini yuborishda xato: {e}")
    else:
        await message.answer("Qo'llanma videosi hozircha yuklanmagan.")


@dp.message(F.text == MENU_RESTART)
async def handle_menu_restart(message: Message, state: FSMContext):
    await handle_start(message, state)


@dp.message(AdminVideoUpload.waiting_note, F.video_note)
async def handle_admin_tagged_video_note(message: Message, state: FSMContext):
    """Yuqoridagi buyruqlardan biri bosilgach kelgan dumaloq videoni saqlaydi."""
    data = await state.get_data()
    tag = data.get("video_tag", WELCOME_VIDEO_NOTE_TAG)
    await state.clear()

    file_id = message.video_note.file_id
    await db.add_video(tag, file_id)
    VIDEO_CACHE.setdefault(tag, [])
    if file_id not in VIDEO_CACHE[tag]:
        VIDEO_CACHE[tag].append(file_id)

    await message.answer(f"✅ Dumaloq video saqlandi ({tag}).")


@dp.message(AdminVideoUpload.waiting_note, F.video)
async def handle_admin_tagged_regular_video(message: Message, state: FSMContext):
    """Yuqoridagi buyruqlardan biri bosilgach kelgan ODDIY (to'rtburchak)
    videoni saqlaydi — masalan qo'llanma videosi uchun ishlatiladi."""
    data = await state.get_data()
    tag = data.get("video_tag", WELCOME_VIDEO_NOTE_TAG)
    await state.clear()

    file_id = message.video.file_id
    await db.add_video(tag, file_id)
    VIDEO_CACHE.setdefault(tag, [])
    if file_id not in VIDEO_CACHE[tag]:
        VIDEO_CACHE[tag].append(file_id)

    await message.answer(f"✅ Video saqlandi ({tag}).")


@dp.message(F.video_note)
async def handle_admin_welcome_video_note(message: Message):
    """
    Faqat admin uchun: agar maxsus buyruq bilan boshlanmagan bo'lsa, bevosita
    yuborilgan dumaloq video — xush kelibsiz (/start) videosi sifatida
    saqlanadi (avvalgi holat bilan mos, orqaga qarab moslashuvchan).
    """
    if not ADMIN_CHAT_ID or str(message.chat.id) != str(ADMIN_CHAT_ID):
        return

    file_id = message.video_note.file_id
    await db.add_video(WELCOME_VIDEO_NOTE_TAG, file_id)
    VIDEO_CACHE.setdefault(WELCOME_VIDEO_NOTE_TAG, [])
    if file_id not in VIDEO_CACHE[WELCOME_VIDEO_NOTE_TAG]:
        VIDEO_CACHE[WELCOME_VIDEO_NOTE_TAG].append(file_id)

    await message.answer(
        "✅ Dumaloq video saqlandi — endi /start bosgan har bir mijozga shu video avtomatik yuboriladi.\n\n"
        "⚠️ Agar bu video boshqa maqsad uchun edi (masalan chek qabul qilinganda yoki "
        "to'lov tasdiqlanganda ko'rsatish uchun), avval mos buyruqni yuborib, SO'NG "
        "videoni qayta yuboring:\n"
        "/setwelcome — /start dagi xush kelibsiz videosi\n"
        "/setreceiptvideo — mijoz chek yuborganda ko'rsatiladigan video\n"
        "/setpaymentapproved — to'lov tasdiqlanganda ko'rsatiladigan video\n"
        "/setpaymentrejected — to'lov rad etilganda ko'rsatiladigan video\n"
        "/setinstructionvideo — qo'llanma (foydalanish yo'riqnomasi) videosi\n"
        "/setreminderday, /setreminderhours, /setfollowupvideo — seminar eslatmalari"
    )


@dp.message(F.video)
async def handle_admin_video_upload(message: Message):
    """
    Faqat admin (siz) uchun: botga video yuborsangiz, caption'da yozgan mavzu
    tegi bo'yicha u avtomatik video kutubxonasiga (bazaga) qo'shiladi.
    Mijozlarning video xabarlariga bot bu yerda hech narsa qilmaydi.
    """
    if not ADMIN_CHAT_ID or str(message.chat.id) != str(ADMIN_CHAT_ID):
        return

    caption = (message.caption or "").strip().lower()
    tag = caption.lstrip("#").split()[0] if caption else ""

    if not tag:
        await message.answer(
            "⚠️ Video qabul qilindi, lekin mavzu tegi topilmadi.\n"
            "Videoni qayta yuboring, caption qismiga mavzuni yozing — "
            "masalan: marketing, sotuv, moliya, hr, boshqaruv, "
            "tizimlashtirish, shogirtlik, konsultatsiya, rahbarlar_kursi"
        )
        return

    file_id = message.video.file_id
    await db.add_video(tag, file_id)
    VIDEO_CACHE.setdefault(tag, [])
    if file_id not in VIDEO_CACHE[tag]:
        VIDEO_CACHE[tag].append(file_id)

    await message.answer(f"✅ Video '{tag}' mavzusiga saqlandi va darhol ishlata boshlaydi.")


@dp.message(F.photo)
async def handle_payment_screenshot(message: Message, state: FSMContext):
    """Mijoz to'lov chekini rasm qilib yuborganda ishlaydi."""
    chat_id = message.chat.id
    pending = await db.get_pending(chat_id)

    if not pending:
        await message.answer(
            "Hozircha kutilayotgan to'lov topilmadi. "
            "Iltimos, avval /start orqali xizmatni tanlang."
        )
        return

    if not ADMIN_CHAT_ID:
        await message.answer(
            "⚠️ Tizimda vaqtinchalik nosozlik. Iltimos, menejer bilan to'g'ridan-to'g'ri bog'laning."
        )
        logging.error("ADMIN_CHAT_ID sozlanmagan — screenshot hech kimga yuborilmadi.")
        return

    # Chek faylini va Telegram username'ni bazaga saqlaymiz —
    # to'liq ism-familiya olingach, hammasi birga adminga yuboriladi
    await db.set_pending(
        chat_id,
        screenshot_file_id=message.photo[-1].file_id,
        username=message.from_user.username,
    )

    lead = await db.get_lead(chat_id)
    if lead.get("full_name"):
        # To'liq ism-familiya avval berilgan — qayta so'ramaymiz, darhol yuboramiz
        await forward_payment_to_admin(chat_id)
        return

    await state.set_state(PaymentForm.full_name)
    await message.answer(
        "📨 Chek qabul qilindi!\n\n"
        "Hisobot yuritish uchun to'liq ism-familiyangizni kiriting "
        "(masalan: Aliyev Vali Aliyevich):"
    )


@dp.message(PaymentForm.full_name, F.text, ~F.text.startswith("/"))
async def handle_payment_full_name(message: Message, state: FSMContext):
    """To'lov skrinshotidan keyin to'liq ism-familiyani qabul qiladi."""
    chat_id = message.chat.id
    full_name = message.text.strip()

    if not full_name:
        await message.answer("Iltimos, to'liq ism-familiyangizni matn ko'rinishida yuboring.")
        return

    await db.upsert_lead(chat_id, full_name=full_name)
    await state.clear()
    await forward_payment_to_admin(chat_id)


async def forward_payment_to_admin(chat_id: int):
    """To'lov chekini (skrinshot + to'liq ma'lumot bilan) adminga yuboradi."""
    pending = await db.get_pending(chat_id)
    if not pending or not pending.get("screenshot_file_id"):
        return

    lead = await db.get_lead(chat_id)
    caption = (
        f"🆕 <b>Yangi to'lov tasdiqlash so'rovi</b>\n\n"
        f"Xizmat: {pending['title']}\n"
        f"Summa: {pending['amount']:,} so'm\n"
        f"F.I.Sh (hisobot uchun): {lead.get('full_name', '—')}\n"
        f"Ism: {lead.get('name', '—')}\n"
        f"Telefon: {lead.get('phone', '—')}\n"
        f"Telegram: @{pending.get('username') or 'username yoq'}\n"
        f"Chat ID: {chat_id}"
    )

    confirm_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Tasdiqlash", callback_data=f"approve_{chat_id}"),
                InlineKeyboardButton(text="❌ Rad etish", callback_data=f"reject_{chat_id}"),
            ]
        ]
    )

    await bot.send_photo(
        chat_id=ADMIN_CHAT_ID,
        photo=pending["screenshot_file_id"],
        caption=caption,
        parse_mode="HTML",
        reply_markup=confirm_keyboard,
    )

    await _send_seminar_video(chat_id, RECEIPT_RECEIVED_VIDEO_TAG)
    await bot.send_message(
        chat_id,
        "📨 Ma'lumotlaringiz qabul qilindi, tekshirilmoqda. Tez orada tasdiqlanadi. Rahmat! 🙏",
    )


@dp.callback_query(F.data.startswith("approve_") | F.data.startswith("reject_"))
async def handle_admin_decision(callback: CallbackQuery):
    """Admin (siz) 'Tasdiqlash' yoki 'Rad etish' tugmasini bosganda ishlaydi."""
    action, customer_chat_id = callback.data.split("_", 1)
    customer_chat_id = int(customer_chat_id)
    pending = await db.get_pending(customer_chat_id)

    if action == "approve":
        await _send_seminar_video(customer_chat_id, PAYMENT_APPROVED_VIDEO_TAG)
        await bot.send_message(
            customer_chat_id,
            "✅ To'lovingiz tasdiqlandi!\n\nRahmat! Sizni kutib qolamiz 🙏",
        )
        await callback.answer("Tasdiqlandi ✅")
        await callback.message.edit_caption(
            caption=callback.message.caption + "\n\n✅ TASDIQLANDI",
            reply_markup=None,
        )

        if pending:
            payment_id = await db.record_payment_decision(
                customer_chat_id,
                pending.get("title"),
                pending.get("amount"),
                "approved",
            )

            # To'lov tasdiqlangach — mijozga tadbirga kirish uchun QR-kod
            # yuboramiz. Tadbirda admin shu QR-kodni skanerlab, kelganini
            # belgilaydi (kamera orqali ochilgan deep-link /start orqali).
            if payment_id and BOT_USERNAME:
                token = uuid.uuid4().hex[:12]
                await db.set_payment_qr_token(payment_id, token)
                qr_link = f"https://t.me/{BOT_USERNAME}?start=checkin_{token}"
                qr_bytes = generate_qr_png(qr_link)
                try:
                    await bot.send_photo(
                        customer_chat_id,
                        BufferedInputFile(qr_bytes, filename="qr.png"),
                        caption=(
                            "🎫 Bu QR-kodni tadbir/seminar kuni o'zingiz bilan olib "
                            "keling — kirishda ko'rsatasiz."
                        ),
                    )
                except Exception as e:
                    logging.error(f"QR-kod yuborishda xato: {e}")

        # Agar bu seminar uchun to'lov bo'lsa — eslatmalar va follow-up
        # xabarlarini avtomatik rejalashtiramiz
        if pending and pending.get("title") == SERVICES["seminar"]["title"]:
            await _schedule_seminar_messages(customer_chat_id)
    else:
        await _send_seminar_video(customer_chat_id, PAYMENT_REJECTED_VIDEO_TAG)
        await bot.send_message(
            customer_chat_id,
            "❌ To'lovingiz tasdiqlanmadi.\n\n"
            "Iltimos, chekni qaytadan tekshirib yuboring, yoki savolingiz bo'lsa "
            "shu yerga yozib qoldiring — sizga yordam beraman.",
        )
        await callback.answer("Rad etildi ❌")
        await callback.message.edit_caption(
            caption=callback.message.caption + "\n\n❌ RAD ETILDI",
            reply_markup=None,
        )

        if pending:
            await db.record_payment_decision(
                customer_chat_id,
                pending.get("title"),
                pending.get("amount"),
                "rejected",
            )

    await db.clear_pending(customer_chat_id)


def _next_seminar_start(from_dt: datetime) -> datetime:
    """Eng yaqin seminar boshlanish vaqtini (shanba, soat 14:00) qaytaradi."""
    weekday = from_dt.weekday()  # Dushanba=0 ... Shanba=5, Yakshanba=6
    days_until_saturday = (5 - weekday) % 7
    seminar_day = from_dt + timedelta(days=days_until_saturday)

    if days_until_saturday == 0 and from_dt.hour >= 18:
        # Bugun shanba, lekin seminar allaqachon tugagan — keyingi haftaga o'tamiz
        seminar_day += timedelta(days=7)

    return seminar_day.replace(hour=14, minute=0, second=0, microsecond=0)


async def _schedule_seminar_messages(chat_id: int) -> None:
    """
    To'lov tasdiqlangach, seminar bilan bog'liq 3 ta xabarni rejalashtiradi:
    - reminder_day: seminardan 1 kun oldin (juma, soat 18:00)
    - reminder_hours: seminar kuni, boshlanishidan bir necha soat oldin (soat 10:00)
    - followup: seminardan keyingi kuni (yakshanba, soat 10:00)
    O'tib ketgan vaqtlar (masalan mijoz seminar kuni tushdan keyin to'lasa)
    rejalashtirilmaydi — faqat hali kelmagan vaqtlar qo'yiladi.
    """
    now = datetime.now(TASHKENT_TZ)
    seminar_start = _next_seminar_start(now)

    reminder_day_at = (seminar_start - timedelta(days=1)).replace(
        hour=18, minute=0, second=0, microsecond=0
    )
    reminder_hours_at = seminar_start.replace(hour=10, minute=0, second=0, microsecond=0)
    followup_at = (seminar_start + timedelta(days=1)).replace(
        hour=10, minute=0, second=0, microsecond=0
    )

    schedule_plan = [
        ("reminder_day", reminder_day_at),
        ("reminder_hours", reminder_hours_at),
        ("followup", followup_at),
    ]

    for kind, send_at in schedule_plan:
        if send_at <= now:
            continue  # bu vaqt allaqachon o'tib ketgan — o'tkazib yuboramiz
        await db.schedule_seminar_message(chat_id, kind, send_at)
        logging.info(f"Seminar xabari rejalashtirildi: chat_id={chat_id}, kind={kind}, vaqt={send_at}")


async def _send_seminar_video(chat_id: int, tag: str) -> None:
    """Bor bo'lsa, mos videoni yuboradi (yo'q bo'lsa jim o'tkazib yuboradi).
    INSTRUCTION_VIDEO_TAG oddiy (to'rtburchak) video sifatida, qolgan barcha
    turkumlar an'anaviy dumaloq (video note) sifatida yuboriladi."""
    videos = VIDEO_CACHE.get(tag)
    if videos:
        try:
            if tag == INSTRUCTION_VIDEO_TAG:
                await bot.send_video(chat_id, videos[0], protect_content=True)
            else:
                await bot.send_video_note(chat_id, videos[0], protect_content=True)
        except Exception as e:
            logging.error(f"Video yuborishda xato ({tag}): {e}")


async def send_seminar_reminder_day(chat_id: int) -> None:
    """Seminardan 1 kun oldin yuboriladigan eslatma."""
    await _send_seminar_video(chat_id, SEMINAR_REMINDER_DAY_TAG)
    location = SERVICES.get("seminar", {}).get("location", {})
    address = location.get("address", "")
    text = (
        "Assalomu alaykum! 👋\n\n"
        "Ertaga (shanba) soat <b>14:00</b> da seminarimiz bo'lib o'tadi — sizni "
        "kutib qolamiz! 🎓\n\n" + (f"📍 Manzil: {address}\n\n" if address else "") +
        "Ko'rishguncha!"
    )
    await bot.send_message(chat_id, text, parse_mode="HTML")


async def send_seminar_reminder_hours(chat_id: int) -> None:
    """Seminar kuni, boshlanishidan bir necha soat oldin yuboriladigan eslatma."""
    await _send_seminar_video(chat_id, SEMINAR_REMINDER_HOURS_TAG)
    location = SERVICES.get("seminar", {}).get("location", {})
    address = location.get("address", "")
    text = (
        "Assalomu alaykum! 👋\n\n"
        "Bugun soat <b>14:00</b> da seminarimiz boshlanadi — bir necha soatdan "
        "so'ng ko'rishamiz! 🎓\n\n" + (f"📍 Manzil: {address}\n\n" if address else "") +
        "Vaqtida kelishni unutmang!"
    )
    await bot.send_message(chat_id, text, parse_mode="HTML")


SEMINAR_FEEDBACK_OPTIONS = [
    ("seminar_fb_great", "😍 Juda yoqdi"),
    ("seminar_fb_good", "🙂 Yaxshi o'tdi"),
    ("seminar_fb_ok", "😐 O'rtacha"),
    ("seminar_fb_bad", "😕 Unchalik emas"),
]


async def send_seminar_followup(chat_id: int) -> None:
    """Seminardan keyingi kuni: taassurot so'raydigan dumaloq video + taklif ko'rinishidagi savol."""
    await _send_seminar_video(chat_id, SEMINAR_FOLLOWUP_VIDEO_TAG)

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=cb)]
            for cb, label in SEMINAR_FEEDBACK_OPTIONS
        ]
    )
    await bot.send_message(
        chat_id,
        "Kecha (yoki bugun) o'tgan seminarimiz sizga qanday taassurot qoldirdi? 😊",
        reply_markup=keyboard,
    )


@dp.callback_query(F.data.startswith("seminar_fb_"))
async def handle_seminar_feedback(callback: CallbackQuery):
    """Mijoz taassurot tugmalaridan birini bosganda — rahmat aytib, kursga taklif yuboradi."""
    chat_id = callback.message.chat.id
    label = next((lbl for cb, lbl in SEMINAR_FEEDBACK_OPTIONS if cb == callback.data), "")
    await callback.answer("Rahmat!")
    await callback.message.edit_reply_markup(reply_markup=None)

    if ADMIN_CHAT_ID:
        lead = await db.get_lead(chat_id)
        await bot.send_message(
            ADMIN_CHAT_ID,
            f"📝 <b>Seminar taassuroti</b>\n\n"
            f"Ism: {lead.get('name', '—')}\n"
            f"Telefon: {lead.get('phone', '—')}\n"
            f"Fikri: {label}\n"
            f"Chat ID: {chat_id}",
            parse_mode="HTML",
        )

    pitch = (
        "Fikringiz uchun rahmat! 🙏\n\n"
        "Bilim va tajribangizni yanada chuqurroq oshirib, buni real amaliyotda "
        "qo'llashni istasangiz, <b>Biznes Shogirtlik dasturi</b>miz aynan shunga "
        "mo'ljallangan: 2 yillik dastur (6 oy nazariy tayyorgarlik + 1.5 yil real "
        "loyihalarda amaliyot).\n\n"
        "Qiziqsangiz, batafsil ma'lumot beraman — savolingizni shu yerga yozing! ✍️"
    )
    await bot.send_message(chat_id, pitch, parse_mode="HTML")

    history = await db.get_ai_history(chat_id)
    history.append({"role": "assistant", "content": pitch})
    await db.set_ai_history(chat_id, history)


SEMINAR_MESSAGE_SENDERS = {
    "reminder_day": send_seminar_reminder_day,
    "reminder_hours": send_seminar_reminder_hours,
    "followup": send_seminar_followup,
}


async def seminar_message_worker() -> None:
    """Fonda ishlab, vaqti kelgan seminar eslatma/follow-up xabarlarini yuboradi."""
    while True:
        try:
            now = datetime.now(TASHKENT_TZ)
            due = await db.get_due_seminar_messages(now)
            for chat_id, kind in due:
                sender = SEMINAR_MESSAGE_SENDERS.get(kind)
                if not sender:
                    continue
                try:
                    await sender(chat_id)
                    await db.mark_seminar_message_sent(chat_id, kind)
                except Exception as e:
                    logging.error(f"Seminar xabari yuborishda xato (chat_id={chat_id}, kind={kind}): {e}")
        except Exception as e:
            logging.error(f"Seminar message worker xatosi: {e}")
        await asyncio.sleep(1800)  # har 30 daqiqada tekshiradi


@dp.message(F.text)
async def handle_ai_fallback(message: Message):
    """Boshqa hech qaysi handler mos kelmagan matnli xabarlar uchun — AI javob beradi."""
    answer = await ask_ai(message.chat.id, message.text)
    await message.answer(answer)

    video_file_id = find_video_for_text(message.text, VIDEO_CACHE)
    if video_file_id:
        chat_id = message.chat.id
        if not await db.was_video_sent(chat_id, video_file_id):
            try:
                await bot.send_video(chat_id, video_file_id, protect_content=True)
                await db.mark_video_sent(chat_id, video_file_id)
            except Exception as e:
                logging.error(f"Video yuborishda xato: {e}")


async def main():
    await db.init_pool()
    global VIDEO_CACHE, BOT_USERNAME
    VIDEO_CACHE = await db.get_video_library()
    me = await bot.get_me()
    BOT_USERNAME = me.username
    asyncio.create_task(seminar_message_worker())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
