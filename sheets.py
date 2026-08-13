"""
Google Sheets bilan integratsiya — leadlar va tadbirga kelganlar ro'yxatini
avtomatik shakllantirish uchun.

Kerakli environment o'zgaruvchilar:
- GOOGLE_SERVICE_ACCOUNT_JSON — Google Cloud service account kalitining
  to'liq JSON matni (bir qatorda).
- GOOGLE_SHEET_ID — maqsadli Google Sheet ID (URL'dagi
  .../spreadsheets/d/<SHU_YER>/edit dagi qism).

Google Sheet'ni service account email'iga (JSON ichidagi "client_email")
Editor huquqi bilan ulashishni unutmang — aks holda yozib bo'lmaydi.

Bu modul "best effort" tarzida ishlaydi: agar sozlanmagan yoki xato yuz
bersa, botning asosiy funksiyasiga ta'sir qilmasligi uchun faqat log
yozadi va jim davom etadi.
"""

import json
import logging
import os

_client = None
_spreadsheet = None
_initialized_but_unavailable = False


def _get_client():
    global _client, _initialized_but_unavailable
    if _client is not None:
        return _client
    if _initialized_but_unavailable:
        return None

    raw = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not raw:
        _initialized_but_unavailable = True
        logging.warning("GOOGLE_SERVICE_ACCOUNT_JSON sozlanmagan — Sheets integratsiyasi o'chirilgan.")
        return None

    try:
        import gspread
        from google.oauth2.service_account import Credentials

        info = json.loads(raw)
        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        creds = Credentials.from_service_account_info(info, scopes=scopes)
        _client = gspread.authorize(creds)
        return _client
    except Exception as e:
        _initialized_but_unavailable = True
        logging.error(f"Google Sheets client yaratishda xato: {e}")
        return None


def _get_spreadsheet():
    global _spreadsheet
    if _spreadsheet is not None:
        return _spreadsheet

    client = _get_client()
    sheet_id = os.getenv("GOOGLE_SHEET_ID")
    if not client or not sheet_id:
        return None

    try:
        _spreadsheet = client.open_by_key(sheet_id)
        return _spreadsheet
    except Exception as e:
        logging.error(f"Google Sheet (ID={sheet_id}) ochishda xato: {e}")
        return None


def _get_or_create_worksheet(title: str, headers: list[str]):
    sh = _get_spreadsheet()
    if not sh:
        return None
    try:
        import gspread
        try:
            ws = sh.worksheet(title)
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet(title=title, rows=2000, cols=max(len(headers), 6))
            ws.append_row(headers)
        return ws
    except Exception as e:
        logging.error(f"Worksheet ('{title}') olishda xato: {e}")
        return None


def append_lead_row(row: list) -> None:
    """Leadlar ro'yxatiga bitta qator qo'shadi: [Sana, Ism, Telefon, Hudud, Format, Telegram, Chat ID]"""
    ws = _get_or_create_worksheet(
        "Leadlar", ["Sana", "Ism", "Telefon", "Hudud", "Format", "Telegram", "Chat ID"]
    )
    if not ws:
        return
    try:
        ws.append_row([str(x) if x is not None else "" for x in row])
    except Exception as e:
        logging.error(f"Lead qatorini Sheets'ga yozishda xato: {e}")


def append_checkin_row(row: list) -> None:
    """Tadbirga kelganlar ro'yxatiga bitta qator qo'shadi: [Vaqt, Ism, Telefon, Xizmat, Chat ID]"""
    ws = _get_or_create_worksheet(
        "Kelganlar", ["Sana/Vaqt", "Ism", "Telefon", "Xizmat", "Chat ID"]
    )
    if not ws:
        return
    try:
        ws.append_row([str(x) if x is not None else "" for x in row])
    except Exception as e:
        logging.error(f"Check-in qatorini Sheets'ga yozishda xato: {e}")
