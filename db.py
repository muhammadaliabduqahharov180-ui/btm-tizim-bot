"""
PostgreSQL orqali doimiy saqlash qatlami.

Bot qayta ishga tushsa (deploy, restart, crash) ham hech qanday ma'lumot
yo'qolmasligi uchun barcha "holat" shu yerda, bazada saqlanadi:
- leads: mijozlarning ism/telefon/to'liq ism-familiyasi
- pending_payments: mijoz hozir to'lamoqchi bo'lgan xizmat (vaqtinchalik)
- ai_history: har bir mijoz bilan AI suhbat tarixi
- videos: mavzu bo'yicha video kutubxonasi (+ xush kelibsiz dumaloq video)
- sent_videos: bitta mijozga bitta video faqat bir marta yuborilishi uchun
- seminar_followups: seminardan keyin yuboriladigan taklif xabarlari rejasi

Ishlatishdan oldin, dastur boshida bitta marta `await init_pool()` chaqiriladi.
"""

import json
import logging
import os
from datetime import datetime

import asyncpg

DATABASE_URL = os.getenv("DATABASE_URL")

_pool: asyncpg.Pool | None = None


async def init_pool() -> None:
    """Ulanishlar pulini yaratadi va barcha jadvallarni (agar yo'q bo'lsa) tuzadi."""
    global _pool
    if _pool is not None:
        return
    if not DATABASE_URL:
        logging.error("DATABASE_URL topilmadi — baza ulanmagan holda ishga tushmoqda!")
        return
    _pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
    await _create_tables()


async def _create_tables() -> None:
    async with _pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS leads (
                chat_id BIGINT PRIMARY KEY,
                name TEXT,
                phone TEXT,
                full_name TEXT,
                username TEXT,
                source_service TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            """
        )
        # Bot ilgari deploy qilingan bo'lsa, jadval allaqachon mavjud bo'lishi
        # mumkin — shu ustunlarni bor-yo'qligini tekshirib qo'shamiz.
        await conn.execute(
            "ALTER TABLE leads ADD COLUMN IF NOT EXISTS source_service TEXT;"
        )
        await conn.execute(
            "ALTER TABLE leads ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now();"
        )
        await conn.execute(
            "ALTER TABLE leads ADD COLUMN IF NOT EXISTS region TEXT;"
        )
        await conn.execute(
            "ALTER TABLE leads ADD COLUMN IF NOT EXISTS mode TEXT;"
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pending_payments (
                chat_id BIGINT PRIMARY KEY,
                title TEXT,
                amount BIGINT,
                screenshot_file_id TEXT,
                username TEXT,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            """
        )
        await conn.execute(
            "ALTER TABLE pending_payments ADD COLUMN IF NOT EXISTS plan_months INT;"
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS installment_plans (
                id SERIAL PRIMARY KEY,
                chat_id BIGINT NOT NULL,
                service_title TEXT,
                amount_per_month BIGINT,
                total_months INT,
                paid_months INT NOT NULL DEFAULT 1,
                next_due_date DATE,
                active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ai_history (
                chat_id BIGINT PRIMARY KEY,
                history JSONB NOT NULL DEFAULT '[]'::jsonb,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS videos (
                tag TEXT NOT NULL,
                file_id TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (tag, file_id)
            );
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sent_videos (
                chat_id BIGINT NOT NULL,
                file_id TEXT NOT NULL,
                PRIMARY KEY (chat_id, file_id)
            );
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS seminar_followups (
                chat_id BIGINT PRIMARY KEY,
                send_at TIMESTAMPTZ NOT NULL,
                sent BOOLEAN NOT NULL DEFAULT FALSE
            );
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS payments (
                id SERIAL PRIMARY KEY,
                chat_id BIGINT NOT NULL,
                title TEXT,
                amount BIGINT,
                status TEXT NOT NULL,
                decided_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            """
        )
        await conn.execute(
            "ALTER TABLE payments ADD COLUMN IF NOT EXISTS qr_token TEXT;"
        )
        await conn.execute(
            "ALTER TABLE payments ADD COLUMN IF NOT EXISTS checked_in BOOLEAN NOT NULL DEFAULT FALSE;"
        )
        await conn.execute(
            "ALTER TABLE payments ADD COLUMN IF NOT EXISTS checked_in_at TIMESTAMPTZ;"
        )
        await conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS payments_qr_token_idx ON payments (qr_token) WHERE qr_token IS NOT NULL;"
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scheduled_seminar_messages (
                chat_id BIGINT NOT NULL,
                kind TEXT NOT NULL,
                send_at TIMESTAMPTZ NOT NULL,
                sent BOOLEAN NOT NULL DEFAULT FALSE,
                PRIMARY KEY (chat_id, kind)
            );
            """
        )


def _connected() -> bool:
    if _pool is None:
        logging.error("DB pool mavjud emas — DATABASE_URL sozlanmagan bo'lishi mumkin.")
        return False
    return True


# ---------------------------------------------------------------- LEADS ----

async def get_lead(chat_id: int) -> dict:
    if not _connected():
        return {}
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT name, phone, full_name, username, source_service, region, mode "
            "FROM leads WHERE chat_id = $1",
            chat_id,
        )
        if not row:
            return {}
        return {k: v for k, v in dict(row).items() if v is not None}


async def upsert_lead(chat_id: int, **fields) -> None:
    """fields ichida berilgan maydonlarnigina yangilaydi (boshqalarini saqlab qoladi)."""
    if not fields or not _connected():
        return
    async with _pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO leads (chat_id) VALUES ($1) ON CONFLICT (chat_id) DO NOTHING",
            chat_id,
        )
        columns = list(fields.keys())
        set_clause = ", ".join(f"{col} = ${i + 2}" for i, col in enumerate(columns))
        values = [fields[col] for col in columns]
        await conn.execute(
            f"UPDATE leads SET {set_clause}, updated_at = now() WHERE chat_id = $1",
            chat_id,
            *values,
        )


# ------------------------------------------------------ PENDING PAYMENTS ----

async def set_pending(chat_id: int, **fields) -> None:
    if not _connected():
        return
    async with _pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO pending_payments (chat_id, title, amount, screenshot_file_id, username, plan_months)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (chat_id) DO UPDATE SET
                title = COALESCE($2, pending_payments.title),
                amount = COALESCE($3, pending_payments.amount),
                screenshot_file_id = COALESCE($4, pending_payments.screenshot_file_id),
                username = COALESCE($5, pending_payments.username),
                plan_months = COALESCE($6, pending_payments.plan_months),
                updated_at = now()
            """,
            chat_id,
            fields.get("title"),
            fields.get("amount"),
            fields.get("screenshot_file_id"),
            fields.get("username"),
            fields.get("plan_months"),
        )


async def get_pending(chat_id: int) -> dict | None:
    if not _connected():
        return None
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT title, amount, screenshot_file_id, username, plan_months "
            "FROM pending_payments WHERE chat_id = $1",
            chat_id,
        )
        return dict(row) if row else None


async def clear_pending(chat_id: int) -> None:
    if not _connected():
        return
    async with _pool.acquire() as conn:
        await conn.execute("DELETE FROM pending_payments WHERE chat_id = $1", chat_id)


# ------------------------------------------------------- INSTALLMENT PLANS --

async def create_installment_plan(
    chat_id: int, service_title: str, amount_per_month: int, total_months: int
) -> None:
    """To'lov tasdiqlangach (bo'lib to'lash rejasi tanlangan bo'lsa) chaqiriladi.
    1-oy to'lovi hisobga olinadi, keyingi eslatma 1 oydan keyin rejalashtiriladi."""
    if not _connected() or total_months <= 1:
        return
    async with _pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO installment_plans
                (chat_id, service_title, amount_per_month, total_months, paid_months, next_due_date)
            VALUES ($1, $2, $3, $4, 1, (CURRENT_DATE + INTERVAL '1 month')::date)
            """,
            chat_id,
            service_title,
            amount_per_month,
            total_months,
        )


async def get_due_installments() -> list[dict]:
    """Bugun (yoki undan oldin) to'lov muddati kelgan, hali faol bo'lgan rejalarni qaytaradi."""
    if not _connected():
        return []
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, chat_id, service_title, amount_per_month, total_months, paid_months
            FROM installment_plans
            WHERE active = TRUE AND next_due_date <= CURRENT_DATE
            """
        )
        return [dict(r) for r in rows]


async def advance_installment(plan_id: int) -> None:
    """Eslatma yuborilgach chaqiriladi — keyingi oy uchun muddatni belgilaydi,
    oxirgi oy bo'lsa rejani yakunlaydi (active=False)."""
    if not _connected():
        return
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT paid_months, total_months FROM installment_plans WHERE id = $1",
            plan_id,
        )
        if not row:
            return
        new_paid = row["paid_months"] + 1
        if new_paid >= row["total_months"]:
            await conn.execute(
                "UPDATE installment_plans SET paid_months = $2, active = FALSE WHERE id = $1",
                plan_id,
                new_paid,
            )
        else:
            await conn.execute(
                """
                UPDATE installment_plans
                SET paid_months = $2, next_due_date = (CURRENT_DATE + INTERVAL '1 month')::date
                WHERE id = $1
                """,
                plan_id,
                new_paid,
            )


# ------------------------------------------------------------ AI HISTORY ----

async def get_ai_history(chat_id: int) -> list:
    if not _connected():
        return []
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT history FROM ai_history WHERE chat_id = $1", chat_id
        )
        if not row:
            return []
        history = row["history"]
        return json.loads(history) if isinstance(history, str) else (history or [])


async def set_ai_history(chat_id: int, history: list) -> None:
    if not _connected():
        return
    trimmed = history[-20:]
    async with _pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO ai_history (chat_id, history, updated_at)
            VALUES ($1, $2::jsonb, now())
            ON CONFLICT (chat_id) DO UPDATE SET history = $2::jsonb, updated_at = now()
            """,
            chat_id,
            json.dumps(trimmed),
        )


# ----------------------------------------------------------------- VIDEOS --

async def get_video_library() -> dict:
    """{"tag": ["file_id", ...], ...} ko'rinishida butun video kutubxonasini qaytaradi."""
    if not _connected():
        return {}
    async with _pool.acquire() as conn:
        rows = await conn.fetch("SELECT tag, file_id FROM videos ORDER BY created_at")
        library: dict = {}
        for row in rows:
            library.setdefault(row["tag"], []).append(row["file_id"])
        return library


async def add_video(tag: str, file_id: str) -> None:
    if not _connected():
        return
    async with _pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO videos (tag, file_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
            tag,
            file_id,
        )


async def was_video_sent(chat_id: int, file_id: str) -> bool:
    if not _connected():
        return False
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT 1 FROM sent_videos WHERE chat_id = $1 AND file_id = $2",
            chat_id,
            file_id,
        )
        return row is not None


async def mark_video_sent(chat_id: int, file_id: str) -> None:
    if not _connected():
        return
    async with _pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO sent_videos (chat_id, file_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
            chat_id,
            file_id,
        )


# ------------------------------------------------ SCHEDULED SEMINAR MESSAGES --

async def schedule_seminar_message(chat_id: int, kind: str, send_at: datetime) -> None:
    """Seminar bilan bog'liq xabarni (eslatma yoki follow-up) rejalashtiradi.
    kind: 'reminder_day' | 'reminder_hours' | 'followup'"""
    if not _connected():
        return
    async with _pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO scheduled_seminar_messages (chat_id, kind, send_at, sent)
            VALUES ($1, $2, $3, FALSE)
            ON CONFLICT (chat_id, kind) DO UPDATE SET send_at = $3, sent = FALSE
            """,
            chat_id,
            kind,
            send_at,
        )


async def get_due_seminar_messages(now: datetime) -> list:
    """Vaqti kelgan, hali yuborilmagan xabarlar ro'yxati: [(chat_id, kind), ...]."""
    if not _connected():
        return []
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT chat_id, kind FROM scheduled_seminar_messages "
            "WHERE sent = FALSE AND send_at <= $1",
            now,
        )
        return [(row["chat_id"], row["kind"]) for row in rows]


async def mark_seminar_message_sent(chat_id: int, kind: str) -> None:
    if not _connected():
        return
    async with _pool.acquire() as conn:
        await conn.execute(
            "UPDATE scheduled_seminar_messages SET sent = TRUE WHERE chat_id = $1 AND kind = $2",
            chat_id,
            kind,
        )


# ------------------------------------------------------------- PAYMENTS ----

async def record_payment_decision(chat_id: int, title: str, amount: int, status: str) -> int | None:
    """Har bir 'Tasdiqlash'/'Rad etish' qarorini tarixga yozib boradi (statistika uchun).
    Yangi yozuv id'sini qaytaradi (QR-kod tokenini ulash uchun kerak bo'ladi)."""
    if not _connected():
        return None
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO payments (chat_id, title, amount, status) VALUES ($1, $2, $3, $4) RETURNING id",
            chat_id,
            title,
            amount,
            status,
        )
        return row["id"] if row else None


# ---------------------------------------------------------- QR / CHECK-IN --

async def set_payment_qr_token(payment_id: int, token: str) -> None:
    """Tasdiqlangan to'lovga QR-kod tokenini bog'laydi."""
    if not _connected():
        return
    async with _pool.acquire() as conn:
        await conn.execute(
            "UPDATE payments SET qr_token = $1 WHERE id = $2", token, payment_id
        )


async def checkin_by_token(token: str) -> dict | None:
    """
    QR-kod skanerlanganda chaqiriladi. Token to'g'ri bo'lsa mijozni "kelgan" deb
    belgilaydi va uning ma'lumotlarini qaytaradi. Token topilmasa None, avval
    ishlatilgan bo'lsa already=True bilan qaytaradi.
    """
    if not _connected():
        return None
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, chat_id, title, checked_in, checked_in_at "
            "FROM payments WHERE qr_token = $1",
            token,
        )
        if not row:
            return None

        lead = await get_lead(row["chat_id"])
        display_name = lead.get("full_name") or lead.get("name") or "Noma'lum"

        if row["checked_in"]:
            return {
                "already": True,
                "name": display_name,
                "checked_in_at": row["checked_in_at"],
            }

        await conn.execute(
            "UPDATE payments SET checked_in = TRUE, checked_in_at = now() WHERE id = $1",
            row["id"],
        )
        return {
            "already": False,
            "name": display_name,
            "phone": lead.get("phone", "—"),
            "title": row["title"],
            "chat_id": row["chat_id"],
        }


# ------------------------------------------------------------- STATS/ADMIN --

async def get_stats() -> dict:
    """Umumiy va davriy statistika: leadlar soni va tasdiqlangan to'lovlar."""
    if not _connected():
        return {}
    async with _pool.acquire() as conn:
        leads_total = await conn.fetchval("SELECT COUNT(*) FROM leads")
        leads_today = await conn.fetchval(
            "SELECT COUNT(*) FROM leads WHERE created_at >= date_trunc('day', now())"
        )
        leads_month = await conn.fetchval(
            "SELECT COUNT(*) FROM leads WHERE created_at >= date_trunc('month', now())"
        )

        approved = await conn.fetchrow(
            "SELECT COUNT(*) AS cnt, COALESCE(SUM(amount), 0) AS total "
            "FROM payments WHERE status = 'approved'"
        )
        approved_today = await conn.fetchrow(
            "SELECT COUNT(*) AS cnt, COALESCE(SUM(amount), 0) AS total FROM payments "
            "WHERE status = 'approved' AND decided_at >= date_trunc('day', now())"
        )
        approved_month = await conn.fetchrow(
            "SELECT COUNT(*) AS cnt, COALESCE(SUM(amount), 0) AS total FROM payments "
            "WHERE status = 'approved' AND decided_at >= date_trunc('month', now())"
        )
        rejected_total = await conn.fetchval(
            "SELECT COUNT(*) FROM payments WHERE status = 'rejected'"
        )

        return {
            "leads_total": leads_total,
            "leads_today": leads_today,
            "leads_month": leads_month,
            "payments_total_count": approved["cnt"],
            "payments_total_sum": approved["total"],
            "payments_today_count": approved_today["cnt"],
            "payments_today_sum": approved_today["total"],
            "payments_month_count": approved_month["cnt"],
            "payments_month_sum": approved_month["total"],
            "payments_rejected_count": rejected_total,
        }


async def list_leads(limit: int = 10, offset: int = 0, search: str | None = None) -> list:
    """Eng so'nggi leadlar ro'yxati; `search` berilsa ism/telefon/F.I.Sh bo'yicha qidiradi."""
    if not _connected():
        return []
    async with _pool.acquire() as conn:
        if search:
            pattern = f"%{search}%"
            rows = await conn.fetch(
                """
                SELECT chat_id, name, phone, full_name, source_service, region, mode, created_at
                FROM leads
                WHERE name ILIKE $1 OR phone ILIKE $1 OR full_name ILIKE $1
                ORDER BY created_at DESC
                LIMIT $2 OFFSET $3
                """,
                pattern,
                limit,
                offset,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT chat_id, name, phone, full_name, source_service, region, mode, created_at
                FROM leads
                ORDER BY created_at DESC
                LIMIT $1 OFFSET $2
                """,
                limit,
                offset,
            )
        return [dict(row) for row in rows]


async def count_all_leads(search: str | None = None) -> int:
    if not _connected():
        return 0
    async with _pool.acquire() as conn:
        if search:
            pattern = f"%{search}%"
            return await conn.fetchval(
                "SELECT COUNT(*) FROM leads WHERE name ILIKE $1 OR phone ILIKE $1 OR full_name ILIKE $1",
                pattern,
            )
        return await conn.fetchval("SELECT COUNT(*) FROM leads")
