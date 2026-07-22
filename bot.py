# ============================
# KODLI KINO BOT (bot.py)
# Til: O'zbekcha
# Texnologiya: Python + aiogram 3.x + SQLite
# ============================

import asyncio
import logging
import os
import sqlite3
import json
import random
import difflib
import hmac
import hashlib
import base64
import secrets
from urllib.parse import parse_qsl
from datetime import datetime, timedelta
from typing import List, Tuple

from aiohttp import web, ClientSession, ClientTimeout

from aiogram import Bot, Dispatcher, types, F, BaseMiddleware
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.enums import ChatMemberStatus
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup,
    InlineKeyboardButton, CallbackQuery, Message, WebAppInfo, BufferedInputFile
)
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

# ============================
# 1. KONFIGURATSIYA
# ============================
# Render'da bu qiymatlarni Dashboard -> Environment bo'limidan ham boshqarish mumkin
# (agar shu yerda environment variable topilmasa, pastdagi standart qiymat ishlatiladi).
BOT_TOKEN = os.getenv("BOT_TOKEN", "8492424383:AAFoAmLdvquiP0JwFUYE2grgyF2d2zQREUA")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "8283067497,5153285706").split(",") if x.strip()]
PORT = int(os.getenv("PORT", 10000))  # Render avtomatik PORT beradi

# --- Kamera orqali ro'yxatdan o'tish uchun Mini App manzili ---
# Render avtomatik beradigan RENDER_EXTERNAL_URL ishlatiladi (qo'shimcha sozlash shart emas)
WEBAPP_URL = os.getenv("WEBAPP_URL") or os.getenv("RENDER_EXTERNAL_URL", "")

# --- Oylik obuna narxlari va karta ma'lumotlari ---
# Bularni Render -> Environment bo'limida ham sozlashingiz mumkin (yoki shu yerda o'zgartiring).
CARD_NUMBER = os.getenv("CARD_NUMBER", "8600 0000 0000 0000")   # <-- to'lov qabul qilinadigan karta
CARD_HOLDER = os.getenv("CARD_HOLDER", "F.I.SH")                # <-- karta egasining ismi
VIP_PRICE = int(os.getenv("VIP_PRICE", "15000"))                # so'm / oy
PREMIUM_PRICE = int(os.getenv("PREMIUM_PRICE", "25000"))        # so'm / oy
SUBSCRIPTION_DAYS = 30                                           # obuna necha kunlik

# --- Referal mukofoti: N kishini taklif qilgan foydalanuvchiga bepul VIP ---
REFERRAL_TARGET_COUNT = 5                                         # necha kishi taklif qilinsa
REFERRAL_REWARD_DAYS = 15                                         # necha kunlik bepul VIP beriladi
REFERRAL_REWARD_PLAN = "vip"                                      # qaysi status beriladi

# --- Yangi kino qo'shilganda avtomatik e'lon qilinadigan kanal (ixtiyoriy) ---
POST_CHANNEL = os.getenv("POST_CHANNEL", "")                      # masalan: @kino_yangiliklari

# --- Faqat VIP/PREMIUM to'lagan foydalanuvchilar kira oladigan yopiq kanal (ixtiyoriy) ---
# Bot shu kanalda ADMIN bo'lishi va "Invite users via link" + "Ban users" huquqiga ega bo'lishi shart.
VIP_CHANNEL_ID = os.getenv("VIP_CHANNEL_ID", "")                  # masalan: -1001234567890

# --- Barcha kino/serial kodlari ro'yxati avtomatik joylanadigan ochiq katalog kanal (ixtiyoriy) ---
CATALOG_CHANNEL = os.getenv("CATALOG_CHANNEL", "")                # masalan: @kino_kodlari_katalog

# ============================
# 2. SQLite BAZA (DB)
# ============================
DB_NAME = os.getenv("DB_PATH", "bot_database.db")  # Persistent Disk ulansa, masalan: /var/data/bot_database.db

def init_db():
    """Barcha kerakli jadvallarni yaratadi (agar mavjud bo'lmasa)"""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    # Foydalanuvchilar jadvali
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        last_name TEXT,
        status TEXT DEFAULT 'oddiy',
        referrer_id INTEGER DEFAULT NULL,
        bonus_balance INTEGER DEFAULT 0,
        last_daily_bonus TEXT DEFAULT NULL,
        registered_at TEXT DEFAULT CURRENT_TIMESTAMP,
        total_requests INTEGER DEFAULT 0,
        is_banned BOOLEAN DEFAULT 0,
        last_request_time TEXT DEFAULT NULL,
        request_count INTEGER DEFAULT 0
    )''')

    # Kinolar jadvali
    c.execute('''CREATE TABLE IF NOT EXISTS movies (
        code TEXT PRIMARY KEY,
        title TEXT,
        description TEXT,
        file_id TEXT,
        category TEXT,
        is_vip BOOLEAN DEFAULT 0,
        is_premium BOOLEAN DEFAULT 0,
        added_by INTEGER,
        added_at TEXT DEFAULT CURRENT_TIMESTAMP,
        views INTEGER DEFAULT 0,
        rating REAL DEFAULT 0,
        rating_count INTEGER DEFAULT 0
    )''')

    # Kategoriyalar jadvali
    c.execute('''CREATE TABLE IF NOT EXISTS categories (
        name TEXT PRIMARY KEY,
        emoji TEXT
    )''')

    # Referallar jadvali
    c.execute('''CREATE TABLE IF NOT EXISTS referrals (
        referrer_id INTEGER,
        referred_id INTEGER PRIMARY KEY,
        bonus_given INTEGER DEFAULT 0,
        date TEXT DEFAULT CURRENT_TIMESTAMP
    )''')

    # Loglar jadvali (har bir harakat qayd etiladi)
    c.execute('''CREATE TABLE IF NOT EXISTS logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        action TEXT,
        details TEXT,
        timestamp TEXT DEFAULT CURRENT_TIMESTAMP
    )''')

    # Umumiy sozlamalar (masalan: majburiy obuna kanallari) doimiy saqlanadi
    c.execute('''CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )''')

    # To'lov so'rovlari jadvali (karta orqali qo'lda tasdiqlanadigan obunalar)
    c.execute('''CREATE TABLE IF NOT EXISTS payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        plan TEXT,
        amount INTEGER,
        screenshot_file_id TEXT,
        status TEXT DEFAULT 'pending',
        requested_at TEXT DEFAULT CURRENT_TIMESTAMP,
        decided_at TEXT,
        decided_by INTEGER
    )''')

    # Seriallar jadvali
    c.execute('''CREATE TABLE IF NOT EXISTS series (
        code TEXT PRIMARY KEY,
        title TEXT,
        description TEXT,
        poster_file_id TEXT,
        category TEXT,
        is_vip BOOLEAN DEFAULT 0,
        is_premium BOOLEAN DEFAULT 0,
        free_episodes INTEGER DEFAULT 0,
        added_by INTEGER,
        added_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')

    # Serial qismlari (epizodlar) jadvali
    c.execute('''CREATE TABLE IF NOT EXISTS episodes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        series_code TEXT,
        episode_number INTEGER,
        file_id TEXT,
        added_at TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(series_code, episode_number)
    )''')

    # Ro'yxatdan o'tish uchun bir martalik tokenlar (Telegram tashqarisida ham ishlashi uchun)
    c.execute('''CREATE TABLE IF NOT EXISTS reg_tokens (
        token TEXT PRIMARY KEY,
        user_id INTEGER,
        full_name TEXT,
        username TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        expires_at TEXT,
        used BOOLEAN DEFAULT 0
    )''')

    conn.commit()

    # Eski bazalarda mavjud bo'lmasa, kerakli ustunlarni qo'shamiz (xavfsiz migratsiya)
    migrations = [
        "ALTER TABLE users ADD COLUMN subscription_expires_at TEXT DEFAULT NULL",
        "ALTER TABLE users ADD COLUMN referral_reward_claimed BOOLEAN DEFAULT 0",
        "ALTER TABLE users ADD COLUMN reminder_sent BOOLEAN DEFAULT 0",
        "ALTER TABLE users ADD COLUMN language TEXT DEFAULT 'uz'",
        "ALTER TABLE users ADD COLUMN notify_new_movies BOOLEAN DEFAULT 1",
        "ALTER TABLE users ADD COLUMN registration_status TEXT DEFAULT 'approved'",
        "ALTER TABLE users ADD COLUMN full_name TEXT DEFAULT NULL",
        "ALTER TABLE users ADD COLUMN registration_photo TEXT DEFAULT NULL",
        "ALTER TABLE movies ADD COLUMN poster_file_id TEXT DEFAULT NULL",
    ]
    for m in migrations:
        try:
            c.execute(m)
            conn.commit()
        except sqlite3.OperationalError:
            pass  # ustun allaqachon mavjud

    conn.close()

init_db()

# ============================
# 3. BAZA YORDAMCHI FUNKSIYALARI
# ============================
def db_execute(query, params=(), fetchone=False, fetchall=False, commit=False):
    """Har qanday SQL so'rovni bajaruvchi universal funksiya"""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute(query, params)
    result = None
    if fetchone:
        result = c.fetchone()
    elif fetchall:
        result = c.fetchall()
    if commit:
        conn.commit()
    conn.close()
    return result

# ------------------------------
# Foydalanuvchi bilan bog'liq funksiyalar
# ------------------------------
def add_user(user_id, username, first_name, last_name, referrer_id=None):
    """Yangi foydalanuvchini ro'yxatdan o'tkazadi (kutilmoqda holatida) yoki mavjudini yangilaydi"""
    user = db_execute("SELECT user_id FROM users WHERE user_id=?", (user_id,), fetchone=True)
    if not user:
        db_execute(
            "INSERT INTO users (user_id, username, first_name, last_name, referrer_id, registration_status) "
            "VALUES (?, ?, ?, ?, ?, 'pending')",
            (user_id, username, first_name, last_name, referrer_id),
            commit=True
        )
        # Agar referal orqali kelgan bo'lsa - referal beruvchiga bonus beriladi
        if referrer_id:
            db_execute("UPDATE users SET bonus_balance = bonus_balance + 10 WHERE user_id=?", (referrer_id,), commit=True)
            db_execute("INSERT INTO referrals (referrer_id, referred_id, bonus_given) VALUES (?, ?, ?)",
                       (referrer_id, user_id, 10), commit=True)
        log_action(user_id, "register", f"Referrer: {referrer_id}")
    else:
        db_execute("UPDATE users SET username=?, first_name=?, last_name=? WHERE user_id=?",
                   (username, first_name, last_name, user_id), commit=True)

def get_registration_status(user_id):
    row = db_execute("SELECT registration_status FROM users WHERE user_id=?", (user_id,), fetchone=True)
    return row[0] if row else "pending"

def set_registration_status(user_id, status):
    db_execute("UPDATE users SET registration_status=? WHERE user_id=?", (status, user_id), commit=True)

def get_user_status(user_id):
    """Foydalanuvchi statusini qaytaradi: oddiy / vip / premium / admin.
    Agar VIP/PREMIUM muddati tugagan bo'lsa, avtomatik 'oddiy'ga tushiriladi."""
    row = db_execute("SELECT status, subscription_expires_at FROM users WHERE user_id=?", (user_id,), fetchone=True)
    if not row:
        return "oddiy"
    status, expires_at = row
    if status in ("vip", "premium") and expires_at:
        if datetime.now() > datetime.fromisoformat(expires_at):
            set_user_status(user_id, "oddiy")
            db_execute("UPDATE users SET subscription_expires_at=NULL WHERE user_id=?", (user_id,), commit=True)
            log_action(user_id, "subscription_expired", f"was={status}")
            return "oddiy"
    return status

def get_subscription_expiry(user_id):
    row = db_execute("SELECT subscription_expires_at FROM users WHERE user_id=?", (user_id,), fetchone=True)
    return row[0] if row and row[0] else None

def activate_subscription(user_id, plan, days=None):
    """Foydalanuvchiga vip/premium statusni belgilangan kunga faollashtiradi.
    Agar mavjud faol obuna bo'lsa, muddatga qo'shib (uzaytirib) beriladi."""
    days = days or SUBSCRIPTION_DAYS
    current_expiry = get_subscription_expiry(user_id)
    base = datetime.now()
    if current_expiry:
        try:
            existing = datetime.fromisoformat(current_expiry)
            if existing > base:
                base = existing  # mavjud muddat ustiga qo'shamiz
        except ValueError:
            pass
    expires = (base + timedelta(days=days)).isoformat()
    set_user_status(user_id, plan)
    db_execute("UPDATE users SET subscription_expires_at=?, reminder_sent=0 WHERE user_id=?", (expires, user_id), commit=True)
    return expires

def get_referral_count(user_id):
    row = db_execute("SELECT COUNT(*) FROM referrals WHERE referrer_id=?", (user_id,), fetchone=True)
    return row[0] if row else 0

def check_and_grant_referral_reward(referrer_id):
    """REFERRAL_TARGET_COUNT kishini taklif qilgan foydalanuvchiga bir martalik bepul obuna beradi.
    Mukofot berilsa True, aks holda False qaytaradi."""
    row = db_execute("SELECT referral_reward_claimed FROM users WHERE user_id=?", (referrer_id,), fetchone=True)
    if not row or row[0]:
        return False
    if get_referral_count(referrer_id) < REFERRAL_TARGET_COUNT:
        return False
    activate_subscription(referrer_id, REFERRAL_REWARD_PLAN, days=REFERRAL_REWARD_DAYS)
    db_execute("UPDATE users SET referral_reward_claimed=1 WHERE user_id=?", (referrer_id,), commit=True)
    log_action(referrer_id, "referral_reward", f"count={REFERRAL_TARGET_COUNT} days={REFERRAL_REWARD_DAYS}")
    return True

def set_user_status(user_id, status):
    db_execute("UPDATE users SET status=? WHERE user_id=?", (status, user_id), commit=True)

def get_user_bonus(user_id):
    row = db_execute("SELECT bonus_balance FROM users WHERE user_id=?", (user_id,), fetchone=True)
    return row[0] if row else 0

def update_bonus(user_id, amount):
    db_execute("UPDATE users SET bonus_balance = bonus_balance + ? WHERE user_id=?", (amount, user_id), commit=True)

def check_daily_bonus(user_id):
    """Foydalanuvchi bugun kunlik bonus olganmi-yo'qmi tekshiradi"""
    row = db_execute("SELECT last_daily_bonus FROM users WHERE user_id=?", (user_id,), fetchone=True)
    if row and row[0]:
        last = datetime.fromisoformat(row[0])
        if datetime.now() - last < timedelta(days=1):
            return False
    return True

def set_daily_bonus(user_id):
    db_execute("UPDATE users SET last_daily_bonus=? WHERE user_id=?", (datetime.now().isoformat(), user_id), commit=True)

def is_user_banned(user_id):
    row = db_execute("SELECT is_banned FROM users WHERE user_id=?", (user_id,), fetchone=True)
    return bool(row and row[0] == 1)

def set_user_ban(user_id, banned=True):
    db_execute("UPDATE users SET is_banned=? WHERE user_id=?", (1 if banned else 0, user_id), commit=True)

def check_spam(user_id):
    """Spam himoyasi: daqiqasiga 10 tadan ortiq so'rov yuborilsa bloklanadi"""
    now = datetime.now()
    row = db_execute("SELECT last_request_time, request_count FROM users WHERE user_id=?", (user_id,), fetchone=True)
    if not row:
        return True
    last_time_str, count = row
    if last_time_str:
        last_time = datetime.fromisoformat(last_time_str)
        if now - last_time > timedelta(minutes=1):
            db_execute("UPDATE users SET request_count=1, last_request_time=? WHERE user_id=?", (now.isoformat(), user_id), commit=True)
            return True
        else:
            if count >= 10:
                return False
            db_execute("UPDATE users SET request_count=request_count+1 WHERE user_id=?", (user_id,), commit=True)
            return True
    else:
        db_execute("UPDATE users SET request_count=1, last_request_time=? WHERE user_id=?", (now.isoformat(), user_id), commit=True)
        return True

def log_action(user_id, action, details=""):
    """Har bir muhim harakatni logs jadvaliga yozadi"""
    db_execute("INSERT INTO logs (user_id, action, details) VALUES (?, ?, ?)", (user_id, action, details), commit=True)

# ------------------------------
# Kinolar bilan bog'liq funksiyalar
# ------------------------------
def add_movie(code, title, description, file_id, category, is_vip=False, is_premium=False, added_by=0, poster_file_id=None):
    db_execute(
        "INSERT OR REPLACE INTO movies (code, title, description, file_id, category, is_vip, is_premium, added_by, poster_file_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (code, title, description, file_id, category, is_vip, is_premium, added_by, poster_file_id),
        commit=True
    )

def get_movie(code):
    return db_execute("SELECT * FROM movies WHERE code=?", (code,), fetchone=True)

def get_next_suggested_code():
    """Mavjud kodlar orasidan eng katta raqamli kodni topib, +1 taklif qiladi"""
    codes = db_execute("SELECT code FROM movies", fetchall=True)
    numeric = [int(c[0]) for c in codes if c[0].isdigit()]
    return str(max(numeric) + 1) if numeric else "101"

def delete_movie(code):
    db_execute("DELETE FROM movies WHERE code=?", (code,), commit=True)

def get_all_movies():
    return db_execute("SELECT code, title FROM movies ORDER BY added_at DESC", fetchall=True)

def get_movies_by_category(category):
    return db_execute("SELECT code, title FROM movies WHERE category=? ORDER BY added_at DESC", (category,), fetchall=True)

def get_categories():
    return db_execute("SELECT name, emoji FROM categories", fetchall=True)

def add_category(name, emoji="📁"):
    db_execute("INSERT OR IGNORE INTO categories (name, emoji) VALUES (?, ?)", (name, emoji), commit=True)

def increment_views(code):
    db_execute("UPDATE movies SET views = views + 1 WHERE code=?", (code,), commit=True)

def update_rating(code, rating):
    """Kino reytingini yangi baho bilan qayta hisoblaydi (o'rtacha qiymat)"""
    movie = get_movie(code)
    if movie:
        old_rating = movie[10] or 0
        old_count = movie[11] or 0
        new_count = old_count + 1
        new_rating = (old_rating * old_count + rating) / new_count
        db_execute("UPDATE movies SET rating=?, rating_count=? WHERE code=?", (new_rating, new_count, code), commit=True)

def search_movies(query):
    return db_execute(
        "SELECT code, title FROM movies WHERE code LIKE ? OR title LIKE ? ORDER BY added_at DESC",
        (f"%{query}%", f"%{query}%"),
        fetchall=True
    )

def find_similar_titles(query, limit=3):
    """Kod topilmasa, kino/serial nomlari orasidan o'xshashlarini topadi (imlo xatolariga chidamli)"""
    movies = db_execute("SELECT code, title FROM movies", fetchall=True) or []
    series = db_execute("SELECT code, title FROM series", fetchall=True) or []
    all_items = list(movies) + list(series)
    if not all_items:
        return []
    titles = [t for _, t in all_items]
    close = difflib.get_close_matches(query, titles, n=limit, cutoff=0.4)
    seen, result = set(), []
    for code, title in all_items:
        if title in close and title not in seen:
            seen.add(title)
            result.append((code, title))
    return result

# ------------------------------
# To'lovlar (obuna) bilan bog'liq funksiyalar
# ------------------------------
def create_payment(user_id, plan, amount, screenshot_file_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute(
        "INSERT INTO payments (user_id, plan, amount, screenshot_file_id) VALUES (?, ?, ?, ?)",
        (user_id, plan, amount, screenshot_file_id)
    )
    conn.commit()
    payment_id = c.lastrowid
    conn.close()
    return payment_id

def get_payment(payment_id):
    return db_execute("SELECT * FROM payments WHERE id=?", (payment_id,), fetchone=True)

def set_payment_status(payment_id, status, decided_by):
    db_execute(
        "UPDATE payments SET status=?, decided_at=?, decided_by=? WHERE id=?",
        (status, datetime.now().isoformat(), decided_by, payment_id),
        commit=True
    )

def has_pending_payment(user_id):
    row = db_execute("SELECT id FROM payments WHERE user_id=? AND status='pending'", (user_id,), fetchone=True)
    return row is not None

def get_pending_payments():
    return db_execute("SELECT id, user_id, plan, amount, requested_at FROM payments WHERE status='pending' ORDER BY requested_at ASC", fetchall=True)

# ------------------------------
# Seriallar va epizodlar bilan bog'liq funksiyalar
# ------------------------------
def add_series(code, title, description, category, is_vip, is_premium, free_episodes, added_by, poster_file_id=None):
    db_execute(
        "INSERT OR REPLACE INTO series (code, title, description, poster_file_id, category, is_vip, is_premium, free_episodes, added_by) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (code, title, description, poster_file_id, category, is_vip, is_premium, free_episodes, added_by),
        commit=True
    )

def get_series(code):
    return db_execute("SELECT * FROM series WHERE code=?", (code,), fetchone=True)

def get_all_series():
    return db_execute("SELECT code, title FROM series ORDER BY added_at DESC", fetchall=True)

def add_episode(series_code, episode_number, file_id):
    db_execute(
        "INSERT OR REPLACE INTO episodes (series_code, episode_number, file_id) VALUES (?, ?, ?)",
        (series_code, episode_number, file_id),
        commit=True
    )

def get_episode(series_code, episode_number):
    return db_execute(
        "SELECT * FROM episodes WHERE series_code=? AND episode_number=?",
        (series_code, episode_number), fetchone=True
    )

def get_episode_numbers(series_code):
    rows = db_execute(
        "SELECT episode_number FROM episodes WHERE series_code=? ORDER BY episode_number ASC",
        (series_code,), fetchall=True
    )
    return [r[0] for r in rows]

# ------------------------------
# VIP yopiq kanalga avtomatik kirish/chiqish
# ------------------------------
def create_registration_token(user_id, full_name, username):
    """Telegram tashqarisida (oddiy brauzerda) ro'yxatdan o'tish uchun bir martalik, muddatli token yaratadi"""
    token = secrets.token_urlsafe(24)
    expires_at = (datetime.now() + timedelta(minutes=15)).isoformat()
    db_execute(
        "INSERT INTO reg_tokens (token, user_id, full_name, username, expires_at) VALUES (?, ?, ?, ?, ?)",
        (token, user_id, full_name, username or "", expires_at),
        commit=True
    )
    return token

def consume_registration_token(token):
    """Tokenni tekshiradi va bir martalik ishlatadi. To'g'ri bo'lsa (user_id, full_name, username) qaytaradi."""
    row = db_execute(
        "SELECT user_id, full_name, username, expires_at, used FROM reg_tokens WHERE token=?",
        (token,), fetchone=True
    )
    if not row:
        return None
    user_id, full_name, username, expires_at, used = row
    if used:
        return None
    if datetime.now() > datetime.fromisoformat(expires_at):
        return None
    db_execute("UPDATE reg_tokens SET used=1 WHERE token=?", (token,), commit=True)
    return {"id": user_id, "full_name": full_name, "username": username}

def validate_webapp_init_data(init_data: str):
    """Telegram Mini App yuborgan initData imzosini tekshiradi (soxtalashtirishning oldini olish uchun).
    To'g'ri bo'lsa foydalanuvchi ma'lumotini (dict) qaytaradi, aks holda None."""
    try:
        parsed = dict(parse_qsl(init_data, strict_parsing=True))
        received_hash = parsed.pop("hash", None)
        if not received_hash:
            return None
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(computed_hash, received_hash):
            return None
        user_json = parsed.get("user")
        return json.loads(user_json) if user_json else None
    except Exception as e:
        logging.exception(f"WebApp initData tekshiruvi xatosi: {e}")
        return None

async def grant_vip_channel_access(user_id):
    """Foydalanuvchiga VIP yopiq kanalga bir martalik, muddatli taklif havolasini yuboradi"""
    if not VIP_CHANNEL_ID:
        return
    try:
        expire_ts = int((datetime.now() + timedelta(hours=24)).timestamp())
        invite = await bot.create_chat_invite_link(chat_id=VIP_CHANNEL_ID, member_limit=1, expire_date=expire_ts)
        await bot.send_message(
            user_id,
            f"🔐 <b>VIP yopiq kanalga qo'shilish havolasi:</b>\n{invite.invite_link}\n\n"
            f"⚠️ Havola faqat 24 soat va 1 kishi uchun amal qiladi.",
            parse_mode="HTML"
        )
    except Exception as e:
        logging.exception(f"VIP kanalga taklif xatosi (user={user_id}): {e}")

async def revoke_vip_channel_access(user_id):
    """Obunasi tugagan foydalanuvchini VIP kanaldan chiqaradi (ban+unban = oddiy kick)"""
    if not VIP_CHANNEL_ID:
        return
    try:
        await bot.ban_chat_member(chat_id=VIP_CHANNEL_ID, user_id=user_id)
        await bot.unban_chat_member(chat_id=VIP_CHANNEL_ID, user_id=user_id, only_if_banned=True)
    except Exception as e:
        logging.exception(f"VIP kanaldan chiqarish xatosi (user={user_id}): {e}")

async def post_to_catalog(code, title, deep_link, is_series=False):
    """Yangi kino/serial qo'shilganda ochiq katalog kanalga qisqa yozuv joylaydi"""
    if not CATALOG_CHANNEL:
        return
    try:
        icon = "🎞" if is_series else "🎬"
        kb = InlineKeyboardBuilder()
        kb.button(text="▶️ Ko'rish", url=deep_link)
        await bot.send_message(
            CATALOG_CHANNEL,
            f"{icon} <b>{title}</b>\n🔑 Kod: <code>{code}</code>",
            parse_mode="HTML",
            reply_markup=kb.as_markup()
        )
    except Exception as e:
        logging.exception(f"Katalog kanalga post xatosi: {e}")

async def notify_users_new_content(title, code, deep_link, is_series=False):
    """Bildirishnomani yoqqan barcha foydalanuvchilarga yangi kino/serial haqida xabar yuboradi"""
    icon = "🎞 Yangi serial" if is_series else "🆕 Yangi kino"
    users = db_execute(
        "SELECT user_id FROM users WHERE notify_new_movies=1 AND is_banned=0", fetchall=True
    ) or []
    kb = InlineKeyboardBuilder()
    kb.button(text="▶️ Ko'rish", url=deep_link)
    for (user_id,) in users:
        try:
            await bot.send_message(
                user_id,
                f"{icon}: <b>{title}</b>\n🔑 Kod: <code>{code}</code>",
                parse_mode="HTML",
                reply_markup=kb.as_markup()
            )
        except Exception:
            pass


def get_channels():
    row = db_execute("SELECT value FROM settings WHERE key='channels'", fetchone=True)
    if row:
        try:
            return json.loads(row[0])
        except Exception:
            pass
    return []

def save_channels(channels):
    db_execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
               ("channels", json.dumps(channels)), commit=True)

CHANNELS = get_channels()  # Bot ishga tushganda bazadan kanal ro'yxati yuklanadi

# ============================
# 4. OBUNA TEKSHIRISH FUNKSIYASI
# ============================
async def is_subscribed(user_id: int, bot: Bot) -> bool:
    """Foydalanuvchi barcha majburiy kanallarga obuna bo'lganmi tekshiradi.
    Adminlar va pullik VIP/PREMIUM obunachilar bu tekshiruvdan ozod qilinadi."""
    if user_id in ADMIN_IDS:
        return True
    if get_user_status(user_id) in ("vip", "premium"):
        return True
    if not CHANNELS:
        return True
    for channel in CHANNELS:
        try:
            member = await bot.get_chat_member(chat_id=channel, user_id=user_id)
            if member.status not in [ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR]:
                return False
        except Exception as e:
            # Botning o'zi kanalda admin bo'lmasa yoki kanal noto'g'ri kiritilgan bo'lsa shu yerga tushadi
            logging.warning(f"Obuna tekshiruvi xatosi (kanal={channel}, user={user_id}): {e}")
            return False
    return True

# ============================
# 5. STATE MACHINE (Admin bosqichma-bosqich amallar uchun)
# ============================
class AdminStates(StatesGroup):
    waiting_movie_code = State()
    waiting_movie_title = State()
    waiting_movie_desc = State()
    waiting_movie_poster = State()
    waiting_movie_file = State()
    waiting_movie_category = State()
    waiting_movie_vip = State()
    waiting_movie_premium = State()
    waiting_delete_code = State()
    waiting_broadcast_text = State()
    waiting_broadcast_confirm = State()
    waiting_give_status_user = State()
    waiting_give_status_type = State()
    waiting_remove_status_user = State()
    waiting_add_channel = State()
    waiting_remove_channel = State()
    waiting_search_query = State()
    waiting_ban_user = State()
    waiting_payment_screenshot = State()
    waiting_series_code = State()
    waiting_series_title = State()
    waiting_series_desc = State()
    waiting_series_poster = State()
    waiting_series_category = State()
    waiting_series_vip = State()
    waiting_series_premium = State()
    waiting_series_free_count = State()
    waiting_episode_series_code = State()
    waiting_episode_number = State()
    waiting_episode_video = State()
    waiting_edit_code = State()
    waiting_edit_field = State()
    waiting_edit_value = State()
    waiting_registration_name = State()
    waiting_registration_photo = State()

# ============================
# 6. KLAVIATURALAR
# ============================
def main_reply_keyboard(user_id=None):
    builder = ReplyKeyboardBuilder()
    builder.button(text="🎬 Kinolar")
    builder.button(text="🔍 Qidirish")
    builder.button(text="🏆 Top kinolar")
    builder.button(text="🆕 Yangi kinolar")
    builder.button(text="🎁 Bonus")
    builder.button(text="💳 Obuna")
    builder.button(text="👤 Profil")
    builder.button(text="📂 Kategoriyalar")
    if user_id in ADMIN_IDS:
        builder.button(text="⚙️ Admin panel")
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)

def main_inline_keyboard(user_id):
    status = get_user_status(user_id)
    builder = InlineKeyboardBuilder()
    builder.button(text="🎬 Barcha kinolar", callback_data="movies_menu")
    builder.button(text="🔍 Qidirish", callback_data="search_movie")
    builder.button(text="🏆 Top kinolar", callback_data="top_movies")
    builder.button(text="🆕 Yangi kinolar", callback_data="new_movies")
    builder.button(text="🎁 Bonus", callback_data="bonus_menu")
    builder.button(text="💳 Obuna sotib olish", callback_data="subscription_menu")
    builder.button(text="👤 Profil", callback_data="profile")
    builder.button(text="📂 Kategoriyalar", callback_data="categories_menu")
    if status in ["vip", "premium", "admin"]:
        builder.button(text="💎 Maxsus kinolar", callback_data="special_movies")
    if status == "admin":
        builder.button(text="⚙️ Admin panel", callback_data="admin_panel")
    builder.adjust(2)
    return builder.as_markup()

def admin_panel_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="🕐 Oxirgi harakatlar", callback_data="admin_recent")
    builder.button(text="➕ Kino qo'shish", callback_data="admin_add_movie")
    builder.button(text="➖ Kino o'chirish", callback_data="admin_delete_movie")
    builder.button(text="✏️ Kino tahrirlash", callback_data="admin_edit_movie")
    builder.button(text="🎞 Serial qo'shish", callback_data="admin_add_series")
    builder.button(text="➕ Epizod qo'shish", callback_data="admin_add_episode")
    builder.button(text="📊 Statistika", callback_data="admin_stats")
    builder.button(text="📢 Broadcast", callback_data="admin_broadcast")
    builder.button(text="👑 Status berish", callback_data="admin_give_status")
    builder.button(text="👑 Status olib tashlash", callback_data="admin_remove_status")
    builder.button(text="💳 To'lov so'rovlari", callback_data="admin_payments")
    builder.button(text="📡 Kanal sozlash", callback_data="admin_channels")
    builder.button(text="📋 Foydalanuvchilar", callback_data="admin_users")
    builder.button(text="🚫 Ban qilish", callback_data="admin_ban")
    builder.button(text="🔙 Orqaga", callback_data="back_main")
    builder.adjust(2)
    return builder.as_markup()

def movie_list_keyboard(movies: List[Tuple[str, str]], page=0, per_page=5):
    total = len(movies)
    start = page * per_page
    end = min(start + per_page, total)
    builder = InlineKeyboardBuilder()
    for code, title in movies[start:end]:
        builder.button(text=f"{title} ({code})", callback_data=f"movie_{code}")
    builder.adjust(1)
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(text="⬅️", callback_data=f"movies_page_{page-1}"))
    if end < total:
        nav_buttons.append(InlineKeyboardButton(text="➡️", callback_data=f"movies_page_{page+1}"))
    if nav_buttons:
        builder.row(*nav_buttons)
    builder.row(InlineKeyboardButton(text="🔙 Orqaga", callback_data="back_main"))
    return builder.as_markup()

def category_list_keyboard(categories):
    builder = InlineKeyboardBuilder()
    for name, emoji in categories:
        builder.button(text=f"{emoji} {name}", callback_data=f"category_{name}")
    builder.adjust(2)
    builder.row(InlineKeyboardButton(text="🔙 Orqaga", callback_data="back_main"))
    return builder.as_markup()

def movie_action_keyboard(code, share_link=None):
    builder = InlineKeyboardBuilder()
    builder.button(text="⭐️ Baholash", callback_data=f"rate_{code}")
    if share_link:
        share_url = f"https://t.me/share/url?url={share_link}&text=Bu%20kinoni%20ko'ring!"
        builder.button(text="📤 Ulashish", url=share_url)
    builder.button(text="🔙 Orqaga", callback_data="movies_menu")
    builder.adjust(2)
    return builder.as_markup()

def rate_keyboard(code):
    builder = InlineKeyboardBuilder()
    for i in range(1, 6):
        builder.button(text=f"{i}⭐️", callback_data=f"rate_val_{code}_{i}")
    builder.adjust(5)
    builder.row(InlineKeyboardButton(text="🔙 Orqaga", callback_data=f"movie_{code}"))
    return builder.as_markup()

def bonus_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="🎁 Kunlik bonus", callback_data="daily_bonus")
    builder.button(text="💰 Bonus balansim", callback_data="bonus_balance")
    builder.button(text="🔗 Referal havola", callback_data="referral_link")
    builder.button(text="🔙 Orqaga", callback_data="back_main")
    builder.adjust(1)
    return builder.as_markup()

def subscribe_keyboard():
    builder = InlineKeyboardBuilder()
    for ch in CHANNELS:
        builder.button(text=f"📢 Obuna bo'lish: {ch}", url=f"https://t.me/{ch.lstrip('@')}")
    builder.adjust(1)
    builder.row(InlineKeyboardButton(text="✅ Tekshirish", callback_data="check_subscription"))
    return builder.as_markup()

def subscription_plans_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text=f"👑 VIP — {VIP_PRICE:,} so'm/oy".replace(",", " "), callback_data="buy_vip")
    builder.button(text=f"💎 PREMIUM — {PREMIUM_PRICE:,} so'm/oy".replace(",", " "), callback_data="buy_premium")
    builder.adjust(1)
    builder.row(InlineKeyboardButton(text="🔙 Orqaga", callback_data="back_main"))
    return builder.as_markup()

def upsell_keyboard(plan):
    """Qulflangan kino uchun to'g'ridan-to'g'ri sotib olish tugmasi"""
    builder = InlineKeyboardBuilder()
    if plan == "vip":
        builder.button(text=f"👑 VIP sotib olish — {VIP_PRICE:,} so'm/oy".replace(",", " "), callback_data="buy_vip")
    else:
        builder.button(text=f"💎 PREMIUM sotib olish — {PREMIUM_PRICE:,} so'm/oy".replace(",", " "), callback_data="buy_premium")
    builder.button(text="📋 Boshqa rejalar", callback_data="subscription_menu")
    builder.adjust(1)
    return builder.as_markup()

def episode_list_keyboard(series_code, episode_numbers, free_episodes):
    """Serial qismlari ro'yxati — pullik qismlar 🔒 belgisi bilan ko'rsatiladi"""
    builder = InlineKeyboardBuilder()
    for ep in episode_numbers:
        lock = "" if ep <= free_episodes else "🔒 "
        builder.button(text=f"{lock}{ep}-qism", callback_data=f"episode_{series_code}_{ep}")
    builder.adjust(4)
    builder.row(InlineKeyboardButton(text="🔙 Orqaga", callback_data="back_main"))
    return builder.as_markup()

def payment_cancel_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Bekor qilish", callback_data="subscription_menu")
    return builder.as_markup()

def payment_admin_keyboard(payment_id):
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Tasdiqlash", callback_data=f"approve_pay_{payment_id}")
    builder.button(text="❌ Rad etish", callback_data=f"reject_pay_{payment_id}")
    builder.adjust(2)
    return builder.as_markup()

def channel_settings_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Kanal qo'shish", callback_data="admin_add_channel")
    builder.button(text="➖ Kanal o'chirish", callback_data="admin_remove_channel")
    builder.adjust(2)
    builder.row(InlineKeyboardButton(text="🔙 Orqaga", callback_data="admin_panel"))
    return builder.as_markup()

# ============================
# 7. BOT VA DISPATCHER
# ============================
storage = MemoryStorage()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=storage)

class ApprovalMiddleware(BaseMiddleware):
    """Admin tomonidan tasdiqlanmagan foydalanuvchilarni /start va ro'yxatdan o'tish
    bosqichidan tashqari HECH QANDAY funksiyaga kirita olmaydi."""
    async def __call__(self, handler, event, data):
        user = event.from_user if hasattr(event, "from_user") else None
        if user is None:
            return await handler(event, data)

        if user.id in ADMIN_IDS:
            return await handler(event, data)

        # /start komandasi doim o'tkaziladi (ro'yxatdan o'tish shu orqali boshlanadi)
        if isinstance(event, Message) and event.text and event.text.startswith("/start"):
            return await handler(event, data)

        # Ro'yxatdan o'tish jarayonidagi xabar (F.I.Sh kiritish) o'tkaziladi
        state: FSMContext = data.get("state")
        if state:
            current_state = await state.get_state()
            if current_state in (AdminStates.waiting_registration_name.state, AdminStates.waiting_registration_photo.state):
                return await handler(event, data)

        reg_status = get_registration_status(user.id)
        if reg_status != "approved":
            text = (
                "⏳ Arizangiz hali admin tomonidan ko'rib chiqilmoqda. Iltimos, kuting."
                if reg_status == "pending" else
                "❌ Arizangiz rad etilgan. Qayta urinish uchun /start bosing."
            )
            if isinstance(event, Message):
                await event.answer(text)
            elif isinstance(event, CallbackQuery):
                await event.answer(text, show_alert=True)
            return  # handlerga yo'l berilmaydi

        return await handler(event, data)

dp.message.middleware(ApprovalMiddleware())
dp.callback_query.middleware(ApprovalMiddleware())

# ============================
# 8. HANDLERLAR
# ============================

# ---------- /start komandasi ----------
@dp.message(Command("start"))
async def start_command(message: Message, command: CommandObject, state: FSMContext):
    """Botni ishga tushiruvchi asosiy komanda.
    Uch turdagi havolani qabul qiladi:
    • Referal:    t.me/BOT_USERNAME?start=123456789          (foydalanuvchi ID)
    • Kino kodi:  t.me/BOT_USERNAME?start=movie_101           (Instagram bio va h.k. uchun qulay)
    • Serial kodi: t.me/BOT_USERNAME?start=series_S101
    """
    user = message.from_user
    args = command.args
    referrer_id = None
    movie_code = None
    series_code = None
    source = None

    if args:
        if args.startswith("movie_"):
            payload = args.split("movie_", 1)[1].strip()
            if "_" in payload:
                movie_code, source = payload.split("_", 1)
            else:
                movie_code = payload
        elif args.startswith("series_"):
            series_code = args.split("series_", 1)[1].strip()
        elif args.isdigit():
            referrer_id = int(args)
            if referrer_id == user.id:
                referrer_id = None

    try:
        is_new_user = not db_execute("SELECT user_id FROM users WHERE user_id=?", (user.id,), fetchone=True)
        add_user(user.id, user.username, user.first_name, user.last_name, referrer_id)
        if user.id in ADMIN_IDS:
            set_user_status(user.id, "admin")
            set_registration_status(user.id, "approved")
        await state.clear()

        # --- Ro'yxatdan o'tish/admin tasdig'i darvozasi ---
        if user.id not in ADMIN_IDS:
            reg_status = get_registration_status(user.id)
            if reg_status == "pending":
                row = db_execute("SELECT full_name, registration_photo FROM users WHERE user_id=?", (user.id,), fetchone=True)
                full_name, reg_photo = (row[0], row[1]) if row else (None, None)
                if full_name and reg_photo:
                    await message.answer(
                        "⏳ Arizangiz hali admin tomonidan ko'rib chiqilmoqda.\n"
                        "Tasdiqlangach botdan foydalanishingiz mumkin bo'ladi. Iltimos, kuting."
                    )
                elif full_name and not reg_photo:
                    if WEBAPP_URL:
                        token = create_registration_token(user.id, full_name, user.username)
                        kb = InlineKeyboardBuilder()
                        kb.button(text="🔎 Skanerni ochish", url=f"{WEBAPP_URL}/register?token={token}")
                        kb.adjust(1)
                        await message.answer(
                            "🔎 Ro'yxatdan o'tishni yakunlash uchun tugmani bosing (havola 15 daqiqa amal qiladi):",
                            reply_markup=kb.as_markup()
                        )
                    else:
                        await message.answer(
                            "📸 Iltimos, ro'yxatdan o'tishni yakunlash uchun 📎 tugmasini bosib, "
                            "<b>Kamera</b>ni tanlang va hoziroq jonli selfie oling.",
                            parse_mode="HTML"
                        )
                    await state.set_state(AdminStates.waiting_registration_photo)
                else:
                    await message.answer(
                        f"👋 Assalomu alaykum, {user.first_name}!\n\n"
                        f"🎬 <b>KODLI KINO BOT</b>dan foydalanish uchun avval ro'yxatdan o'tishingiz kerak.\n\n"
                        f"✍️ Iltimos, to'liq ismingizni (F.I.Sh) kiriting:",
                        parse_mode="HTML"
                    )
                    await state.set_state(AdminStates.waiting_registration_name)
                return
            elif reg_status == "rejected":
                await message.answer(
                    "❌ Sizning oldingi arizangiz rad etilgan edi.\n\n"
                    "✍️ Qayta urinish uchun to'liq ismingizni (F.I.Sh) kiriting:"
                )
                set_registration_status(user.id, "pending")
                await state.set_state(AdminStates.waiting_registration_name)
                return
            # reg_status == "approved" bo'lsa, pastdagi oddiy oqim davom etadi

        if movie_code and source:
            log_action(user.id, "deeplink_source", f"code={movie_code} source={source}")

        # Agar yangi foydalanuvchi referal orqali kelgan bo'lsa — referal beruvchida mukofot mezoni tekshiriladi
        if is_new_user and referrer_id:
            if check_and_grant_referral_reward(referrer_id):
                plan_title = "👑 VIP" if REFERRAL_REWARD_PLAN == "vip" else "💎 PREMIUM"
                try:
                    await bot.send_message(
                        referrer_id,
                        f"🎉 Tabriklaymiz! Siz {REFERRAL_TARGET_COUNT} kishini taklif qildingiz va "
                        f"{REFERRAL_REWARD_DAYS} kunlik {plan_title} obunani bepul qo'lga kiritdingiz!"
                    )
                except Exception:
                    pass
                await grant_vip_channel_access(referrer_id)

        await message.answer(
            f"👋 Assalomu alaykum, {user.first_name}!\n\n"
            f"🎬 <b>KODLI KINO BOT</b>ga xush kelibsiz!\n"
            f"🔰 Sizning holatingiz: <b>{get_user_status(user.id)}</b>\n\n"
            f"🎞 Kino ko'rish uchun kino kodini yuboring (masalan: <code>101</code>)\n"
            f"yoki quyidagi menyudan foydalaning 👇",
            reply_markup=main_reply_keyboard(user.id),
            parse_mode="HTML"
        )

        if not await is_subscribed(user.id, bot):
            await message.answer(
                "❗️ Botdan foydalanish uchun avval quyidagi kanal(lar)ga obuna bo'ling:",
                reply_markup=subscribe_keyboard()
            )
            return  # obuna bo'lmaguncha kino kodi ham ko'rsatilmaydi

        if movie_code:
            # Instagram/boshqa joydan kino kodi bilan to'g'ridan-to'g'ri kelgan foydalanuvchi
            await send_movie(message, user.id, movie_code)
        elif series_code:
            await send_series(message, user.id, series_code)
        else:
            await message.answer("📋 Asosiy menyu:", reply_markup=main_inline_keyboard(user.id))
    except Exception as e:
        logging.exception(f"Start xatosi: {e}")
        await message.answer("❌ Xatolik yuz berdi. Iltimos, keyinroq urinib ko'ring.")

# ---------- Ro'yxatdan o'tish: F.I.Sh. qabul qilish ----------
@dp.message(AdminStates.waiting_registration_name)
async def registration_name_received(message: Message, state: FSMContext):
    try:
        full_name = message.text.strip() if message.text else ""
        if len(full_name) < 3:
            await message.answer("❗️ Iltimos, to'liq ismingizni to'g'ri kiriting (masalan: Aliyev Vali).")
            return

        user = message.from_user
        db_execute("UPDATE users SET full_name=? WHERE user_id=?", (full_name, user.id), commit=True)
        log_action(user.id, "registration_name_submitted", full_name)

        if WEBAPP_URL:
            token = create_registration_token(user.id, full_name, user.username)
            kb = InlineKeyboardBuilder()
            kb.button(text="🔎 Skanerni ochish", url=f"{WEBAPP_URL}/register?token={token}")
            kb.adjust(1)
            await message.answer(
                "🔎 Rahmat! Endi tasdiqlash uchun quyidagi tugmani bosing — skaner sahifasi ochiladi "
                "(havola 15 daqiqa amal qiladi, istalgan brauzerda ishlaydi):",
                reply_markup=kb.as_markup()
            )
        else:
            await message.answer(
                "📸 Rahmat! Endi tasdiqlash uchun 📎 tugmasini bosib, <b>Kamera</b>ni tanlang va "
                "hoziroq jonli (live) selfie oling — eski/galereyadagi rasm emas, aynan hozir olingan surat bo'lsin.",
                parse_mode="HTML"
            )
        await state.set_state(AdminStates.waiting_registration_photo)
    except Exception as e:
        logging.exception(f"Ro'yxatdan o'tish (ism) xatosi: {e}")
        await message.answer("❌ Xatolik yuz berdi. Qaytadan urinib ko'ring.")

@dp.message(AdminStates.waiting_registration_photo, F.photo)
async def registration_photo_received(message: Message, state: FSMContext):
    try:
        user = message.from_user
        photo_file_id = message.photo[-1].file_id
        db_execute("UPDATE users SET registration_photo=? WHERE user_id=?", (photo_file_id, user.id), commit=True)
        log_action(user.id, "registration_submitted", "photo received")

        await message.answer(
            "✅ Arizangiz qabul qilindi!\n"
            "⏳ Admin tasdiqlashini kuting — tasdiqlangach sizga xabar boradi."
        )

        full_name_row = db_execute("SELECT full_name FROM users WHERE user_id=?", (user.id,), fetchone=True)
        full_name = full_name_row[0] if full_name_row else "—"
        username_display = user.username or "yo\u02bcq"
        kb = InlineKeyboardBuilder()
        kb.button(text="✅ Tasdiqlash", callback_data=f"approve_reg_{user.id}")
        kb.button(text="❌ Rad etish", callback_data=f"reject_reg_{user.id}")
        kb.adjust(2)
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_photo(
                    admin_id,
                    photo_file_id,
                    caption=(
                        f"🆕 <b>Yangi ro'yxatdan o'tish so'rovi</b>\n\n"
                        f"👤 F.I.Sh: {full_name}\n"
                        f"🆔 ID: <code>{user.id}</code>\n"
                        f"📱 Username: @{username_display}"
                    ),
                    parse_mode="HTML",
                    reply_markup=kb.as_markup()
                )
            except Exception:
                pass

        await state.clear()
    except Exception as e:
        logging.exception(f"Ro'yxatdan o'tish (surat) xatosi: {e}")
        await message.answer("❌ Xatolik yuz berdi. Qaytadan urinib ko'ring.")

@dp.message(AdminStates.waiting_registration_photo)
async def registration_photo_invalid(message: Message):
    await message.answer("❗️ Iltimos, o'zingizning rasmingizni (selfie) yuboring — matn emas.")

@dp.callback_query(F.data.startswith("approve_reg_"))
async def approve_registration(call: CallbackQuery):
    try:
        if call.from_user.id not in ADMIN_IDS:
            await call.answer("⛔️ Ruxsat yo'q", show_alert=True)
            return
        target_user_id = int(call.data.split("_")[-1])
        set_registration_status(target_user_id, "approved")
        log_action(call.from_user.id, "approve_registration", f"user={target_user_id}")
        try:
            await bot.send_message(
                target_user_id,
                "✅ Arizangiz tasdiqlandi! Endi botdan to'liq foydalanishingiz mumkin.\n"
                "Boshlash uchun /start bosing."
            )
        except Exception:
            pass
        await call.message.edit_text(call.message.text + "\n\n✅ <b>TASDIQLANDI</b>", parse_mode="HTML")
    except Exception as e:
        logging.exception(f"Ro'yxatdan o'tishni tasdiqlash xatosi: {e}")
        await call.answer("Xatolik yuz berdi.", show_alert=True)
    await call.answer()

@dp.callback_query(F.data.startswith("reject_reg_"))
async def reject_registration(call: CallbackQuery):
    try:
        if call.from_user.id not in ADMIN_IDS:
            await call.answer("⛔️ Ruxsat yo'q", show_alert=True)
            return
        target_user_id = int(call.data.split("_")[-1])
        set_registration_status(target_user_id, "rejected")
        log_action(call.from_user.id, "reject_registration", f"user={target_user_id}")
        try:
            await bot.send_message(
                target_user_id,
                "❌ Arizangiz rad etildi.\n"
                "Qayta urinish uchun /start bosing."
            )
        except Exception:
            pass
        await call.message.edit_text(call.message.text + "\n\n❌ <b>RAD ETILDI</b>", parse_mode="HTML")
    except Exception as e:
        logging.exception(f"Ro'yxatdan o'tishni rad etish xatosi: {e}")
        await call.answer("Xatolik yuz berdi.", show_alert=True)
    await call.answer()

# ---------- Obuna tekshirish tugmasi ----------
@dp.callback_query(F.data == "check_subscription")
async def check_subscription_callback(call: CallbackQuery):
    try:
        user_id = call.from_user.id
        if await is_subscribed(user_id, bot):
            await call.message.delete()
            await call.message.answer(
                "✅ Obuna tasdiqlandi! Endi botdan to'liq foydalanishingiz mumkin.",
                reply_markup=main_inline_keyboard(user_id)
            )
        else:
            await call.answer("❌ Siz hali barcha kanallarga obuna bo'lmagansiz!", show_alert=True)
    except Exception as e:
        logging.exception(f"Obuna tekshirish xatosi: {e}")
        await call.answer("Xatolik yuz berdi.", show_alert=True)
    await call.answer()

# ---------- Reply menyu tugmalari ----------
@dp.message(F.text.in_(["🎬 Kinolar", "🔍 Qidirish", "🏆 Top kinolar", "🆕 Yangi kinolar", "🎁 Bonus", "💳 Obuna", "👤 Profil", "📂 Kategoriyalar", "⚙️ Admin panel"]))
async def reply_menu_handler(message: Message, state: FSMContext):
    try:
        user_id = message.from_user.id
        if is_user_banned(user_id):
            await message.answer("🚫 Siz botdan foydalanish huquqidan mahrum qilingansiz.")
            return
        if message.text == "⚙️ Admin panel":
            if user_id not in ADMIN_IDS:
                await message.answer("⛔️ Siz admin emassiz!")
                return
            await message.answer("⚙️ Admin panel:", reply_markup=admin_panel_keyboard())
            return
        if not await is_subscribed(user_id, bot):
            await message.answer("❗️ Iltimos, avval kanal(lar)ga obuna bo'ling.", reply_markup=subscribe_keyboard())
            return

        text = message.text
        if text == "🎬 Kinolar":
            movies = get_all_movies()
            if not movies:
                await message.answer("❌ Hozircha kinolar mavjud emas.")
                return
            await message.answer("🎬 Barcha kinolar ro'yxati:", reply_markup=movie_list_keyboard(movies, 0))

        elif text == "🔍 Qidirish":
            await message.answer("🔍 Qidirish uchun kino nomi yoki kodini yozing:")
            await state.set_state(AdminStates.waiting_search_query)

        elif text == "🏆 Top kinolar":
            movies = db_execute("SELECT code, title, rating FROM movies ORDER BY rating DESC LIMIT 10", fetchall=True)
            if not movies:
                await message.answer("📊 Hozircha reyting mavjud emas.")
                return
            ans = "🏆 <b>Eng mashhur kinolar:</b>\n\n"
            for i, (code, title, rating) in enumerate(movies, 1):
                ans += f"{i}. {title} (<code>{code}</code>) — ⭐️ {round(rating or 0, 1)}\n"
            await message.answer(ans, parse_mode="HTML")

        elif text == "🆕 Yangi kinolar":
            movies = db_execute("SELECT code, title FROM movies ORDER BY added_at DESC LIMIT 10", fetchall=True)
            if not movies:
                await message.answer("🆕 Hozircha yangi kinolar yo'q.")
                return
            ans = "🆕 <b>So'nggi qo'shilgan kinolar:</b>\n\n"
            for code, title in movies:
                ans += f"• {title} (<code>{code}</code>)\n"
            await message.answer(ans, parse_mode="HTML")

        elif text == "🎁 Bonus":
            await message.answer("🎁 Bonus menyusi:", reply_markup=bonus_keyboard())

        elif text == "💳 Obuna":
            await message.answer(
                "💳 <b>Obuna rejalarini tanlang:</b>\n\n"
                f"👑 VIP — {VIP_PRICE:,} so'm/oy\n".replace(",", " ") +
                f"💎 PREMIUM — {PREMIUM_PRICE:,} so'm/oy\n".replace(",", " "),
                parse_mode="HTML",
                reply_markup=subscription_plans_keyboard()
            )

        elif text == "👤 Profil":
            await message.answer(profile_text(user_id), parse_mode="HTML", reply_markup=profile_keyboard(user_id))

        elif text == "📂 Kategoriyalar":
            categories = get_categories()
            if not categories:
                await message.answer("📂 Hozircha kategoriyalar mavjud emas.")
                return
            await message.answer("📂 Kategoriyalar:", reply_markup=category_list_keyboard(categories))
    except Exception as e:
        logging.exception(f"Reply menyu xatosi: {e}")
        await message.answer("❌ Xatolik yuz berdi.")

def profile_text(user_id):
    """Profil matnini shakllantiruvchi yordamchi funksiya"""
    status = get_user_status(user_id)  # bu yerda avtomatik muddat tekshiruvi ham amalga oshadi
    user = db_execute(
        "SELECT username, first_name, total_requests, bonus_balance FROM users WHERE user_id=?",
        (user_id,), fetchone=True
    )
    if not user:
        return "❌ Ma'lumot topilmadi."
    username, first_name, total_requests, bonus = user
    status_emoji = {"oddiy": "👤", "vip": "👑", "premium": "💎", "admin": "⚙️"}.get(status, "👤")
    username_display = username or "yo\u02bcq"
    text = (
        f"👤 <b>Sizning profilingiz:</b>\n\n"
        f"Ism: {first_name}\n"
        f"Username: @{username_display}\n"
        f"Status: {status_emoji} {status}\n"
        f"So'rovlar soni: {total_requests}\n"
        f"Bonus balans: {bonus} ball"
    )
    if status in ("vip", "premium"):
        expires_at = get_subscription_expiry(user_id)
        if expires_at:
            expires_date = datetime.fromisoformat(expires_at).strftime("%d.%m.%Y")
            text += f"\n📅 Obuna tugash sanasi: {expires_date}"

    reward_claimed = db_execute("SELECT referral_reward_claimed FROM users WHERE user_id=?", (user_id,), fetchone=True)
    if reward_claimed and not reward_claimed[0]:
        ref_count = get_referral_count(user_id)
        remaining = max(0, REFERRAL_TARGET_COUNT - ref_count)
        plan_title = "👑 VIP" if REFERRAL_REWARD_PLAN == "vip" else "💎 PREMIUM"
        text += (
            f"\n\n🎁 Referal: {ref_count}/{REFERRAL_TARGET_COUNT} kishi taklif qildingiz\n"
            f"Yana {remaining} kishi taklif qilsangiz — {REFERRAL_REWARD_DAYS} kunlik {plan_title} bepul!"
        )
    return text

# ---------- Qidiruv so'rovi ----------
@dp.message(AdminStates.waiting_search_query)
async def handle_search_query(message: Message, state: FSMContext):
    try:
        query = message.text.strip()
        if len(query) < 2:
            await message.answer("❌ Kamida 2 ta belgi kiriting.")
            return
        results = search_movies(query)
        if not results:
            await message.answer("❌ Hech narsa topilmadi.")
        else:
            await message.answer(f"🔍 '{query}' bo'yicha natijalar:", reply_markup=movie_list_keyboard(results, 0))
    except Exception as e:
        logging.exception(f"Qidiruv xatosi: {e}")
        await message.answer("❌ Xatolik yuz berdi.")
    await state.clear()

# ---------- Kategoriya bo'yicha kinolar ----------
@dp.callback_query(F.data.startswith("category_"))
async def category_movies(call: CallbackQuery):
    try:
        category = call.data.split("_", 1)[1]
        movies = get_movies_by_category(category)
        if not movies:
            await call.answer("Bu kategoriyada kinolar yo'q", show_alert=True)
            return
        await call.message.edit_text(f"📂 {category} kategoriyasi:", reply_markup=movie_list_keyboard(movies, 0))
    except Exception as e:
        logging.exception(f"Kategoriya xatosi: {e}")
        await call.answer("Xatolik yuz berdi.", show_alert=True)
    await call.answer()

@dp.callback_query(F.data == "categories_menu")
async def categories_menu(call: CallbackQuery):
    try:
        categories = get_categories()
        if not categories:
            await call.message.edit_text("📂 Hozircha kategoriyalar mavjud emas.")
            return
        await call.message.edit_text("📂 Kategoriyalar:", reply_markup=category_list_keyboard(categories))
    except Exception as e:
        logging.exception(f"Kategoriya menyusi xatosi: {e}")
    await call.answer()

# ---------- Top va yangi kinolar (inline) ----------
@dp.callback_query(F.data == "top_movies")
async def top_movies_callback(call: CallbackQuery):
    try:
        movies = db_execute("SELECT code, title, rating FROM movies ORDER BY rating DESC LIMIT 10", fetchall=True)
        if not movies:
            await call.answer("Hozircha reyting mavjud emas.", show_alert=True)
            return
        ans = "🏆 <b>Eng mashhur kinolar:</b>\n\n"
        for i, (code, title, rating) in enumerate(movies, 1):
            ans += f"{i}. {title} (<code>{code}</code>) — ⭐️ {round(rating or 0, 1)}\n"
        await call.message.edit_text(ans, parse_mode="HTML", reply_markup=main_inline_keyboard(call.from_user.id))
    except Exception as e:
        logging.exception(f"Top kinolar xatosi: {e}")
    await call.answer()

@dp.callback_query(F.data == "new_movies")
async def new_movies_callback(call: CallbackQuery):
    try:
        movies = db_execute("SELECT code, title FROM movies ORDER BY added_at DESC LIMIT 10", fetchall=True)
        if not movies:
            await call.answer("Hozircha yangi kinolar yo'q.", show_alert=True)
            return
        ans = "🆕 <b>So'nggi qo'shilgan kinolar:</b>\n\n"
        for code, title in movies:
            ans += f"• {title} (<code>{code}</code>)\n"
        await call.message.edit_text(ans, parse_mode="HTML", reply_markup=main_inline_keyboard(call.from_user.id))
    except Exception as e:
        logging.exception(f"Yangi kinolar xatosi: {e}")
    await call.answer()

@dp.callback_query(F.data == "search_movie")
async def search_movie_callback(call: CallbackQuery, state: FSMContext):
    try:
        await call.message.edit_text("🔍 Qidirish uchun kino nomi yoki kodini yozing:")
        await state.set_state(AdminStates.waiting_search_query)
    except Exception as e:
        logging.exception(f"Qidiruv callback xatosi: {e}")
    await call.answer()

# ---------- Maxsus kinolar (VIP/Premium) ----------
@dp.callback_query(F.data == "special_movies")
async def special_movies(call: CallbackQuery):
    try:
        user_id = call.from_user.id
        status = get_user_status(user_id)
        if status not in ["vip", "premium", "admin"]:
            await call.answer("💎 Bu bo'lim faqat VIP va PREMIUM foydalanuvchilar uchun!", show_alert=True)
            return
        if status == "vip":
            movies = db_execute("SELECT code, title FROM movies WHERE is_vip=1", fetchall=True)
        elif status == "premium":
            movies = db_execute("SELECT code, title FROM movies WHERE is_premium=1", fetchall=True)
        else:
            movies = db_execute("SELECT code, title FROM movies WHERE is_vip=1 OR is_premium=1", fetchall=True)
        if not movies:
            await call.message.edit_text("💎 Hozircha maxsus kinolar mavjud emas.")
            return
        await call.message.edit_text("💎 Maxsus kinolar:", reply_markup=movie_list_keyboard(movies, 0))
    except Exception as e:
        logging.exception(f"Maxsus kinolar xatosi: {e}")
        await call.answer("Xatolik yuz berdi.", show_alert=True)
    await call.answer()

# ---------- Kino ko'rsatish (umumiy funksiya) ----------
async def send_movie(target_message: Message, user_id: int, code: str):
    """Kino kodini tekshirib, ruxsat bo'lsa videoni yuboradi"""
    if is_user_banned(user_id):
        await target_message.answer("🚫 Siz botdan foydalanish huquqidan mahrum qilingansiz.")
        return
    if not await is_subscribed(user_id, bot):
        await target_message.answer("❗️ Iltimos, avval kanal(lar)ga obuna bo'ling.", reply_markup=subscribe_keyboard())
        return
    if not check_spam(user_id):
        await target_message.answer("⏳ Juda ko'p so'rov yubordingiz. Bir daqiqa kuting.")
        return

    movie = get_movie(code)
    if not movie:
        series = get_series(code)
        if series:
            await send_series(target_message, user_id, code)
            return

        similar = find_similar_titles(code)
        if similar:
            kb = InlineKeyboardBuilder()
            for s_code, s_title in similar:
                kb.button(text=f"{s_title} ({s_code})", callback_data=f"movie_{s_code}")
            kb.adjust(1)
            await target_message.answer(
                "❌ Bunday kodli kino yoki serial topilmadi.\n\n🔍 Ehtimol shularni qidiryapsizmi?",
                reply_markup=kb.as_markup()
            )
        else:
            await target_message.answer("❌ Bunday kodli kino yoki serial topilmadi. Kodni tekshirib qayta yuboring.")
        return

    status = get_user_status(user_id)
    if movie[5] and status not in ["vip", "premium", "admin"]:
        await target_message.answer(
            f"🔒 <b>«{movie[1]}»</b> — bu kino faqat 👑 VIP obunachilar uchun ochiq!\n\n"
            f"👑 VIP obuna — atigi {VIP_PRICE:,} so'm/oy".replace(",", " ") + "\n"
            f"✅ Barcha VIP kinolarga to'liq kirish\n"
            f"✅ Cheklovlarsiz tomosha qiling\n\n"
            f"👇 Hozir sotib oling va kinoni darhol ko'ring:",
            parse_mode="HTML",
            reply_markup=upsell_keyboard("vip")
        )
        return
    if movie[6] and status not in ["premium", "admin"]:
        await target_message.answer(
            f"🔒 <b>«{movie[1]}»</b> — bu kino faqat 💎 PREMIUM obunachilar uchun ochiq!\n\n"
            f"💎 PREMIUM obuna — atigi {PREMIUM_PRICE:,} so'm/oy".replace(",", " ") + "\n"
            f"✅ Barcha VIP va PREMIUM kinolarga to'liq kirish\n"
            f"✅ Eng so'nggi va eksklyuziv kinolar\n\n"
            f"👇 Hozir sotib oling va kinoni darhol ko'ring:",
            parse_mode="HTML",
            reply_markup=upsell_keyboard("premium")
        )
        return

    caption = (
        f"🎬 <b>{movie[1]}</b>\n"
        f"📝 {movie[2]}\n"
        f"⭐️ Reyting: {round(movie[10] or 0, 1)} ({movie[11] or 0} ta baho)\n"
        f"👁 Ko'rishlar: {movie[9]}"
    )
    bot_info = await bot.get_me()
    share_link = f"https://t.me/{bot_info.username}?start=movie_{code}"
    poster_file_id = movie[12] if len(movie) > 12 else None
    keyboard = movie_action_keyboard(code, share_link)

    if poster_file_id:
        await target_message.answer_photo(photo=poster_file_id, caption=caption, parse_mode="HTML", reply_markup=keyboard)
        await target_message.answer_video(video=movie[3])
    else:
        await target_message.answer_video(video=movie[3], caption=caption, parse_mode="HTML", reply_markup=keyboard)

    increment_views(code)
    db_execute("UPDATE users SET total_requests = total_requests + 1 WHERE user_id=?", (user_id,), commit=True)
    log_action(user_id, "view_movie", f"code={code}")

async def send_series(target_message: Message, user_id: int, code: str):
    """Serial haqida ma'lumot va epizodlar ro'yxatini ko'rsatadi"""
    if is_user_banned(user_id):
        await target_message.answer("🚫 Siz botdan foydalanish huquqidan mahrum qilingansiz.")
        return
    if not await is_subscribed(user_id, bot):
        await target_message.answer("❗️ Iltimos, avval kanal(lar)ga obuna bo'ling.", reply_markup=subscribe_keyboard())
        return

    series = get_series(code)
    if not series:
        await target_message.answer("❌ Bunday kodli serial topilmadi.")
        return

    episode_numbers = get_episode_numbers(code)
    if not episode_numbers:
        await target_message.answer("⏳ Bu serialga hali qismlar yuklanmagan. Keyinroq qayta urinib ko'ring.")
        return

    free_episodes = series[7] or 0
    free_line = f"🆓 Bepul qismlar: 1—{free_episodes}" if free_episodes > 0 else "🔒 Barcha qismlar pullik"
    caption = (
        f"🎞 <b>{series[1]}</b>\n"
        f"📝 {series[2]}\n"
        f"📂 {series[4]}\n"
        f"{free_line}\n\n"
        f"👇 Qismni tanlang:"
    )
    keyboard = episode_list_keyboard(code, episode_numbers, free_episodes)
    poster_file_id = series[3]
    if poster_file_id:
        await target_message.answer_photo(photo=poster_file_id, caption=caption, parse_mode="HTML", reply_markup=keyboard)
    else:
        await target_message.answer(caption, parse_mode="HTML", reply_markup=keyboard)

@dp.callback_query(F.data.startswith("episode_"))
async def episode_detail(call: CallbackQuery):
    try:
        user_id = call.from_user.id
        parts = call.data.split("_")
        episode_number = int(parts[-1])
        series_code = "_".join(parts[1:-1])

        if is_user_banned(user_id):
            await call.answer("🚫 Siz bloklangansiz.", show_alert=True)
            return
        if not check_spam(user_id):
            await call.answer("⏳ Juda ko'p so'rov. Bir daqiqa kuting.", show_alert=True)
            return

        series = get_series(series_code)
        if not series:
            await call.answer("❌ Serial topilmadi.", show_alert=True)
            return

        free_episodes = series[7] or 0
        requires_premium = bool(series[6])
        status = get_user_status(user_id)
        locked = episode_number > free_episodes

        if locked:
            allowed_statuses = ["premium", "admin"] if requires_premium else ["vip", "premium", "admin"]
            if status not in allowed_statuses:
                plan = "premium" if requires_premium else "vip"
                plan_title = "💎 PREMIUM" if plan == "premium" else "👑 VIP"
                price = PREMIUM_PRICE if plan == "premium" else VIP_PRICE
                await call.message.answer(
                    f"🔒 <b>«{series[1]}»</b> {episode_number}-qismi faqat {plan_title} obunachilar uchun ochiq!\n\n"
                    f"{plan_title} obuna — atigi {price:,} so'm/oy".replace(",", " ") + "\n"
                    f"✅ Ushbu serialning barcha qismlariga to'liq kirish\n\n"
                    f"👇 Hozir sotib oling va darhol tomosha qiling:",
                    parse_mode="HTML",
                    reply_markup=upsell_keyboard(plan)
                )
                await call.answer()
                return

        episode = get_episode(series_code, episode_number)
        if not episode:
            await call.answer("❌ Bu qism topilmadi.", show_alert=True)
            return

        await call.message.answer_video(
            video=episode[3],
            caption=f"🎞 <b>{series[1]}</b> — {episode_number}-qism"
        , parse_mode="HTML")
        log_action(user_id, "view_episode", f"series={series_code} ep={episode_number}")
    except Exception as e:
        logging.exception(f"Epizod ko'rsatish xatosi: {e}")
        await call.answer("Xatolik yuz berdi.", show_alert=True)
    await call.answer()

@dp.callback_query(F.data.startswith("movie_"))
async def movie_detail(call: CallbackQuery):
    try:
        code = call.data.split("_", 1)[1]
        await send_movie(call.message, call.from_user.id, code)
    except Exception as e:
        logging.exception(f"Kino ko'rish xatosi: {e}")
        await call.answer("Xatolik yuz berdi.", show_alert=True)
    await call.answer()

@dp.callback_query(F.data == "movies_menu")
async def movies_menu(call: CallbackQuery):
    try:
        movies = get_all_movies()
        if not movies:
            await call.message.edit_text("❌ Hozircha kinolar mavjud emas.")
            return
        await call.message.edit_text("🎬 Barcha kinolar ro'yxati:", reply_markup=movie_list_keyboard(movies, 0))
    except Exception as e:
        logging.exception(f"Kinolar menyusi xatosi: {e}")
    await call.answer()

@dp.callback_query(F.data.startswith("movies_page_"))
async def movies_page(call: CallbackQuery):
    try:
        page = int(call.data.split("_")[-1])
        movies = get_all_movies()
        await call.message.edit_reply_markup(reply_markup=movie_list_keyboard(movies, page))
    except Exception as e:
        logging.exception(f"Sahifalash xatosi: {e}")
    await call.answer()

# ---------- Baholash ----------
@dp.callback_query(F.data.startswith("rate_val_"))
async def rate_value(call: CallbackQuery):
    try:
        _, _, code, rating = call.data.split("_")
        rating = int(rating)
        update_rating(code, rating)
        await call.message.edit_text(f"✅ Siz {rating}⭐️ baho berdingiz. Rahmat!")
    except Exception as e:
        logging.exception(f"Baholash qiymati xatosi: {e}")
        await call.answer("Xatolik yuz berdi.", show_alert=True)
    await call.answer()

@dp.callback_query(F.data.startswith("rate_"))
async def rate_movie(call: CallbackQuery):
    try:
        code = call.data.split("_", 1)[1]
        await call.message.edit_text(f"⭐️ \"{code}\" kodli kinoga baho bering:", reply_markup=rate_keyboard(code))
    except Exception as e:
        logging.exception(f"Baholash boshlash xatosi: {e}")
    await call.answer()

# ---------- Bonus tizimi ----------
@dp.callback_query(F.data == "bonus_menu")
async def bonus_menu(call: CallbackQuery):
    try:
        await call.message.edit_text("🎁 Bonus menyusi:", reply_markup=bonus_keyboard())
    except Exception as e:
        logging.exception(f"Bonus menyusi xatosi: {e}")
    await call.answer()

@dp.callback_query(F.data == "daily_bonus")
async def daily_bonus(call: CallbackQuery):
    try:
        user_id = call.from_user.id
        if check_daily_bonus(user_id):
            bonus = random.randint(5, 20)
            update_bonus(user_id, bonus)
            set_daily_bonus(user_id)
            await call.message.edit_text(f"✅ Kunlik bonusingiz: +{bonus} ball!\n💰 Jami balans: {get_user_bonus(user_id)} ball")
            log_action(user_id, "daily_bonus", f"bonus={bonus}")
        else:
            await call.answer("❌ Siz bugun bonusni allaqachon olgansiz! Ertaga qayting.", show_alert=True)
    except Exception as e:
        logging.exception(f"Kunlik bonus xatosi: {e}")
        await call.answer("Xatolik yuz berdi.", show_alert=True)
    await call.answer()

@dp.callback_query(F.data == "bonus_balance")
async def bonus_balance(call: CallbackQuery):
    try:
        bal = get_user_bonus(call.from_user.id)
        await call.message.edit_text(f"💰 Sizning bonus balansingiz: {bal} ball.")
    except Exception as e:
        logging.exception(f"Bonus balans xatosi: {e}")
    await call.answer()

@dp.callback_query(F.data == "referral_link")
async def referral_link(call: CallbackQuery):
    try:
        user_id = call.from_user.id
        bot_info = await bot.get_me()
        link = f"https://t.me/{bot_info.username}?start={user_id}"
        await call.message.edit_text(
            f"🔗 Sizning shaxsiy referal havolangiz:\n{link}\n\n"
            f"👥 Har bir taklif qilingan foydalanuvchi uchun 10 ball bonus olasiz."
        )
    except Exception as e:
        logging.exception(f"Referal havola xatosi: {e}")
    await call.answer()

# ---------- Profil ----------
def profile_keyboard(user_id):
    builder = InlineKeyboardBuilder()
    row = db_execute("SELECT notify_new_movies FROM users WHERE user_id=?", (user_id,), fetchone=True)
    notify_on = row[0] if row else 1
    notify_text = "🔔 Bildirishnomalar: Yoqilgan" if notify_on else "🔕 Bildirishnomalar: O'chirilgan"
    builder.button(text=notify_text, callback_data="toggle_notify")
    if get_user_status(user_id) in ("vip", "premium"):
        builder.button(text="❌ Obunani bekor qilish", callback_data="cancel_subscription")
    builder.button(text="🔙 Orqaga", callback_data="back_main")
    builder.adjust(1)
    return builder.as_markup()

@dp.callback_query(F.data == "profile")
async def profile_callback(call: CallbackQuery):
    try:
        await call.message.edit_text(profile_text(call.from_user.id), parse_mode="HTML", reply_markup=profile_keyboard(call.from_user.id))
    except Exception as e:
        logging.exception(f"Profil xatosi: {e}")
    await call.answer()

@dp.callback_query(F.data == "toggle_notify")
async def toggle_notify(call: CallbackQuery):
    try:
        user_id = call.from_user.id
        row = db_execute("SELECT notify_new_movies FROM users WHERE user_id=?", (user_id,), fetchone=True)
        current = row[0] if row else 1
        new_value = 0 if current else 1
        db_execute("UPDATE users SET notify_new_movies=? WHERE user_id=?", (new_value, user_id), commit=True)
        await call.message.edit_text(profile_text(user_id), parse_mode="HTML", reply_markup=profile_keyboard(user_id))
        await call.answer("🔔 Yoqildi!" if new_value else "🔕 O'chirildi!")
    except Exception as e:
        logging.exception(f"Bildirishnoma almashtirish xatosi: {e}")
        await call.answer("Xatolik yuz berdi.", show_alert=True)

@dp.callback_query(F.data == "cancel_subscription")
async def cancel_subscription_confirm(call: CallbackQuery):
    try:
        kb = InlineKeyboardBuilder()
        kb.button(text="✅ Ha, bekor qilaman", callback_data="cancel_subscription_yes")
        kb.button(text="⬅️ Yo'q, qaytaman", callback_data="profile")
        kb.adjust(1)
        await call.message.edit_text(
            "⚠️ Obunangizni bekor qilsangiz, VIP/PREMIUM imkoniyatlaringiz darhol yo'qoladi "
            "va to'langan summa qaytarilmaydi. Rostdan ham bekor qilmoqchimisiz?",
            reply_markup=kb.as_markup()
        )
    except Exception as e:
        logging.exception(f"Obunani bekor qilish so'rovi xatosi: {e}")
    await call.answer()

@dp.callback_query(F.data == "cancel_subscription_yes")
async def cancel_subscription_execute(call: CallbackQuery):
    try:
        user_id = call.from_user.id
        set_user_status(user_id, "oddiy")
        db_execute("UPDATE users SET subscription_expires_at=NULL WHERE user_id=?", (user_id,), commit=True)
        log_action(user_id, "cancel_subscription", "user_initiated")
        await revoke_vip_channel_access(user_id)
        await call.message.edit_text("✅ Obunangiz bekor qilindi.", reply_markup=main_inline_keyboard(user_id))
    except Exception as e:
        logging.exception(f"Obunani bekor qilish xatosi: {e}")
        await call.answer("Xatolik yuz berdi.", show_alert=True)
    await call.answer()

# ---------- Obuna (VIP/PREMIUM) sotib olish ----------
@dp.callback_query(F.data == "subscription_menu")
async def subscription_menu(call: CallbackQuery, state: FSMContext):
    try:
        await state.clear()
        vip_line = f"👑 VIP — {VIP_PRICE:,} so'm/oy".replace(",", " ")
        premium_line = f"💎 PREMIUM — {PREMIUM_PRICE:,} so'm/oy".replace(",", " ")
        await call.message.edit_text(
            f"💳 <b>Obuna rejalarini tanlang:</b>\n\n{vip_line}\n{premium_line}\n\n"
            f"👑 VIP: maxsus VIP kinolarga kirish\n"
            f"💎 PREMIUM: barcha maxsus kinolarga to'liq kirish",
            parse_mode="HTML",
            reply_markup=subscription_plans_keyboard()
        )
    except Exception as e:
        logging.exception(f"Obuna menyusi xatosi: {e}")
    await call.answer()

@dp.callback_query(F.data.in_(["buy_vip", "buy_premium"]))
async def buy_plan(call: CallbackQuery, state: FSMContext):
    try:
        user_id = call.from_user.id
        if has_pending_payment(user_id):
            await call.answer("⏳ Sizda hali ko'rib chiqilayotgan to'lov so'rovi bor. Admin javobini kuting.", show_alert=True)
            return
        plan = "vip" if call.data == "buy_vip" else "premium"
        amount = VIP_PRICE if plan == "vip" else PREMIUM_PRICE
        plan_title = "👑 VIP" if plan == "vip" else "💎 PREMIUM"
        await state.update_data(plan=plan, amount=amount)
        await state.set_state(AdminStates.waiting_payment_screenshot)
        await call.message.edit_text(
            f"{plan_title} obunasi — {amount:,} so'm/oy".replace(",", " ") + "\n\n"
            f"💳 Quyidagi kartaga to'lov qiling:\n"
            f"<code>{CARD_NUMBER}</code>\n"
            f"👤 Karta egasi: {CARD_HOLDER}\n\n"
            f"✅ To'lovni amalga oshirgach, to'lov chekining <b>screenshot (rasm)</b>ini shu yerga yuboring.\n"
            f"Admin tekshirib, obunangizni faollashtiradi.",
            parse_mode="HTML",
            reply_markup=payment_cancel_keyboard()
        )
    except Exception as e:
        logging.exception(f"Obuna tanlash xatosi: {e}")
    await call.answer()

@dp.message(AdminStates.waiting_payment_screenshot, F.photo)
async def receive_payment_screenshot(message: Message, state: FSMContext):
    try:
        data = await state.get_data()
        plan = data.get("plan")
        amount = data.get("amount")
        if not plan:
            await message.answer("❌ Xatolik: reja tanlanmagan. Qaytadan /start bosing.")
            await state.clear()
            return

        screenshot_file_id = message.photo[-1].file_id
        user_id = message.from_user.id
        payment_id = create_payment(user_id, plan, amount, screenshot_file_id)
        log_action(user_id, "payment_request", f"plan={plan} amount={amount} payment_id={payment_id}")

        await message.answer(
            "✅ To'lov so'rovingiz qabul qilindi!\n"
            "⏳ Admin tekshirib, tez orada obunangizni faollashtiradi. Iltimos, kuting."
        )

        plan_title = "👑 VIP" if plan == "vip" else "💎 PREMIUM"
        username = message.from_user.username
        username_display = username or "yo\u02bcq"
        caption = (
            f"💳 <b>Yangi to'lov so'rovi</b> (#{payment_id})\n\n"
            f"👤 Foydalanuvchi: {message.from_user.first_name} (@{username_display})\n"
            f"🆔 ID: <code>{user_id}</code>\n"
            f"📦 Reja: {plan_title}\n"
            f"💰 Summasi: {amount:,} so'm".replace(",", " ")
        )
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_photo(
                    admin_id, screenshot_file_id, caption=caption,
                    parse_mode="HTML", reply_markup=payment_admin_keyboard(payment_id)
                )
            except Exception:
                pass

        await state.clear()
    except Exception as e:
        logging.exception(f"To'lov skrinshoti xatosi: {e}")
        await message.answer("❌ Xatolik yuz berdi. Qaytadan urinib ko'ring.")
        await state.clear()

@dp.message(AdminStates.waiting_payment_screenshot)
async def receive_payment_screenshot_invalid(message: Message):
    """Agar foydalanuvchi rasm o'rniga boshqa narsa yuborsa"""
    await message.answer("❗️ Iltimos, to'lov chekining rasm (screenshot) ko'rinishida yuboring.")

@dp.callback_query(F.data.startswith("approve_pay_"))
async def approve_payment(call: CallbackQuery):
    try:
        if call.from_user.id not in ADMIN_IDS:
            await call.answer("⛔️ Ruxsat yo'q", show_alert=True)
            return
        payment_id = int(call.data.split("_")[-1])
        payment = get_payment(payment_id)
        if not payment:
            await call.answer("❌ To'lov topilmadi.", show_alert=True)
            return
        if payment[5] != "pending":
            await call.answer("Bu so'rov allaqachon ko'rib chiqilgan.", show_alert=True)
            return

        _, user_id, plan, amount, _, _, _, _, _ = payment
        expires_at = activate_subscription(user_id, plan)
        set_payment_status(payment_id, "approved", call.from_user.id)
        log_action(call.from_user.id, "approve_payment", f"payment_id={payment_id} user={user_id} plan={plan}")

        expires_date = datetime.fromisoformat(expires_at).strftime("%d.%m.%Y")
        plan_title = "👑 VIP" if plan == "vip" else "💎 PREMIUM"
        try:
            await bot.send_message(
                user_id,
                f"✅ To'lovingiz tasdiqlandi!\n{plan_title} obunangiz faollashtirildi.\n"
                f"📅 Amal qilish muddati: {expires_date} gacha"
            )
        except Exception:
            pass
        await grant_vip_channel_access(user_id)

        await call.message.edit_caption(caption=call.message.caption + "\n\n✅ <b>TASDIQLANDI</b>", parse_mode="HTML")
    except Exception as e:
        logging.exception(f"To'lov tasdiqlash xatosi: {e}")
        await call.answer("Xatolik yuz berdi.", show_alert=True)
    await call.answer()

@dp.callback_query(F.data.startswith("reject_pay_"))
async def reject_payment(call: CallbackQuery):
    try:
        if call.from_user.id not in ADMIN_IDS:
            await call.answer("⛔️ Ruxsat yo'q", show_alert=True)
            return
        payment_id = int(call.data.split("_")[-1])
        payment = get_payment(payment_id)
        if not payment:
            await call.answer("❌ To'lov topilmadi.", show_alert=True)
            return
        if payment[5] != "pending":
            await call.answer("Bu so'rov allaqachon ko'rib chiqilgan.", show_alert=True)
            return

        user_id = payment[1]
        set_payment_status(payment_id, "rejected", call.from_user.id)
        log_action(call.from_user.id, "reject_payment", f"payment_id={payment_id} user={user_id}")

        try:
            await bot.send_message(
                user_id,
                "❌ To'lovingiz tasdiqlanmadi.\n"
                "Iltimos, to'lov chekini tekshirib qaytadan urinib ko'ring yoki admin bilan bog'laning."
            )
        except Exception:
            pass

        await call.message.edit_caption(caption=call.message.caption + "\n\n❌ <b>RAD ETILDI</b>", parse_mode="HTML")
    except Exception as e:
        logging.exception(f"To'lov rad etish xatosi: {e}")
        await call.answer("Xatolik yuz berdi.", show_alert=True)
    await call.answer()

@dp.callback_query(F.data == "admin_payments")
async def admin_payments(call: CallbackQuery):
    try:
        if call.from_user.id not in ADMIN_IDS:
            await call.answer("⛔️ Ruxsat yo'q", show_alert=True)
            return
        pending = get_pending_payments()
        if not pending:
            await call.message.edit_text("💳 Hozircha kutilayotgan to'lov so'rovlari yo'q.", reply_markup=admin_panel_keyboard())
            return
        text = "💳 <b>Kutilayotgan to'lovlar:</b>\n\n"
        for pid, user_id, plan, amount, requested_at in pending:
            plan_title = "👑 VIP" if plan == "vip" else "💎 PREMIUM"
            text += f"#{pid} — ID: <code>{user_id}</code> — {plan_title} — {amount:,} so'm".replace(",", " ") + "\n"
        text += "\nHar bir so'rov skrinshoti bilan alohida xabar qilib yuborilgan — o'sha yerdan tasdiqlang/rad eting."
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=admin_panel_keyboard())
    except Exception as e:
        logging.exception(f"Admin to'lovlar ro'yxati xatosi: {e}")
    await call.answer()

# ---------- Admin panel ----------
@dp.callback_query(F.data == "admin_panel")
async def admin_panel(call: CallbackQuery):
    try:
        if call.from_user.id not in ADMIN_IDS:
            await call.answer("⛔️ Siz admin emassiz!", show_alert=True)
            return
        await call.message.edit_text("⚙️ Admin panel:", reply_markup=admin_panel_keyboard())
    except Exception as e:
        logging.exception(f"Admin panel xatosi: {e}")
    await call.answer()

# ---------- Kino qo'shish (bosqichma-bosqich) ----------
@dp.callback_query(F.data == "admin_add_movie")
async def admin_add_movie_start(call: CallbackQuery, state: FSMContext):
    try:
        if call.from_user.id not in ADMIN_IDS:
            await call.answer("⛔️ Ruxsat yo'q", show_alert=True)
            return
        suggested_code = get_next_suggested_code()
        await call.message.edit_text(
            f"➕ Yangi kino qo'shish.\n\n"
            f"1️⃣ Kodni kiriting (taklif etilgan: <code>{suggested_code}</code>, xohlasangiz shu raqamni yuboring):",
            parse_mode="HTML"
        )
        await state.set_state(AdminStates.waiting_movie_code)
    except Exception as e:
        logging.exception(f"Admin qo'shish boshlash xatosi: {e}")
    await call.answer()

@dp.message(AdminStates.waiting_movie_code)
async def admin_add_movie_code(message: Message, state: FSMContext):
    try:
        code = message.text.strip()
        if get_movie(code):
            await message.answer("❌ Bu kod allaqachon mavjud. Boshqa kod kiriting.")
            return
        await state.update_data(code=code)
        await message.answer("2️⃣ Kino nomini kiriting:")
        await state.set_state(AdminStates.waiting_movie_title)
    except Exception as e:
        logging.exception(f"Admin kino kodi xatosi: {e}")
        await message.answer("Xatolik yuz berdi.")

@dp.message(AdminStates.waiting_movie_title)
async def admin_add_movie_title(message: Message, state: FSMContext):
    try:
        await state.update_data(title=message.text.strip())
        await message.answer("3️⃣ Kino tavsifini kiriting:")
        await state.set_state(AdminStates.waiting_movie_desc)
    except Exception as e:
        logging.exception(f"Admin kino nomi xatosi: {e}")
        await message.answer("Xatolik yuz berdi.")

@dp.message(AdminStates.waiting_movie_desc)
async def admin_add_movie_desc(message: Message, state: FSMContext):
    try:
        await state.update_data(desc=message.text.strip())
        await message.answer(
            "4️⃣ Kino uchun poster (afisha) rasmini yuboring.\n"
            "Agar poster bo'lmasa — /skip deb yozing."
        )
        await state.set_state(AdminStates.waiting_movie_poster)
    except Exception as e:
        logging.exception(f"Admin kino tavsifi xatosi: {e}")
        await message.answer("Xatolik yuz berdi.")

@dp.message(AdminStates.waiting_movie_poster, F.photo)
async def admin_add_movie_poster(message: Message, state: FSMContext):
    try:
        await state.update_data(poster_file_id=message.photo[-1].file_id)
        await message.answer("5️⃣ Endi kino faylini (video) yuboring:")
        await state.set_state(AdminStates.waiting_movie_file)
    except Exception as e:
        logging.exception(f"Admin poster xatosi: {e}")
        await message.answer("Xatolik yuz berdi.")

@dp.message(AdminStates.waiting_movie_poster, Command("skip"))
async def admin_skip_movie_poster(message: Message, state: FSMContext):
    try:
        await state.update_data(poster_file_id=None)
        await message.answer("5️⃣ Endi kino faylini (video) yuboring:")
        await state.set_state(AdminStates.waiting_movie_file)
    except Exception as e:
        logging.exception(f"Poster o'tkazib yuborish xatosi: {e}")

@dp.message(AdminStates.waiting_movie_poster)
async def admin_add_movie_poster_invalid(message: Message):
    await message.answer("❗️ Iltimos, rasm yuboring yoki /skip deb yozing.")

@dp.message(AdminStates.waiting_movie_file, F.video)
async def admin_add_movie_file(message: Message, state: FSMContext):
    try:
        await state.update_data(file_id=message.video.file_id)
        await message.answer("6️⃣ Kategoriyasini kiriting (masalan: Jangari):")
        await state.set_state(AdminStates.waiting_movie_category)
    except Exception as e:
        logging.exception(f"Admin kino fayl xatosi: {e}")
        await message.answer("Xatolik yuz berdi.")

@dp.message(AdminStates.waiting_movie_file)
async def admin_add_movie_file_invalid(message: Message):
    """Agar admin video o'rniga boshqa narsa yuborsa"""
    await message.answer("❗️ Iltimos, video fayl yuboring.")

@dp.message(AdminStates.waiting_movie_category)
async def admin_add_movie_category(message: Message, state: FSMContext):
    try:
        category = message.text.strip()
        add_category(category, "📁")
        await state.update_data(category=category)
        await message.answer("6️⃣ VIP kinomi? (ha / yo'q):")
        await state.set_state(AdminStates.waiting_movie_vip)
    except Exception as e:
        logging.exception(f"Admin kategoriya xatosi: {e}")
        await message.answer("Xatolik yuz berdi.")

@dp.message(AdminStates.waiting_movie_vip)
async def admin_add_movie_vip(message: Message, state: FSMContext):
    try:
        vip = message.text.lower() in ["ha", "yes", "1", "true"]
        await state.update_data(vip=vip)
        await message.answer("7️⃣ PREMIUM kinomi? (ha / yo'q):")
        await state.set_state(AdminStates.waiting_movie_premium)
    except Exception as e:
        logging.exception(f"Admin VIP xatosi: {e}")
        await message.answer("Xatolik yuz berdi.")

@dp.message(AdminStates.waiting_movie_premium)
async def admin_add_movie_premium(message: Message, state: FSMContext):
    try:
        premium = message.text.lower() in ["ha", "yes", "1", "true"]
        data = await state.get_data()
        poster_file_id = data.get("poster_file_id")
        add_movie(
            data["code"], data["title"], data["desc"], data["file_id"],
            data["category"], data["vip"], premium, message.from_user.id, poster_file_id
        )
        vip_display = "Ha" if data["vip"] else "Yo\u02bcq"
        premium_display = "Ha" if premium else "Yo\u02bcq"
        bot_info = await bot.get_me()
        share_link = f"https://t.me/{bot_info.username}?start=movie_{data['code']}"
        await message.answer(
            f"✅ Kino muvaffaqiyatli qo'shildi!\n\n"
            f"🔑 Kod: {data['code']}\n"
            f"🎬 Nomi: {data['title']}\n"
            f"📂 Kategoriya: {data['category']}\n"
            f"👑 VIP: {vip_display}\n"
            f"💎 PREMIUM: {premium_display}\n\n"
            f"🔗 <b>Instagram/bio uchun havola</b> (bosilganda kino to'g'ridan-to'g'ri ochiladi):\n"
            f"<code>{share_link}</code>",
            parse_mode="HTML"
        )
        log_action(message.from_user.id, "add_movie", f"code={data['code']}")

        # Agar majburiy e'lon kanali sozlangan bo'lsa — yangi kino haqida avtomatik post qilinadi
        if POST_CHANNEL:
            announce = (
                f"🆕 <b>Yangi kino qo'shildi!</b>\n\n"
                f"🎬 {data['title']}\n"
                f"📝 {data['desc']}\n"
                f"📂 {data['category']}\n\n"
                f"🔑 Kod: <code>{data['code']}</code>"
            )
            announce_kb = InlineKeyboardBuilder()
            announce_kb.button(text="🎬 Ko'rish", url=share_link)
            try:
                if poster_file_id:
                    await bot.send_photo(POST_CHANNEL, poster_file_id, caption=announce, parse_mode="HTML", reply_markup=announce_kb.as_markup())
                else:
                    await bot.send_message(POST_CHANNEL, announce, parse_mode="HTML", reply_markup=announce_kb.as_markup())
            except Exception as e:
                logging.exception(f"Kanalga avtomatik post xatosi: {e}")
                await message.answer("⚠️ Kanalga avtomatik post yuborilmadi (kanal sozlamalarini tekshiring).")

        await post_to_catalog(data['code'], data['title'], share_link, is_series=False)
        asyncio.create_task(notify_users_new_content(data['title'], data['code'], share_link, is_series=False))
        await state.clear()
    except Exception as e:
        logging.exception(f"Admin kino qo'shish yakunlash xatosi: {e}")
        await message.answer("Xatolik yuz berdi.")
        await state.clear()

# ---------- Serial qo'shish ----------
@dp.callback_query(F.data == "admin_add_series")
async def admin_add_series_start(call: CallbackQuery, state: FSMContext):
    try:
        if call.from_user.id not in ADMIN_IDS:
            await call.answer("⛔️ Ruxsat yo'q", show_alert=True)
            return
        await call.message.edit_text("🎞 Yangi serial qo'shish.\n\n1️⃣ Serial kodini kiriting (masalan: S101):")
        await state.set_state(AdminStates.waiting_series_code)
    except Exception as e:
        logging.exception(f"Serial qo'shish boshlash xatosi: {e}")
    await call.answer()

@dp.message(AdminStates.waiting_series_code)
async def admin_add_series_code(message: Message, state: FSMContext):
    try:
        code = message.text.strip()
        if get_series(code) or get_movie(code):
            await message.answer("❌ Bu kod allaqachon mavjud (kino yoki serial sifatida). Boshqa kod kiriting.")
            return
        await state.update_data(code=code)
        await message.answer("2️⃣ Serial nomini kiriting:")
        await state.set_state(AdminStates.waiting_series_title)
    except Exception as e:
        logging.exception(f"Serial kodi xatosi: {e}")
        await message.answer("Xatolik yuz berdi.")

@dp.message(AdminStates.waiting_series_title)
async def admin_add_series_title(message: Message, state: FSMContext):
    try:
        await state.update_data(title=message.text.strip())
        await message.answer("3️⃣ Serial tavsifini kiriting:")
        await state.set_state(AdminStates.waiting_series_desc)
    except Exception as e:
        logging.exception(f"Serial nomi xatosi: {e}")
        await message.answer("Xatolik yuz berdi.")

@dp.message(AdminStates.waiting_series_desc)
async def admin_add_series_desc(message: Message, state: FSMContext):
    try:
        await state.update_data(desc=message.text.strip())
        await message.answer("4️⃣ Serial uchun poster rasm yuboring (yoki /skip):")
        await state.set_state(AdminStates.waiting_series_poster)
    except Exception as e:
        logging.exception(f"Serial tavsifi xatosi: {e}")
        await message.answer("Xatolik yuz berdi.")

@dp.message(AdminStates.waiting_series_poster, F.photo)
async def admin_add_series_poster(message: Message, state: FSMContext):
    try:
        await state.update_data(poster_file_id=message.photo[-1].file_id)
        await message.answer("5️⃣ Kategoriyasini kiriting (masalan: Turk seriali):")
        await state.set_state(AdminStates.waiting_series_category)
    except Exception as e:
        logging.exception(f"Serial poster xatosi: {e}")
        await message.answer("Xatolik yuz berdi.")

@dp.message(AdminStates.waiting_series_poster, Command("skip"))
async def admin_skip_series_poster(message: Message, state: FSMContext):
    try:
        await state.update_data(poster_file_id=None)
        await message.answer("5️⃣ Kategoriyasini kiriting (masalan: Turk seriali):")
        await state.set_state(AdminStates.waiting_series_category)
    except Exception as e:
        logging.exception(f"Serial poster o'tkazish xatosi: {e}")

@dp.message(AdminStates.waiting_series_poster)
async def admin_add_series_poster_invalid(message: Message):
    await message.answer("❗️ Iltimos, rasm yuboring yoki /skip deb yozing.")

@dp.message(AdminStates.waiting_series_category)
async def admin_add_series_category(message: Message, state: FSMContext):
    try:
        category = message.text.strip()
        add_category(category, "📁")
        await state.update_data(category=category)
        await message.answer("6️⃣ Pullik qismlar VIP darajasidami yoki PREMIUM darajasidami? (vip/premium):")
        await state.set_state(AdminStates.waiting_series_vip)
    except Exception as e:
        logging.exception(f"Serial kategoriyasi xatosi: {e}")
        await message.answer("Xatolik yuz berdi.")

@dp.message(AdminStates.waiting_series_vip)
async def admin_add_series_vip(message: Message, state: FSMContext):
    try:
        answer = message.text.strip().lower()
        is_premium_series = answer == "premium"
        await state.update_data(is_vip=True, is_premium=is_premium_series)
        await message.answer(
            "7️⃣ Necha qismgacha BEPUL bo'lsin? (masalan: 3)\n"
            "Shu raqamdan keyingi qismlar avtomatik pullik bo'ladi."
        )
        await state.set_state(AdminStates.waiting_series_free_count)
    except Exception as e:
        logging.exception(f"Serial VIP/PREMIUM tanlash xatosi: {e}")
        await message.answer("Xatolik yuz berdi.")

@dp.message(AdminStates.waiting_series_free_count)
async def admin_add_series_free_count(message: Message, state: FSMContext):
    try:
        text = message.text.strip()
        if not text.isdigit():
            await message.answer("❗️ Iltimos, raqam kiriting (masalan: 3).")
            return
        free_count = int(text)
        data = await state.get_data()
        add_series(
            data["code"], data["title"], data["desc"], data["category"],
            data["is_vip"], data["is_premium"], free_count,
            message.from_user.id, data.get("poster_file_id")
        )
        bot_info = await bot.get_me()
        share_link = f"https://t.me/{bot_info.username}?start=series_{data['code']}"
        plan_title = "💎 PREMIUM" if data["is_premium"] else "👑 VIP"
        await message.answer(
            f"✅ Serial muvaffaqiyatli qo'shildi!\n\n"
            f"🔑 Kod: {data['code']}\n"
            f"🎞 Nomi: {data['title']}\n"
            f"🆓 Bepul qismlar: 1—{free_count}\n"
            f"🔒 {free_count}-dan keyingi qismlar: {plan_title}\n\n"
            f"🔗 Havola: <code>{share_link}</code>\n\n"
            f"Endi \"➕ Epizod qo'shish\" orqali qismlarni yuklang.",
            parse_mode="HTML"
        )
        log_action(message.from_user.id, "add_series", f"code={data['code']}")
        await post_to_catalog(data["code"], data["title"], share_link, is_series=True)
        asyncio.create_task(notify_users_new_content(data["title"], data["code"], share_link, is_series=True))
        await state.clear()
    except Exception as e:
        logging.exception(f"Serial yakunlash xatosi: {e}")
        await message.answer("Xatolik yuz berdi.")
        await state.clear()

# ---------- Epizod qo'shish ----------
@dp.callback_query(F.data == "admin_add_episode")
async def admin_add_episode_start(call: CallbackQuery, state: FSMContext):
    try:
        if call.from_user.id not in ADMIN_IDS:
            await call.answer("⛔️ Ruxsat yo'q", show_alert=True)
            return
        all_series = get_all_series()
        if not all_series:
            await call.message.edit_text("❌ Hozircha hech qanday serial qo'shilmagan. Avval serial qo'shing.", reply_markup=admin_panel_keyboard())
            return
        series_list = "\n".join([f"• {title} — {code}" for code, title in all_series])
        await call.message.edit_text(f"➕ Epizod qo'shish.\n\nMavjud seriallar:\n{series_list}\n\n1️⃣ Serial kodini kiriting:")
        await state.set_state(AdminStates.waiting_episode_series_code)
    except Exception as e:
        logging.exception(f"Epizod qo'shish boshlash xatosi: {e}")
    await call.answer()

@dp.message(AdminStates.waiting_episode_series_code)
async def admin_add_episode_series_code(message: Message, state: FSMContext):
    try:
        code = message.text.strip()
        series = get_series(code)
        if not series:
            await message.answer("❌ Bunday kodli serial topilmadi. Qaytadan kiriting.")
            return
        await state.update_data(series_code=code)
        existing = get_episode_numbers(code)
        existing_text = f"Mavjud qismlar: {', '.join(map(str, existing))}" if existing else "Hali qism yuklanmagan."
        await message.answer(f"2️⃣ Qism raqamini kiriting (masalan: 1).\n{existing_text}")
        await state.set_state(AdminStates.waiting_episode_number)
    except Exception as e:
        logging.exception(f"Epizod serial kodi xatosi: {e}")
        await message.answer("Xatolik yuz berdi.")

@dp.message(AdminStates.waiting_episode_number)
async def admin_add_episode_number(message: Message, state: FSMContext):
    try:
        text = message.text.strip()
        if not text.isdigit():
            await message.answer("❗️ Iltimos, raqam kiriting (masalan: 1).")
            return
        await state.update_data(episode_number=int(text))
        await message.answer("3️⃣ Endi shu qismning video faylini yuboring:")
        await state.set_state(AdminStates.waiting_episode_video)
    except Exception as e:
        logging.exception(f"Epizod raqami xatosi: {e}")
        await message.answer("Xatolik yuz berdi.")

@dp.message(AdminStates.waiting_episode_video, F.video)
async def admin_add_episode_video(message: Message, state: FSMContext):
    try:
        data = await state.get_data()
        add_episode(data["series_code"], data["episode_number"], message.video.file_id)
        await message.answer(
            f"✅ {data['episode_number']}-qism qo'shildi!\n\n"
            f"Yana qism qo'shishni xohlasangiz, qayta \"➕ Epizod qo'shish\" tugmasini bosing."
        )
        log_action(message.from_user.id, "add_episode", f"series={data['series_code']} ep={data['episode_number']}")
        await state.clear()
    except Exception as e:
        logging.exception(f"Epizod video xatosi: {e}")
        await message.answer("Xatolik yuz berdi.")
        await state.clear()

@dp.message(AdminStates.waiting_episode_video)
async def admin_add_episode_video_invalid(message: Message):
    await message.answer("❗️ Iltimos, video fayl yuboring.")

# ---------- Kino o'chirish ----------
@dp.callback_query(F.data == "admin_delete_movie")
async def admin_delete_movie_start(call: CallbackQuery, state: FSMContext):
    try:
        if call.from_user.id not in ADMIN_IDS:
            await call.answer("⛔️ Ruxsat yo'q", show_alert=True)
            return
        await call.message.edit_text("➖ O'chirish uchun kino kodini kiriting:")
        await state.set_state(AdminStates.waiting_delete_code)
    except Exception as e:
        logging.exception(f"Admin o'chirish boshlash xatosi: {e}")
    await call.answer()

@dp.message(AdminStates.waiting_delete_code)
async def admin_delete_movie_code(message: Message, state: FSMContext):
    try:
        code = message.text.strip()
        if not get_movie(code):
            await message.answer("❌ Bunday kodli kino topilmadi.")
            return
        delete_movie(code)
        await message.answer(f"✅ Kino o'chirildi: {code}")
        log_action(message.from_user.id, "delete_movie", f"code={code}")
        await state.clear()
    except Exception as e:
        logging.exception(f"Admin o'chirish xatosi: {e}")
        await message.answer("Xatolik yuz berdi.")

# ---------- Oxirgi harakatlar (tezkor panel) ----------
@dp.callback_query(F.data == "admin_recent")
async def admin_recent(call: CallbackQuery):
    try:
        if call.from_user.id not in ADMIN_IDS:
            await call.answer("⛔️ Ruxsat yo'q", show_alert=True)
            return
        recent_users = db_execute(
            "SELECT first_name, username, registered_at FROM users ORDER BY registered_at DESC LIMIT 5",
            fetchall=True
        ) or []
        recent_payments = db_execute(
            "SELECT id, user_id, plan, amount, status FROM payments ORDER BY requested_at DESC LIMIT 5",
            fetchall=True
        ) or []

        text = "🕐 <b>Oxirgi 5 ta yangi foydalanuvchi:</b>\n"
        if recent_users:
            for first_name, username, registered_at in recent_users:
                username_display = username or "yo\u02bcq"
                text += f"• {first_name} (@{username_display})\n"
        else:
            text += "— yo'q —\n"

        text += "\n💳 <b>Oxirgi 5 ta to'lov so'rovi:</b>\n"
        if recent_payments:
            status_emoji = {"pending": "⏳", "approved": "✅", "rejected": "❌"}
            for pid, user_id, plan, amount, status in recent_payments:
                plan_title = "👑 VIP" if plan == "vip" else "💎 PREMIUM"
                text += f"{status_emoji.get(status, '•')} #{pid} — ID {user_id} — {plan_title} — {amount:,} so'm".replace(",", " ") + "\n"
        else:
            text += "— yo'q —\n"

        await call.message.edit_text(text, parse_mode="HTML", reply_markup=admin_panel_keyboard())
    except Exception as e:
        logging.exception(f"Oxirgi harakatlar xatosi: {e}")
    await call.answer()

# ---------- Kino tahrirlash ----------
@dp.callback_query(F.data == "admin_edit_movie")
async def admin_edit_movie_start(call: CallbackQuery, state: FSMContext):
    try:
        if call.from_user.id not in ADMIN_IDS:
            await call.answer("⛔️ Ruxsat yo'q", show_alert=True)
            return
        await call.message.edit_text("✏️ Tahrirlash uchun kino kodini kiriting:")
        await state.set_state(AdminStates.waiting_edit_code)
    except Exception as e:
        logging.exception(f"Kino tahrirlash boshlash xatosi: {e}")
    await call.answer()

@dp.message(AdminStates.waiting_edit_code)
async def admin_edit_movie_code(message: Message, state: FSMContext):
    try:
        code = message.text.strip()
        movie = get_movie(code)
        if not movie:
            await message.answer("❌ Bunday kodli kino topilmadi. Qaytadan kiriting.")
            return
        await state.update_data(edit_code=code)
        await message.answer(
            f"Joriy ma'lumot:\n🎬 Nomi: {movie[1]}\n📝 Tavsifi: {movie[2]}\n\n"
            f"Nimani o'zgartirmoqchisiz? (<b>nom</b> yoki <b>tavsif</b> deb yozing)",
            parse_mode="HTML"
        )
        await state.set_state(AdminStates.waiting_edit_field)
    except Exception as e:
        logging.exception(f"Kino tahrirlash kodi xatosi: {e}")
        await message.answer("Xatolik yuz berdi.")

@dp.message(AdminStates.waiting_edit_field)
async def admin_edit_movie_field(message: Message, state: FSMContext):
    try:
        field = message.text.strip().lower()
        if field not in ("nom", "tavsif"):
            await message.answer("❗️ Iltimos, faqat 'nom' yoki 'tavsif' deb yozing.")
            return
        await state.update_data(edit_field=field)
        await message.answer(f"Yangi {field}ni kiriting:")
        await state.set_state(AdminStates.waiting_edit_value)
    except Exception as e:
        logging.exception(f"Kino tahrirlash maydoni xatosi: {e}")
        await message.answer("Xatolik yuz berdi.")

@dp.message(AdminStates.waiting_edit_value)
async def admin_edit_movie_value(message: Message, state: FSMContext):
    try:
        data = await state.get_data()
        code = data["edit_code"]
        field = data["edit_field"]
        new_value = message.text.strip()
        column = "title" if field == "nom" else "description"
        db_execute(f"UPDATE movies SET {column}=? WHERE code=?", (new_value, code), commit=True)
        await message.answer(f"✅ Kino {field}i muvaffaqiyatli yangilandi!")
        log_action(message.from_user.id, "edit_movie", f"code={code} field={field}")
        await state.clear()
    except Exception as e:
        logging.exception(f"Kino tahrirlash saqlash xatosi: {e}")
        await message.answer("Xatolik yuz berdi.")
        await state.clear()

# ---------- Statistika ----------
@dp.callback_query(F.data == "admin_stats")
async def admin_stats(call: CallbackQuery):
    try:
        if call.from_user.id not in ADMIN_IDS:
            await call.answer("⛔️ Ruxsat yo'q", show_alert=True)
            return
        total_users = db_execute("SELECT COUNT(*) FROM users", fetchone=True)[0]
        total_movies = db_execute("SELECT COUNT(*) FROM movies", fetchone=True)[0]
        total_views = db_execute("SELECT SUM(views) FROM movies", fetchone=True)[0] or 0
        vip_count = db_execute("SELECT COUNT(*) FROM users WHERE status='vip'", fetchone=True)[0]
        premium_count = db_execute("SELECT COUNT(*) FROM users WHERE status='premium'", fetchone=True)[0]
        banned_count = db_execute("SELECT COUNT(*) FROM users WHERE is_banned=1", fetchone=True)[0]
        text = (
            f"📊 <b>Bot statistikasi:</b>\n\n"
            f"👥 Foydalanuvchilar: {total_users}\n"
            f"🚫 Bloklangan: {banned_count}\n"
            f"🎬 Kinolar: {total_movies}\n"
            f"👁 Ko'rishlar: {total_views}\n"
            f"👑 VIP: {vip_count}\n"
            f"💎 PREMIUM: {premium_count}"
        )
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=admin_panel_keyboard())
    except Exception as e:
        logging.exception(f"Admin statistika xatosi: {e}")
    await call.answer()

# ---------- Broadcast (hammaga xabar) ----------
@dp.callback_query(F.data == "admin_broadcast")
async def admin_broadcast_start(call: CallbackQuery, state: FSMContext):
    try:
        if call.from_user.id not in ADMIN_IDS:
            await call.answer("⛔️ Ruxsat yo'q", show_alert=True)
            return
        await call.message.edit_text("📢 Broadcast xabar matnini yozing:")
        await state.set_state(AdminStates.waiting_broadcast_text)
    except Exception as e:
        logging.exception(f"Admin broadcast boshlash xatosi: {e}")
    await call.answer()

@dp.message(AdminStates.waiting_broadcast_text)
async def admin_broadcast_text(message: Message, state: FSMContext):
    try:
        if not message.text:
            await message.answer("❗️ Iltimos, matn ko'rinishida xabar yuboring.")
            return
        await state.update_data(broadcast_text=message.text)
        await message.answer(f"📢 Quyidagi xabar yuborilsinmi?\n\n{message.text}\n\n(ha / yo'q)")
        await state.set_state(AdminStates.waiting_broadcast_confirm)
    except Exception as e:
        logging.exception(f"Admin broadcast matn xatosi: {e}")
        await message.answer("Xatolik yuz berdi.")

@dp.message(AdminStates.waiting_broadcast_confirm)
async def admin_broadcast_confirm(message: Message, state: FSMContext):
    try:
        if message.text.lower() not in ["ha", "yes"]:
            await message.answer("❌ Bekor qilindi.")
            await state.clear()
            return
        data = await state.get_data()
        text = data["broadcast_text"]
        users = db_execute("SELECT user_id FROM users WHERE is_banned=0", fetchall=True)
        count = 0
        for (user_id,) in users:
            try:
                await bot.send_message(user_id, text)
                count += 1
            except Exception:
                pass
        await message.answer(f"✅ Xabar {count} ta foydalanuvchiga yuborildi.")
        log_action(message.from_user.id, "broadcast", f"sent to {count}")
        await state.clear()
    except Exception as e:
        logging.exception(f"Admin broadcast tasdiqlash xatosi: {e}")
        await message.answer("Xatolik yuz berdi.")
        await state.clear()

# ---------- VIP/PREMIUM status berish ----------
@dp.callback_query(F.data == "admin_give_status")
async def admin_give_status_start(call: CallbackQuery, state: FSMContext):
    try:
        if call.from_user.id not in ADMIN_IDS:
            await call.answer("⛔️ Ruxsat yo'q", show_alert=True)
            return
        await call.message.edit_text("👑 Status berish.\nFoydalanuvchi ID sini kiriting:")
        await state.set_state(AdminStates.waiting_give_status_user)
    except Exception as e:
        logging.exception(f"Admin status berish boshlash xatosi: {e}")
    await call.answer()

@dp.message(AdminStates.waiting_give_status_user)
async def admin_give_status_user(message: Message, state: FSMContext):
    try:
        try:
            user_id = int(message.text.strip())
        except ValueError:
            await message.answer("❌ Noto'g'ri ID. Faqat raqam kiriting.")
            return
        if not db_execute("SELECT user_id FROM users WHERE user_id=?", (user_id,), fetchone=True):
            await message.answer("❌ Bunday foydalanuvchi topilmadi.")
            return
        await state.update_data(give_user_id=user_id)
        await message.answer("Qaysi status berilsin? (vip / premium / admin):")
        await state.set_state(AdminStates.waiting_give_status_type)
    except Exception as e:
        logging.exception(f"Admin status berish ID xatosi: {e}")
        await message.answer("Xatolik yuz berdi.")

@dp.message(AdminStates.waiting_give_status_type)
async def admin_give_status_type(message: Message, state: FSMContext):
    try:
        status = message.text.lower().strip()
        if status not in ["vip", "premium", "admin"]:
            await message.answer("❌ Noto'g'ri status. Faqat vip, premium yoki admin kiriting.")
            return
        data = await state.get_data()
        user_id = data["give_user_id"]
        set_user_status(user_id, status)
        await message.answer(f"✅ Foydalanuvchi {user_id} ga {status} statusi berildi.")
        log_action(message.from_user.id, "give_status", f"user={user_id} status={status}")
        await state.clear()
    except Exception as e:
        logging.exception(f"Admin status berish turi xatosi: {e}")
        await message.answer("Xatolik yuz berdi.")
        await state.clear()

# ---------- Status olib tashlash ----------
@dp.callback_query(F.data == "admin_remove_status")
async def admin_remove_status_start(call: CallbackQuery, state: FSMContext):
    try:
        if call.from_user.id not in ADMIN_IDS:
            await call.answer("⛔️ Ruxsat yo'q", show_alert=True)
            return
        await call.message.edit_text("👑 Status olib tashlash.\nFoydalanuvchi ID sini kiriting:")
        await state.set_state(AdminStates.waiting_remove_status_user)
    except Exception as e:
        logging.exception(f"Admin status olib tashlash boshlash xatosi: {e}")
    await call.answer()

@dp.message(AdminStates.waiting_remove_status_user)
async def admin_remove_status_user(message: Message, state: FSMContext):
    try:
        try:
            user_id = int(message.text.strip())
        except ValueError:
            await message.answer("❌ Noto'g'ri ID.")
            return
        if not db_execute("SELECT user_id FROM users WHERE user_id=?", (user_id,), fetchone=True):
            await message.answer("❌ Foydalanuvchi topilmadi.")
            return
        set_user_status(user_id, "oddiy")
        await message.answer(f"✅ Foydalanuvchi {user_id} statusi 'oddiy' ga o'zgartirildi.")
        log_action(message.from_user.id, "remove_status", f"user={user_id}")
        await state.clear()
    except Exception as e:
        logging.exception(f"Admin status olib tashlash xatosi: {e}")
        await message.answer("Xatolik yuz berdi.")
        await state.clear()

# ---------- Kanal sozlash ----------
@dp.callback_query(F.data == "admin_channels")
async def admin_channels(call: CallbackQuery):
    try:
        if call.from_user.id not in ADMIN_IDS:
            await call.answer("⛔️ Ruxsat yo'q", show_alert=True)
            return
        text = "📡 Hozirgi majburiy kanallar:\n" + "\n".join(CHANNELS) if CHANNELS else "📡 Hozircha kanal biriktirilmagan."
        await call.message.edit_text(text, reply_markup=channel_settings_keyboard())
    except Exception as e:
        logging.exception(f"Admin kanal sozlash xatosi: {e}")
    await call.answer()

@dp.callback_query(F.data == "admin_add_channel")
async def admin_add_channel_start(call: CallbackQuery, state: FSMContext):
    try:
        if call.from_user.id not in ADMIN_IDS:
            await call.answer("⛔️ Ruxsat yo'q", show_alert=True)
            return
        await call.message.edit_text("➕ Yangi kanal username'ini kiriting (masalan: @my_channel):")
        await state.set_state(AdminStates.waiting_add_channel)
    except Exception as e:
        logging.exception(f"Admin kanal qo'shish boshlash xatosi: {e}")
    await call.answer()

@dp.message(AdminStates.waiting_add_channel)
async def admin_add_channel(message: Message, state: FSMContext):
    try:
        channel = message.text.strip()
        if not channel.startswith("@"):
            channel = "@" + channel
        if channel in CHANNELS:
            await message.answer("❌ Bu kanal allaqachon ro'yxatda mavjud.")
            return
        CHANNELS.append(channel)
        save_channels(CHANNELS)
        await message.answer(f"✅ Kanal qo'shildi: {channel}")
        log_action(message.from_user.id, "add_channel", channel)
        await state.clear()
    except Exception as e:
        logging.exception(f"Admin kanal qo'shish xatosi: {e}")
        await message.answer("Xatolik yuz berdi.")
        await state.clear()

@dp.callback_query(F.data == "admin_remove_channel")
async def admin_remove_channel_start(call: CallbackQuery, state: FSMContext):
    try:
        if call.from_user.id not in ADMIN_IDS:
            await call.answer("⛔️ Ruxsat yo'q", show_alert=True)
            return
        await call.message.edit_text("➖ O'chirish uchun kanal username'ini kiriting (masalan: @my_channel):")
        await state.set_state(AdminStates.waiting_remove_channel)
    except Exception as e:
        logging.exception(f"Admin kanal o'chirish boshlash xatosi: {e}")
    await call.answer()

@dp.message(AdminStates.waiting_remove_channel)
async def admin_remove_channel(message: Message, state: FSMContext):
    try:
        channel = message.text.strip()
        if not channel.startswith("@"):
            channel = "@" + channel
        if channel not in CHANNELS:
            await message.answer("❌ Bunday kanal ro'yxatda yo'q.")
            return
        CHANNELS.remove(channel)
        save_channels(CHANNELS)
        await message.answer(f"✅ Kanal o'chirildi: {channel}")
        log_action(message.from_user.id, "remove_channel", channel)
        await state.clear()
    except Exception as e:
        logging.exception(f"Admin kanal o'chirish xatosi: {e}")
        await message.answer("Xatolik yuz berdi.")
        await state.clear()

# ---------- Foydalanuvchilar ro'yxati ----------
@dp.callback_query(F.data == "admin_users")
async def admin_users(call: CallbackQuery):
    try:
        if call.from_user.id not in ADMIN_IDS:
            await call.answer("⛔️ Ruxsat yo'q", show_alert=True)
            return
        users = db_execute("SELECT user_id, username, first_name, status, is_banned FROM users ORDER BY registered_at DESC LIMIT 30", fetchall=True)
        if not users:
            await call.message.edit_text("📋 Foydalanuvchilar yo'q.", reply_markup=admin_panel_keyboard())
            return
        text = "📋 <b>Foydalanuvchilar (oxirgi 30 tasi):</b>\n\n"
        for u in users:
            ban_icon = "🚫" if u[4] else "✅"
            text += f"{ban_icon} ID: <code>{u[0]}</code> — @{u[1] or 'no_username'} — {u[3]}\n"
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=admin_panel_keyboard())
    except Exception as e:
        logging.exception(f"Admin foydalanuvchilar xatosi: {e}")
    await call.answer()

# ---------- Ban qilish ----------
@dp.callback_query(F.data == "admin_ban")
async def admin_ban_start(call: CallbackQuery, state: FSMContext):
    try:
        if call.from_user.id not in ADMIN_IDS:
            await call.answer("⛔️ Ruxsat yo'q", show_alert=True)
            return
        await call.message.edit_text("🚫 Ban qilish uchun foydalanuvchi ID sini kiriting:")
        await state.set_state(AdminStates.waiting_ban_user)
    except Exception as e:
        logging.exception(f"Admin ban boshlash xatosi: {e}")
    await call.answer()

@dp.message(AdminStates.waiting_ban_user)
async def admin_ban_user(message: Message, state: FSMContext):
    try:
        try:
            user_id = int(message.text.strip())
        except ValueError:
            await message.answer("❌ Noto'g'ri ID.")
            return
        if not db_execute("SELECT user_id FROM users WHERE user_id=?", (user_id,), fetchone=True):
            await message.answer("❌ Foydalanuvchi topilmadi.")
            return
        set_user_ban(user_id, True)
        await message.answer(f"✅ Foydalanuvchi {user_id} banlandi.")
        log_action(message.from_user.id, "ban_user", f"user={user_id}")
        await state.clear()
    except Exception as e:
        logging.exception(f"Admin ban xatosi: {e}")
        await message.answer("Xatolik yuz berdi.")
        await state.clear()

# ---------- Orqaga qaytish ----------
@dp.callback_query(F.data == "back_main")
async def back_main(call: CallbackQuery):
    try:
        await call.message.edit_text("📋 Asosiy menyu:", reply_markup=main_inline_keyboard(call.from_user.id))
    except Exception as e:
        logging.exception(f"Orqaga qaytish xatosi: {e}")
    await call.answer()

# ---------- Foydalanuvchi kino kodini yozganda (asosiy oqim) ----------
@dp.message(F.text)
async def handle_movie_code(message: Message, state: FSMContext):
    try:
        code = message.text.strip()
        await send_movie(message, message.from_user.id, code)
    except Exception as e:
        logging.exception(f"Kino kodi xatosi: {e}")
        await message.answer("❌ Xatolik yuz berdi. Iltimos, keyinroq urinib ko'ring.")

# ============================
# 9. XATOLIKLARNI GLOBAL USHLASH
# ============================
@dp.errors()
async def errors_handler(update, exception):
    logging.exception(f"Kutilmagan xatolik: {exception}")
    return True

# ============================
# 10. RENDER UCHUN HEALTH-CHECK SERVER
# ============================
async def health_check(request):
    """Render bu manzilga so'rov yuboradi — bot tirikligini bildiradi"""
    return web.Response(text="🤖 KODLI KINO BOT ishlayapti!")

REGISTER_PAGE_HTML = """<!DOCTYPE html>
<html lang="uz">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<title>FACE_SCAN // ACCESS TERMINAL</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
  * { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
  html, body {
    margin:0; padding:0; height:100%;
    background:#000;
    color:#00ff8c;
    font-family: 'Courier New', ui-monospace, monospace;
    text-align:center; overflow:hidden;
  }
  #matrixBg {
    position:fixed; inset:0; width:100%; height:100%; z-index:0; opacity:0.35;
  }
  #app {
    position:relative; z-index:2; display:flex; flex-direction:column; align-items:center;
    padding:18px 16px; min-height:100%;
  }
  .glitch {
    font-size:22px; font-weight:700; letter-spacing:2px; text-transform:uppercase;
    color:#00ff8c; text-shadow: 0 0 6px #00ff8c, 0 0 14px rgba(0,255,140,0.6);
    margin:4px 0 4px; position:relative;
  }
  .subtitle { font-size:11px; color:#0af0c0; opacity:0.75; letter-spacing:3px; margin-bottom:16px; }

  #stage {
    position:relative; width:100%; max-width:340px; aspect-ratio: 3/4;
    border-radius:14px; overflow:hidden; background:#000;
    border: 1px solid rgba(0,255,140,0.5);
    box-shadow: 0 0 0 1px rgba(0,255,140,0.15), 0 0 30px rgba(0,255,140,0.25) inset, 0 20px 50px rgba(0,0,0,0.7);
  }
  video {
    position:absolute; inset:0; width:100%; height:100%; object-fit:cover; display:block;
    background:#000; z-index:1;
  }
  #gridOverlay {
    position:absolute; inset:0; z-index:2; pointer-events:none;
    background-image:
      linear-gradient(rgba(0,255,140,0.08) 1px, transparent 1px),
      linear-gradient(90deg, rgba(0,255,140,0.08) 1px, transparent 1px);
    background-size: 24px 24px;
    mix-blend-mode: screen;
  }
  #scanline {
    position:absolute; left:4%; right:4%; height:2px; top:10%; z-index:3;
    background: linear-gradient(90deg, transparent, #00ff8c, #baffea, #00ff8c, transparent);
    box-shadow: 0 0 12px 4px rgba(0,255,140,0.9);
    animation: scan 1.8s linear infinite; opacity:0;
  }
  @keyframes scan {
    0%   { top:8%; opacity:0; }
    8%   { opacity:1; }
    50%  { top:86%; opacity:1; }
    92%  { opacity:1; }
    100% { top:8%; opacity:0; }
  }
  .corner { position:absolute; width:30px; height:30px; border:2px solid #00ff8c; opacity:0.95; z-index:3; filter: drop-shadow(0 0 5px rgba(0,255,140,0.8)); }
  .tl { top:10px; left:10px; border-right:none; border-bottom:none; }
  .tr { top:10px; right:10px; border-left:none; border-bottom:none; }
  .bl { bottom:10px; left:10px; border-right:none; border-top:none; }
  .br { bottom:10px; right:10px; border-left:none; border-top:none; }

  #ovalGuide {
    position:absolute; left:50%; top:46%; transform:translate(-50%,-50%); z-index:3;
    width:60%; height:54%; border:1.5px dashed rgba(0,255,140,0.55); border-radius:50%;
  }
  #hudTop {
    position:absolute; top:10px; left:50%; transform:translateX(-50%); z-index:4;
    font-size:10px; letter-spacing:1.5px; background:rgba(0,0,0,0.55); padding:4px 10px;
    border:1px solid rgba(0,255,140,0.4); border-radius:3px; display:flex; align-items:center; gap:6px;
  }
  #hudTop span.dot { width:7px; height:7px; border-radius:50%; background:#ff3860; animation:blink 0.9s infinite; }
  @keyframes blink { 0%,100%{opacity:1;} 50%{opacity:0.15;} }

  #hudBottomLeft, #hudBottomRight {
    position:absolute; bottom:8px; z-index:4; font-size:9px; letter-spacing:1px; color:#00ff8c; opacity:0.85;
  }
  #hudBottomLeft { left:12px; text-align:left; }
  #hudBottomRight { right:12px; text-align:right; }

  #terminalLog {
    width:100%; max-width:340px; margin-top:14px; text-align:left;
    background:rgba(0,20,10,0.55); border:1px solid rgba(0,255,140,0.3); border-radius:8px;
    padding:8px 10px; font-size:11px; line-height:1.5; height:76px; overflow:hidden;
    color:#7dffce;
  }
  #terminalLog div { opacity:0; animation: fadeIn 0.25s forwards; }
  @keyframes fadeIn { to { opacity:1; } }

  #progressWrap { width:100%; max-width:340px; margin-top:12px; background:rgba(0,255,140,0.08); border-radius:20px; height:8px; overflow:hidden; border:1px solid rgba(0,255,140,0.25); }
  #progressBar { height:100%; width:0%; background: linear-gradient(90deg,#00ff8c,#baffea); box-shadow: 0 0 10px rgba(0,255,140,0.8); transition: width 0.12s linear; }
  #percent { font-size:12px; color:#7dffce; margin-top:6px; letter-spacing:1px; }

  #status { margin-top:10px; font-size:13px; min-height:20px; color:#baffea; letter-spacing:0.4px; }
  #successMark { display:none; font-size:46px; margin-top:4px; color:#00ff8c; text-shadow:0 0 20px #00ff8c; animation:pop 0.4s ease-out; }
  @keyframes pop { 0%{ transform:scale(0);} 80%{ transform:scale(1.15);} 100%{ transform:scale(1);} }

  #fallbackMsg { display:none; margin-top:14px; font-size:12px; color:#ff9d5c; max-width:320px; line-height:1.5; }
</style>
</head>
<body>
<canvas id="matrixBg"></canvas>
<div id="app">
  <div class="glitch">FACE SCAN // ACCESS</div>
  <div class="subtitle">SECURE IDENTITY VERIFICATION</div>

  <div id="stage">
    <video id="video" autoplay playsinline muted></video>
    <div id="gridOverlay"></div>
    <div id="ovalGuide"></div>
    <div class="corner tl"></div>
    <div class="corner tr"></div>
    <div class="corner bl"></div>
    <div class="corner br"></div>
    <div id="scanline"></div>
    <div id="hudTop"><span class="dot"></span>REC · LIVE</div>
    <div id="hudBottomLeft">CAM:01</div>
    <div id="hudBottomRight" id="clock">00:00:00</div>
  </div>

  <div id="progressWrap"><div id="progressBar"></div></div>
  <div id="percent"></div>
  <div id="status">&gt; kameraga ulanmoqda...</div>
  <div id="successMark">✅</div>
  <div id="terminalLog"></div>
  <div id="fallbackMsg"></div>
</div>

<script>
// ---------- Matrix fon animatsiyasi ----------
(function () {
  const canvas = document.getElementById('matrixBg');
  const ctx = canvas.getContext('2d');
  function resize() { canvas.width = window.innerWidth; canvas.height = window.innerHeight; }
  resize();
  window.addEventListener('resize', resize);
  const chars = "01アイウエオカキクケコサシスセソ";
  const fontSize = 14;
  let columns, drops;
  function setup() {
    columns = Math.floor(canvas.width / fontSize);
    drops = new Array(columns).fill(1);
  }
  setup();
  function draw() {
    ctx.fillStyle = 'rgba(0,0,0,0.08)';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = '#00ff8c';
    ctx.font = fontSize + 'px monospace';
    for (let i = 0; i < drops.length; i++) {
      const text = chars[Math.floor(Math.random() * chars.length)];
      ctx.fillText(text, i * fontSize, drops[i] * fontSize);
      if (drops[i] * fontSize > canvas.height && Math.random() > 0.975) drops[i] = 0;
      drops[i]++;
    }
  }
  setInterval(draw, 55);
})();

// ---------- Terminal jurnal ----------
const logEl = document.getElementById('terminalLog');
function logLine(text) {
  const div = document.createElement('div');
  div.textContent = "> " + text;
  logEl.appendChild(div);
  while (logEl.children.length > 4) logEl.removeChild(logEl.firstChild);
  logEl.scrollTop = logEl.scrollHeight;
}

// ---------- Soat ----------
setInterval(() => {
  const d = new Date();
  document.getElementById('clock').textContent =
    String(d.getHours()).padStart(2,'0') + ':' + String(d.getMinutes()).padStart(2,'0') + ':' + String(d.getSeconds()).padStart(2,'0');
}, 1000);

// ---------- Telegram WebApp (mavjud bo'lsa) yoki token (tashqi brauzer) ----------
const tg = (window.Telegram && window.Telegram.WebApp) ? window.Telegram.WebApp : null;
if (tg) { try { tg.expand(); } catch(e) {} }

const urlParams = new URLSearchParams(window.location.search);
const regToken = urlParams.get('token') || '';

const video = document.getElementById('video');
const scanline = document.getElementById('scanline');
const statusEl = document.getElementById('status');
const progressBar = document.getElementById('progressBar');
const percentEl = document.getElementById('percent');
const successMark = document.getElementById('successMark');
const fallbackMsg = document.getElementById('fallbackMsg');

const RECORD_MS = 4000;
let stream = null;
let mediaRecorder = null;
let chunks = [];

async function startCamera() {
  logLine("init camera module...");
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: "user", width: { ideal: 480 }, height: { ideal: 640 } },
      audio: false
    });
    video.srcObject = stream;
    video.setAttribute('autoplay', '');
    video.setAttribute('muted', '');
    video.muted = true;

    video.onloadedmetadata = async () => {
      try { await video.play(); } catch (e) { logLine("play() warn: " + e.message); }
      const waitReady = setInterval(() => {
        if (video.videoWidth > 0 && video.videoHeight > 0) {
          clearInterval(waitReady);
          logLine("camera OK " + video.videoWidth + "x" + video.videoHeight);
          beginScan();
        }
      }, 100);
      // 4 soniyadan keyin hali ham 0x0 bo'lsa — foydalanuvchini ogohlantiramiz
      setTimeout(() => {
        if (video.videoWidth === 0) {
          fallbackMsg.style.display = 'block';
          fallbackMsg.textContent = "⚠️ Kamera tasviri ko'rinmayapti. Sahifani boshqa brauzerda (Chrome/Safari) to'g'ridan-to'g'ri oching yoki qayta urinib ko'ring.";
        }
      }, 4000);
    };
  } catch (e) {
    statusEl.textContent = "❌ Kameraga ruxsat berilmadi";
    logLine("ERROR: " + e.message);
    fallbackMsg.style.display = 'block';
    fallbackMsg.textContent = "Kameradan foydalanish uchun brauzer sozlamalaridan ruxsat bering, so'ng sahifani qayta yuklang.";
  }
}

function beginScan() {
  statusEl.textContent = "> yuz aniqlanmoqda...";
  logLine("face lock acquired");
  logLine("recording started");
  scanline.style.opacity = '1';

  let mimeType = 'video/webm;codecs=vp8,opus';
  if (!MediaRecorder.isTypeSupported(mimeType)) mimeType = 'video/webm';
  try { mediaRecorder = new MediaRecorder(stream, { mimeType }); }
  catch (e) { mediaRecorder = new MediaRecorder(stream); }

  chunks = [];
  mediaRecorder.ondataavailable = (e) => { if (e.data && e.data.size > 0) chunks.push(e.data); };
  mediaRecorder.onstop = onRecordingStop;
  mediaRecorder.start();

  const startTime = Date.now();
  const timer = setInterval(() => {
    const elapsed = Date.now() - startTime;
    const pct = Math.min(100, Math.round((elapsed / RECORD_MS) * 100));
    progressBar.style.width = pct + '%';
    percentEl.textContent = pct + '%';
    if (elapsed >= RECORD_MS) {
      clearInterval(timer);
      if (mediaRecorder.state !== 'inactive') mediaRecorder.stop();
    }
  }, 80);
}

async function onRecordingStop() {
  scanline.style.opacity = '0';
  statusEl.textContent = "> qayta ishlanmoqda...";
  logLine("encoding stream...");
  if (stream) stream.getTracks().forEach(t => t.stop());

  const blob = new Blob(chunks, { type: 'video/webm' });
  const reader = new FileReader();
  reader.onloadend = async () => { await uploadVideo(reader.result); };
  reader.readAsDataURL(blob);
}

async function uploadVideo(base64data) {
  statusEl.textContent = "> yuborilmoqda...";
  logLine("uploading to server...");
  try {
    const resp = await fetch('/submit-scan', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        initData: tg ? tg.initData : '',
        token: regToken,
        video: base64data
      })
    });
    const result = await resp.json();
    if (result.ok) {
      progressBar.style.width = '100%';
      percentEl.textContent = '100%';
      statusEl.textContent = "✅ muvaffaqiyatli yuborildi";
      logLine("SUCCESS: admin review pending");
      successMark.style.display = 'block';
      setTimeout(() => { if (tg) { try { tg.close(); } catch(e){} } else { statusEl.textContent += " — sahifani yopishingiz mumkin."; } }, 1800);
    } else {
      statusEl.textContent = "❌ xatolik: " + (result.error || "noma'lum");
      logLine("ERROR: " + (result.error || "unknown"));
    }
  } catch (e) {
    statusEl.textContent = "❌ tarmoq xatosi";
    logLine("NETWORK ERROR: " + e.message);
  }
}

startCamera();
</script>
</body>
</html>"""

async def register_page(request):
    """Kamera skaner sahifasi — Telegram ichida ham, oddiy brauzerda ham ishlaydi"""
    return web.Response(text=REGISTER_PAGE_HTML, content_type="text/html")

async def submit_scan(request):
    """Skanerlash videosini tekshirib, adminlarga yuboradi.
    Ikki turdagi identifikatsiyani qabul qiladi:
    1) Telegram WebApp initData (bot ichida ochilganda)
    2) Bir martalik token (tashqi brauzerda ochilganda)"""
    try:
        data = await request.json()
        init_data = data.get("initData", "")
        token = data.get("token", "")
        video_data_url = data.get("video", "")

        user_id = None
        username_display = "yo\u02bcq"

        user_info = validate_webapp_init_data(init_data) if init_data else None
        if user_info:
            user_id = user_info.get("id")
            username_display = user_info.get("username") or "yo\u02bcq"
        elif token:
            token_info = consume_registration_token(token)
            if not token_info:
                return web.json_response({"ok": False, "error": "Havola muddati tugagan yoki noto'g'ri"}, status=403)
            user_id = token_info["id"]
            username_display = token_info.get("username") or "yo\u02bcq"
        else:
            return web.json_response({"ok": False, "error": "Identifikatsiya topilmadi"}, status=403)

        if not video_data_url or "," not in video_data_url:
            return web.json_response({"ok": False, "error": "Video topilmadi"}, status=400)

        video_bytes = base64.b64decode(video_data_url.split(",", 1)[1])
        db_execute("UPDATE users SET registration_photo=? WHERE user_id=?", ("webapp_video_scan", user_id), commit=True)
        log_action(user_id, "registration_submitted", "video scan")

        full_name_row = db_execute("SELECT full_name FROM users WHERE user_id=?", (user_id,), fetchone=True)
        full_name = full_name_row[0] if full_name_row and full_name_row[0] else "—"

        kb = InlineKeyboardBuilder()
        kb.button(text="✅ Tasdiqlash", callback_data=f"approve_reg_{user_id}")
        kb.button(text="❌ Rad etish", callback_data=f"reject_reg_{user_id}")
        kb.adjust(2)
        video_file = BufferedInputFile(video_bytes, filename="scan.webm")
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_video(
                    admin_id, video_file,
                    caption=(
                        f"🆕 <b>Yangi ro'yxatdan o'tish so'rovi (kamera skaneri)</b>\n\n"
                        f"👤 F.I.Sh: {full_name}\n"
                        f"🆔 ID: <code>{user_id}</code>\n"
                        f"📱 Username: @{username_display}"
                    ),
                    parse_mode="HTML",
                    reply_markup=kb.as_markup()
                )
            except Exception as e:
                logging.exception(f"Admin(ID={admin_id})ga video yuborish xatosi: {e}")

        try:
            await bot.send_message(
                user_id,
                "✅ Arizangiz qabul qilindi!\n⏳ Admin tasdiqlashini kuting — tasdiqlangach sizga xabar boradi."
            )
        except Exception:
            pass

        return web.json_response({"ok": True})
    except Exception as e:
        logging.exception(f"Skanerlash videosini qabul qilish xatosi: {e}")
        return web.json_response({"ok": False, "error": "server xatosi"}, status=500)

async def start_web_server():
    """Render Web Service uchun majburiy port ochiladi"""
    app = web.Application(client_max_size=25 * 1024 * 1024)  # video yuklash uchun limitni oshiramiz
    app.router.add_get("/", health_check)
    app.router.add_get("/register", register_page)
    app.router.add_post("/submit-scan", submit_scan)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logging.info(f"🌐 Health-check server {PORT}-portda ishga tushdi")

# ============================
# 11. FONDA ISHLAYDIGAN VAZIFALAR (background tasks)
# ============================
async def subscription_reminder_loop():
    """Muddati 2 kundan kam qolgan VIP/PREMIUM foydalanuvchilarga eslatma yuboradi"""
    while True:
        try:
            soon = (datetime.now() + timedelta(days=2)).isoformat()
            rows = db_execute(
                "SELECT user_id, status, subscription_expires_at FROM users "
                "WHERE status IN ('vip','premium') AND subscription_expires_at IS NOT NULL "
                "AND subscription_expires_at <= ? AND reminder_sent=0",
                (soon,), fetchall=True
            )
            for user_id, status, expires_at in rows:
                try:
                    expires_date = datetime.fromisoformat(expires_at).strftime("%d.%m.%Y")
                    plan_title = "👑 VIP" if status == "vip" else "💎 PREMIUM"
                    await bot.send_message(
                        user_id,
                        f"⏳ Eslatma: sizning {plan_title} obunangiz {expires_date} kuni tugaydi.\n"
                        f"Uzaytirish uchun 💳 Obuna bo'limiga o'ting."
                    )
                    db_execute("UPDATE users SET reminder_sent=1 WHERE user_id=?", (user_id,), commit=True)
                except Exception:
                    pass
        except Exception as e:
            logging.exception(f"Eslatma yuborish xatosi: {e}")
        await asyncio.sleep(6 * 60 * 60)  # har 6 soatda tekshiradi

async def expire_subscriptions_loop():
    """Muddati tugagan VIP/PREMIUM foydalanuvchilarni 'oddiy'ga tushiradi va VIP kanaldan chiqaradi"""
    while True:
        try:
            now = datetime.now().isoformat()
            rows = db_execute(
                "SELECT user_id, status FROM users WHERE status IN ('vip','premium') "
                "AND subscription_expires_at IS NOT NULL AND subscription_expires_at < ?",
                (now,), fetchall=True
            )
            for user_id, status in rows:
                set_user_status(user_id, "oddiy")
                db_execute("UPDATE users SET subscription_expires_at=NULL WHERE user_id=?", (user_id,), commit=True)
                log_action(user_id, "subscription_expired_bg", f"was={status}")
                await revoke_vip_channel_access(user_id)
                try:
                    await bot.send_message(
                        user_id,
                        "⌛️ Obunangiz muddati tugadi va VIP kanaldan chiqarildingiz.\n"
                        "Davom ettirish uchun 💳 Obuna bo'limidan qayta sotib oling."
                    )
                except Exception:
                    pass
        except Exception as e:
            logging.exception(f"Obuna muddatini tekshirish xatosi: {e}")
        await asyncio.sleep(60 * 60)  # har soatda tekshiradi

async def daily_admin_report_loop():
    """Har 24 soatda adminlarga qisqa statistika hisobotini yuboradi"""
    while True:
        await asyncio.sleep(24 * 60 * 60)
        try:
            total_users = db_execute("SELECT COUNT(*) FROM users", fetchone=True)[0]
            total_movies = db_execute("SELECT COUNT(*) FROM movies", fetchone=True)[0]
            vip_count = db_execute("SELECT COUNT(*) FROM users WHERE status='vip'", fetchone=True)[0]
            premium_count = db_execute("SELECT COUNT(*) FROM users WHERE status='premium'", fetchone=True)[0]
            pending = len(get_pending_payments())
            report = (
                f"📊 <b>Kunlik hisobot</b>\n\n"
                f"👥 Foydalanuvchilar: {total_users}\n"
                f"🎬 Kinolar: {total_movies}\n"
                f"👑 VIP: {vip_count} | 💎 PREMIUM: {premium_count}\n"
                f"💳 Kutilayotgan to'lovlar: {pending}"
            )
            for admin_id in ADMIN_IDS:
                try:
                    await bot.send_message(admin_id, report, parse_mode="HTML")
                except Exception:
                    pass
        except Exception as e:
            logging.exception(f"Kunlik hisobot xatosi: {e}")

async def self_ping_loop():
    """Render bepul instansi uxlab qolmasligi uchun bot o'zi o'ziga har 5 daqiqada so'rov yuboradi.
    Render avtomatik beradigan RENDER_EXTERNAL_URL dan foydalanadi (yoki SELF_URL orqali qo'lda sozlash mumkin)."""
    self_url = os.getenv("RENDER_EXTERNAL_URL") or os.getenv("SELF_URL")
    if not self_url:
        logging.info("ℹ️ SELF_URL/RENDER_EXTERNAL_URL topilmadi — o'z-o'zini uyg'otish o'chirilgan.")
        return
    async with ClientSession() as session:
        while True:
            await asyncio.sleep(5 * 60)  # har 5 daqiqada
            try:
                async with session.get(self_url, timeout=ClientTimeout(total=20)) as resp:
                    logging.info(f"🔁 Self-ping: {resp.status}")
            except Exception as e:
                logging.warning(f"Self-ping xatosi: {e}")

# ============================
# 12. BOTNI ISHGA TUSHIRISH
# ============================
async def main():
    logging.basicConfig(level=logging.INFO)
    print("🤖 Bot ishga tushmoqda...")
    await start_web_server()
    asyncio.create_task(subscription_reminder_loop())
    asyncio.create_task(expire_subscriptions_loop())
    asyncio.create_task(daily_admin_report_loop())
    asyncio.create_task(self_ping_loop())
    await dp.start_polling(bot, skip_updates=True)

if __name__ == "__main__":
    asyncio.run(main())
