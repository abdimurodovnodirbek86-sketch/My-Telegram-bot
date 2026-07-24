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
import io
import textwrap
from urllib.parse import parse_qsl, quote
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

from PIL import Image, ImageDraw, ImageFont

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

# --- Bonus do'koni: bonus ballarni bepul VIP kuniga almashtirish ---
BONUS_SHOP_COST = 100        # necha ball kerak
BONUS_SHOP_DAYS = 3          # necha kunlik VIP beriladi

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
DB_NAME = os.getenv("DB_PATH", "bot_database.db")  # Faqat SQLite rejimida ishlatiladi

# --- PostgreSQL (tavsiya etiladi — ma'lumotlar hech qachon o'chmaydi) ---
# Render'da PostgreSQL bazasi ulansa, u avtomatik DATABASE_URL environment
# o'zgaruvchisini beradi va bot AVTOMATIK shu bazaga o'tadi. Hech narsa qo'shimcha
# sozlash shart emas — faqat Render'da "New +" -> "PostgreSQL" yaratib, shu servisga ulash kifoya.
DATABASE_URL = os.getenv("DATABASE_URL", "")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
if DATABASE_URL and "sslmode=" not in DATABASE_URL:
    DATABASE_URL += ("&" if "?" in DATABASE_URL else "?") + "sslmode=require"
USE_POSTGRES = bool(DATABASE_URL)

if USE_POSTGRES:
    import psycopg2
    import psycopg2.extensions

_pg_conn = None

def get_db_connection():
    """PostgreSQL uchun bitta doimiy ulanishni qayta ishlatadi (bot bir oqimli asyncio'da
    ishlaganligi uchun bu xavfsiz); SQLite uchun har safar yengil ulanish ochiladi."""
    global _pg_conn
    if USE_POSTGRES:
        if _pg_conn is None or _pg_conn.closed:
            _pg_conn = psycopg2.connect(DATABASE_URL)
        return _pg_conn
    return sqlite3.connect(DB_NAME)

def init_db():
    """Barcha kerakli jadvallarni yaratadi (agar mavjud bo'lmasa) — SQLite va PostgreSQL uchun mos"""
    conn = get_db_connection()
    c = conn.cursor()
    id_pk = "SERIAL PRIMARY KEY" if USE_POSTGRES else "INTEGER PRIMARY KEY AUTOINCREMENT"

    # Foydalanuvchilar jadvali
    c.execute(f'''CREATE TABLE IF NOT EXISTS users (
        user_id BIGINT PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        last_name TEXT,
        status TEXT DEFAULT 'oddiy',
        referrer_id BIGINT DEFAULT NULL,
        bonus_balance INTEGER DEFAULT 0,
        last_daily_bonus TEXT DEFAULT NULL,
        registered_at TEXT DEFAULT CURRENT_TIMESTAMP,
        total_requests INTEGER DEFAULT 0,
        is_banned INTEGER DEFAULT 0,
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
        is_vip INTEGER DEFAULT 0,
        is_premium INTEGER DEFAULT 0,
        added_by BIGINT,
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
        referrer_id BIGINT,
        referred_id BIGINT PRIMARY KEY,
        bonus_given INTEGER DEFAULT 0,
        date TEXT DEFAULT CURRENT_TIMESTAMP
    )''')

    # Loglar jadvali (har bir harakat qayd etiladi)
    c.execute(f'''CREATE TABLE IF NOT EXISTS logs (
        id {id_pk},
        user_id BIGINT,
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
    c.execute(f'''CREATE TABLE IF NOT EXISTS payments (
        id {id_pk},
        user_id BIGINT,
        plan TEXT,
        amount INTEGER,
        screenshot_file_id TEXT,
        status TEXT DEFAULT 'pending',
        requested_at TEXT DEFAULT CURRENT_TIMESTAMP,
        decided_at TEXT,
        decided_by BIGINT
    )''')

    # Seriallar jadvali
    c.execute('''CREATE TABLE IF NOT EXISTS series (
        code TEXT PRIMARY KEY,
        title TEXT,
        description TEXT,
        poster_file_id TEXT,
        category TEXT,
        is_vip INTEGER DEFAULT 0,
        is_premium INTEGER DEFAULT 0,
        free_episodes INTEGER DEFAULT 0,
        added_by BIGINT,
        added_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')

    # Serial qismlari (epizodlar) jadvali
    c.execute(f'''CREATE TABLE IF NOT EXISTS episodes (
        id {id_pk},
        series_code TEXT,
        episode_number INTEGER,
        file_id TEXT,
        added_at TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(series_code, episode_number)
    )''')

    # Ro'yxatdan o'tish uchun bir martalik tokenlar (Telegram tashqarisida ham ishlashi uchun)
    c.execute('''CREATE TABLE IF NOT EXISTS reg_tokens (
        token TEXT PRIMARY KEY,
        user_id BIGINT,
        full_name TEXT,
        username TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        expires_at TEXT,
        used INTEGER DEFAULT 0
    )''')

    # Sevimlilar jadvali
    c.execute('''CREATE TABLE IF NOT EXISTS favorites (
        user_id BIGINT,
        content_code TEXT,
        content_type TEXT,
        added_at TEXT DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (user_id, content_code)
    )''')

    # Viktorina savollari
    c.execute(f'''CREATE TABLE IF NOT EXISTS quiz_questions (
        id {id_pk},
        question TEXT,
        option_a TEXT,
        option_b TEXT,
        option_c TEXT,
        option_d TEXT,
        correct_option TEXT,
        added_by BIGINT,
        added_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')

    # Foydalanuvchi qaysi savolga javob berganini eslab qolish (qayta farm qilmasin)
    c.execute('''CREATE TABLE IF NOT EXISTS quiz_answers (
        user_id BIGINT,
        question_id INTEGER,
        is_correct INTEGER,
        answered_at TEXT DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (user_id, question_id)
    )''')

    # Fikr-mulohaza (foydalanuvchidan adminga)
    c.execute(f'''CREATE TABLE IF NOT EXISTS feedback (
        id {id_pk},
        user_id BIGINT,
        message TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')

    # Kino so'rovlari (foydalanuvchi so'ragan, hali botda yo'q kinolar)
    c.execute(f'''CREATE TABLE IF NOT EXISTS movie_requests (
        id {id_pk},
        user_id BIGINT,
        request_text TEXT,
        status TEXT DEFAULT 'pending',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')

    # Promo-kodlar
    c.execute('''CREATE TABLE IF NOT EXISTS promo_codes (
        code TEXT PRIMARY KEY,
        bonus_amount INTEGER DEFAULT 0,
        subscription_plan TEXT DEFAULT NULL,
        subscription_days INTEGER DEFAULT 0,
        max_uses INTEGER DEFAULT 1,
        used_count INTEGER DEFAULT 0,
        expires_at TEXT DEFAULT NULL,
        created_by BIGINT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')

    # Promo-kod ishlatilganligini eslab qolish (bitta user bir kodni faqat 1 marta ishlatadi)
    c.execute('''CREATE TABLE IF NOT EXISTS promo_redemptions (
        code TEXT,
        user_id BIGINT,
        redeemed_at TEXT DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (code, user_id)
    )''')

    conn.commit()

    # Eski bazalarda mavjud bo'lmasa, kerakli ustunlarni qo'shamiz (xavfsiz migratsiya)
    migrations = [
        "ALTER TABLE users ADD COLUMN subscription_expires_at TEXT DEFAULT NULL",
        "ALTER TABLE users ADD COLUMN referral_reward_claimed INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN reminder_sent INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN language TEXT DEFAULT 'uz'",
        "ALTER TABLE users ADD COLUMN notify_new_movies INTEGER DEFAULT 1",
        "ALTER TABLE users ADD COLUMN registration_status TEXT DEFAULT 'approved'",
        "ALTER TABLE users ADD COLUMN full_name TEXT DEFAULT NULL",
        "ALTER TABLE users ADD COLUMN registration_photo TEXT DEFAULT NULL",
        "ALTER TABLE users ADD COLUMN last_active_at TEXT DEFAULT NULL",
        "ALTER TABLE users ADD COLUMN last_inactivity_reminder TEXT DEFAULT NULL",
        "ALTER TABLE movies ADD COLUMN poster_file_id TEXT DEFAULT NULL",
    ]
    for m in migrations:
        if USE_POSTGRES:
            # Postgres IF NOT EXISTS'ni qo'llab-quvvatlaydi — xato umuman chiqmaydi
            parts = m.split(" ", 5)
            # "ALTER TABLE <table> ADD COLUMN <col> <rest...>" -> IF NOT EXISTS qo'shamiz
            safe_m = m.replace("ADD COLUMN", "ADD COLUMN IF NOT EXISTS", 1)
            try:
                c.execute(safe_m)
                conn.commit()
            except Exception:
                conn.rollback()
        else:
            try:
                c.execute(m)
                conn.commit()
            except sqlite3.OperationalError:
                pass  # ustun allaqachon mavjud

    if not USE_POSTGRES:
        conn.close()

init_db()
logging.info(f"💾 Baza turi: {'PostgreSQL' if USE_POSTGRES else 'SQLite'}")

# ============================
# 3. BAZA YORDAMCHI FUNKSIYALARI
# ============================
def db_execute(query, params=(), fetchone=False, fetchall=False, commit=False):
    """Har qanday SQL so'rovni bajaruvchi universal funksiya (SQLite va PostgreSQL uchun mos)"""
    conn = get_db_connection()
    if USE_POSTGRES:
        query = query.replace("?", "%s")
    c = conn.cursor()
    try:
        c.execute(query, params)
    except Exception:
        if USE_POSTGRES:
            conn.rollback()  # keyingi so'rovlar ishlashi uchun majburiy
        raise
    result = None
    if fetchone:
        result = c.fetchone()
    elif fetchall:
        result = c.fetchall()
    if commit:
        conn.commit()
    if not USE_POSTGRES:
        conn.close()
    return result

def db_execute_returning_id(query, params=()):
    """INSERT so'rovidan keyin yangi qatorning ID sini qaytaradi (ikkala baza uchun ham mos)"""
    conn = get_db_connection()
    if USE_POSTGRES:
        query = query.replace("?", "%s")
        if "RETURNING" not in query.upper():
            query += " RETURNING id"
        c = conn.cursor()
        try:
            c.execute(query, params)
            new_id = c.fetchone()[0]
            conn.commit()
            return new_id
        except Exception:
            conn.rollback()
            raise
    else:
        conn2 = sqlite3.connect(DB_NAME)
        c = conn2.cursor()
        c.execute(query, params)
        conn2.commit()
        new_id = c.lastrowid
        conn2.close()
        return new_id

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
    if USE_POSTGRES:
        db_execute(
            "INSERT INTO movies (code, title, description, file_id, category, is_vip, is_premium, added_by, poster_file_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (code) DO UPDATE SET "
            "title=EXCLUDED.title, description=EXCLUDED.description, file_id=EXCLUDED.file_id, "
            "category=EXCLUDED.category, is_vip=EXCLUDED.is_vip, is_premium=EXCLUDED.is_premium, "
            "added_by=EXCLUDED.added_by, poster_file_id=EXCLUDED.poster_file_id",
            (code, title, description, file_id, category, is_vip, is_premium, added_by, poster_file_id),
            commit=True
        )
    else:
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
    if USE_POSTGRES:
        db_execute("INSERT INTO categories (name, emoji) VALUES (?, ?) ON CONFLICT (name) DO NOTHING", (name, emoji), commit=True)
    else:
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
# Sevimlilar
# ------------------------------
def is_favorite(user_id, content_code):
    row = db_execute("SELECT 1 FROM favorites WHERE user_id=? AND content_code=?", (user_id, content_code), fetchone=True)
    return row is not None

def toggle_favorite(user_id, content_code, content_type):
    if is_favorite(user_id, content_code):
        db_execute("DELETE FROM favorites WHERE user_id=? AND content_code=?", (user_id, content_code), commit=True)
        return False
    db_execute(
        "INSERT INTO favorites (user_id, content_code, content_type) VALUES (?, ?, ?)",
        (user_id, content_code, content_type), commit=True
    )
    return True

def get_user_favorites(user_id):
    return db_execute(
        "SELECT content_code, content_type FROM favorites WHERE user_id=? ORDER BY added_at DESC",
        (user_id,), fetchall=True
    )

# To'lovlar (obuna) bilan bog'liq funksiyalar
# ------------------------------
def create_payment(user_id, plan, amount, screenshot_file_id):
    return db_execute_returning_id(
        "INSERT INTO payments (user_id, plan, amount, screenshot_file_id) VALUES (?, ?, ?, ?)",
        (user_id, plan, amount, screenshot_file_id)
    )

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
    if USE_POSTGRES:
        db_execute(
            "INSERT INTO series (code, title, description, poster_file_id, category, is_vip, is_premium, free_episodes, added_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (code) DO UPDATE SET "
            "title=EXCLUDED.title, description=EXCLUDED.description, poster_file_id=EXCLUDED.poster_file_id, "
            "category=EXCLUDED.category, is_vip=EXCLUDED.is_vip, is_premium=EXCLUDED.is_premium, "
            "free_episodes=EXCLUDED.free_episodes, added_by=EXCLUDED.added_by",
            (code, title, description, poster_file_id, category, is_vip, is_premium, free_episodes, added_by),
            commit=True
        )
    else:
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
    if USE_POSTGRES:
        db_execute(
            "INSERT INTO episodes (series_code, episode_number, file_id) VALUES (?, ?, ?) "
            "ON CONFLICT (series_code, episode_number) DO UPDATE SET file_id=EXCLUDED.file_id",
            (series_code, episode_number, file_id),
            commit=True
        )
    else:
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

# ------------------------------
# AI vositalar: rasm generatsiya va mem yaratish
# ------------------------------
# ------------------------------
# Tarjimon (bepul, kalitsiz ochiq API orqali)
# ------------------------------
async def translate_text(text: str, target_lang: str = "uz"):
    try:
        url = "https://api.mymemory.translated.net/get"
        params = {"q": text[:490], "langpair": f"autodetect|{target_lang}"}
        async with ClientSession() as session:
            async with session.get(url, params=params, timeout=ClientTimeout(total=20)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("responseData", {}).get("translatedText")
    except Exception as e:
        logging.exception(f"Tarjima xatosi: {e}")
    return None

# ------------------------------
# Kollaj yasash (2-4 rasm)
# ------------------------------
def create_collage(photo_bytes_list):
    images = [Image.open(io.BytesIO(b)).convert("RGB") for b in photo_bytes_list]
    size = 480
    images = [img.resize((size, size)) for img in images]
    n = len(images)
    if n == 2:
        canvas = Image.new("RGB", (size * 2, size), "black")
        canvas.paste(images[0], (0, 0))
        canvas.paste(images[1], (size, 0))
    elif n == 3:
        canvas = Image.new("RGB", (size * 2, size * 2), "black")
        canvas.paste(images[0], (0, 0))
        canvas.paste(images[1], (size, 0))
        half = images[2].resize((size * 2, size))
        canvas.paste(half, (0, size))
    else:
        canvas = Image.new("RGB", (size * 2, size * 2), "black")
        positions = [(0, 0), (size, 0), (0, size), (size, size)]
        for img, pos in zip(images[:4], positions):
            canvas.paste(img, pos)
    buf = io.BytesIO()
    canvas.save(buf, format="JPEG", quality=90)
    return buf.getvalue()

# ------------------------------
# Viktorina
# ------------------------------
def add_quiz_question(question, option_a, option_b, option_c, option_d, correct_option, added_by):
    db_execute(
        "INSERT INTO quiz_questions (question, option_a, option_b, option_c, option_d, correct_option, added_by) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (question, option_a, option_b, option_c, option_d, correct_option.upper(), added_by),
        commit=True
    )

def get_random_unanswered_question(user_id):
    return db_execute(
        "SELECT id, question, option_a, option_b, option_c, option_d, correct_option FROM quiz_questions "
        "WHERE id NOT IN (SELECT question_id FROM quiz_answers WHERE user_id=?) ORDER BY RANDOM() LIMIT 1",
        (user_id,), fetchone=True
    )

def record_quiz_answer(user_id, question_id, is_correct):
    db_execute(
        "INSERT INTO quiz_answers (user_id, question_id, is_correct) VALUES (?, ?, ?)",
        (user_id, question_id, 1 if is_correct else 0),
        commit=True
    )

# ------------------------------
# Fikr-mulohaza va kino so'rovlari
# ------------------------------
def add_feedback(user_id, message):
    db_execute("INSERT INTO feedback (user_id, message) VALUES (?, ?)", (user_id, message), commit=True)

def add_movie_request(user_id, request_text):
    db_execute("INSERT INTO movie_requests (user_id, request_text) VALUES (?, ?)", (user_id, request_text), commit=True)

# ------------------------------
# Promo-kodlar
# ------------------------------
def create_promo_code(code, bonus_amount=0, subscription_plan=None, subscription_days=0, max_uses=1, expires_at=None, created_by=0):
    db_execute(
        "INSERT INTO promo_codes (code, bonus_amount, subscription_plan, subscription_days, max_uses, expires_at, created_by) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (code, bonus_amount, subscription_plan, subscription_days, max_uses, expires_at, created_by),
        commit=True
    )

def redeem_promo_code(code, user_id):
    """Promo-kodni tekshiradi va qo'llaydi. (success: bool, message: str) qaytaradi"""
    promo = db_execute("SELECT * FROM promo_codes WHERE code=?", (code,), fetchone=True)
    if not promo:
        return False, "❌ Bunday promo-kod topilmadi."
    _, bonus_amount, sub_plan, sub_days, max_uses, used_count, expires_at, _, _ = promo
    if expires_at and datetime.now() > datetime.fromisoformat(expires_at):
        return False, "❌ Bu promo-kodning muddati tugagan."
    if used_count >= max_uses:
        return False, "❌ Bu promo-kod ishlatilish limitiga yetgan."
    already = db_execute("SELECT 1 FROM promo_redemptions WHERE code=? AND user_id=?", (code, user_id), fetchone=True)
    if already:
        return False, "❌ Siz bu promo-kodni allaqachon ishlatgansiz."

    if bonus_amount:
        update_bonus(user_id, bonus_amount)
    if sub_plan and sub_days:
        activate_subscription(user_id, sub_plan, days=sub_days)

    db_execute("UPDATE promo_codes SET used_count=used_count+1 WHERE code=?", (code,), commit=True)
    db_execute("INSERT INTO promo_redemptions (code, user_id) VALUES (?, ?)", (code, user_id), commit=True)
    log_action(user_id, "redeem_promo", code)

    parts = []
    if bonus_amount:
        parts.append(f"+{bonus_amount} bonus ball")
    if sub_plan and sub_days:
        plan_title = "👑 VIP" if sub_plan == "vip" else "💎 PREMIUM"
        parts.append(f"{sub_days} kunlik {plan_title}")
    return True, "✅ Promo-kod qabul qilindi! Sizga: " + ", ".join(parts)

# ------------------------------
# Reyting jadvali (leaderboard)
# ------------------------------
def get_top_referrers(limit=10):
    return db_execute(
        "SELECT referrer_id, COUNT(*) as cnt FROM referrals GROUP BY referrer_id ORDER BY cnt DESC LIMIT ?",
        (limit,), fetchall=True
    ) or []

def get_top_bonus_users(limit=10):
    return db_execute(
        "SELECT user_id, first_name, bonus_balance FROM users ORDER BY bonus_balance DESC LIMIT ?",
        (limit,), fetchall=True
    ) or []

async def generate_ai_image(prompt: str):
    """Matn asosida rasm generatsiya qiladi (bepul, kalitsiz ochiq xizmat orqali)"""
    encoded = quote(prompt)
    url = f"https://image.pollinations.ai/prompt/{encoded}?width=768&height=768&nologo=true"
    try:
        async with ClientSession() as session:
            async with session.get(url, timeout=ClientTimeout(total=60)) as resp:
                if resp.status == 200:
                    return await resp.read()
    except Exception as e:
        logging.exception(f"AI rasm generatsiya xatosi: {e}")
    return None

def load_meme_font(size):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()

def create_meme(photo_bytes: bytes, top_text: str, bottom_text: str) -> bytes:
    """Rasmga yuqori/pastki matn qo'shib, klassik "mem" formatida chiqaradi"""
    img = Image.open(io.BytesIO(photo_bytes)).convert("RGB")
    w, h = img.size
    font_size = max(18, int(h * 0.08))
    font = load_meme_font(font_size)
    draw = ImageDraw.Draw(img)
    stroke_w = max(2, font_size // 14)
    chars_per_line = max(6, int(w / (font_size * 0.55)))

    def draw_block(text, top_y, from_bottom=False):
        if not text:
            return
        wrapped = textwrap.fill(text.upper(), width=chars_per_line)
        lines = wrapped.split("\n")
        total_h = 0
        line_sizes = []
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=font, stroke_width=stroke_w)
            lw, lh = bbox[2] - bbox[0], bbox[3] - bbox[1]
            line_sizes.append((lw, lh))
            total_h += lh + 6
        y = (h - total_h - 10) if from_bottom else top_y
        for line, (lw, lh) in zip(lines, line_sizes):
            x = (w - lw) / 2
            draw.text((x, y), line, font=font, fill="white", stroke_width=stroke_w, stroke_fill="black")
            y += lh + 6

    draw_block(top_text, int(h * 0.03))
    draw_block(bottom_text, 0, from_bottom=True)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=92)
    return buf.getvalue()

async def post_movie_to_channel(title, description, category, code, file_id, poster_file_id, share_link, is_vip=False, is_premium=False, episode_number=None):
    """Yangi kino/serial qismi qo'shilishi bilan kino kanaliga FAQAT E'LON (poster + ma'lumot) joylaydi.
    Videoning o'zi kanalga YUBORILMAYDI — faqat botga o'tish tugmasi orqali ko'rish mumkin."""
    if not POST_CHANNEL:
        return
    try:
        badge = "💎 PREMIUM" if is_premium else ("👑 VIP" if is_vip else "🆓 BEPUL")
        ep_line = f"\n🎞 Qism: {episode_number}" if episode_number else ""
        caption = (
            f"🎬 <b>{title}</b>{ep_line}\n"
            f"━━━━━━━━━━━━━━━\n"
            f"📝 {description}\n"
            f"📂 Janr: {category}\n"
            f"🏷 Status: {badge}\n"
            f"🔑 Kod: <code>{code}</code>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"🤖 Botdan foydalanish uchun kodni yuboring 👆"
        )
        kb = InlineKeyboardBuilder()
        kb.button(text="🎬 Botda ko'rish", url=share_link)
        kb.adjust(1)

        if poster_file_id:
            await bot.send_photo(POST_CHANNEL, poster_file_id, caption=caption, parse_mode="HTML", reply_markup=kb.as_markup())
        else:
            await bot.send_message(POST_CHANNEL, caption, parse_mode="HTML", reply_markup=kb.as_markup())
    except Exception as e:
        logging.exception(f"Kino kanaliga e'lon post xatosi: {e}")

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

def save_setting(key, value):
    """settings jadvaliga key-value saqlaydi (ikkala baza uchun ham mos)"""
    if USE_POSTGRES:
        db_execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value",
            (key, value), commit=True
        )
    else:
        db_execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value), commit=True)

def save_channels(channels):
    save_setting("channels", json.dumps(channels))

CHANNELS = get_channels()  # Bot ishga tushganda bazadan kanal ro'yxati yuklanadi

def get_post_channel_db():
    row = db_execute("SELECT value FROM settings WHERE key='post_channel'", fetchone=True)
    return row[0] if row and row[0] else ""

def save_post_channel_db(channel):
    save_setting("post_channel", channel)

# Admin botni ulasa DB'dagi qiymat ustunlik qiladi, aks holda .env dagi POST_CHANNEL ishlatiladi
_db_post_channel = get_post_channel_db()
if _db_post_channel:
    POST_CHANNEL = _db_post_channel

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
    waiting_post_channel = State()
    waiting_user_search = State()
    waiting_ai_prompt = State()
    waiting_meme_photo = State()
    waiting_meme_top_text = State()
    waiting_meme_bottom_text = State()
    waiting_translate_text = State()
    waiting_collage_photos = State()
    waiting_feedback_text = State()
    waiting_movie_request_text = State()
    waiting_promo_redeem = State()
    waiting_quiz_question = State()
    waiting_quiz_options = State()
    waiting_quiz_correct = State()
    waiting_promo_create = State()

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
    builder.button(text="🎨 AI vositalar")
    builder.button(text="🎮 Ko'ngilochar")
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
    builder.button(text="❤️ Sevimlilar", callback_data="favorites_menu")
    builder.button(text="📂 Kategoriyalar", callback_data="categories_menu")
    builder.button(text="🎨 AI vositalar", callback_data="ai_tools_menu")
    builder.button(text="🎮 Ko'ngilochar", callback_data="fun_menu")
    if status in ["vip", "premium", "admin"]:
        builder.button(text="💎 Maxsus kinolar", callback_data="special_movies")
    if status == "admin":
        builder.button(text="⚙️ Admin panel", callback_data="admin_panel")
    builder.adjust(2)
    return builder.as_markup()

def ai_tools_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="🖼 Rasm yaratish (matndan)", callback_data="ai_image_gen")
    builder.button(text="😂 Mem yaratish", callback_data="ai_meme_start")
    builder.button(text="🎭 Kollaj yasash", callback_data="ai_collage_start")
    builder.button(text="🌐 Tarjimon", callback_data="ai_translate_start")
    builder.adjust(1)
    builder.row(InlineKeyboardButton(text="🔙 Orqaga", callback_data="back_main"))
    return builder.as_markup()

def fun_menu_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="🧠 Viktorina", callback_data="quiz_start")
    builder.button(text="🎲 Tasodifiy kino", callback_data="random_movie")
    builder.button(text="🏆 Reyting jadvali", callback_data="leaderboard_menu")
    builder.button(text="🎟 Promo-kod kiritish", callback_data="promo_redeem_start")
    builder.button(text="🛍 Bonus do'koni", callback_data="bonus_shop")
    builder.button(text="💭 Fikr bildirish", callback_data="feedback_start")
    builder.button(text="⭐ Kino so'rash", callback_data="movie_request_start")
    builder.adjust(1)
    builder.row(InlineKeyboardButton(text="🔙 Orqaga", callback_data="back_main"))
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
    builder.button(text="🔎 Foydalanuvchi qidirish", callback_data="admin_search_user")
    builder.button(text="🧠 Viktorina savoli qo'shish", callback_data="admin_add_quiz")
    builder.button(text="🎟 Promo-kod yaratish", callback_data="admin_create_promo")
    builder.button(text="💭 Fikrlar", callback_data="admin_feedback_list")
    builder.button(text="⭐ Kino so'rovlari", callback_data="admin_requests_list")
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

def movie_action_keyboard(code, share_link=None, user_id=None):
    builder = InlineKeyboardBuilder()
    builder.button(text="⭐️ Baholash", callback_data=f"rate_{code}")
    if user_id is not None:
        fav_text = "💔 Sevimlilardan olib tashlash" if is_favorite(user_id, code) else "❤️ Sevimlilarga qo'shish"
        builder.button(text=fav_text, callback_data=f"fav_{code}")
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

def episode_list_keyboard(series_code, episode_numbers, free_episodes, user_id=None):
    """Serial qismlari ro'yxati — pullik qismlar 🔒 belgisi bilan ko'rsatiladi"""
    builder = InlineKeyboardBuilder()
    for ep in episode_numbers:
        lock = "" if ep <= free_episodes else "🔒 "
        builder.button(text=f"{lock}{ep}-qism", callback_data=f"episode_{series_code}_{ep}")
    builder.adjust(4)
    if user_id is not None:
        fav_text = "💔 Sevimlilardan olib tashlash" if is_favorite(user_id, series_code) else "❤️ Sevimlilarga qo'shish"
        builder.row(InlineKeyboardButton(text=fav_text, callback_data=f"fav_{series_code}"))
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
    builder.button(text="➕ Majburiy kanal qo'shish", callback_data="admin_add_channel")
    builder.button(text="➖ Majburiy kanal o'chirish", callback_data="admin_remove_channel")
    builder.button(text="🎥 Kino kanalini ulash", callback_data="admin_set_post_channel")
    builder.button(text="🚫 Kino kanalini uzish", callback_data="admin_unset_post_channel")
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
    bosqichidan tashqari HECH QANDAY funksiyaga kirita olmaydi. Shu bilan birga
    har bir foydalanuvchining oxirgi faollik vaqtini ham yangilab boradi."""
    async def __call__(self, handler, event, data):
        user = event.from_user if hasattr(event, "from_user") else None
        if user is None:
            return await handler(event, data)

        try:
            db_execute(
                "UPDATE users SET last_active_at=? WHERE user_id=?",
                (datetime.now().isoformat(), user.id), commit=True
            )
        except Exception:
            pass

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
    promo_code = None
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
        elif args.startswith("promo_"):
            promo_code = args.split("promo_", 1)[1].strip().upper()
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
        elif promo_code:
            success, result_text = redeem_promo_code(promo_code, user.id)
            await message.answer(result_text)
            await message.answer("📋 Asosiy menyu:", reply_markup=main_inline_keyboard(user.id))
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
@dp.message(F.text.in_(["🎬 Kinolar", "🔍 Qidirish", "🏆 Top kinolar", "🆕 Yangi kinolar", "🎁 Bonus", "💳 Obuna", "👤 Profil", "📂 Kategoriyalar", "🎨 AI vositalar", "🎮 Ko'ngilochar", "⚙️ Admin panel"]))
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

        elif text == "🎨 AI vositalar":
            await message.answer(
                "🎨 <b>AI vositalar:</b>\n\n"
                "🖼 Matn yozib, undan rasm yaratishingiz mumkin\n"
                "😂 Rasm yuklab, unga mem matni qo'shishingiz mumkin",
                parse_mode="HTML",
                reply_markup=ai_tools_keyboard()
            )

        elif text == "🎮 Ko'ngilochar":
            await message.answer(
                "🎮 <b>Ko'ngilochar bo'lim:</b>\n\n"
                "Viktorina, tasodifiy kino, reyting, promo-kod va boshqa qiziqarli narsalar shu yerda!",
                parse_mode="HTML",
                reply_markup=fun_menu_keyboard()
            )
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
    keyboard = movie_action_keyboard(code, share_link, user_id)

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
    keyboard = episode_list_keyboard(code, episode_numbers, free_episodes, user_id)
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

@dp.callback_query(F.data.startswith("fav_"))
async def toggle_favorite_callback(call: CallbackQuery):
    try:
        code = call.data.split("_", 1)[1]
        user_id = call.from_user.id
        content_type = "series" if get_series(code) else "movie"
        added = toggle_favorite(user_id, code, content_type)
        bot_info = await bot.get_me()
        prefix = "series" if content_type == "series" else "movie"
        share_link = f"https://t.me/{bot_info.username}?start={prefix}_{code}"
        try:
            await call.message.edit_reply_markup(reply_markup=movie_action_keyboard(code, share_link, user_id))
        except Exception:
            pass
        await call.answer("❤️ Sevimlilarga qo'shildi!" if added else "💔 Sevimlilardan olib tashlandi")
    except Exception as e:
        logging.exception(f"Sevimli almashtirish xatosi: {e}")
        await call.answer("Xatolik yuz berdi.", show_alert=True)

@dp.callback_query(F.data == "favorites_menu")
async def favorites_menu(call: CallbackQuery):
    try:
        user_id = call.from_user.id
        favs = get_user_favorites(user_id)
        if not favs:
            await call.message.edit_text("❤️ Sizda hali sevimlilar yo'q.", reply_markup=main_inline_keyboard(user_id))
            return
        kb = InlineKeyboardBuilder()
        for code, content_type in favs:
            if content_type == "series":
                series = get_series(code)
                title = series[1] if series else code
                kb.button(text=f"🎞 {title}", callback_data=f"series_view_{code}")
            else:
                movie = get_movie(code)
                title = movie[1] if movie else code
                kb.button(text=f"🎬 {title}", callback_data=f"movie_{code}")
        kb.adjust(1)
        kb.row(InlineKeyboardButton(text="🔙 Orqaga", callback_data="back_main"))
        await call.message.edit_text("❤️ <b>Sevimlilaringiz:</b>", parse_mode="HTML", reply_markup=kb.as_markup())
    except Exception as e:
        logging.exception(f"Sevimlilar ro'yxati xatosi: {e}")
    await call.answer()

@dp.callback_query(F.data.startswith("series_view_"))
async def series_view_from_favorites(call: CallbackQuery):
    try:
        code = call.data.split("series_view_", 1)[1]
        await send_series(call.message, call.from_user.id, code)
    except Exception as e:
        logging.exception(f"Sevimli serial ko'rish xatosi: {e}")
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

        # Kino kanaliga AVTOMATIK to'liq VIDEO joylanadi (poster bilan chiroyli dizaynda)
        await post_movie_to_channel(
            title=data['title'], description=data['desc'], category=data['category'],
            code=data['code'], file_id=data['file_id'], poster_file_id=poster_file_id,
            share_link=share_link, is_vip=data['vip'], is_premium=premium
        )

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

        series = get_series(data["series_code"])
        if series:
            bot_info = await bot.get_me()
            share_link = f"https://t.me/{bot_info.username}?start=series_{data['series_code']}"
            await post_movie_to_channel(
                title=series[1], description=series[2], category=series[4],
                code=data["series_code"], file_id=message.video.file_id, poster_file_id=series[3],
                share_link=share_link, is_vip=bool(series[5]), is_premium=bool(series[6]),
                episode_number=data["episode_number"]
            )

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
        channels_text = "📡 Majburiy kanallar:\n" + "\n".join(CHANNELS) if CHANNELS else "📡 Majburiy kanal biriktirilmagan."
        post_text = f"\n\n🎥 Kino kanali: {POST_CHANNEL}" if POST_CHANNEL else "\n\n🎥 Kino kanali ulanmagan."
        await call.message.edit_text(channels_text + post_text, reply_markup=channel_settings_keyboard())
    except Exception as e:
        logging.exception(f"Admin kanal sozlash xatosi: {e}")
    await call.answer()

@dp.callback_query(F.data == "admin_set_post_channel")
async def admin_set_post_channel_start(call: CallbackQuery, state: FSMContext):
    try:
        if call.from_user.id not in ADMIN_IDS:
            await call.answer("⛔️ Ruxsat yo'q", show_alert=True)
            return
        await call.message.edit_text(
            "🎥 Kino kanalini ulash.\n\n"
            "1️⃣ Botni shu kanalga <b>admin</b> qilib qo'shing (xabar yuborish huquqi bilan)\n"
            "2️⃣ Kanal username'ini yuboring (masalan: @mening_kino_kanalim)\n\n"
            "Agar kanal yopiq bo'lsa, uning ID raqamini yuboring (masalan: -1001234567890)",
            parse_mode="HTML"
        )
        await state.set_state(AdminStates.waiting_post_channel)
    except Exception as e:
        logging.exception(f"Kino kanalini ulash boshlash xatosi: {e}")
    await call.answer()

@dp.message(AdminStates.waiting_post_channel)
async def admin_set_post_channel_save(message: Message, state: FSMContext):
    global POST_CHANNEL
    try:
        channel = message.text.strip()
        if not channel.startswith("@") and not channel.startswith("-"):
            channel = "@" + channel
        try:
            await bot.send_message(channel, "✅ Bot ushbu kanalga muvaffaqiyatli ulandi!")
        except Exception as e:
            await message.answer(
                f"❌ Botni shu kanalga ulab bo'lmadi. Bot kanalda admin ekanligini tekshiring.\nXato: {e}"
            )
            return
        save_post_channel_db(channel)
        POST_CHANNEL = channel
        await message.answer(f"✅ Kino kanali ulandi: {channel}\nEndi barcha yangi kino/qismlar avtomatik shu yerga joylanadi.")
        log_action(message.from_user.id, "set_post_channel", channel)
        await state.clear()
    except Exception as e:
        logging.exception(f"Kino kanalini saqlash xatosi: {e}")
        await message.answer("Xatolik yuz berdi.")
        await state.clear()

@dp.callback_query(F.data == "admin_unset_post_channel")
async def admin_unset_post_channel(call: CallbackQuery):
    global POST_CHANNEL
    try:
        if call.from_user.id not in ADMIN_IDS:
            await call.answer("⛔️ Ruxsat yo'q", show_alert=True)
            return
        save_post_channel_db("")
        POST_CHANNEL = ""
        await call.message.edit_text("✅ Kino kanali uzildi.", reply_markup=channel_settings_keyboard())
        log_action(call.from_user.id, "unset_post_channel", "")
    except Exception as e:
        logging.exception(f"Kino kanalini uzish xatosi: {e}")
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

# ---------- Foydalanuvchi qidirish ----------
@dp.callback_query(F.data == "admin_search_user")
async def admin_search_user_start(call: CallbackQuery, state: FSMContext):
    try:
        if call.from_user.id not in ADMIN_IDS:
            await call.answer("⛔️ Ruxsat yo'q", show_alert=True)
            return
        await call.message.edit_text("🔎 Foydalanuvchi ID sini yoki ismini kiriting:")
        await state.set_state(AdminStates.waiting_user_search)
    except Exception as e:
        logging.exception(f"Foydalanuvchi qidirish boshlash xatosi: {e}")
    await call.answer()

@dp.message(AdminStates.waiting_user_search)
async def admin_search_user_result(message: Message, state: FSMContext):
    try:
        query = message.text.strip()
        if query.isdigit():
            user = db_execute(
                "SELECT user_id, username, first_name, last_name, status, total_requests, bonus_balance, "
                "is_banned, registered_at, subscription_expires_at FROM users WHERE user_id=?",
                (int(query),), fetchone=True
            )
            results = [user] if user else []
        else:
            results = db_execute(
                "SELECT user_id, username, first_name, last_name, status, total_requests, bonus_balance, "
                "is_banned, registered_at, subscription_expires_at FROM users WHERE first_name LIKE ? OR last_name LIKE ? LIMIT 5",
                (f"%{query}%", f"%{query}%"), fetchall=True
            ) or []

        if not results:
            await message.answer("❌ Foydalanuvchi topilmadi.")
            await state.clear()
            return

        for u in results:
            uid, username, first_name, last_name, status, total_requests, bonus, is_banned, registered_at, sub_expires = u
            ban_text = "🚫 Bloklangan" if is_banned else "✅ Faol"
            username_display = username or "yo\u02bcq"
            text = (
                f"👤 <b>{first_name or ''} {last_name or ''}</b>\n"
                f"🆔 ID: <code>{uid}</code>\n"
                f"📱 Username: @{username_display}\n"
                f"🔰 Status: {status}\n"
                f"📊 So'rovlar: {total_requests}\n"
                f"💰 Bonus: {bonus}\n"
                f"📅 Ro'yxatdan o'tgan: {registered_at}\n"
                f"{ban_text}"
            )
            if sub_expires:
                text += f"\n📅 Obuna tugash: {sub_expires[:10]}"
            await message.answer(text, parse_mode="HTML")
        await state.clear()
    except Exception as e:
        logging.exception(f"Foydalanuvchi qidirish natijasi xatosi: {e}")
        await message.answer("Xatolik yuz berdi.")
        await state.clear()

# ---------- Admin: Viktorina savoli qo'shish ----------
@dp.callback_query(F.data == "admin_add_quiz")
async def admin_add_quiz_start(call: CallbackQuery, state: FSMContext):
    try:
        if call.from_user.id not in ADMIN_IDS:
            await call.answer("⛔️ Ruxsat yo'q", show_alert=True)
            return
        await call.message.edit_text("🧠 Savol matnini kiriting:")
        await state.set_state(AdminStates.waiting_quiz_question)
    except Exception as e:
        logging.exception(f"Viktorina savoli boshlash xatosi: {e}")
    await call.answer()

@dp.message(AdminStates.waiting_quiz_question)
async def admin_add_quiz_question(message: Message, state: FSMContext):
    try:
        await state.update_data(quiz_question=message.text.strip())
        await message.answer(
            "4 ta variantni <b>|</b> belgisi bilan ajratib yozing.\n"
            "Masalan: <code>Toshkent|Samarqand|Buxoro|Xiva</code>",
            parse_mode="HTML"
        )
        await state.set_state(AdminStates.waiting_quiz_options)
    except Exception as e:
        logging.exception(f"Viktorina savol matni xatosi: {e}")
        await message.answer("Xatolik yuz berdi.")

@dp.message(AdminStates.waiting_quiz_options)
async def admin_add_quiz_options(message: Message, state: FSMContext):
    try:
        parts = [p.strip() for p in message.text.split("|")]
        if len(parts) != 4:
            await message.answer("❗️ Aniq 4 ta variant kiriting, | belgisi bilan ajrating.")
            return
        await state.update_data(quiz_a=parts[0], quiz_b=parts[1], quiz_c=parts[2], quiz_d=parts[3])
        await message.answer("To'g'ri variantning harfini kiriting (A, B, C yoki D):")
        await state.set_state(AdminStates.waiting_quiz_correct)
    except Exception as e:
        logging.exception(f"Viktorina variantlari xatosi: {e}")
        await message.answer("Xatolik yuz berdi.")

@dp.message(AdminStates.waiting_quiz_correct)
async def admin_add_quiz_correct(message: Message, state: FSMContext):
    try:
        correct = message.text.strip().upper()
        if correct not in ("A", "B", "C", "D"):
            await message.answer("❗️ Faqat A, B, C yoki D deb yozing.")
            return
        data = await state.get_data()
        add_quiz_question(
            data["quiz_question"], data["quiz_a"], data["quiz_b"], data["quiz_c"], data["quiz_d"],
            correct, message.from_user.id
        )
        await message.answer("✅ Viktorina savoli qo'shildi!")
        log_action(message.from_user.id, "add_quiz_question", data["quiz_question"][:80])
        await state.clear()
    except Exception as e:
        logging.exception(f"Viktorina to'g'ri javob xatosi: {e}")
        await message.answer("Xatolik yuz berdi.")
        await state.clear()

# ---------- Admin: Promo-kod yaratish ----------
@dp.callback_query(F.data == "admin_create_promo")
async def admin_create_promo_start(call: CallbackQuery, state: FSMContext):
    try:
        if call.from_user.id not in ADMIN_IDS:
            await call.answer("⛔️ Ruxsat yo'q", show_alert=True)
            return
        await call.message.edit_text(
            "🎟 Promo-kodni quyidagi formatda yuboring:\n\n"
            "<code>KOD|bonus_ball|reja|kunlar|max_ishlatish</code>\n\n"
            "Masalan (bonus + VIP): <code>YANGIYIL2026|50|vip|7|100</code>\n"
            "Faqat bonus uchun reja o'rniga <code>-</code> yozing: <code>BONUS10|10|-|0|500</code>",
            parse_mode="HTML"
        )
        await state.set_state(AdminStates.waiting_promo_create)
    except Exception as e:
        logging.exception(f"Promo-kod yaratish boshlash xatosi: {e}")
    await call.answer()

@dp.message(AdminStates.waiting_promo_create)
async def admin_create_promo_save(message: Message, state: FSMContext):
    try:
        parts = [p.strip() for p in message.text.split("|")]
        if len(parts) != 5:
            await message.answer("❗️ Format noto'g'ri. 5 ta qism kerak: KOD|bonus|reja|kunlar|max_ishlatish")
            return
        code, bonus_str, plan, days_str, max_uses_str = parts
        code = code.upper()
        if db_execute("SELECT code FROM promo_codes WHERE code=?", (code,), fetchone=True):
            await message.answer("❌ Bu kod allaqachon mavjud.")
            return
        bonus_amount = int(bonus_str) if bonus_str.isdigit() else 0
        sub_plan = None if plan == "-" else plan.lower()
        sub_days = int(days_str) if days_str.isdigit() else 0
        max_uses = int(max_uses_str) if max_uses_str.isdigit() else 1

        create_promo_code(code, bonus_amount, sub_plan, sub_days, max_uses, None, message.from_user.id)
        await message.answer(f"✅ Promo-kod yaratildi: <code>{code}</code>", parse_mode="HTML")
        log_action(message.from_user.id, "create_promo", code)

        # Kino kanaliga avtomatik chiroyli e'lon (agar kanal ulangan bo'lsa)
        if POST_CHANNEL:
            bot_info = await bot.get_me()
            promo_link = f"https://t.me/{bot_info.username}?start=promo_{code}"
            reward_parts = []
            if bonus_amount:
                reward_parts.append(f"💰 {bonus_amount} bonus ball")
            if sub_plan and sub_days:
                plan_title = "👑 VIP" if sub_plan == "vip" else "💎 PREMIUM"
                reward_parts.append(f"{plan_title} — {sub_days} kun")
            reward_text = " + ".join(reward_parts) if reward_parts else "sovg'a"
            kb = InlineKeyboardBuilder()
            kb.button(text="🎁 Hoziroq olish", url=promo_link)
            try:
                await bot.send_message(
                    POST_CHANNEL,
                    f"🎟 <b>YANGI PROMO-KOD!</b>\n\n"
                    f"🎁 Sovg'a: {reward_text}\n"
                    f"👥 Faqat birinchi {max_uses} kishiga\n\n"
                    f"👇 Tugmani bosing — bot avtomatik faollashtiradi!",
                    parse_mode="HTML",
                    reply_markup=kb.as_markup()
                )
            except Exception as e:
                logging.exception(f"Promo-kod kanal e'loni xatosi: {e}")

        await state.clear()
    except Exception as e:
        logging.exception(f"Promo-kod saqlash xatosi: {e}")
        await message.answer("Xatolik yuz berdi. Formatni tekshiring.")
        await state.clear()

# ---------- Admin: Fikrlar va so'rovlar ----------
@dp.callback_query(F.data == "admin_feedback_list")
async def admin_feedback_list(call: CallbackQuery):
    try:
        if call.from_user.id not in ADMIN_IDS:
            await call.answer("⛔️ Ruxsat yo'q", show_alert=True)
            return
        rows = db_execute(
            "SELECT user_id, message, created_at FROM feedback ORDER BY created_at DESC LIMIT 10",
            fetchall=True
        ) or []
        if not rows:
            await call.message.edit_text("💭 Hozircha fikrlar yo'q.", reply_markup=admin_panel_keyboard())
            return
        text = "💭 <b>So'nggi 10 ta fikr:</b>\n\n"
        for user_id, msg, created_at in rows:
            text += f"👤 ID {user_id}:\n{msg}\n\n"
        await call.message.edit_text(text[:4000], parse_mode="HTML", reply_markup=admin_panel_keyboard())
    except Exception as e:
        logging.exception(f"Fikrlar ro'yxati xatosi: {e}")
    await call.answer()

@dp.callback_query(F.data == "admin_requests_list")
async def admin_requests_list(call: CallbackQuery):
    try:
        if call.from_user.id not in ADMIN_IDS:
            await call.answer("⛔️ Ruxsat yo'q", show_alert=True)
            return
        rows = db_execute(
            "SELECT user_id, request_text, created_at FROM movie_requests ORDER BY created_at DESC LIMIT 10",
            fetchall=True
        ) or []
        if not rows:
            await call.message.edit_text("⭐ Hozircha kino so'rovlari yo'q.", reply_markup=admin_panel_keyboard())
            return
        text = "⭐ <b>So'nggi 10 ta kino so'rovi:</b>\n\n"
        for user_id, req, created_at in rows:
            text += f"👤 ID {user_id}: {req}\n\n"
        await call.message.edit_text(text[:4000], parse_mode="HTML", reply_markup=admin_panel_keyboard())
    except Exception as e:
        logging.exception(f"So'rovlar ro'yxati xatosi: {e}")
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

# ---------- AI vositalar: rasm generatsiya va mem yaratish ----------
@dp.callback_query(F.data == "ai_tools_menu")
async def ai_tools_menu(call: CallbackQuery, state: FSMContext):
    try:
        await state.clear()
        await call.message.edit_text(
            "🎨 <b>AI vositalar:</b>\n\n"
            "🖼 Matn yozib, undan rasm yaratishingiz mumkin\n"
            "😂 Rasm yuklab, unga mem matni qo'shishingiz mumkin",
            parse_mode="HTML",
            reply_markup=ai_tools_keyboard()
        )
    except Exception as e:
        logging.exception(f"AI vositalar menyusi xatosi: {e}")
    await call.answer()

@dp.callback_query(F.data == "ai_image_gen")
async def ai_image_gen_start(call: CallbackQuery, state: FSMContext):
    try:
        await call.message.edit_text(
            "🖼 Yaratmoqchi bo'lgan rasmingizni so'zlar bilan tasvirlab bering.\n\n"
            "Masalan: <i>tog' bag'rida qizil chodir, quyosh botishi, sinematik uslub</i>",
            parse_mode="HTML"
        )
        await state.set_state(AdminStates.waiting_ai_prompt)
    except Exception as e:
        logging.exception(f"AI rasm boshlash xatosi: {e}")
    await call.answer()

@dp.message(AdminStates.waiting_ai_prompt)
async def ai_image_gen_result(message: Message, state: FSMContext):
    try:
        prompt = message.text.strip() if message.text else ""
        if len(prompt) < 3:
            await message.answer("❗️ Iltimos, kamida bir necha so'z bilan tasvirlab bering.")
            return
        wait_msg = await message.answer("⏳ Rasm yaratilmoqda, biroz kuting...")
        image_bytes = await generate_ai_image(prompt)
        if not image_bytes:
            await wait_msg.edit_text("❌ Rasm yaratib bo'lmadi. Birozdan keyin qaytadan urinib ko'ring.")
            await state.clear()
            return
        photo_file = BufferedInputFile(image_bytes, filename="ai_image.jpg")
        await message.answer_photo(photo_file, caption=f"🖼 <b>{prompt}</b>", parse_mode="HTML")
        await wait_msg.delete()
        log_action(message.from_user.id, "ai_image_gen", prompt[:100])
        await state.clear()
    except Exception as e:
        logging.exception(f"AI rasm natijasi xatosi: {e}")
        await message.answer("❌ Xatolik yuz berdi. Qaytadan urinib ko'ring.")
        await state.clear()

@dp.callback_query(F.data == "ai_meme_start")
async def ai_meme_start(call: CallbackQuery, state: FSMContext):
    try:
        await call.message.edit_text("😂 Mem yaratish uchun avval rasm yuboring:")
        await state.set_state(AdminStates.waiting_meme_photo)
    except Exception as e:
        logging.exception(f"Mem boshlash xatosi: {e}")
    await call.answer()

@dp.message(AdminStates.waiting_meme_photo, F.photo)
async def ai_meme_photo_received(message: Message, state: FSMContext):
    try:
        await state.update_data(meme_photo_id=message.photo[-1].file_id)
        await message.answer("✍️ Yuqori matnni kiriting (bo'lmasa /skip deb yozing):")
        await state.set_state(AdminStates.waiting_meme_top_text)
    except Exception as e:
        logging.exception(f"Mem rasm xatosi: {e}")
        await message.answer("Xatolik yuz berdi.")

@dp.message(AdminStates.waiting_meme_photo)
async def ai_meme_photo_invalid(message: Message):
    await message.answer("❗️ Iltimos, rasm yuboring.")

@dp.message(AdminStates.waiting_meme_top_text)
async def ai_meme_top_text(message: Message, state: FSMContext):
    try:
        text = "" if message.text.strip().lower() == "/skip" else message.text.strip()
        await state.update_data(meme_top=text)
        await message.answer("✍️ Endi pastki matnni kiriting (bo'lmasa /skip deb yozing):")
        await state.set_state(AdminStates.waiting_meme_bottom_text)
    except Exception as e:
        logging.exception(f"Mem yuqori matn xatosi: {e}")
        await message.answer("Xatolik yuz berdi.")

@dp.message(AdminStates.waiting_meme_bottom_text)
async def ai_meme_bottom_text(message: Message, state: FSMContext):
    try:
        bottom_text = "" if message.text.strip().lower() == "/skip" else message.text.strip()
        data = await state.get_data()
        top_text = data.get("meme_top", "")
        photo_id = data.get("meme_photo_id")

        wait_msg = await message.answer("⏳ Mem tayyorlanmoqda...")
        file_info = await bot.get_file(photo_id)
        downloaded = await bot.download_file(file_info.file_path)
        photo_bytes = downloaded.read()

        meme_bytes = create_meme(photo_bytes, top_text, bottom_text)
        meme_file = BufferedInputFile(meme_bytes, filename="meme.jpg")
        await message.answer_photo(meme_file, caption="😂 Tayyor!")
        await wait_msg.delete()
        log_action(message.from_user.id, "ai_meme_created", "")
        await state.clear()
    except Exception as e:
        logging.exception(f"Mem yaratish xatosi: {e}")
        await message.answer("❌ Xatolik yuz berdi. Qaytadan urinib ko'ring.")
        await state.clear()

# ---------- Kollaj yasash ----------
@dp.callback_query(F.data == "ai_collage_start")
async def ai_collage_start(call: CallbackQuery, state: FSMContext):
    try:
        await state.update_data(collage_photos=[])
        await call.message.edit_text(
            "🎭 2 dan 4 tagacha rasm yuboring.\n"
            "Hammasini yuborib bo'lgach, <b>/tayyor</b> deb yozing.",
            parse_mode="HTML"
        )
        await state.set_state(AdminStates.waiting_collage_photos)
    except Exception as e:
        logging.exception(f"Kollaj boshlash xatosi: {e}")
    await call.answer()

@dp.message(AdminStates.waiting_collage_photos, F.photo)
async def ai_collage_photo(message: Message, state: FSMContext):
    try:
        data = await state.get_data()
        photos = data.get("collage_photos", [])
        if len(photos) >= 4:
            await message.answer("❗️ Ko'pi bilan 4 ta rasm. Endi /tayyor deb yozing.")
            return
        photos.append(message.photo[-1].file_id)
        await state.update_data(collage_photos=photos)
        await message.answer(f"✅ {len(photos)} ta rasm qabul qilindi. Yana yuboring yoki /tayyor deb yozing.")
    except Exception as e:
        logging.exception(f"Kollaj rasm xatosi: {e}")

@dp.message(AdminStates.waiting_collage_photos, Command("tayyor"))
async def ai_collage_finish(message: Message, state: FSMContext):
    try:
        data = await state.get_data()
        photos = data.get("collage_photos", [])
        if len(photos) < 2:
            await message.answer("❗️ Kamida 2 ta rasm kerak.")
            return
        wait_msg = await message.answer("⏳ Kollaj tayyorlanmoqda...")
        photo_bytes_list = []
        for file_id in photos:
            file_info = await bot.get_file(file_id)
            downloaded = await bot.download_file(file_info.file_path)
            photo_bytes_list.append(downloaded.read())
        collage_bytes = create_collage(photo_bytes_list)
        collage_file = BufferedInputFile(collage_bytes, filename="collage.jpg")
        await message.answer_photo(collage_file, caption="🎭 Kollaj tayyor!")
        await wait_msg.delete()
        log_action(message.from_user.id, "ai_collage_created", str(len(photos)))
        await state.clear()
    except Exception as e:
        logging.exception(f"Kollaj yakunlash xatosi: {e}")
        await message.answer("❌ Xatolik yuz berdi.")
        await state.clear()

@dp.message(AdminStates.waiting_collage_photos)
async def ai_collage_invalid(message: Message):
    await message.answer("❗️ Rasm yuboring yoki tugatish uchun /tayyor deb yozing.")

# ---------- Tarjimon ----------
@dp.callback_query(F.data == "ai_translate_start")
async def ai_translate_start(call: CallbackQuery, state: FSMContext):
    try:
        await call.message.edit_text("🌐 Tarjima qilmoqchi bo'lgan matningizni yuboring (avtomatik o'zbekchaga tarjima qilinadi):")
        await state.set_state(AdminStates.waiting_translate_text)
    except Exception as e:
        logging.exception(f"Tarjimon boshlash xatosi: {e}")
    await call.answer()

@dp.message(AdminStates.waiting_translate_text)
async def ai_translate_result(message: Message, state: FSMContext):
    try:
        text = message.text.strip() if message.text else ""
        if len(text) < 1:
            await message.answer("❗️ Iltimos, matn yuboring.")
            return
        wait_msg = await message.answer("⏳ Tarjima qilinmoqda...")
        translated = await translate_text(text, "uz")
        if not translated:
            await wait_msg.edit_text("❌ Tarjima qilib bo'lmadi. Qaytadan urinib ko'ring.")
        else:
            await wait_msg.edit_text(f"🌐 <b>Tarjima:</b>\n\n{translated}", parse_mode="HTML")
        await state.clear()
    except Exception as e:
        logging.exception(f"Tarjima natijasi xatosi: {e}")
        await message.answer("❌ Xatolik yuz berdi.")
        await state.clear()

# ---------- Ko'ngilochar menyu ----------
@dp.callback_query(F.data == "fun_menu")
async def fun_menu(call: CallbackQuery, state: FSMContext):
    try:
        await state.clear()
        await call.message.edit_text(
            "🎮 <b>Ko'ngilochar bo'lim:</b>\n\n"
            "Viktorina, tasodifiy kino, reyting, promo-kod va boshqa qiziqarli narsalar shu yerda!",
            parse_mode="HTML",
            reply_markup=fun_menu_keyboard()
        )
    except Exception as e:
        logging.exception(f"Ko'ngilochar menyu xatosi: {e}")
    await call.answer()

# ---------- Viktorina ----------
def quiz_keyboard(question_id, option_a, option_b, option_c, option_d):
    builder = InlineKeyboardBuilder()
    builder.button(text=f"A) {option_a}", callback_data=f"quiz_ans_{question_id}_A")
    builder.button(text=f"B) {option_b}", callback_data=f"quiz_ans_{question_id}_B")
    builder.button(text=f"C) {option_c}", callback_data=f"quiz_ans_{question_id}_C")
    builder.button(text=f"D) {option_d}", callback_data=f"quiz_ans_{question_id}_D")
    builder.adjust(1)
    return builder.as_markup()

@dp.callback_query(F.data == "quiz_start")
async def quiz_start(call: CallbackQuery):
    try:
        q = get_random_unanswered_question(call.from_user.id)
        if not q:
            await call.message.edit_text("🧠 Sizga barcha savollar taqdim etildi! Keyinroq yangilari qo'shilishini kuting.", reply_markup=fun_menu_keyboard())
            return
        qid, question, a, b, c, d, _ = q
        await call.message.edit_text(f"🧠 <b>Savol:</b>\n\n{question}", parse_mode="HTML", reply_markup=quiz_keyboard(qid, a, b, c, d))
    except Exception as e:
        logging.exception(f"Viktorina boshlash xatosi: {e}")
    await call.answer()

@dp.callback_query(F.data.startswith("quiz_ans_"))
async def quiz_answer(call: CallbackQuery):
    try:
        parts = call.data.split("_")
        question_id = int(parts[2])
        chosen = parts[3]
        user_id = call.from_user.id

        already = db_execute("SELECT 1 FROM quiz_answers WHERE user_id=? AND question_id=?", (user_id, question_id), fetchone=True)
        if already:
            await call.answer("Siz bu savolga allaqachon javob bergansiz.", show_alert=True)
            return

        row = db_execute("SELECT correct_option FROM quiz_questions WHERE id=?", (question_id,), fetchone=True)
        if not row:
            await call.answer("Savol topilmadi.", show_alert=True)
            return
        correct = row[0]
        is_correct = chosen == correct
        record_quiz_answer(user_id, question_id, is_correct)

        if is_correct:
            bonus = random.randint(5, 15)
            update_bonus(user_id, bonus)
            await call.message.edit_text(f"✅ To'g'ri javob! +{bonus} bonus ball qo'lga kiritdingiz.")
        else:
            await call.message.edit_text(f"❌ Noto'g'ri javob. To'g'ri variant: {correct}")

        kb = InlineKeyboardBuilder()
        kb.button(text="➡️ Keyingi savol", callback_data="quiz_start")
        kb.button(text="🔙 Orqaga", callback_data="fun_menu")
        kb.adjust(1)
        await call.message.edit_reply_markup(reply_markup=kb.as_markup())
    except Exception as e:
        logging.exception(f"Viktorina javob xatosi: {e}")
        await call.answer("Xatolik yuz berdi.", show_alert=True)
    await call.answer()

# ---------- Tasodifiy kino ----------
@dp.callback_query(F.data == "random_movie")
async def random_movie(call: CallbackQuery):
    try:
        user_id = call.from_user.id
        status = get_user_status(user_id)
        if status in ("vip", "premium", "admin"):
            row = db_execute("SELECT code FROM movies ORDER BY RANDOM() LIMIT 1", fetchone=True)
        else:
            row = db_execute("SELECT code FROM movies WHERE is_vip=0 AND is_premium=0 ORDER BY RANDOM() LIMIT 1", fetchone=True)
        if not row:
            await call.answer("Hozircha kinolar mavjud emas.", show_alert=True)
            return
        await send_movie(call.message, user_id, row[0])
    except Exception as e:
        logging.exception(f"Tasodifiy kino xatosi: {e}")
        await call.answer("Xatolik yuz berdi.", show_alert=True)
    await call.answer()

# ---------- Reyting jadvali ----------
@dp.callback_query(F.data == "leaderboard_menu")
async def leaderboard_menu(call: CallbackQuery):
    try:
        top_ref = get_top_referrers(10)
        top_bonus = get_top_bonus_users(10)

        text = "🏆 <b>Eng ko'p referal qilganlar:</b>\n"
        if top_ref:
            for i, (referrer_id, cnt) in enumerate(top_ref, 1):
                user_row = db_execute("SELECT first_name FROM users WHERE user_id=?", (referrer_id,), fetchone=True)
                name = user_row[0] if user_row else str(referrer_id)
                text += f"{i}. {name} — {cnt} kishi\n"
        else:
            text += "— hozircha yo'q —\n"

        text += "\n💰 <b>Eng ko'p bonus to'plaganlar:</b>\n"
        if top_bonus:
            for i, (uid, first_name, bonus) in enumerate(top_bonus, 1):
                text += f"{i}. {first_name or uid} — {bonus} ball\n"
        else:
            text += "— hozircha yo'q —\n"

        kb = InlineKeyboardBuilder()
        kb.button(text="🔙 Orqaga", callback_data="fun_menu")
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb.as_markup())
    except Exception as e:
        logging.exception(f"Reyting jadvali xatosi: {e}")
    await call.answer()

# ---------- Promo-kod ----------
@dp.callback_query(F.data == "promo_redeem_start")
async def promo_redeem_start(call: CallbackQuery, state: FSMContext):
    try:
        await call.message.edit_text("🎟 Promo-kodni kiriting:")
        await state.set_state(AdminStates.waiting_promo_redeem)
    except Exception as e:
        logging.exception(f"Promo-kod boshlash xatosi: {e}")
    await call.answer()

@dp.message(AdminStates.waiting_promo_redeem)
async def promo_redeem_result(message: Message, state: FSMContext):
    try:
        code = message.text.strip().upper()
        success, msg = redeem_promo_code(code, message.from_user.id)
        await message.answer(msg)
        await state.clear()
    except Exception as e:
        logging.exception(f"Promo-kod natijasi xatosi: {e}")
        await message.answer("❌ Xatolik yuz berdi.")
        await state.clear()

# ---------- Bonus do'koni ----------
@dp.callback_query(F.data == "bonus_shop")
async def bonus_shop(call: CallbackQuery):
    try:
        user_id = call.from_user.id
        balance = get_user_bonus(user_id)
        kb = InlineKeyboardBuilder()
        kb.button(text=f"✅ Sotib olish ({BONUS_SHOP_COST} ball)", callback_data="buy_bonus_vip")
        kb.button(text="🔙 Orqaga", callback_data="fun_menu")
        kb.adjust(1)
        await call.message.edit_text(
            f"🛍 <b>Bonus do'koni</b>\n\n"
            f"💰 Balansingiz: {balance} ball\n\n"
            f"👑 {BONUS_SHOP_COST} ball → {BONUS_SHOP_DAYS} kunlik VIP obuna",
            parse_mode="HTML",
            reply_markup=kb.as_markup()
        )
    except Exception as e:
        logging.exception(f"Bonus do'koni xatosi: {e}")
    await call.answer()

@dp.callback_query(F.data == "buy_bonus_vip")
async def buy_bonus_vip(call: CallbackQuery):
    try:
        user_id = call.from_user.id
        balance = get_user_bonus(user_id)
        if balance < BONUS_SHOP_COST:
            await call.answer(f"❌ Yetarli ball yo'q. Kerak: {BONUS_SHOP_COST}, sizda: {balance}", show_alert=True)
            return
        update_bonus(user_id, -BONUS_SHOP_COST)
        expires_at = activate_subscription(user_id, "vip", days=BONUS_SHOP_DAYS)
        expires_date = datetime.fromisoformat(expires_at).strftime("%d.%m.%Y")
        log_action(user_id, "bonus_shop_purchase", f"days={BONUS_SHOP_DAYS}")
        await call.message.edit_text(f"✅ {BONUS_SHOP_DAYS} kunlik VIP obuna faollashtirildi!\n📅 Muddati: {expires_date} gacha")
    except Exception as e:
        logging.exception(f"Bonus do'konidan sotib olish xatosi: {e}")
        await call.answer("Xatolik yuz berdi.", show_alert=True)
    await call.answer()

# ---------- Fikr-mulohaza ----------
@dp.callback_query(F.data == "feedback_start")
async def feedback_start(call: CallbackQuery, state: FSMContext):
    try:
        await call.message.edit_text("💭 Fikr, taklif yoki shikoyatingizni yozing:")
        await state.set_state(AdminStates.waiting_feedback_text)
    except Exception as e:
        logging.exception(f"Fikr boshlash xatosi: {e}")
    await call.answer()

@dp.message(AdminStates.waiting_feedback_text)
async def feedback_result(message: Message, state: FSMContext):
    try:
        text = message.text.strip() if message.text else ""
        add_feedback(message.from_user.id, text)
        await message.answer("✅ Fikringiz uchun rahmat! Admin ko'rib chiqadi.")
        username_display = message.from_user.username or "yo\u02bcq"
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(
                    admin_id,
                    f"💭 <b>Yangi fikr-mulohaza</b>\n\n"
                    f"👤 {message.from_user.first_name} (@{username_display})\n"
                    f"🆔 ID: <code>{message.from_user.id}</code>\n\n"
                    f"{text}",
                    parse_mode="HTML"
                )
            except Exception:
                pass
        await state.clear()
    except Exception as e:
        logging.exception(f"Fikr natijasi xatosi: {e}")
        await message.answer("❌ Xatolik yuz berdi.")
        await state.clear()

# ---------- Kino so'rash ----------
@dp.callback_query(F.data == "movie_request_start")
async def movie_request_start(call: CallbackQuery, state: FSMContext):
    try:
        await call.message.edit_text("⭐ Qaysi kino yoki serialni qo'shishimizni xohlaysiz? Nomini yozing:")
        await state.set_state(AdminStates.waiting_movie_request_text)
    except Exception as e:
        logging.exception(f"Kino so'rash boshlash xatosi: {e}")
    await call.answer()

@dp.message(AdminStates.waiting_movie_request_text)
async def movie_request_result(message: Message, state: FSMContext):
    try:
        text = message.text.strip() if message.text else ""
        add_movie_request(message.from_user.id, text)
        await message.answer("✅ So'rovingiz qabul qilindi! Admin ko'rib chiqadi.")
        username_display = message.from_user.username or "yo\u02bcq"
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(
                    admin_id,
                    f"⭐ <b>Yangi kino so'rovi</b>\n\n"
                    f"👤 {message.from_user.first_name} (@{username_display})\n"
                    f"🆔 ID: <code>{message.from_user.id}</code>\n\n"
                    f"🎬 {text}",
                    parse_mode="HTML"
                )
            except Exception:
                pass
        await state.clear()
    except Exception as e:
        logging.exception(f"Kino so'rash natijasi xatosi: {e}")
        await message.answer("❌ Xatolik yuz berdi.")
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

async def inactive_users_reminder_loop():
    """7 kundan beri faol bo'lmagan foydalanuvchilarga qaytib kelish uchun eslatma yuboradi
    (bir foydalanuvchiga takroran 7 kunda bir martadan ortiq yubormaydi)"""
    while True:
        await asyncio.sleep(24 * 60 * 60)  # sutkasiga bir marta tekshiradi
        try:
            week_ago = (datetime.now() - timedelta(days=7)).isoformat()
            rows = db_execute(
                "SELECT user_id FROM users WHERE registration_status='approved' AND is_banned=0 "
                "AND last_active_at IS NOT NULL AND last_active_at < ? "
                "AND (last_inactivity_reminder IS NULL OR last_inactivity_reminder < ?)",
                (week_ago, week_ago), fetchall=True
            ) or []
            latest_movies = db_execute("SELECT title FROM movies ORDER BY added_at DESC LIMIT 3", fetchall=True) or []
            movie_names = ", ".join([m[0] for m in latest_movies]) if latest_movies else ""
            extra = f"\n\n🆕 So'nggi qo'shilganlar: {movie_names}" if movie_names else ""

            for (user_id,) in rows:
                try:
                    await bot.send_message(
                        user_id,
                        "👋 Sizni sog'indik!\n"
                        "Botimizda ko'plab yangi qiziqarli kino va seriallar qo'shildi." + extra + "\n\n"
                        "🎬 Qaytib kelib, yangiliklarni ko'rib chiqing!"
                    )
                    db_execute(
                        "UPDATE users SET last_inactivity_reminder=? WHERE user_id=?",
                        (datetime.now().isoformat(), user_id), commit=True
                    )
                except Exception:
                    pass
        except Exception as e:
            logging.exception(f"Faol bo'lmaganlarga eslatma yuborish xatosi: {e}")

async def weekly_leaderboard_channel_post_loop():
    """Har 7 kunda kino kanaliga eng faol foydalanuvchilar reytingini chiroyli qilib e'lon qiladi"""
    while True:
        await asyncio.sleep(7 * 24 * 60 * 60)
        if not POST_CHANNEL:
            continue
        try:
            top_ref = get_top_referrers(5)
            medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
            text = "🏆 <b>HAFTALIK REYTING</b>\n━━━━━━━━━━━━━━━\n\n👥 Eng ko'p do'st taklif qilganlar:\n\n"
            if top_ref:
                for i, (referrer_id, cnt) in enumerate(top_ref):
                    user_row = db_execute("SELECT first_name FROM users WHERE user_id=?", (referrer_id,), fetchone=True)
                    name = user_row[0] if user_row else "Foydalanuvchi"
                    text += f"{medals[i]} {name} — {cnt} kishi\n"
            else:
                text += "— hozircha ma'lumot yo'q —\n"
            text += "\n🎁 Siz ham do'stlaringizni taklif qilib, bepul VIP obunaga ega bo'ling!"

            bot_info = await bot.get_me()
            kb = InlineKeyboardBuilder()
            kb.button(text="🤖 Botga o'tish", url=f"https://t.me/{bot_info.username}")
            await bot.send_message(POST_CHANNEL, text, parse_mode="HTML", reply_markup=kb.as_markup())
        except Exception as e:
            logging.exception(f"Haftalik reyting kanal posti xatosi: {e}")

async def daily_quiz_channel_announcement_loop():
    """Har 24 soatda kino kanaliga kunlik viktorina haqida qiziqarli e'lon qiladi (agar savollar mavjud bo'lsa)"""
    while True:
        await asyncio.sleep(24 * 60 * 60)
        if not POST_CHANNEL:
            continue
        try:
            has_questions = db_execute("SELECT COUNT(*) FROM quiz_questions", fetchone=True)
            if not has_questions or has_questions[0] == 0:
                continue
            bot_info = await bot.get_me()
            kb = InlineKeyboardBuilder()
            kb.button(text="🧠 Viktorinani boshlash", url=f"https://t.me/{bot_info.username}")
            await bot.send_message(
                POST_CHANNEL,
                "🧠 <b>Bugungi bilim sinovi tayyor!</b>\n\n"
                "Kino haqidagi savollarga javob bering, bonus ball yutib oling! 🎁",
                parse_mode="HTML",
                reply_markup=kb.as_markup()
            )
        except Exception as e:
            logging.exception(f"Kunlik viktorina kanal posti xatosi: {e}")

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
    asyncio.create_task(inactive_users_reminder_loop())
    asyncio.create_task(weekly_leaderboard_channel_post_loop())
    asyncio.create_task(daily_quiz_channel_announcement_loop())
    asyncio.create_task(daily_admin_report_loop())
    asyncio.create_task(self_ping_loop())
    await dp.start_polling(bot, skip_updates=True)

if __name__ == "__main__":
    asyncio.run(main())
