#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ALL-IN-ONE DISCORD BOT (Full Updated Version)
"""

import asyncio
import collections
import copy
import email.utils
import json
import logging
import logging.handlers
import os
import random
import re
import shutil
import sqlite3
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Set, Tuple

import aiohttp
from aiohttp import web
import discord
import static_ffmpeg
import yt_dlp
from discord import app_commands
from discord.ext import commands, tasks

# Render / Linux ortamlari icin FFmpeg ikili dosyalarini PATH'e ekle
static_ffmpeg.add_paths()

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ════════════════════════════════════════════════════════════════════════════
#  CONFIGURATION
# ════════════════════════════════════════════════════════════════════════════
TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
PREFIX = os.getenv("BOT_PREFIX", "!")
PORT = int(os.getenv("PORT", 10000))
DATA_FILE = Path(os.getenv("DATA_FILE", "bot_data.json"))
ECONOMY_DB_FILE = Path(os.getenv("ECONOMY_DB_FILE", "economy.db"))
WORDS_FILE = Path(os.getenv("WORDS_FILE", "kelimeler.txt"))
DEFAULT_YT_SOURCE = (os.getenv("YOUTUBE_CHANNEL_ID") or os.getenv("YOUTUBE_RSS_URL") or "").strip()

try:
    DEV_GUILD_ID = int(os.getenv("DEV_GUILD_ID") or 0)
except ValueError:
    DEV_GUILD_ID = 0

# Kanal isimleri
CH_WELCOME = "gelen-giden"
CH_VIDEO = "video-duyuru"
CH_ANNOUNCE = "duyurular"
CH_LEVELUP = "seviye-atladi"
CH_GENERAL = "genel-sohbet"
CH_BOTCMD = "bot-komut"
CH_COUNT = "sayim-oyunu"
CH_WORD = "kelime-turetme"
CH_HANGMAN = "adam-asmaca"
CH_GAME_CHAT = "oyun-sohbet"
CH_VIP_CHAT = "vip-sohbet"
CH_MODLOG = "mod-log"
CH_STAFF = "yonetim-sohbet"
VC_MASTER = "+ | Oda Oluştur"
VC_STATS = "📊 Toplam Üye:"

# Anti-spam ayarlari
SPAM_WINDOW = 3.0
SPAM_MAX_MESSAGES = 5
SPAM_REPEAT_WINDOW = 15.0
SPAM_REPEAT_COUNT = 4
SPAM_TIMEOUT = timedelta(minutes=5)

# Sayı sayma oyunu
COUNT_NO_DOUBLE = True

# XP & Ekonomi ayarları
MSG_XP_COOLDOWN = 45.0
MSG_XP_RANGE = (15, 25)
MSG_COIN_RANGE = (2, 5)
VOICE_XP_PER_MIN = 12
VOICE_COINS_PER_MIN = 5
DAILY_REWARD = 500
DAILY_COOLDOWN = 86400  # 24 saat

# Müzik
MAX_QUEUE = 100
IDLE_TIMEOUT = 300
ALONE_TIMEOUT = 60
STREAM_TTL = 1200

# Renkler
C_OK = discord.Color.green()
C_ERR = discord.Color.red()
C_INFO = discord.Color.blurple()
C_WARN = discord.Color.orange()
C_GOLD = discord.Color.gold()

# +18 ve Yasaklı Kelime Filtresi
BAD_WORDS = {
    "amk", "aq", "siktir", "orospu", "piç", "yavşak", "pezevenk", "porno", "sex", "sikim", "yarrak"
}

# Varsayılan başlangıç sözlüğü (kelimeler.txt yoksa oluşturulur)
FALLBACK_WORDS = [
    "araba", "aslan", "ağaç", "akşam", "altın", "armut", "ayna", "bulut", "bahçe", "balık",
    "bayrak", "bilgisayar", "çiçek", "çocuk", "çorba", "deniz", "dünya", "elma", "ekmek", "evren",
    "fare", "fener", "gemi", "güneş", "gökyüzü", "haber", "hayat", "ırmak", "insan", "kalem",
    "kapı", "kedi", "kelebek", "kitap", "köpek", "kuş", "limon", "masa", "macera", "meyve",
    "nehir", "orman", "oyun", "öğrenci", "pencere", "portakal", "rüzgar", "saat", "sabah", "sevgi",
    "sokak", "şehir", "şemsiye", "tabak", "telefon", "toprak", "uçak", "uçurtma", "ümit", "üzüm",
    "vatan", "yağmur", "yaprak", "yazılım", "yıldız", "zaman", "zeytin", "zürafa",
]

# ════════════════════════════════════════════════════════════════════════════
#  LOGGING
# ════════════════════════════════════════════════════════════════════════════
log = logging.getLogger("allinone")

def setup_logging() -> None:
    fmt = logging.Formatter("[{asctime}] [{levelname:<8}] {name}: {message}", "%Y-%m-%d %H:%M:%S", style="{")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root.addHandler(console)
    try:
        fileh = logging.handlers.RotatingFileHandler("bot.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        fileh.setFormatter(fmt)
        root.addHandler(fileh)
    except OSError:
        pass
    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("discord.http").setLevel(logging.WARNING)

# ════════════════════════════════════════════════════════════════════════════
#  PERSISTENT STORE (JSON)
# ════════════════════════════════════════════════════════════════════════════
DEFAULT_GUILD: Dict[str, Any] = {
    "counting": {"last": 0, "last_user": None, "high": 0},
    "wordchain": {"last_word": None, "last_user": None, "used": [], "chain": 0},
    "antispam": {"global": True, "channels": {}, "action": "timeout"},
    "filter": {"enabled": True},
    "youtube": {"source": None, "seen": [], "initialized": False},
    "temp_channels": {},
}

def _merge_defaults(target: Dict[str, Any], defaults: Dict[str, Any]) -> None:
    for key, value in defaults.items():
        if key not in target:
            target[key] = copy.deepcopy(value)
        elif isinstance(value, dict) and isinstance(target[key], dict):
            _merge_defaults(target[key], value)

class Store:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: Dict[str, Any] = {"guilds": {}}
        self._dirty = False
        self._task: Optional[asyncio.Task] = None
        self._load()

    def _load(self) -> None:
        try:
            if self.path.exists():
                self.data = json.loads(self.path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            log.exception("Veri dosyası okunamadı, bozuk dosya yedekleniyor.")
            try:
                self.path.rename(self.path.with_suffix(".corrupt"))
            except OSError:
                pass
            self.data = {"guilds": {}}
        self.data.setdefault("guilds", {})

    def guild(self, guild_id: int) -> Dict[str, Any]:
        g = self.data["guilds"].setdefault(str(guild_id), {})
        _merge_defaults(g, DEFAULT_GUILD)
        return g

    def flush(self) -> None:
        if not self._dirty:
            return
        self._dirty = False
        tmp = self.path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), "utf-8")
            os.replace(tmp, self.path)
        except OSError:
            log.exception("Veri dosyası yazılamadı.")

    async def _flush_later(self) -> None:
        await asyncio.sleep(2.0)
        self.flush()

    def save_soon(self) -> None:
        self._dirty = True
        try:
            if self._task is None or self._task.done():
                self._task = asyncio.get_running_loop().create_task(self._flush_later())
        except RuntimeError:
            self.flush()

# ════════════════════════════════════════════════════════════════════════════
#  SQLITE ECONOMY & PROGRESSION DATABASE
# ════════════════════════════════════════════════════════════════════════════
class EconomyDB:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = asyncio.Lock()
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.execute("PRAGMA synchronous=NORMAL;")
        self._init_tables()

    def _init_tables(self) -> None:
        with self.conn:
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    xp INTEGER NOT NULL DEFAULT 0,
                    level INTEGER NOT NULL DEFAULT 1,
                    coins INTEGER NOT NULL DEFAULT 100,
                    last_daily INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (guild_id, user_id)
                )
            """)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS guild_economy (
                    guild_id INTEGER PRIMARY KEY,
                    level_channel_id INTEGER DEFAULT NULL
                )
            """)

    @staticmethod
    def xp_needed(level: int) -> int:
        return level * 150

    def _ensure_user(self, guild_id: int, user_id: int) -> Dict[str, int]:
        cur = self.conn.execute(
            "SELECT xp, level, coins, last_daily FROM users WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        row = cur.fetchone()
        if row is None:
            self.conn.execute(
                "INSERT INTO users (guild_id, user_id, xp, level, coins, last_daily) VALUES (?, ?, 0, 1, 100, 0)",
                (guild_id, user_id),
            )
            return {"xp": 0, "level": 1, "coins": 100, "last_daily": 0}
        return {"xp": row[0], "level": row[1], "coins": row[2], "last_daily": row[3]}

    async def get_user(self, guild_id: int, user_id: int) -> Dict[str, int]:
        async with self.lock:
            with self.conn:
                return self._ensure_user(guild_id, user_id)

    async def add_xp_and_coins(
        self, guild_id: int, user_id: int, xp_gain: int, coin_gain: int = 0
    ) -> Tuple[bool, int, int, int]:
        async with self.lock:
            with self.conn:
                u = self._ensure_user(guild_id, user_id)
                xp = u["xp"] + xp_gain
                level = u["level"]
                coins = u["coins"] + coin_gain
                leveled_up = False
                reward_coins = 0

                while xp >= self.xp_needed(level):
                    xp -= self.xp_needed(level)
                    level += 1
                    leveled_up = True
                    reward_coins += level * 100

                coins += reward_coins
                self.conn.execute(
                    "UPDATE users SET xp = ?, level = ?, coins = ? WHERE guild_id = ? AND user_id = ?",
                    (xp, level, coins, guild_id, user_id),
                )
                return leveled_up, level, reward_coins, coins

    async def claim_daily(self, guild_id: int, user_id: int) -> Tuple[bool, int, int]:
        now = int(time.time())
        async with self.lock:
            with self.conn:
                u = self._ensure_user(guild_id, user_id)
                elapsed = now - u["last_daily"]
                if elapsed < DAILY_COOLDOWN:
                    return False, DAILY_COOLDOWN - elapsed, u["coins"]
                new_coins = u["coins"] + DAILY_REWARD
                self.conn.execute(
                    "UPDATE users SET coins = ?, last_daily = ? WHERE guild_id = ? AND user_id = ?",
                    (new_coins, now, guild_id, user_id),
                )
                return True, 0, new_coins

    async def transfer_coins(
        self, guild_id: int, sender_id: int, receiver_id: int, amount: int
    ) -> Tuple[bool, str, int, int]:
        async with self.lock:
            with self.conn:
                sender = self._ensure_user(guild_id, sender_id)
                receiver = self._ensure_user(guild_id, receiver_id)
                if sender["coins"] < amount:
                    return False, f"Yetersiz bakiye! Mevcut coinin: **{sender['coins']}**", sender["coins"], receiver["coins"]
                s_new = sender["coins"] - amount
                r_new = receiver["coins"] + amount
                self.conn.execute(
                    "UPDATE users SET coins = ? WHERE guild_id = ? AND user_id = ?",
                    (s_new, guild_id, sender_id),
                )
                self.conn.execute(
                    "UPDATE users SET coins = ? WHERE guild_id = ? AND user_id = ?",
                    (r_new, guild_id, receiver_id),
                )
                return True, "OK", s_new, r_new

    async def gamble(
        self, guild_id: int, user_id: int, amount: Optional[int]
    ) -> Tuple[bool, str, bool, int, int]:
        async with self.lock:
            with self.conn:
                u = self._ensure_user(guild_id, user_id)
                balance = u["coins"]
                wager = balance if amount is None else amount
                if wager <= 0:
                    return False, "Oynamak için en az **1 Coin** bakiyen olmalı!", False, 0, balance
                if balance < wager:
                    return False, f"Yetersiz bakiye! Cüzdanında **{balance} Coin** var.", False, wager, balance

                won = random.random() < 0.50
                new_balance = balance + wager if won else balance - wager
                self.conn.execute(
                    "UPDATE users SET coins = ? WHERE guild_id = ? AND user_id = ?",
                    (new_balance, guild_id, user_id),
                )
                return True, "OK", won, wager, new_balance

    async def set_level_channel(self, guild_id: int, channel_id: int) -> None:
        async with self.lock:
            with self.conn:
                self.conn.execute(
                    """
                    INSERT INTO guild_economy (guild_id, level_channel_id)
                    VALUES (?, ?)
                    ON CONFLICT(guild_id) DO UPDATE SET level_channel_id = excluded.level_channel_id
                    """,
                    (guild_id, channel_id),
                )

    async def get_level_channel(self, guild_id: int) -> Optional[int]:
        async with self.lock:
            cur = self.conn.execute(
                "SELECT level_channel_id FROM guild_economy WHERE guild_id = ?", (guild_id,)
            )
            row = cur.fetchone()
            return row[0] if row and row[0] else None

    async def get_leaderboard(self, guild_id: int, by_coins: bool = False, limit: int = 10) -> List[Tuple[int, int, int, int]]:
        order = "coins DESC, level DESC, xp DESC" if by_coins else "level DESC, xp DESC, coins DESC"
        async with self.lock:
            cur = self.conn.execute(
                f"SELECT user_id, level, xp, coins FROM users WHERE guild_id = ? ORDER BY {order} LIMIT ?",
                (guild_id, limit),
            )
            return list(cur.fetchall())

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass

# ════════════════════════════════════════════════════════════════════════════
#  GENERIC HELPERS & LOCAL DICTIONARY LOADER
# ════════════════════════════════════════════════════════════════════════════
TR_ALPHABET = set("abcçdefgğhıijklmnoöprsştuüvyz")
WORD_RE = re.compile(r"[a-zçğıöşüâîû]{2,40}")
COUNT_RE = re.compile(r"[0-9]{1,15}")
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

def tr_lower(text: str) -> str:
    return text.replace("I", "ı").replace("İ", "i").lower()

def tr_upper(text: str) -> str:
    return text.replace("i", "İ").replace("ı", "I").upper()

def load_local_dictionary() -> Set[str]:
    candidates = [WORDS_FILE, Path(__file__).resolve().parent / WORDS_FILE.name]
    target_path: Optional[Path] = next((p for p in candidates if p.exists()), None)

    if target_path is None:
        target_path = WORDS_FILE
        try:
            target_path.write_text("\n".join(FALLBACK_WORDS) + "\n", encoding="utf-8")
            log.warning("'%s' bulunamadı; örnek başlangıç sözlüğü oluşturuldu. Tam liste ile değiştirebilirsiniz.", target_path)
        except OSError:
            log.warning("'%s' bulunamadı ve diske yazılamadı; yerleşik yedek kelime seti kullanılıyor.", target_path)
            return {tr_lower(w) for w in FALLBACK_WORDS}

    words: Set[str] = set()
    try:
        with target_path.open("r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                w = tr_lower(line.strip())
                if w:
                    words.add(w)
        log.info("Yerel sözlük yüklendi (%s): %d kelime O(1) set yapısında hazır.", target_path, len(words))
    except OSError:
        log.exception("kelimeler.txt okunurken hata oluştu, yedek set yükleniyor.")
        words = {tr_lower(w) for w in FALLBACK_WORDS}
    return words

def short_err(exc: BaseException, limit: int = 250) -> str:
    return ANSI_RE.sub("", str(exc)).strip()[:limit] or exc.__class__.__name__

def trunc(text: str, limit: int = 1000) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"

def fmt_duration(seconds: Optional[int]) -> str:
    if not seconds:
        return "🔴 CANLI"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

async def safe_delete(message: discord.Message) -> bool:
    try:
        await message.delete()
        return True
    except (discord.NotFound, discord.Forbidden):
        return False
    except discord.HTTPException:
        return False

async def send_temp(channel: discord.abc.Messageable, content: str, seconds: float = 6.0) -> None:
    try:
        await channel.send(content, delete_after=seconds)
    except discord.HTTPException:
        pass

async def send_log(guild: discord.Guild, embed: discord.Embed) -> None:
    ch = discord.utils.get(guild.text_channels, name=CH_MODLOG)
    if ch is None:
        return
    try:
        await ch.send(embed=embed)
    except discord.HTTPException:
        pass

async def respond(
    interaction: discord.Interaction,
    content: Optional[str] = None,
    *,
    embed: Optional[discord.Embed] = None,
    ephemeral: bool = False,
    **kwargs: Any,
) -> None:
    try:
        if interaction.response.is_done():
            await interaction.followup.send(content=content, embed=embed, ephemeral=ephemeral, **kwargs)
        else:
            await interaction.response.send_message(content=content, embed=embed, ephemeral=ephemeral, **kwargs)
    except discord.HTTPException:
        pass

def simple_embed(title: str, description: str = "", color: discord.Color = C_INFO) -> discord.Embed:
    return discord.Embed(title=title, description=description or None, color=color)

# ════════════════════════════════════════════════════════════════════════════
#  ROLE DEFINITIONS & PERMISSION LOGIC
# ════════════════════════════════════════════════════════════════════════════
_BASE = dict(
    view_channel=True, send_messages=True, embed_links=True, attach_files=True, read_message_history=True,
    add_reactions=True, external_emojis=True, use_application_commands=True, connect=True, speak=True,
    stream=True, use_voice_activation=True, change_nickname=True, create_public_threads=True,
    send_messages_in_threads=True,
)

@dataclass(frozen=True)
class RoleSpec:
    key: str
    name: str
    aliases: Tuple[str, ...]
    color: discord.Color
    perms: discord.Permissions
    hoist: bool
    mentionable: bool

ROLE_SPECS: List[RoleSpec] = [
    RoleSpec("owner", "👑 Kurucu", ("Kurucu", "Owner"), discord.Color.gold(),
             discord.Permissions(administrator=True), True, False),
    RoleSpec("admin", "🛡️ Yönetici", ("Yönetici", "Yonetici", "Administrator", "Admin"), discord.Color.red(),
             discord.Permissions(**_BASE, kick_members=True, ban_members=True, manage_messages=True,
                                 manage_threads=True, moderate_members=True, manage_nicknames=True,
                                 manage_channels=True, view_audit_log=True, mention_everyone=True,
                                 mute_members=True, deafen_members=True, move_members=True,
                                 priority_speaker=True, create_instant_invite=True),
             True, True),
    RoleSpec("mod", "⚔️ Moderatör", ("Moderatör", "Moderator", "Mod"), discord.Color.orange(),
             discord.Permissions(**_BASE, manage_messages=True, manage_threads=True, moderate_members=True,
                                 mute_members=True, deafen_members=True, move_members=True),
             True, True),
    RoleSpec("bot", "🤖 Bot", ("Bot", "Botlar"), discord.Color.blurple(),
             discord.Permissions(**_BASE, manage_messages=True, manage_channels=True, manage_roles=True,
                                 moderate_members=True, move_members=True, mute_members=True,
                                 mention_everyone=True),
             True, False),
    RoleSpec("vip", "⭐ Özel Üye", ("Özel Üye", "Ozel Uye", "VIP"), discord.Color.purple(),
             discord.Permissions(**_BASE, priority_speaker=True, create_instant_invite=True),
             True, True),
    RoleSpec("gamer", "🎮 Oyuncu", ("Oyuncu", "Gamer"), discord.Color.green(),
             discord.Permissions(**_BASE), False, True),
    RoleSpec("member", "👤 Üye", ("Üye", "Uye", "Member"), discord.Color.light_grey(),
             discord.Permissions(**_BASE), False, False),
]
ROLE_BY_KEY: Dict[str, RoleSpec] = {s.key: s for s in ROLE_SPECS}

def find_role(guild: discord.Guild, spec: RoleSpec) -> Optional[discord.Role]:
    wanted = {spec.name.casefold(), *[a.casefold() for a in spec.aliases]}
    for role in guild.roles:
        if role.name.casefold() in wanted:
            return role
    return None

def member_has_role(member: discord.Member, key: str) -> bool:
    spec = ROLE_BY_KEY[key]
    wanted = {spec.name.casefold(), *[a.casefold() for a in spec.aliases]}
    return any(r.name.casefold() in wanted for r in member.roles)

def staff_level(user: Any) -> int:
    if not isinstance(user, discord.Member):
        return 0
    perms = user.guild_permissions
    if user.id == user.guild.owner_id or perms.administrator or member_has_role(user, "owner"):
        return 3
    if member_has_role(user, "admin") or perms.manage_guild:
        return 2
    if member_has_role(user, "mod") or perms.manage_messages or perms.moderate_members:
        return 1
    return 0

class NotAuthorized(app_commands.CheckFailure):
    pass

def require_staff(level: int):
    async def predicate(interaction: discord.Interaction) -> bool:
        if staff_level(interaction.user) >= level:
            return True
        raise NotAuthorized("Bu komut için yeterli yetkin yok.")
    return app_commands.check(predicate)

# ════════════════════════════════════════════════════════════════════════════
#  SERVER LAYOUT (categories / channels)
# ════════════════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class ChannelSpec:
    name: str
    kind: str
    access: Optional[str] = None
    topic: Optional[str] = None
    user_limit: int = 0

@dataclass(frozen=True)
class CategorySpec:
    name: str
    access: str
    channels: Tuple[ChannelSpec, ...]

LAYOUT: List[CategorySpec] = [
    CategorySpec("📢・BİLGİ", "readonly", (
        ChannelSpec(CH_WELCOME, "text", "readonly", "Giren çıkan üyeler burada görünür."),
        ChannelSpec(CH_VIDEO, "text", "readonly", "Yeni YouTube videoları otomatik duyurulur."),
        ChannelSpec(CH_ANNOUNCE, "text", "readonly", "Resmî sunucu duyuruları."),
        ChannelSpec(CH_LEVELUP, "text", "readonly", "Seviye atlayan üyeler ve coin ödülleri burada duyurulur."),
        ChannelSpec(VC_STATS, "voice", "stats", "Toplam üye sayısı (kilitli)."),
    )),
    CategorySpec("💬・SOHBET", "public", (
        ChannelSpec(CH_GENERAL, "text", "public", "Genel sohbet kanalı."),
        ChannelSpec(CH_BOTCMD, "text", "public", "Sadece bot komutları! Sohbet yasak."),
        ChannelSpec(CH_COUNT, "text", "public", "1'den başla ve sırayla say! Yanlışta sıfırlanmaz, kaldığı yerden devam eder."),
        ChannelSpec(CH_WORD, "text", "public", "Kelime türetme: önceki kelimenin son harfiyle başla (yerel sözlük destekli)."),
        ChannelSpec(CH_HANGMAN, "text", "public", "Tek veya iki kişilik gizli kelimeli Adam Asmaca için /adam-asmaca yaz."),
    )),
    CategorySpec("🔊・SES KANALLARI", "public", (
        ChannelSpec(VC_MASTER, "voice", "public"),
        ChannelSpec("🎵 Müzik", "voice", "public"),
        ChannelSpec("💤 AFK", "voice", "public"),
    )),
    CategorySpec("🎮・OYUN", "gamer", (
        ChannelSpec(CH_GAME_CHAT, "text", "gamer", "Sadece Oyuncu rolü olanlar."),
        ChannelSpec("🎮 Oyun 1", "voice", "gamer", user_limit=5),
        ChannelSpec("🎮 Oyun 2", "voice", "gamer", user_limit=5),
        ChannelSpec("🎮 Oyun 3", "voice", "gamer", user_limit=5),
    )),
    CategorySpec("⭐・VIP", "vip", (
        ChannelSpec(CH_VIP_CHAT, "text", "vip", "Özel Üyelere özel sohbet."),
        ChannelSpec("⭐ VIP Lounge", "voice", "vip"),
    )),
    CategorySpec("🛡️・YÖNETİM", "staff", (
        ChannelSpec(CH_MODLOG, "text", "staff", "Bot & moderasyon kayıtları."),
        ChannelSpec(CH_STAFF, "text", "staff", "Yetkili sohbeti."),
        ChannelSpec("🛡️ Yönetim Odası", "voice", "staff"),
    )),
]

def build_overwrites(
    guild: discord.Guild, roles: Dict[str, discord.Role], access: str, kind: str = "both"
) -> Dict[Any, discord.PermissionOverwrite]:
    po = discord.PermissionOverwrite
    text = kind in ("text", "both")
    voice = kind in ("voice", "both")

    def full() -> discord.PermissionOverwrite:
        d: Dict[str, bool] = {"view_channel": True}
        if text:
            d.update(send_messages=True, embed_links=True, attach_files=True, read_message_history=True, add_reactions=True)
        if voice:
            d.update(connect=True, speak=True, stream=True, use_voice_activation=True)
        return po(**d)

    def muted() -> discord.PermissionOverwrite:
        d: Dict[str, bool] = {}
        if text:
            d.update(send_messages=False, create_public_threads=False, create_private_threads=False, send_messages_in_threads=False)
        if voice:
            d.update(connect=False)
        return po(**d)

    ow: Dict[Any, discord.PermissionOverwrite] = {}
    everyone = guild.default_role

    def allow(keys: List[str]) -> None:
        for k in keys:
            r = roles.get(k)
            if r is not None:
                ow[r] = full()

    if access == "public":
        ow[everyone] = muted()
        allow(["member", "gamer", "vip"])
    elif access == "readonly":
        ow[everyone] = muted()
    elif access == "stats":
        ow[everyone] = po(view_channel=True, connect=False)
    elif access == "gamer":
        ow[everyone] = po(view_channel=False)
        allow(["gamer", "vip"])
    elif access == "vip":
        ow[everyone] = po(view_channel=False)
        allow(["vip"])
    elif access == "staff":
        ow[everyone] = po(view_channel=False)

    allow(["owner", "admin", "mod", "bot"])
    ow[guild.me] = po(
        view_channel=True, send_messages=True, embed_links=True, attach_files=True, read_message_history=True,
        add_reactions=True, manage_messages=True, manage_channels=True, connect=True, speak=True,
        move_members=True,
    )
    return ow

# ════════════════════════════════════════════════════════════════════════════
#  ADMIN COG  – /setup-server, /purge, /ban, /kick, /oyuncu-rolu, /yardim
# ════════════════════════════════════════════════════════════════════════════
class AdminCog(commands.Cog, name="Yönetim"):
    def __init__(self, bot: "AllInOneBot") -> None:
        self.bot = bot
        self._running: Set[int] = set()

    async def _do_setup(self, guild: discord.Guild, assign_existing: bool, fix_perms: bool, temizle: bool) -> discord.Embed:
        if guild.id in self._running:
            return simple_embed("⏳ Kurulum zaten çalışıyor", "Lütfen mevcut kurulumun bitmesini bekle.", C_WARN)
        self._running.add(guild.id)
        try:
            return await self._setup_impl(guild, assign_existing, fix_perms, temizle)
        finally:
            self._running.discard(guild.id)

    async def _setup_impl(self, guild: discord.Guild, assign_existing: bool, fix_perms: bool, temizle: bool) -> discord.Embed:
        me = guild.me
        gp = me.guild_permissions
        if not (gp.manage_roles and gp.manage_channels):
            return simple_embed(
                "❌ Bot yetkisi yetersiz",
                "Botun **Rolleri Yönet** ve **Kanalları Yönet** yetkisine ihtiyacı var.", C_ERR)

        if temizle:
            for ch in list(guild.channels):
                try:
                    await ch.delete(reason="Sunucu sıfırlama (setup-server)")
                except Exception:
                    pass
            for r in list(guild.roles):
                if r != guild.default_role and r < me.top_role and not r.managed:
                    try:
                        await r.delete(reason="Sunucu sıfırlama (setup-server)")
                    except Exception:
                        pass

        roles_created: List[str] = []
        roles_existing: List[str] = []
        cats_created: List[str] = []
        ch_created: List[str] = []
        ch_existing = 0
        warnings: List[str] = []
        roles: Dict[str, discord.Role] = {}

        # 1) Roller
        for spec in ROLE_SPECS:
            perms = discord.Permissions(spec.perms.value & gp.value)
            limited = perms.value != spec.perms.value
            role = find_role(guild, spec)
            if role is None:
                try:
                    role = await guild.create_role(
                        name=spec.name, permissions=perms, colour=spec.color, hoist=spec.hoist,
                        mentionable=spec.mentionable, reason="Sunucu otomatik kurulumu")
                    roles_created.append(role.name)
                    if limited:
                        warnings.append(f"`{spec.name}` rolü, botun sahip olmadığı yetkiler çıkarılarak oluşturuldu.")
                except discord.HTTPException as exc:
                    warnings.append(f"`{spec.name}` oluşturulamadı: {short_err(exc, 100)}")
                    continue
                await asyncio.sleep(0.4)
            else:
                roles_existing.append(role.name)
                if fix_perms and role < me.top_role:
                    try:
                        await role.edit(permissions=perms, hoist=spec.hoist, reason="Yetkiler onarıldı")
                    except discord.HTTPException as exc:
                        warnings.append(f"`{role.name}` yetkileri güncellenemedi: {short_err(exc, 100)}")
            roles[spec.key] = role

        # 2) Rol Hiyerarşisi
        await self._fix_hierarchy(guild, roles, warnings)

        # 3) Özel Atamalar
        owner_role = roles.get("owner")
        if owner_role is not None:
            try:
                owner = guild.owner or await guild.fetch_member(guild.owner_id)
                if owner is not None and owner_role not in owner.roles:
                    await owner.add_roles(owner_role, reason="Sunucu sahibi → Kurucu")
            except discord.HTTPException as exc:
                warnings.append(f"Kurucu rolü sunucu sahibine verilemedi: {short_err(exc, 100)}")

        bot_role = roles.get("bot")
        if bot_role is not None:
            for m in guild.members:
                if m.bot and bot_role not in m.roles:
                    try:
                        await m.add_roles(bot_role, reason="Bot rolü")
                    except discord.HTTPException:
                        break

        # 4) Kategoriler ve Kanallar
        for cat_spec in LAYOUT:
            category = discord.utils.get(guild.categories, name=cat_spec.name)
            if category is None:
                try:
                    category = await guild.create_category(
                        cat_spec.name, overwrites=build_overwrites(guild, roles, cat_spec.access, "both"),
                        reason="Sunucu otomatik kurulumu")
                    cats_created.append(cat_spec.name)
                    await asyncio.sleep(0.4)
                except discord.HTTPException as exc:
                    warnings.append(f"Kategori `{cat_spec.name}` oluşturulamadı: {short_err(exc, 100)}")
                    continue
            for spec in cat_spec.channels:
                pool = guild.text_channels if spec.kind == "text" else guild.voice_channels
                ch_found = (
                    discord.utils.find(lambda c: c.name.startswith(spec.name), pool)
                    if spec.kind == "voice" and spec.name == VC_STATS
                    else discord.utils.get(pool, name=spec.name)
                )

                if ch_found is not None:
                    ch_existing += 1
                    continue
                ow = build_overwrites(guild, roles, spec.access or cat_spec.access, spec.kind)
                try:
                    if spec.kind == "text":
                        created_ch = await guild.create_text_channel(
                            spec.name, category=category, overwrites=ow, topic=spec.topic,
                            reason="Sunucu otomatik kurulumu")
                        if spec.name == CH_LEVELUP:
                            await self.bot.db.set_level_channel(guild.id, created_ch.id)
                    else:
                        ch_name = f"{spec.name} {guild.member_count}" if spec.name == VC_STATS else spec.name
                        await guild.create_voice_channel(
                            ch_name, category=category, overwrites=ow, user_limit=spec.user_limit,
                            bitrate=min(int(guild.bitrate_limit), 128000), reason="Sunucu otomatik kurulumu")
                    ch_created.append(spec.name)
                    await asyncio.sleep(0.5)
                except discord.HTTPException as exc:
                    warnings.append(f"Kanal `{spec.name}` oluşturulamadı: {short_err(exc, 100)}")

        # 5) Üye Rolü Dağıtımı
        member_role = roles.get("member")
        bulk_note = ""
        if assign_existing and member_role is not None:
            todo = sum(1 for m in guild.members if not m.bot and member_role not in m.roles)
            if todo:
                asyncio.create_task(self._bulk_assign(guild, member_role))
                bulk_note = f"\n👤 **{todo}** mevcut üyeye arka planda `{member_role.name}` rolü veriliyor."

        embed = discord.Embed(
            title="✅ Sunucu kurulumu tamamlandı",
            description="Roller, kategoriler ve kanallar düzenlendi." + bulk_note,
            color=C_OK, timestamp=discord.utils.utcnow())
        embed.add_field(name=f"🎭 Oluşturulan roller ({len(roles_created)})",
                        value=trunc("\n".join(roles_created) or "—"), inline=True)
        embed.add_field(name=f"📌 Mevcut roller ({len(roles_existing)})",
                        value=trunc("\n".join(roles_existing) or "—"), inline=True)
        embed.add_field(name=f"📁 Kategoriler ({len(cats_created)})",
                        value=trunc("\n".join(cats_created) or "—"), inline=False)
        embed.add_field(name=f"💬 Kanallar ({len(ch_created)}) · mevcut: {ch_existing}",
                        value=trunc("\n".join(ch_created) or "—"), inline=False)
        if warnings:
            embed.add_field(name="⚠️ Uyarılar", value=trunc("\n".join(f"• {w}" for w in warnings)), inline=False)
            embed.color = C_WARN
        return embed

    async def _fix_hierarchy(self, guild: discord.Guild, roles: Dict[str, discord.Role], warnings: List[str]) -> None:
        order = [roles[k] for k in ("owner", "admin", "mod", "bot", "vip", "gamer", "member") if k in roles]
        if len(order) < 2:
            return
        if all(order[i].position > order[i + 1].position for i in range(len(order) - 1)):
            return
        top = guild.me.top_role.position
        base = top - 1
        positions = {role: base - i for i, role in enumerate(order) if base - i > 0}
        try:
            fn = getattr(guild, "edit_role_positions", None)
            if fn is not None:
                await fn(positions, reason="Rol hiyerarşisi")
            else:
                for role, pos in positions.items():
                    await role.edit(position=pos, reason="Rol hiyerarşisi")
        except discord.HTTPException as exc:
            warnings.append(f"Rol sıralaması ayarlanamadı: {short_err(exc, 120)}")

    async def _bulk_assign(self, guild: discord.Guild, role: discord.Role) -> None:
        for m in list(guild.members):
            if m.bot or role in m.roles:
                continue
            try:
                await m.add_roles(role, reason="Kurulum: üyelere rol ataması")
            except discord.HTTPException:
                pass
            await asyncio.sleep(0.4)

    @app_commands.command(name="setup-server", description="Rolleri, kategorileri ve kanalları otomatik kurar.")
    @app_commands.guild_only()
    @app_commands.describe(
        assign_existing="Mevcut üyelere 'Üye' rolü verilsin mi?",
        fix_permissions="Zaten var olan rollerin yetkilerini de sıfırla/onar",
        temizle="DİKKAT: Eski kanal ve rolleri silip sıfırdan kurar!")
    async def setup_slash(self, interaction: discord.Interaction, assign_existing: bool = True,
                          fix_permissions: bool = False, temizle: bool = False) -> None:
        if staff_level(interaction.user) < 3:
            raise NotAuthorized("Bu komutu sadece sunucu sahibi / Yönetici yetkisi olanlar kullanabilir.")
        await interaction.response.defer(thinking=True)
        embed = await self._do_setup(interaction.guild, assign_existing, fix_permissions, temizle)
        try:
            await interaction.followup.send(embed=embed)
        except discord.HTTPException:
            pass

    @app_commands.command(name="ban", description="Belirtilen kullanıcıyı sunucudan yasaklar (Moderatör+).")
    @app_commands.guild_only()
    @require_staff(1)
    async def ban_cmd(self, interaction: discord.Interaction, user: discord.Member, reason: str = "Sebep belirtilmedi"):
        if user.top_role >= interaction.user.top_role and interaction.user.id != interaction.guild.owner_id:
            await respond(interaction, "❌ Kendinden üst veya aynı yetkideki birini banlayamazsın.", ephemeral=True)
            return
        try:
            await user.ban(reason=f"{interaction.user} - {reason}")
            await respond(interaction, f"🔨 {user.mention} sunucudan banlandı! Sebep: {reason}")
            e = discord.Embed(title="🔨 Üye Banlandı", color=C_ERR, timestamp=discord.utils.utcnow())
            e.add_field(name="Yetkili", value=interaction.user.mention)
            e.add_field(name="Kullanıcı", value=f"{user} (`{user.id}`)")
            e.add_field(name="Sebep", value=reason)
            await send_log(interaction.guild, e)
        except discord.Forbidden:
            await respond(interaction, "❌ Botun bu kullanıcıyı banlama yetkisi yok.", ephemeral=True)

    @app_commands.command(name="kick", description="Belirtilen kullanıcıyı sunucudan atar (Moderatör+).")
    @app_commands.guild_only()
    @require_staff(1)
    async def kick_cmd(self, interaction: discord.Interaction, user: discord.Member, reason: str = "Sebep belirtilmedi"):
        if user.top_role >= interaction.user.top_role and interaction.user.id != interaction.guild.owner_id:
            await respond(interaction, "❌ Kendinden üst veya aynı yetkideki birini atamazsın.", ephemeral=True)
            return
        try:
            await user.kick(reason=f"{interaction.user} - {reason}")
            await respond(interaction, f"👢 {user.mention} sunucudan atıldı! Sebep: {reason}")
            e = discord.Embed(title="👢 Üye Atıldı", color=C_WARN, timestamp=discord.utils.utcnow())
            e.add_field(name="Yetkili", value=interaction.user.mention)
            e.add_field(name="Kullanıcı", value=f"{user} (`{user.id}`)")
            e.add_field(name="Sebep", value=reason)
            await send_log(interaction.guild, e)
        except discord.Forbidden:
            await respond(interaction, "❌ Botun bu kullanıcıyı atma yetkisi yok.", ephemeral=True)

    @app_commands.command(name="purge", description="Kanaldan toplu mesaj siler (Moderatör+).")
    @app_commands.guild_only()
    @require_staff(1)
    @app_commands.describe(amount="Silinecek mesaj sayısı (1-100)", user="Sadece bu kullanıcının mesajları")
    async def purge(self, interaction: discord.Interaction, amount: app_commands.Range[int, 1, 100],
                    user: Optional[discord.Member] = None) -> None:
        channel = interaction.channel
        if not isinstance(channel, (discord.TextChannel, discord.Thread, discord.VoiceChannel)):
            await respond(interaction, "Bu kanalda mesaj silinemez.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            deleted = await channel.purge(
                limit=amount, check=(lambda m: m.author.id == user.id) if user else (lambda m: True),
                reason=f"/purge — {interaction.user}")
        except discord.Forbidden:
            await interaction.followup.send("❌ Botun mesaj silme yetkisi yok.", ephemeral=True)
            return
        await interaction.followup.send(f"🧹 **{len(deleted)}** mesaj silindi.", ephemeral=True)
        e = discord.Embed(title="🧹 Mesaj temizliği", color=C_WARN, timestamp=discord.utils.utcnow())
        e.add_field(name="Yetkili", value=interaction.user.mention)
        e.add_field(name="Kanal", value=channel.mention)
        e.add_field(name="Adet", value=str(len(deleted)))
        await send_log(interaction.guild, e)

    @app_commands.command(name="oyuncu-rolu", description="Oyuncu rolünü al veya bırak.")
    @app_commands.guild_only()
    async def gamer_toggle(self, interaction: discord.Interaction) -> None:
        role = find_role(interaction.guild, ROLE_BY_KEY["gamer"])
        if role is None:
            await respond(interaction, "Oyuncu rolü yok. Önce `/setup-server` çalıştırılmalı.", ephemeral=True)
            return
        member = interaction.user
        try:
            if role in member.roles:
                await member.remove_roles(role, reason="Kendi isteğiyle")
                await respond(interaction, f"🎮 {role.mention} rolü kaldırıldı.", ephemeral=True)
            else:
                await member.add_roles(role, reason="Kendi isteğiyle")
                await respond(interaction, f"🎮 {role.mention} rolü verildi!", ephemeral=True)
        except discord.Forbidden:
            await respond(interaction, "❌ Rolü veremiyorum, bot yetkisini kontrol et.", ephemeral=True)

    @app_commands.command(name="yardim", description="Komut listesini gösterir.")
    async def help_cmd(self, interaction: discord.Interaction) -> None:
        e = discord.Embed(title="📖 Komutlar", color=C_INFO)
        e.add_field(name="🎵 Müzik (Özel Kontrol Panelli)", inline=False,
                    value="`/play` `/panel` `/pause` `/resume` `/skip` `/queue` `/stop` `/leave` `/nowplaying` `/volume`")
        e.add_field(name="🎮 Oyunlar", inline=False,
                    value="`/adam-asmaca` (Tek & 2 Kişilik Gizli Kelime) `/cf` `/roll` `/sayim-durum` `/kelime-durum`")
        e.add_field(name="💰 Seviye & Ekonomi", inline=False,
                    value="`/profil` `/daily` `/transfer` `/kumar` `/allin` `/liderlik` `/seviye-kanali`")
        e.add_field(name="🔊 Özel Oda", inline=False,
                    value="`/oda-kilitle` `/oda-ac` `/oda-limit` `/oda-isim` `/oda-davet`")
        e.add_field(name="🛡️ Yetkili", inline=False,
                    value="`/setup-server` `/announce` `/purge` `/ban` `/kick` `/antispam-ayarla` `/antispam-durum` `/youtube-ayarla`")
        await respond(interaction, embed=e, ephemeral=True)

# ════════════════════════════════════════════════════════════════════════════
#  STATS COG (Kilitli Dinamik İstatistik Kanalı)
# ════════════════════════════════════════════════════════════════════════════
class StatsCog(commands.Cog, name="İstatistik"):
    def __init__(self, bot: "AllInOneBot") -> None:
        self.bot = bot
        self.update_stats.start()

    def cog_unload(self) -> None:
        self.update_stats.cancel()

    async def update_channel(self, guild: discord.Guild) -> None:
        ch = discord.utils.find(lambda c: c.name.startswith(VC_STATS), guild.voice_channels)
        if ch:
            expected_name = f"{VC_STATS} {guild.member_count}"
            if ch.name != expected_name:
                try:
                    await ch.edit(name=expected_name, reason="İstatistik güncellemesi")
                except discord.HTTPException:
                    pass

    @tasks.loop(minutes=10)
    async def update_stats(self) -> None:
        for guild in self.bot.guilds:
            await self.update_channel(guild)

    @update_stats.before_loop
    async def before_stats(self) -> None:
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        await self.update_channel(member.guild)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        await self.update_channel(member.guild)

# ════════════════════════════════════════════════════════════════════════════
#  CHANNEL GUARD COG (Sıfırlanmayan Sayım & Yerel Sözlüklü Kelime Türetmece)
# ════════════════════════════════════════════════════════════════════════════
def required_letter(word: str) -> str:
    if word.endswith("ğ") and len(word) > 1:
        return word[-2]
    return word[-1]

class ChannelGuardCog(commands.Cog, name="Kanal Koruma"):
    def __init__(self, bot: "AllInOneBot") -> None:
        self.bot = bot
        self._locks: Dict[Tuple[int, str], asyncio.Lock] = {}
        self._warn_cd: Dict[Tuple[int, int], float] = {}
        self._used_sets: Dict[int, Set[str]] = {}

    def _lock(self, guild_id: int, name: str) -> asyncio.Lock:
        return self._locks.setdefault((guild_id, name), asyncio.Lock())

    def _get_used_set(self, guild_id: int, stored_list: List[str]) -> Set[str]:
        s = self._used_sets.get(guild_id)
        if s is None:
            s = set(stored_list)
            self._used_sets[guild_id] = s
        return s

    async def _warn(self, message: discord.Message, text: str, seconds: float = 6.0) -> None:
        key = (message.guild.id, message.author.id)
        now = time.monotonic()
        if now - self._warn_cd.get(key, 0.0) < 2.0:
            return
        self._warn_cd[key] = now
        await send_temp(message.channel, text, seconds)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return

        # +18 / Küfür Filtresi
        filter_cfg = self.bot.store.guild(message.guild.id).get("filter", {"enabled": True})
        if filter_cfg.get("enabled", True) and staff_level(message.author) == 0:
            clean_msg = message.content.lower().replace(" ", "").replace(".", "").replace("*", "")
            if any(bad in clean_msg for bad in BAD_WORDS):
                await safe_delete(message)
                await self._warn(message, f"🚨 {message.author.mention}, sunucuda uygunsuz kelimeler yasaktır!")
                e = discord.Embed(title="🤬 Filtre Yakaladı", color=C_ERR, timestamp=discord.utils.utcnow())
                e.add_field(name="Kullanıcı", value=message.author.mention)
                e.add_field(name="Kanal", value=message.channel.mention)
                e.add_field(name="Mesaj", value=f"||{message.content}||")
                await send_log(message.guild, e)
                return

        if message.type not in (discord.MessageType.default, discord.MessageType.reply):
            return
        if not isinstance(message.channel, discord.TextChannel):
            return
        name = message.channel.name
        try:
            if name == CH_COUNT:
                await self._counting(message)
            elif name == CH_WORD:
                await self._wordchain(message)
            elif name == CH_BOTCMD:
                await self._botcmd(message)
        except Exception:
            log.exception("Kanal koruma hatası (#%s)", name)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        if after.author.bot or after.guild is None or not isinstance(after.channel, discord.TextChannel):
            return
        if after.channel.name in (CH_COUNT, CH_WORD) and before.content != after.content:
            await self._warn(after, f"✏️ {after.author.mention} bu kanalda mesaj düzenlemek oyun sırasını değiştirmez!")

    async def _counting(self, message: discord.Message) -> None:
        """1. Madde: Yanlış tahminde ASLA sıfırlanmaz; ❌ eklenir ve son geçerli sayı hatırlatılır."""
        async with self._lock(message.guild.id, "count"):
            g = self.bot.store.guild(message.guild.id)["counting"]
            last_valid = int(g["last"])
            expected = last_valid + 1
            content = message.content.strip()

            problem: Optional[str] = None
            if not COUNT_RE.fullmatch(content):
                problem = "Sadece sayı girmelisin!"
            elif int(content) != expected:
                problem = "Yanlış sayı!"
            elif COUNT_NO_DOUBLE and g.get("last_user") == message.author.id:
                problem = "Üst üste iki kez sayamazsın!"

            if problem:
                try:
                    await message.add_reaction("❌")
                except discord.HTTPException:
                    pass
                await self._warn(
                    message,
                    f"❌ {message.author.mention} {problem} Son geçerli sayı **{last_valid}** idi. "
                    f"Lütfen **{expected}** sayısından devam edin!",
                    7.0,
                )
                return

            g["last"] = expected
            g["last_user"] = message.author.id
            g["high"] = max(int(g.get("high", 0)), expected)
            self.bot.store.save_soon()
            try:
                await message.add_reaction("🎉" if expected % 100 == 0 else "✅")
            except discord.HTTPException:
                pass

    async def _wordchain(self, message: discord.Message) -> None:
        """2. Madde: Yanlışta sıfırlanmaz + O(1) kelimeler.txt set doğrulaması."""
        async with self._lock(message.guild.id, "word"):
            g = self.bot.store.guild(message.guild.id)["wordchain"]
            used_set = self._get_used_set(message.guild.id, g["used"])
            word = tr_lower(message.content.strip())
            last_word: Optional[str] = g.get("last_word")

            problem: Optional[str] = None
            if not WORD_RE.fullmatch(word):
                problem = "Sadece tek bir Türkçe kelime yazmalısın!"
            elif last_word and g.get("last_user") == message.author.id:
                problem = "Üst üste iki kelime yazamazsın, başkasını bekle!"
            elif last_word and word[0] != required_letter(last_word):
                problem = f"Kelime **{tr_upper(required_letter(last_word))}** harfiyle başlamalı!"
            elif word not in self.bot.dictionary:
                problem = f"**{word}** yerel sözlükte bulunamadı!"
            elif word in used_set:
                problem = f"**{word}** bu oturumda zaten kullanıldı!"

            if problem:
                try:
                    await message.add_reaction("❌")
                except discord.HTTPException:
                    pass
                if last_word:
                    req_char = tr_upper(required_letter(last_word))
                    reminder = f"Son geçerli kelime **{last_word}** idi; **{req_char}** harfiyle devam edin!"
                else:
                    reminder = "Henüz başlangıç kelimesi yok; sözlükte yer alan geçerli bir kelimeyle başlayın!"
                await self._warn(message, f"❌ {message.author.mention} {problem} {reminder}", 7.5)
                return

            g["last_word"] = word
            g["last_user"] = message.author.id
            g["used"].append(word)
            used_set.add(word)
            if len(g["used"]) > 20000:
                removed = g["used"][:-20000]
                g["used"] = g["used"][-20000:]
                for rw in removed:
                    used_set.discard(rw)
            g["chain"] = int(g.get("chain", 0)) + 1
            self.bot.store.save_soon()
            try:
                await message.add_reaction("✅")
            except discord.HTTPException:
                pass

    async def _botcmd(self, message: discord.Message) -> None:
        ctx = await self.bot.get_context(message)
        if ctx.valid:
            return
        await safe_delete(message)
        chat = discord.utils.get(message.guild.text_channels, name=CH_GENERAL)
        where = f" Sohbet için {chat.mention} kanalını kullan." if chat else ""
        await self._warn(
            message,
            f"💬 {message.author.mention} burası **sadece bot komutları** içindir!{where}", 8.0)

    @app_commands.command(name="sayim-durum", description="Sayı oyununun durumunu gösterir.")
    @app_commands.guild_only()
    async def count_status(self, interaction: discord.Interaction) -> None:
        g = self.bot.store.guild(interaction.guild_id)["counting"]
        e = discord.Embed(title="🔢 Sayım Oyunu", color=C_INFO)
        e.add_field(name="Son geçerli sayı", value=f"**{g['last']}**")
        e.add_field(name="Sıradaki", value=f"**{int(g['last']) + 1}**")
        e.add_field(name="Rekor", value=f"**{g['high']}**")
        await respond(interaction, embed=e)

    @app_commands.command(name="sayim-sifirla", description="Sayı oyununu sıfırlar (Yönetici+).")
    @app_commands.guild_only()
    @require_staff(2)
    async def count_reset(self, interaction: discord.Interaction) -> None:
        g = self.bot.store.guild(interaction.guild_id)["counting"]
        g.update({"last": 0, "last_user": None})
        self.bot.store.save_soon()
        await respond(interaction, "🔄 Sayım yönetici tarafından sıfırlandı. **1** ile yeniden başlayın!")

    @app_commands.command(name="kelime-durum", description="Kelime türetme oyununun durumunu gösterir.")
    @app_commands.guild_only()
    async def word_status(self, interaction: discord.Interaction) -> None:
        g = self.bot.store.guild(interaction.guild_id)["wordchain"]
        e = discord.Embed(title="🔤 Kelime Türetme", color=C_INFO)
        if g["last_word"]:
            e.add_field(name="Son kelime", value=f"**{g['last_word']}**")
            e.add_field(name="Sıradaki harf", value=f"**{tr_upper(required_letter(g['last_word']))}**")
        else:
            e.description = "Henüz kelime yok. Sözlükteki istediğin bir kelimeyle başla!"
        e.add_field(name="Zincir uzunluğu", value=str(g.get("chain", 0)))
        e.add_field(name="Sözlük boyutu", value=f"{len(self.bot.dictionary):,} kelime")
        await respond(interaction, embed=e)

    @app_commands.command(name="kelime-sifirla", description="Kelime türetme oyununu sıfırlar (Yönetici+).")
    @app_commands.guild_only()
    @require_staff(2)
    async def word_reset(self, interaction: discord.Interaction) -> None:
        g = self.bot.store.guild(interaction.guild_id)["wordchain"]
        g.update({"last_word": None, "last_user": None, "used": [], "chain": 0})
        self._used_sets.pop(interaction.guild_id, None)
        self.bot.store.save_soon()
        await respond(interaction, "🔄 Kelime zinciri yönetici tarafından sıfırlandı. Yeni bir kelimeyle başlayın!")

# ════════════════════════════════════════════════════════════════════════════
#  ANTI-SPAM COG (Ceza Seçenekli)
# ════════════════════════════════════════════════════════════════════════════
class AntiSpamCog(commands.Cog, name="Anti-Spam"):
    def __init__(self, bot: "AllInOneBot") -> None:
        self.bot = bot
        self.history: Dict[Tuple[int, int], Deque[Tuple[float, discord.Message]]] = collections.defaultdict(
            lambda: collections.deque(maxlen=40))
        self.punishing: Set[Tuple[int, int]] = set()

    def _enabled(self, guild_id: int, channel_id: int) -> bool:
        cfg = self.bot.store.guild(guild_id)["antispam"]
        override = cfg["channels"].get(str(channel_id))
        return bool(cfg["global"] if override is None else override)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None or not isinstance(message.author, discord.Member):
            return
        if not self._enabled(message.guild.id, message.channel.id):
            return
        member = message.author
        if staff_level(member) >= 1:
            return
        key = (message.guild.id, member.id)
        if key in self.punishing:
            return
        now = time.monotonic()
        dq = self.history[key]
        dq.append((now, message))
        while dq and now - dq[0][0] > SPAM_REPEAT_WINDOW:
            dq.popleft()

        reason: Optional[str] = None
        burst = [m for t, m in dq if now - t <= SPAM_WINDOW]
        if len(burst) > SPAM_MAX_MESSAGES:
            reason = f"{SPAM_WINDOW:.0f} saniyede {len(burst)} mesaj"
        else:
            text = message.content.strip().casefold()
            if text:
                same = [m for _, m in dq if m.content.strip().casefold() == text]
                if len(same) >= SPAM_REPEAT_COUNT:
                    reason = f"Aynı mesajı {len(same)} kez tekrarlama"
        if reason:
            entries = [m for _, m in dq]
            dq.clear()
            await self._punish(member, message.channel, entries, reason)

    async def _punish(self, member: discord.Member, channel: discord.abc.Messageable,
                      messages: List[discord.Message], reason: str) -> None:
        key = (member.guild.id, member.id)
        self.punishing.add(key)
        cfg = self.bot.store.guild(member.guild.id)["antispam"]
        action = cfg.get("action", "timeout")

        try:
            for m in messages:
                await safe_delete(m)

            applied = False
            err = ""
            try:
                if action == "ban":
                    await member.ban(reason=f"Anti-spam: {reason}")
                    applied = True
                elif action == "kick":
                    await member.kick(reason=f"Anti-spam: {reason}")
                    applied = True
                else:
                    await member.timeout(SPAM_TIMEOUT, reason=f"Anti-spam: {reason}")
                    applied = True
            except discord.Forbidden:
                err = "Botun yetkisi yetersiz."
            except discord.HTTPException as exc:
                err = short_err(exc, 120)

            act_tr = {"ban": "banlandı", "kick": "atıldı", "timeout": "susturuldu"}.get(action, "susturuldu")
            if applied:
                await send_temp(channel, f"🚨 {member.mention} spam yaptığı için **{act_tr}**.", 10)
            else:
                await send_temp(channel, f"🚨 {member.mention} spam yapmayı bırak! Mesajların silindi.", 10)

            e = discord.Embed(title="🚨 Anti-Spam", color=C_ERR, timestamp=discord.utils.utcnow())
            e.add_field(name="Kullanıcı", value=f"{member.mention} (`{member.id}`)")
            e.add_field(name="Sebep", value=reason)
            e.add_field(name="İşlem", value=action.upper() if applied else f"Başarısız: {err}")
            await send_log(member.guild, e)
        except Exception:
            log.exception("Anti-spam cezası uygulanırken hata")
        finally:
            self.punishing.discard(key)

    @app_commands.command(name="antispam-ayarla", description="Anti-spam cezasını ve durumunu ayarlar (Yönetici+).")
    @app_commands.guild_only()
    @require_staff(2)
    @app_commands.choices(ceza=[
        app_commands.Choice(name="Susturma (Timeout)", value="timeout"),
        app_commands.Choice(name="Sunucudan At (Kick)", value="kick"),
        app_commands.Choice(name="Yasakla (Ban)", value="ban")
    ])
    async def antispam_set(self, interaction: discord.Interaction, enabled: bool,
                           ceza: Optional[app_commands.Choice[str]] = None,
                           channel: Optional[discord.TextChannel] = None) -> None:
        cfg = self.bot.store.guild(interaction.guild_id)["antispam"]
        if ceza:
            cfg["action"] = ceza.value
        if channel is None:
            cfg["global"] = enabled
            scope = "tüm sunucu"
        else:
            cfg["channels"][str(channel.id)] = enabled
            scope = channel.mention
        self.bot.store.save_soon()
        await respond(interaction, f"🛡️ Anti-spam **{'açıldı' if enabled else 'kapatıldı'}** (Ceza: `{cfg.get('action', 'timeout')}`) → {scope}")

    @app_commands.command(name="antispam-durum", description="Anti-spam ayarlarını gösterir.")
    @app_commands.guild_only()
    @require_staff(1)
    async def antispam_status(self, interaction: discord.Interaction) -> None:
        cfg = self.bot.store.guild(interaction.guild_id)["antispam"]
        lines = [
            f"**Genel Durum:** {'🟢 Açık' if cfg['global'] else '🔴 Kapalı'}",
            f"**Uygulanan Ceza:** `{cfg.get('action', 'timeout').upper()}`"
        ]
        for cid, state in cfg["channels"].items():
            ch = interaction.guild.get_channel(int(cid))
            lines.append(f"{ch.mention if ch else cid}: {'🟢 Açık' if state else '🔴 Kapalı'}")
        await respond(interaction, embed=simple_embed("🛡️ Anti-Spam", "\n".join(lines)), ephemeral=True)

# ════════════════════════════════════════════════════════════════════════════
#  GAMES COG  – Two-Player (P2P) & Single Hangman · Coinflip · Dice
# ════════════════════════════════════════════════════════════════════════════
HANGMAN_WORDS: Dict[str, List[str]] = {
    "Hayvan": ["aslan", "kaplan", "kartal", "penguen", "zürafa", "kaplumbağa", "papağan", "yunus", "tilki", "geyik", "leylek", "karınca", "yarasa", "flamingo"],
    "Şehir / Ülke": ["istanbul", "ankara", "izmir", "trabzon", "almanya", "japonya", "brezilya", "kanada", "norveç", "mısır", "portekiz", "eskişehir"],
    "Teknoloji": ["klavye", "bilgisayar", "yazılım", "internet", "telefon", "sunucu", "algoritma", "monitör", "işlemci", "tarayıcı", "şifreleme", "robot"],
    "Yiyecek": ["baklava", "lahmacun", "mercimek", "karnıyarık", "simit", "künefe", "pastırma", "dondurma", "mantı", "börek", "çorba"],
    "Genel": ["kütüphane", "gökyüzü", "arkadaş", "orman", "deniz", "pencere", "uçurtma", "bahçe", "yıldız", "gökkuşağı", "kahraman", "macera"],
}

HANGMAN_ART = [
    "  +---+\n  |   |\n      |\n      |\n      |\n      |\n=========",
    "  +---+\n  |   |\n  O   |\n      |\n      |\n      |\n=========",
    "  +---+\n  |   |\n  O   |\n  |   |\n      |\n      |\n=========",
    "  +---+\n  |   |\n  O   |\n /|   |\n      |\n      |\n=========",
    "  +---+\n  |   |\n  O   |\n /|\\  |\n      |\n      |\n=========",
    "  +---+\n  |   |\n  O   |\n /|\\  |\n /    |\n      |\n=========",
    "  +---+\n  |   |\n  O   |\n /|\\  |\n / \\  |\n      |\n=========",
]

class HangmanGame:
    MAX_WRONG = 6

    def __init__(
        self,
        raw_word: str,
        category: str,
        owner_id: int,
        *,
        setter_id: Optional[int] = None,
        initial_players: Optional[List[int]] = None,
    ) -> None:
        self.display_word = " ".join(raw_word.strip().split())
        self.norm_word = tr_lower(self.display_word)
        self.category = category
        self.owner_id = owner_id
        self.setter_id = setter_id  # P2P modunda kelimeyi belirleyen kişi
        self.turn_order: List[int] = list(dict.fromkeys(initial_players or []))
        self.fixed_players: bool = bool(self.turn_order)
        self.turn_idx: int = 0

        self.correct: Set[str] = set()
        self.wrong: List[str] = []
        self.wrong_words: List[str] = []
        self.mistakes = 0
        self.status = "playing"
        self.last_action = "Oyun başladı! Harf veya kelime tahmin edin."

    @property
    def target_chars(self) -> Set[str]:
        return {c for c in self.norm_word if c.isalnum()}

    @property
    def masked(self) -> str:
        tokens: List[str] = []
        for orig_c, norm_c in zip(self.display_word, self.norm_word):
            if not norm_c.isalnum():
                tokens.append("  " if orig_c == " " else orig_c)
            elif norm_c in self.correct:
                tokens.append(tr_upper(orig_c))
            else:
                tokens.append("_")
        return " ".join(tokens)

    @property
    def current_turn_user_id(self) -> Optional[int]:
        if not self.turn_order:
            return None
        return self.turn_order[self.turn_idx % len(self.turn_order)]

    def check_turn(self, user_id: int) -> Optional[str]:
        if self.setter_id is not None and user_id == self.setter_id:
            return "❌ Gizli kelimeyi belirleyen kişi kendi oyununda tahmin yapamaz!"
        if self.fixed_players:
            if user_id not in self.turn_order:
                return "❌ Bu oyunda sadece davet edilen oyuncular tahmin yapabilir!"
            expected = self.current_turn_user_id
            if expected is not None and user_id != expected:
                return f"⏳ Şu an tahmin sırası <@{expected}> oyuncusunda!"
        else:
            if user_id in self.turn_order and len(self.turn_order) >= 2:
                expected = self.current_turn_user_id
                if expected is not None and user_id != expected:
                    return f"⏳ Sırayla oynanıyor! Şu an sıra <@{expected}> oyuncusunda."
        return None

    def _register_and_advance_turn(self, user_id: int) -> None:
        if not self.fixed_players and user_id not in self.turn_order:
            self.turn_order.append(user_id)
            self.turn_idx = len(self.turn_order) - 1
        if self.turn_order:
            self.turn_idx = (self.turn_idx + 1) % len(self.turn_order)

    def _check_end(self) -> None:
        if self.target_chars.issubset(self.correct):
            self.status = "won"
        elif self.mistakes >= self.MAX_WRONG:
            self.status = "lost"

    def guess_letter(self, raw_letter: str, user_id: int, who: str) -> str:
        letter = tr_lower(raw_letter)
        if letter in self.correct or letter in self.wrong:
            return "dup"
        self._register_and_advance_turn(user_id)
        if letter in self.target_chars:
            self.correct.add(letter)
            self.last_action = f"✅ {who}, '{tr_upper(raw_letter)}' harfini buldu!"
            self._check_end()
            return "hit"
        self.wrong.append(letter)
        self.mistakes += 1
        self.last_action = f"❌ {who}, '{tr_upper(raw_letter)}' harfi kelimede yok!"
        self._check_end()
        return "miss"

    def guess_word(self, raw_guess: str, user_id: int, who: str) -> str:
        clean_guess = tr_lower(" ".join(raw_guess.strip().split()))
        if clean_guess in self.wrong_words:
            return "dup"
        self._register_and_advance_turn(user_id)
        if clean_guess == self.norm_word:
            self.correct.update(self.target_chars)
            self.last_action = f"🎯 {who} gizli kelimeyi tam olarak bildi!"
            self._check_end()
            return "hit"
        self.mistakes += 1
        self.wrong_words.append(clean_guess)
        self.last_action = f"❌ {who}, '{tr_upper(raw_guess.strip())}' yanlış tahmin!"
        self._check_end()
        return "miss"

class LetterModal(discord.ui.Modal):
    def __init__(self, game_view: "HangmanView") -> None:
        super().__init__(title="Harf Tahmin Et", timeout=120)
        self.game_view = game_view
        self.letter = discord.ui.TextInput(label="Bir harf veya rakam yaz", min_length=1, max_length=1, required=True)
        self.add_item(self.letter)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw = self.letter.value.strip()
        if len(raw) != 1 or not raw.isalnum():
            await interaction.response.send_message("⚠️ Lütfen geçerli tek bir harf veya rakam girin.", ephemeral=True)
            return
        game = self.game_view.game
        if game.status != "playing":
            await interaction.response.send_message("Oyun sona erdi.", ephemeral=True)
            return
        turn_err = game.check_turn(interaction.user.id)
        if turn_err:
            await interaction.response.send_message(turn_err, ephemeral=True)
            return
        result = game.guess_letter(raw, interaction.user.id, interaction.user.display_name)
        if result == "dup":
            await interaction.response.send_message("⚠️ Bu harf zaten denendi!", ephemeral=True)
            return
        await self.game_view.refresh(interaction)

class WordModal(discord.ui.Modal):
    def __init__(self, game_view: "HangmanView") -> None:
        super().__init__(title="Kelimeyi / İsmi Tahmin Et", timeout=120)
        self.game_view = game_view
        self.word = discord.ui.TextInput(label="Tahmininiz (özel isim / yabancı terim dahil)", min_length=1, max_length=60, required=True)
        self.add_item(self.word)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw_guess = self.word.value.strip()
        if not any(c.isalnum() for c in raw_guess):
            await interaction.response.send_message("⚠️ Geçerli bir kelime veya isim yazın.", ephemeral=True)
            return
        game = self.game_view.game
        if game.status != "playing":
            await interaction.response.send_message("Oyun sona erdi.", ephemeral=True)
            return
        turn_err = game.check_turn(interaction.user.id)
        if turn_err:
            await interaction.response.send_message(turn_err, ephemeral=True)
            return
        result = game.guess_word(raw_guess, interaction.user.id, interaction.user.display_name)
        if result == "dup":
            await interaction.response.send_message("⚠️ Bu kelime zaten denendi!", ephemeral=True)
            return
        await self.game_view.refresh(interaction)

class HangmanView(discord.ui.View):
    def __init__(self, game: HangmanGame, cog: "GamesCog", channel_id: int) -> None:
        super().__init__(timeout=600)
        self.game = game
        self.cog = cog
        self.channel_id = channel_id
        self.message: Optional[discord.Message] = None

    def build_embed(self) -> discord.Embed:
        g = self.game
        color = {"playing": C_INFO, "won": C_OK, "lost": C_ERR}.get(g.status, C_WARN)
        mode_tag = " (2 Kişilik P2P)" if g.setter_id else ""
        titles = {
            "playing": f"🪢 Adam Asmaca{mode_tag}",
            "won": f"🎉 Kelime Bulundu!{mode_tag}",
            "lost": f"💀 Adam Asıldı!{mode_tag}",
            "timeout": "⌛ Süre Doldu",
            "quit": "🏳️ Oyun Sonlandırıldı",
        }
        e = discord.Embed(title=titles[g.status], color=color)
        e.description = f"```\n{HANGMAN_ART[min(g.mistakes, 6)]}\n```\n### `{g.masked}`"
        e.add_field(name="Mod / Kategori", value=g.category, inline=True)
        e.add_field(name="Kalan Hak", value=f"{'❤️' * (g.MAX_WRONG - g.mistakes)}{'🖤' * g.mistakes}", inline=True)

        if g.setter_id:
            e.add_field(name="Kelimeyi Belirleyen", value=f"<@{g.setter_id}>", inline=True)

        if g.status == "playing" and g.current_turn_user_id:
            e.add_field(name="🎯 Sıradaki Tahminci", value=f"<@{g.current_turn_user_id}>", inline=True)

        wrong = ", ".join(tr_upper(w) for w in g.wrong) or "—"
        e.add_field(name="Yanlış Harfler", value=wrong, inline=False)
        if g.wrong_words:
            e.add_field(name="Yanlış Tahminler", value=trunc(", ".join(tr_upper(w) for w in g.wrong_words), 500), inline=False)
        if g.status != "playing":
            e.add_field(name="🔓 Gizli Kelime", value=f"**{tr_upper(g.display_word)}**", inline=False)
        e.set_footer(text=g.last_action)
        return e

    def _end(self) -> None:
        for child in self.children:
            child.disabled = True
        self.cog.active.pop(self.channel_id, None)
        self.stop()

    async def refresh(self, interaction: discord.Interaction) -> None:
        if self.game.status != "playing":
            self._end()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Harf Tahmin Et", emoji="🔤", style=discord.ButtonStyle.primary)
    async def letter_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        turn_err = self.game.check_turn(interaction.user.id)
        if turn_err:
            await interaction.response.send_message(turn_err, ephemeral=True)
            return
        await interaction.response.send_modal(LetterModal(self))

    @discord.ui.button(label="Kelimeyi Tahmin Et", emoji="🎯", style=discord.ButtonStyle.success)
    async def word_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        turn_err = self.game.check_turn(interaction.user.id)
        if turn_err:
            await interaction.response.send_message(turn_err, ephemeral=True)
            return
        await interaction.response.send_modal(WordModal(self))

    @discord.ui.button(label="Bırak", emoji="🏳️", style=discord.ButtonStyle.danger)
    async def quit_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != self.game.owner_id and staff_level(interaction.user) < 1:
            await interaction.response.send_message("Oyunu sadece başlatan kişi veya yetkililer sonlandırabilir.", ephemeral=True)
            return
        self.game.status = "quit"
        self.game.last_action = f"{interaction.user.display_name} oyunu sonlandırdı."
        await self.refresh(interaction)

    async def on_timeout(self) -> None:
        if self.game.status == "playing":
            self.game.status = "timeout"
        self._end()
        if self.message:
            try:
                await self.message.edit(embed=self.build_embed(), view=self)
            except discord.HTTPException:
                pass

class GamesCog(commands.Cog, name="Oyunlar"):
    def __init__(self, bot: "AllInOneBot") -> None:
        self.bot = bot
        self.active: Dict[int, HangmanView] = {}

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """#adam-asmaca kanalında oyuncular doğrudan tek harf yazarak da sırayla tahmin yapabilir."""
        if message.author.bot or message.guild is None:
            return
        view = self.active.get(message.channel.id)
        if view is None or view.game.status != "playing":
            return
        if not isinstance(message.channel, discord.TextChannel) or message.channel.name != CH_HANGMAN:
            return
        raw = message.content.strip()
        if len(raw) != 1 or not raw.isalnum():
            return

        turn_err = view.game.check_turn(message.author.id)
        if turn_err:
            await safe_delete(message)
            await send_temp(message.channel, f"{message.author.mention} {turn_err}", 5.0)
            return

        res = view.game.guess_letter(raw, message.author.id, message.author.display_name)
        await safe_delete(message)
        if res == "dup":
            await send_temp(message.channel, f"⚠️ {message.author.mention} **{tr_upper(raw)}** harfi zaten denendi!", 4.0)
            return

        if view.game.status != "playing":
            view._end()
        if view.message:
            try:
                await view.message.edit(embed=view.build_embed(), view=view)
            except discord.HTTPException:
                pass

    @app_commands.command(name="adam-asmaca", description="Tek kişilik veya 2 kişilik (gizli kelimeli) Adam Asmaca başlatır.")
    @app_commands.guild_only()
    @app_commands.describe(
        gizli_kelime="2 Kişilik Mod: Gizli kelime/özel isim girin (komut mesajı anında silinir)",
        rakip="Tahmin edecek oyuncu (boş bırakılırsa kanaldakiler sırayla tahmin eder)",
        ikinci_oyuncu="İsteğe bağlı 2. tahminci (rakip ile sırayla tahmin yapar)",
        kategori="Tek kişilik mod için kategori (gizli_kelime boşsa kullanılır)",
    )
    @app_commands.choices(kategori=[app_commands.Choice(name=k, value=k) for k in HANGMAN_WORDS])
    async def hangman(
        self,
        interaction: discord.Interaction,
        gizli_kelime: Optional[app_commands.Range[str, 1, 60]] = None,
        rakip: Optional[discord.Member] = None,
        ikinci_oyuncu: Optional[discord.Member] = None,
        kategori: Optional[app_commands.Choice[str]] = None,
    ) -> None:
        if interaction.channel_id in self.active:
            await respond(interaction, "⚠️ Bu kanalda zaten devam eden bir Adam Asmaca oyunu var!", ephemeral=True)
            return

        # 3. Madde: İki Kişilik (P2P) Gizli Kelime Modu
        if gizli_kelime is not None:
            secret = gizli_kelime.strip()
            if not any(c.isalnum() for c in secret):
                await respond(interaction, "❌ Gizli kelime en az bir harf veya rakam içermelidir.", ephemeral=True)
                return

            players: List[int] = []
            for p in (rakip, ikinci_oyuncu):
                if p is not None:
                    if p.bot:
                        await respond(interaction, "❌ Botları tahminci olarak seçemezsin.", ephemeral=True)
                        return
                    if p.id == interaction.user.id:
                        await respond(interaction, "❌ Kelimeyi belirleyen kişi olarak kendini rakip seçemezsin.", ephemeral=True)
                        return
                    players.append(p.id)

            # Gizlilik Önceliği: Ephemeral yanıt verip orijinal yanıtı anında silerek iz bırakma
            await interaction.response.send_message("🔒 Gizli kelime alındı, oyun başlatılıyor...", ephemeral=True)
            try:
                await interaction.delete_original_response()
            except discord.HTTPException:
                pass

            game = HangmanGame(
                secret,
                "Özel Kelime / P2P",
                interaction.user.id,
                setter_id=interaction.user.id,
                initial_players=players,
            )
            view = HangmanView(game, self, interaction.channel_id)
            self.active[interaction.channel_id] = view
            mention_line = " ".join(f"<@{uid}>" for uid in players)
            header = (
                f"🪢 **{interaction.user.display_name}** gizli bir kelime belirledi! {mention_line}".strip()
            )
            try:
                view.message = await interaction.channel.send(content=header, embed=view.build_embed(), view=view)
            except discord.HTTPException:
                self.active.pop(interaction.channel_id, None)
            return

        # Klasik Bot Kelimesi Modu
        cat = kategori.value if kategori else random.choice(list(HANGMAN_WORDS))
        word = random.choice(HANGMAN_WORDS[cat])
        game = HangmanGame(word, cat, interaction.user.id)
        view = HangmanView(game, self, interaction.channel_id)
        self.active[interaction.channel_id] = view
        await interaction.response.send_message(embed=view.build_embed(), view=view)
        try:
            view.message = await interaction.original_response()
        except discord.HTTPException:
            pass

    @commands.command(name="adam-asmaca", aliases=["hangman", "adamasmaca"])
    @commands.guild_only()
    async def hangman_prefix(self, ctx: commands.Context, *, gizli_kelime: str) -> None:
        """Prefix komutu ile gizli kelime girildiğinde mesajı anında siler."""
        await safe_delete(ctx.message)
        if ctx.channel.id in self.active:
            await send_temp(ctx.channel, f"⚠️ {ctx.author.mention} bu kanalda zaten aktif bir oyun var!", 5.0)
            return
        secret = gizli_kelime.strip()
        if not any(c.isalnum() for c in secret) or len(secret) > 60:
            await send_temp(ctx.channel, f"❌ {ctx.author.mention} geçerli bir gizli kelime girin (1-60 karakter).", 5.0)
            return
        game = HangmanGame(secret, "Özel Kelime / P2P", ctx.author.id, setter_id=ctx.author.id)
        view = HangmanView(game, self, ctx.channel.id)
        self.active[ctx.channel.id] = view
        view.message = await ctx.channel.send(
            content=f"🪢 **{ctx.author.display_name}** gizli bir kelime belirledi! Tahmin zamanı:",
            embed=view.build_embed(),
            view=view,
        )

    @app_commands.command(name="cf", description="Yazı-tura atar.")
    @app_commands.describe(tahmin="İstersen tahmin et!")
    @app_commands.choices(tahmin=[app_commands.Choice(name="Yazı", value="Yazı"),
                                  app_commands.Choice(name="Tura", value="Tura")])
    async def coinflip(self, interaction: discord.Interaction,
                       tahmin: Optional[app_commands.Choice[str]] = None) -> None:
        result = random.choice(["Yazı", "Tura"])
        e = discord.Embed(title="🪙 Yazı Tura", description=f"Sonuç: **{result}**", color=C_GOLD)
        if tahmin:
            won = tahmin.value == result
            e.add_field(name="Tahminin", value=f"{tahmin.value} → {'🎉 Bildin!' if won else '😢 Bilemedin'}")
        await respond(interaction, embed=e)

    @app_commands.command(name="roll", description="Zar atar.")
    @app_commands.describe(sides="Yüz sayısı (2-1000)", count="Zar adedi (1-20)")
    async def roll(self, interaction: discord.Interaction, sides: app_commands.Range[int, 2, 1000] = 6,
                   count: app_commands.Range[int, 1, 20] = 1) -> None:
        rolls = [random.randint(1, sides) for _ in range(count)]
        e = discord.Embed(title=f"🎲 {count}d{sides}", color=C_GOLD)
        e.description = "Sonuçlar: " + ", ".join(f"**{r}**" for r in rolls)
        if count > 1:
            e.add_field(name="Toplam", value=str(sum(rolls)))
        await respond(interaction, embed=e)

# ════════════════════════════════════════════════════════════════════════════
#  MUSIC COG  – yt-dlp + FFmpeg + Ephemeral Control Panel
# ════════════════════════════════════════════════════════════════════════════
import tempfile

class _YtdlpLogger:
    def debug(self, msg: str) -> None:
        pass
    def info(self, msg: str) -> None:
        pass
    def warning(self, msg: str) -> None:
        log.warning("[yt-dlp] %s", msg)
    def error(self, msg: str) -> None:
        log.error("[yt-dlp] %s", msg)

def get_cookie_file() -> Optional[str]:
    """Orijinal cookies.txt dosyasının yt-dlp tarafından bozulmasını önlemek için her istekte temiz kopya oluşturur."""
    candidates = ["/etc/secrets/cookies.txt", "cookies.txt"]
    src = next((p for p in candidates if os.path.exists(p) and os.path.getsize(p) > 0), None)
    if not src:
        return None
    work_path = os.path.join(tempfile.gettempdir(), "ytdlp_working_cookies.txt")
    try:
        shutil.copyfile(src, work_path)
        return work_path
    except OSError:
        return src

YTDL_OPTS: Dict[str, Any] = {
    "format": "bestaudio/bestaudio*/best/best*",
    "noplaylist": True,
    "default_search": "ytsearch",
    "quiet": True,
    "no_warnings": False,
    "logger": _YtdlpLogger(),
    "skip_download": True,
    "socket_timeout": 15,
    "retries": 3,
    "cachedir": False,
    "source_address": "0.0.0.0",
    "geo_bypass": True,
    "nocheckcertificate": True,
    # YouTube JS imza şifrelemesini (EJS) çözmek için Deno ve Node.js'i aktif eder
    "js_runtimes": {"deno": {}, "node": {}, "bun": {}, "quickjs": {}},
    "remote_components": ["ejs:github"],
}

FFMPEG_BEFORE = (
    '-user_agent "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
    'AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Safari/605.1.15" '
    '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -nostdin'
)
FFMPEG_OPTIONS = "-vn"
URL_RE = re.compile(r"^https?://", re.I)

def _pick_stream_url(info: Dict[str, Any]) -> Optional[str]:
    """Storyboard (mhtml) ve sessiz video formatlarını eleyerek geçerli ses/HLS akışını seçer."""
    if info.get("url") and info.get("protocol") != "mhtml":
        return info["url"]
    for f in info.get("requested_formats") or []:
        if f.get("url") and f.get("acodec") != "none" and f.get("protocol") != "mhtml":
            return f["url"]
    valid_fmts = [
        f for f in (info.get("formats") or [])
        if f.get("url")
        and f.get("acodec") != "none"
        and f.get("protocol") != "mhtml"
        and f.get("ext") != "mhtml"
    ]
    if valid_fmts:
        # Önce saf ses akışlarını, yoksa birleşik (HLS/mp4) akışı seç
        pure_audio = [f for f in valid_fmts if f.get("vcodec") == "none"]
        return (pure_audio[-1] if pure_audio else valid_fmts[-1])["url"]
    return None

def _ytdl_extract(query: str) -> Dict[str, Any]:
    target = query if URL_RE.match(query) else f"ytsearch1:{query}"
    has_cookies = any(os.path.exists(p) and os.path.getsize(p) > 0 for p in ("/etc/secrets/cookies.txt", "cookies.txt"))

    attempts: List[Tuple[str, Dict[str, Any]]] = []

    if has_cookies:
        # 1. Deneme: Çerez + web_safari & tv (SABR engelini HLS/TV akışıyla aşar)
        attempts.append((
            "cookies+safari_tv",
            {
                **YTDL_OPTS,
                "extractor_args": {"youtube": {"player_client": ["web_safari", "tv", "web"]}},
            },
        ))
        # 2. Deneme: Çerez + varsayılan yt-dlp istemcileri (EJS/Deno ile)
        attempts.append((
            "cookies+default",
            {**YTDL_OPTS},
        ))

    # 3. Deneme: Çerezsiz mobil/safari istemcileri (çerez süresi dolmuşsa yedek olarak devreye girer)
    attempts.append((
        "nocookies+vr_safari",
        {
            **YTDL_OPTS,
            "extractor_args": {"youtube": {"player_client": ["android_vr", "web_safari", "tv"]}},
        },
    ))

    errors: List[str] = []
    for label, opts in attempts:
        if label.startswith("cookies"):
            cpath = get_cookie_file()
            if cpath:
                opts["cookiefile"] = cpath
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(target, download=False)
            if not info:
                continue
            if "entries" in info:
                entries = [e for e in info["entries"] if e]
                if not entries:
                    continue
                info = entries[0]

            stream_url = _pick_stream_url(info)
            if stream_url:
                info["url"] = stream_url
                return info
        except Exception as exc:
            err_txt = short_err(exc, 160)
            log.warning("yt-dlp denemesi başarısız [%s]: %s", label, err_txt)
            errors.append(f"[{label}] {err_txt}")
            continue

    raise RuntimeError(" | ".join(errors) if errors else "Uygun ses formatı bulunamadı.")

async def ytdl_extract(query: str) -> Dict[str, Any]:
    loop = asyncio.get_running_loop()
    return await asyncio.wait_for(loop.run_in_executor(None, _ytdl_extract, query), timeout=60)
@dataclass
class Track:
    title: str
    webpage_url: str
    duration: Optional[int]
    thumbnail: Optional[str]
    uploader: Optional[str]
    requester: discord.abc.User
    stream_url: Optional[str] = None
    fetched_at: float = 0.0

    @classmethod
    def from_info(cls, info: Dict[str, Any], requester: discord.abc.User) -> "Track":
        t = cls(
            title=info.get("title") or "Bilinmeyen şarkı",
            webpage_url=info.get("webpage_url") or info.get("original_url") or info.get("url"),
            duration=int(info["duration"]) if info.get("duration") else None,
            thumbnail=info.get("thumbnail"),
            uploader=info.get("uploader") or info.get("channel"),
            requester=requester,
        )
        t.stream_url = info.get("url")
        t.fetched_at = time.time()
        return t

    async def ensure_stream(self) -> None:
        if self.stream_url and time.time() - self.fetched_at < STREAM_TTL:
            return
        info = await ytdl_extract(self.webpage_url)
        self.stream_url = info["url"]
        self.fetched_at = time.time()

class MusicControlView(discord.ui.View):
    """4. Madde: Sadece komutu kullanan kişiye (ephemeral=True) gösterilen kontrol paneli."""

    def __init__(self, cog: "MusicCog", guild_id: int) -> None:
        super().__init__(timeout=300)
        self.cog = cog
        self.guild_id = guild_id

    def build_panel_embed(self, status_note: Optional[str] = None) -> discord.Embed:
        player = self.cog.players.get(self.guild_id)
        e = discord.Embed(title="🎛️ Özel Müzik Kontrol Paneli", color=C_INFO)
        if player and player.current:
            t = player.current
            e.description = f"🎶 **[{t.title}]({t.webpage_url})**"
            e.add_field(name="Süre", value=fmt_duration(t.duration), inline=True)
            e.add_field(name="Kuyruk", value=f"{len(player.queue)} şarkı", inline=True)
            e.add_field(name="Ses", value=f"%{int(player.volume * 100)}", inline=True)
            if t.thumbnail:
                e.set_thumbnail(url=t.thumbnail)
        else:
            e.description = "Şu an aktif çalan bir parça yok veya kuyruk hazırlanıyor."
        if status_note:
            e.set_footer(text=status_note)
        else:
            e.set_footer(text="Bu panel yalnızca sana görünür.")
        return e

    @discord.ui.button(label="Duraklat / Devam", emoji="⏯️", style=discord.ButtonStyle.primary)
    async def pause_resume_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        vc = await self.cog._require_same_channel(interaction)
        if not vc:
            return
        if vc.is_playing():
            vc.pause()
            note = "⏸️ Müzik duraklatıldı."
        elif vc.is_paused():
            vc.resume()
            note = "▶️ Müzik devam ediyor."
        else:
            await interaction.response.send_message("❌ Şu an çalan bir şarkı yok.", ephemeral=True)
            return
        await interaction.response.edit_message(embed=self.build_panel_embed(note), view=self)

    @discord.ui.button(label="Atla", emoji="⏭️", style=discord.ButtonStyle.secondary)
    async def skip_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        vc = await self.cog._require_same_channel(interaction)
        if not vc:
            return
        if vc.is_playing() or vc.is_paused():
            vc.stop()
            await interaction.response.edit_message(embed=self.build_panel_embed("⏭️ Şarkı atlandı."), view=self)
        else:
            await interaction.response.send_message("❌ Atlanacak şarkı yok.", ephemeral=True)

    @discord.ui.button(label="Durdur", emoji="⏹️", style=discord.ButtonStyle.danger)
    async def stop_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        vc = await self.cog._require_same_channel(interaction)
        if not vc:
            return
        player = self.cog.players.get(self.guild_id)
        if player:
            player.queue.clear()
        if vc.is_playing() or vc.is_paused():
            vc.stop()
        await interaction.response.edit_message(
            embed=self.build_panel_embed("⏹️ Müzik durduruldu ve kuyruk temizlendi."), view=self
        )

    @discord.ui.button(label="Bağlantıyı Kes", emoji="🔌", style=discord.ButtonStyle.danger)
    async def disconnect_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        vc = await self.cog._require_same_channel(interaction)
        if not vc:
            return
        player = self.cog.players.get(self.guild_id)
        if player:
            await player.destroy()
        else:
            await vc.disconnect(force=True)
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            embed=simple_embed("👋 Bağlantı Kesildi", "Bot ses kanalından ayrıldı.", C_WARN), view=self
        )
        self.stop()

class GuildPlayer:
    def __init__(self, cog: "MusicCog", guild: discord.Guild, text_channel: discord.abc.Messageable) -> None:
        self.cog = cog
        self.guild = guild
        self.text_channel = text_channel
        self.loop = asyncio.get_running_loop()
        self.queue: Deque[Track] = collections.deque()
        self.current: Optional[Track] = None
        self.volume = 0.5
        self.wakeup = asyncio.Event()
        self.next_evt = asyncio.Event()
        self.task = self.loop.create_task(self.run())

    def _after(self, error: Optional[Exception]) -> None:
        self.loop.call_soon_threadsafe(self.next_evt.set)

    async def say(self, content: Optional[str] = None, embed: Optional[discord.Embed] = None) -> None:
        try:
            await self.text_channel.send(content=content, embed=embed)
        except discord.HTTPException:
            pass

    def now_playing_embed(self, track: Track, title: str = "🎶 Şimdi çalıyor") -> discord.Embed:
        e = discord.Embed(title=title, description=f"[{track.title}]({track.webpage_url})", color=C_INFO)
        if track.thumbnail:
            e.set_thumbnail(url=track.thumbnail)
        e.add_field(name="Süre", value=fmt_duration(track.duration))
        e.add_field(name="Başlatan", value=track.requester.mention)
        if track.uploader:
            e.add_field(name="Kanal", value=track.uploader)
        return e

    async def run(self) -> None:
        try:
            while True:
                if not self.queue:
                    self.current = None
                    self.wakeup.clear()
                    try:
                        await asyncio.wait_for(self.wakeup.wait(), timeout=IDLE_TIMEOUT)
                    except asyncio.TimeoutError:
                        await self.destroy("💤 Uzun süre müzik çalınmadığı için kanaldan ayrıldım.")
                        return
                    continue

                track = self.queue.popleft()
                vc = self.guild.voice_client
                if vc is None or not vc.is_connected():
                    await self.destroy()
                    return
                try:
                    await track.ensure_stream()
                    source = discord.PCMVolumeTransformer(
                        discord.FFmpegPCMAudio(track.stream_url, before_options=FFMPEG_BEFORE, options=FFMPEG_OPTIONS),
                        volume=self.volume)
                except Exception as exc:
                    await self.say(f"⚠️ **{track.title}** çalınamadı: `{short_err(exc, 180)}`")
                    continue

                self.current = track
                self.next_evt.clear()
                try:
                    vc.play(source, after=self._after)
                except discord.ClientException as exc:
                    await self.say(f"⚠️ Oynatma başlatılamadı: `{short_err(exc)}`")
                    continue

                # Herkese açık sade bildirim (kontrol butonları olmadan)
                await self.say(
                    f"🎶 {track.requester.mention} bir şarkı başlattı: **{track.title}** (`{fmt_duration(track.duration)}`)"
                )
                await self.next_evt.wait()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Müzik döngüsü çöktü")
            await self.destroy("⚠️ Beklenmeyen bir hata oluştu, müzik kapatıldı.")

    async def destroy(self, announce: Optional[str] = None) -> None:
        self.queue.clear()
        self.current = None
        vc = self.guild.voice_client
        if vc is not None:
            try:
                vc.stop()
            except Exception:
                pass
            try:
                await vc.disconnect(force=True)
            except Exception:
                pass
        self.cog.players.pop(self.guild.id, None)
        if self.task is not asyncio.current_task():
            self.task.cancel()
        if announce:
            await self.say(announce)

class MusicCog(commands.Cog, name="Müzik"):
    def __init__(self, bot: "AllInOneBot") -> None:
        self.bot = bot
        self.players: Dict[int, GuildPlayer] = {}
        self._alone: Set[int] = set()

    async def cog_unload(self) -> None:
        for player in list(self.players.values()):
            await player.destroy()

    async def _require_same_channel(self, interaction: discord.Interaction) -> Optional[discord.VoiceClient]:
        vc = interaction.guild.voice_client
        member = interaction.user
        if vc is None or not vc.is_connected():
            await respond(interaction, "❌ Şu an bir ses kanalında değilim.", ephemeral=True)
            return None
        if not member.voice or member.voice.channel != vc.channel:
            await respond(interaction, "❌ Benimle **aynı ses kanalında** olmalısın.", ephemeral=True)
            return None
        return vc

    @app_commands.command(name="play", description="YouTube linki veya arama ile müzik çalar (Özel kontrol panelli).")
    @app_commands.guild_only()
    @app_commands.describe(query="YouTube linki veya arama kelimesi")
    async def play(self, interaction: discord.Interaction, query: str) -> None:
        member = interaction.user
        if not member.voice or not member.voice.channel:
            await respond(interaction, "❌ Önce bir ses kanalına katılmalısın.", ephemeral=True)
            return
        channel = member.voice.channel
        perms = channel.permissions_for(interaction.guild.me)
        if not (perms.connect and perms.speak):
            await respond(interaction, "❌ Bu kanala bağlanma/konuşma yetkim yok.", ephemeral=True)
            return

        # Yanıt tamamen ephemeral=True olarak bekletilir
        await interaction.response.defer(ephemeral=True, thinking=True)

        vc = interaction.guild.voice_client
        player = self.players.get(interaction.guild_id)
        if vc is None and player is not None:
            await player.destroy()
            player = None
        try:
            if vc is None:
                vc = await channel.connect(self_deaf=True, timeout=20, reconnect=True)
            elif vc.channel != channel:
                if vc.is_playing() or vc.is_paused() or (player and player.queue):
                    await interaction.followup.send("❌ Başka bir ses kanalında müzik çalıyorum.", ephemeral=True)
                    return
                await vc.move_to(channel)
        except Exception as exc:
            await interaction.followup.send(f"❌ Ses kanalına bağlanılamadı: `{short_err(exc)}`", ephemeral=True)
            return

        if player is None:
            player = GuildPlayer(self, interaction.guild, interaction.channel)
            self.players[interaction.guild_id] = player
        else:
            player.text_channel = interaction.channel

        if len(player.queue) >= MAX_QUEUE:
            await interaction.followup.send(f"❌ Kuyruk dolu (maks. {MAX_QUEUE}).", ephemeral=True)
            return
        try:
            info = await ytdl_extract(query.strip())
        except Exception as exc:
            await interaction.followup.send(f"❌ Şarkı bulunamadı/çözümlenemedi: `{short_err(exc, 200)}`", ephemeral=True)
            return

        track = Track.from_info(info, member)
        was_idle = player.current is None and not player.queue
        player.queue.append(track)
        player.wakeup.set()

        # Kuyruğa eklendiyse sade genel mesaj (ilk şarkıysa GuildPlayer.run() temiz mesaj atar)
        if not was_idle:
            await player.say(
                f"➕ {member.mention} kuyruğa bir şarkı ekledi: **{track.title}** (`{fmt_duration(track.duration)}`) — Sıra: #{len(player.queue)}"
            )

        # Komutu kullanan kişiye özel (ephemeral) butonlu kontrol paneli
        panel_view = MusicControlView(self, interaction.guild_id)
        note = f"'{track.title}' başlatıldı." if was_idle else f"'{track.title}' kuyruğa eklendi (#{len(player.queue)})."
        panel_embed = player.now_playing_embed(
            track, "🎛️ Özel Müzik Kontrol Paneli"
        )
        panel_embed.set_footer(text=f"{note} • Bu kontrol panelini sadece sen görebilirsin.")
        await interaction.followup.send(embed=panel_embed, view=panel_view, ephemeral=True)

    @app_commands.command(name="panel", description="Sadece sana özel müzik kontrol panelini açar.")
    @app_commands.guild_only()
    async def panel_cmd(self, interaction: discord.Interaction) -> None:
        vc = await self._require_same_channel(interaction)
        if not vc:
            return
        view = MusicControlView(self, interaction.guild_id)
        await respond(interaction, embed=view.build_panel_embed(), view=view, ephemeral=True)

    @app_commands.command(name="pause", description="Müziği duraklatır.")
    @app_commands.guild_only()
    async def pause(self, interaction: discord.Interaction) -> None:
        vc = await self._require_same_channel(interaction)
        if not vc:
            return
        if vc.is_playing():
            vc.pause()
            view = MusicControlView(self, interaction.guild_id)
            await respond(interaction, embed=view.build_panel_embed("⏸️ Duraklatıldı."), view=view, ephemeral=True)
        else:
            await respond(interaction, "❌ Şu an çalan bir şey yok.", ephemeral=True)

    @app_commands.command(name="resume", description="Duraklatılan müziği devam ettirir.")
    @app_commands.guild_only()
    async def resume(self, interaction: discord.Interaction) -> None:
        vc = await self._require_same_channel(interaction)
        if not vc:
            return
        if vc.is_paused():
            vc.resume()
            view = MusicControlView(self, interaction.guild_id)
            await respond(interaction, embed=view.build_panel_embed("▶️ Devam ediyor."), view=view, ephemeral=True)
        else:
            await respond(interaction, "❌ Müzik duraklatılmış değil.", ephemeral=True)

    @app_commands.command(name="skip", description="Şarkıyı atlar.")
    @app_commands.guild_only()
    async def skip(self, interaction: discord.Interaction) -> None:
        vc = await self._require_same_channel(interaction)
        if not vc:
            return
        if vc.is_playing() or vc.is_paused():
            player = self.players.get(interaction.guild_id)
            title = player.current.title if player and player.current else "şarkı"
            vc.stop()
            view = MusicControlView(self, interaction.guild_id)
            await respond(interaction, embed=view.build_panel_embed(f"⏭️ '{title}' atlandı."), view=view, ephemeral=True)
        else:
            await respond(interaction, "❌ Atlanacak bir şey yok.", ephemeral=True)

    @app_commands.command(name="queue", description="Şarkı kuyruğunu gösterir.")
    @app_commands.guild_only()
    async def queue_cmd(self, interaction: discord.Interaction, page: app_commands.Range[int, 1, 50] = 1) -> None:
        player = self.players.get(interaction.guild_id)
        if player is None or (player.current is None and not player.queue):
            await respond(interaction, "📭 Kuyruk boş.", ephemeral=True)
            return
        per = 10
        items = list(player.queue)
        pages = max(1, (len(items) + per - 1) // per)
        page = min(page, pages)
        chunk = items[(page - 1) * per: page * per]
        e = discord.Embed(title="📜 Şarkı Kuyruğu", color=C_INFO)
        if player.current:
            e.add_field(name="🎶 Şimdi çalıyor", inline=False,
                        value=f"[{player.current.title}]({player.current.webpage_url}) `{fmt_duration(player.current.duration)}`")
        if chunk:
            start = (page - 1) * per
            lines = [f"`{start + i + 1}.` [{t.title}]({t.webpage_url}) `{fmt_duration(t.duration)}` — {t.requester.display_name}" for i, t in enumerate(chunk)]
            e.add_field(name="Sıradakiler", value=trunc("\n".join(lines), 1000), inline=False)
        await respond(interaction, embed=e, ephemeral=True)

    @app_commands.command(name="nowplaying", description="Çalan şarkıyı ve özel kontrol panelini gösterir.")
    @app_commands.guild_only()
    async def nowplaying(self, interaction: discord.Interaction) -> None:
        player = self.players.get(interaction.guild_id)
        if player is None or player.current is None:
            await respond(interaction, "❌ Şu an çalan bir şey yok.", ephemeral=True)
            return
        view = MusicControlView(self, interaction.guild_id)
        await respond(interaction, embed=view.build_panel_embed(), view=view, ephemeral=True)

    @app_commands.command(name="volume", description="Ses seviyesini ayarlar (1-150).")
    @app_commands.guild_only()
    async def volume(self, interaction: discord.Interaction, level: app_commands.Range[int, 1, 150]) -> None:
        vc = await self._require_same_channel(interaction)
        if not vc:
            return
        player = self.players.get(interaction.guild_id)
        if player:
            player.volume = level / 100
            if isinstance(vc.source, discord.PCMVolumeTransformer):
                vc.source.volume = player.volume
            await respond(interaction, f"🔊 Ses seviyesi **%{level}** olarak ayarlandı.", ephemeral=True)

    @app_commands.command(name="stop", description="Müziği durdurur ve kuyruğu temizler.")
    @app_commands.guild_only()
    async def stop(self, interaction: discord.Interaction) -> None:
        vc = await self._require_same_channel(interaction)
        if not vc:
            return
        player = self.players.get(interaction.guild_id)
        if player:
            player.queue.clear()
        if vc.is_playing() or vc.is_paused():
            vc.stop()
        await respond(interaction, "⏹️ Müzik durduruldu, kuyruk temizlendi.", ephemeral=True)

    @app_commands.command(name="leave", description="Ses kanalından ayrılır.")
    @app_commands.guild_only()
    async def leave(self, interaction: discord.Interaction) -> None:
        vc = await self._require_same_channel(interaction)
        if not vc:
            return
        player = self.players.get(interaction.guild_id)
        if player:
            await player.destroy()
        else:
            await vc.disconnect(force=True)
        await respond(interaction, "👋 Kanaldan ayrıldım.", ephemeral=True)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState) -> None:
        guild = member.guild
        if self.bot.user and member.id == self.bot.user.id:
            if before.channel is not None and after.channel is None:
                player = self.players.get(guild.id)
                if player:
                    await player.destroy()
            return
        vc = guild.voice_client
        if vc is None or before.channel is None or before.channel != vc.channel:
            return
        if all(m.bot for m in vc.channel.members) and guild.id not in self._alone:
            self._alone.add(guild.id)
            asyncio.create_task(self._alone_check(guild, vc.channel))

    async def _alone_check(self, guild: discord.Guild, channel: discord.abc.Connectable) -> None:
        try:
            await asyncio.sleep(ALONE_TIMEOUT)
            vc = guild.voice_client
            if vc is not None and vc.channel == channel and all(m.bot for m in channel.members):
                player = self.players.get(guild.id)
                if player:
                    await player.destroy("👋 Kanalda yalnız kaldığım için ayrıldım.")
                else:
                    await vc.disconnect(force=True)
        finally:
            self._alone.discard(guild.id)

# ════════════════════════════════════════════════════════════════════════════
#  ECONOMY, PROGRESSION & VIRTUAL GAMBLING COG (SQLite)
# ════════════════════════════════════════════════════════════════════════════
class EconomyCog(commands.Cog, name="Ekonomi & Seviye"):
    """5. Madde: Mesaj/Ses XP, Seviye Atlama Kanalı, /daily, /transfer ve /kumar (/allin)."""

    def __init__(self, bot: "AllInOneBot") -> None:
        self.bot = bot
        self._msg_cd: Dict[Tuple[int, int], float] = {}
        self._voice_sessions: Dict[Tuple[int, int], float] = {}
        self.voice_xp_loop.start()

    def cog_unload(self) -> None:
        self.voice_xp_loop.cancel()

    @staticmethod
    def _is_eligible_voice(channel: Optional[discord.abc.GuildChannel], state: discord.VoiceState) -> bool:
        if channel is None or not isinstance(channel, discord.VoiceChannel):
            return False
        guild = channel.guild
        if guild.afk_channel and channel.id == guild.afk_channel.id:
            return False
        cname = channel.name.casefold()
        if "afk" in cname or channel.name == VC_MASTER or channel.name.startswith(VC_STATS):
            return False
        if state.afk:
            return False
        return True

    async def _announce_level_up(self, guild: discord.Guild, member: discord.Member, new_level: int, reward: int) -> None:
        ch_id = await self.bot.db.get_level_channel(guild.id)
        channel: Optional[discord.TextChannel] = guild.get_channel(ch_id) if ch_id else None
        if channel is None:
            channel = (
                discord.utils.get(guild.text_channels, name=CH_LEVELUP)
                or discord.utils.get(guild.text_channels, name=CH_GENERAL)
            )
        if channel is None:
            return

        embed = discord.Embed(
            title="🎉 Seviye Atladın!",
            description=(
                f"Tebrikler {member.mention}, **Seviye {new_level}** oldun!\n"
                f"💰 Ödül: **{reward:,} Coin** otomatik olarak cüzdanına yatırıldı!"
            ),
            color=C_GOLD,
            timestamp=discord.utils.utcnow(),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        try:
            await channel.send(
                content=f"🎉 Tebrikler {member.mention}, **Seviye {new_level}** oldun! Ödül: **{reward:,} Coin**",
                embed=embed,
            )
        except discord.HTTPException:
            pass

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        now = time.monotonic()
        for guild in self.bot.guilds:
            for vc in guild.voice_channels:
                for member in vc.members:
                    if not member.bot and member.voice and self._is_eligible_voice(vc, member.voice):
                        self._voice_sessions.setdefault((guild.id, member.id), now)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None or not isinstance(message.author, discord.Member):
            return
        if message.type not in (discord.MessageType.default, discord.MessageType.reply):
            return
        if message.content.startswith(PREFIX):
            return

        key = (message.guild.id, message.author.id)
        now = time.monotonic()
        if now - self._msg_cd.get(key, 0.0) < MSG_XP_COOLDOWN:
            return
        self._msg_cd[key] = now

        xp_gain = random.randint(*MSG_XP_RANGE)
        coin_gain = random.randint(*MSG_COIN_RANGE)
        leveled_up, new_level, reward, _ = await self.bot.db.add_xp_and_coins(
            message.guild.id, message.author.id, xp_gain, coin_gain
        )
        if leveled_up:
            await self._announce_level_up(message.guild, message.author, new_level, reward)

    @commands.Cog.listener()
    async def on_voice_state_update(
        self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState
    ) -> None:
        if member.bot:
            return
        key = (member.guild.id, member.id)
        now = time.monotonic()
        was_eligible = self._is_eligible_voice(before.channel, before)
        is_eligible = self._is_eligible_voice(after.channel, after)

        if was_eligible and not is_eligible:
            started = self._voice_sessions.pop(key, None)
            if started is not None:
                minutes = int((now - started) // 60)
                if minutes >= 1:
                    leveled_up, new_level, reward, _ = await self.bot.db.add_xp_and_coins(
                        member.guild.id,
                        member.id,
                        minutes * VOICE_XP_PER_MIN,
                        minutes * VOICE_COINS_PER_MIN,
                    )
                    if leveled_up:
                        await self._announce_level_up(member.guild, member, new_level, reward)
        elif is_eligible and not was_eligible:
            self._voice_sessions[key] = now

    @tasks.loop(minutes=1)
    async def voice_xp_loop(self) -> None:
        now = time.monotonic()
        for guild in list(self.bot.guilds):
            for vc in guild.voice_channels:
                for member in vc.members:
                    if member.bot or not member.voice:
                        continue
                    key = (guild.id, member.id)
                    if not self._is_eligible_voice(vc, member.voice):
                        self._voice_sessions.pop(key, None)
                        continue
                    last_tick = self._voice_sessions.get(key)
                    if last_tick is None:
                        self._voice_sessions[key] = now
                        continue
                    if now - last_tick >= 55.0:
                        self._voice_sessions[key] = now
                        leveled_up, new_level, reward, _ = await self.bot.db.add_xp_and_coins(
                            guild.id, member.id, VOICE_XP_PER_MIN, VOICE_COINS_PER_MIN
                        )
                        if leveled_up:
                            await self._announce_level_up(guild, member, new_level, reward)

    @voice_xp_loop.before_loop
    async def _before_voice_xp(self) -> None:
        await self.bot.wait_until_ready()

    @app_commands.command(name="daily", description="Günlük sabit sanal coin ödülünü alır.")
    @app_commands.guild_only()
    async def daily_cmd(self, interaction: discord.Interaction) -> None:
        claimed, rem, balance = await self.bot.db.claim_daily(interaction.guild_id, interaction.user.id)
        if not claimed:
            h, r = divmod(rem, 3600)
            m, s = divmod(r, 60)
            await respond(
                interaction,
                f"⏳ Günlük ödülünü zaten aldın! Tekrar almak için **{h}s {m}dk {s}sn** beklemelisin.",
                ephemeral=True,
            )
            return
        e = discord.Embed(
            title="🎁 Günlük Ödül Alındı!",
            description=f"Cüzdanına **+{DAILY_REWARD:,} Coin** eklendi!\n💰 Güncel Bakiyen: **{balance:,} Coin**",
            color=C_OK,
        )
        await respond(interaction, embed=e)

    @app_commands.command(name="transfer", description="Başka bir kullanıcıya güvenli şekilde sanal coin gönderir.")
    @app_commands.guild_only()
    @app_commands.describe(user="Coin gönderilecek kullanıcı", amount="Gönderilecek miktar")
    async def transfer_cmd(
        self, interaction: discord.Interaction, user: discord.Member, amount: app_commands.Range[int, 1, 10_000_000]
    ) -> None:
        if user.bot:
            await respond(interaction, "❌ Botlara coin transfer edemezsin.", ephemeral=True)
            return
        if user.id == interaction.user.id:
            await respond(interaction, "❌ Kendine coin transfer edemezsin.", ephemeral=True)
            return

        ok, msg, s_bal, _ = await self.bot.db.transfer_coins(
            interaction.guild_id, interaction.user.id, user.id, amount
        )
        if not ok:
            await respond(interaction, f"❌ {msg}", ephemeral=True)
            return

        e = discord.Embed(
            title="💸 Transfer Başarılı",
            description=(
                f"{interaction.user.mention} ➡️ {user.mention} kullanıcısına **{amount:,} Coin** gönderdi!\n"
                f"💰 Kalan Bakiyen: **{s_bal:,} Coin**"
            ),
            color=C_OK,
        )
        await respond(interaction, embed=e)

    async def _run_gamble(self, interaction: discord.Interaction, amount: Optional[int]) -> None:
        ok, err_msg, won, wager, new_bal = await self.bot.db.gamble(
            interaction.guild_id, interaction.user.id, amount
        )
        if not ok:
            await respond(interaction, f"❌ {err_msg}", ephemeral=True)
            return

        if won:
            e = discord.Embed(
                title="🎰 KAZANDIN! (2x)",
                description=(
                    f"Ortaya koyduğun **{wager:,} Coin** ikiye katlandı! (**+{wager:,} Coin** kâr)\n"
                    f"💰 Yeni Cüzdan Bakiyen: **{new_bal:,} Coin**"
                ),
                color=C_OK,
            )
        else:
            e = discord.Embed(
                title="💀 KAYBETTİN!",
                description=(
                    f"Ortaya koyduğun **{wager:,} Coin** maalesef gitti...\n"
                    f"💰 Kalan Cüzdan Bakiyen: **{new_bal:,} Coin**"
                ),
                color=C_ERR,
            )
        e.set_footer(text="Tamamen oyun içi sanal puandır; gerçek maddi değeri yoktur.")
        await respond(interaction, embed=e)

    @app_commands.command(name="kumar", description="Sanal coin ile şans oyunu oynar (Ya 2 katı kazanırsın ya kaybedersin).")
    @app_commands.guild_only()
    @app_commands.describe(amount="Oynanacak sanal coin miktarı")
    async def kumar_cmd(self, interaction: discord.Interaction, amount: app_commands.Range[int, 1, 10_000_000]) -> None:
        await self._run_gamble(interaction, amount)

    @app_commands.command(name="allin", description="Belirtilen miktarı veya tüm sanal coin bakiyeni riske atar!")
    @app_commands.guild_only()
    @app_commands.describe(amount="Riske atılacak miktar (boş bırakırsan cüzdanındaki TÜM coinleri basar)")
    async def allin_cmd(
        self, interaction: discord.Interaction, amount: Optional[app_commands.Range[int, 1, 10_000_000]] = None
    ) -> None:
        await self._run_gamble(interaction, amount)

    @app_commands.command(name="profil", description="Seviye, XP ve sanal cüzdan bilgilerini gösterir.")
    @app_commands.guild_only()
    @app_commands.describe(user="Profiline bakılacak üye (boş = kendin)")
    async def profile_cmd(self, interaction: discord.Interaction, user: Optional[discord.Member] = None) -> None:
        target = user or interaction.user
        data = await self.bot.db.get_user(interaction.guild_id, target.id)
        needed = EconomyDB.xp_needed(data["level"])
        filled = min(10, int((data["xp"] / max(1, needed)) * 10))
        bar = "🟦" * filled + "⬜" * (10 - filled)

        e = discord.Embed(title=f"👤 {target.display_name} — Profil", color=C_INFO)
        e.set_thumbnail(url=target.display_avatar.url)
        e.add_field(name="⭐ Seviye", value=f"**{data['level']}**", inline=True)
        e.add_field(name="💰 Cüzdan", value=f"**{data['coins']:,} Coin**", inline=True)
        e.add_field(name="✨ İlerleme", value=f"`{data['xp']} / {needed} XP`\n{bar}", inline=False)
        await respond(interaction, embed=e)

    @app_commands.command(name="liderlik", description="Sunucunun seviye veya coin liderlik tablosunu gösterir.")
    @app_commands.guild_only()
    @app_commands.choices(tur=[
        app_commands.Choice(name="Seviye & XP", value="xp"),
        app_commands.Choice(name="Zenginlik (Coin)", value="coins"),
    ])
    async def leaderboard_cmd(
        self, interaction: discord.Interaction, tur: Optional[app_commands.Choice[str]] = None
    ) -> None:
        by_coins = (tur.value if tur else "xp") == "coins"
        rows = await self.bot.db.get_leaderboard(interaction.guild_id, by_coins=by_coins, limit=10)
        if not rows:
            await respond(interaction, "Henüz sıralama verisi yok.", ephemeral=True)
            return
        medals = ["🥇", "🥈", "🥉"]
        lines: List[str] = []
        for idx, (uid, lvl, xp, coins) in enumerate(rows):
            prefix = medals[idx] if idx < 3 else f"`#{idx + 1}`"
            lines.append(f"{prefix} <@{uid}> — **Sv. {lvl}** (`{xp} XP`) · 💰 **{coins:,} Coin**")
        title = "💰 Coin Liderlik Tablosu" if by_coins else "🏆 Seviye Liderlik Tablosu"
        await respond(interaction, embed=simple_embed(title, "\n".join(lines), C_GOLD))

    @app_commands.command(name="seviye-kanali", description="Seviye atlama mesajlarının gönderileceği kanalı ayarlar (Yönetici+).")
    @app_commands.guild_only()
    @require_staff(2)
    async def set_level_channel_cmd(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        await self.bot.db.set_level_channel(interaction.guild_id, channel.id)
        await respond(interaction, f"✅ Seviye atlama duyuru kanalı {channel.mention} olarak ayarlandı!", ephemeral=True)

# ════════════════════════════════════════════════════════════════════════════
#  DYNAMIC VOICE ROOMS ("Join to Create")
# ════════════════════════════════════════════════════════════════════════════
class VoiceRoomsCog(commands.Cog, name="Özel Odalar"):
    def __init__(self, bot: "AllInOneBot") -> None:
        self.bot = bot
        self._user_locks: Dict[int, asyncio.Lock] = {}
        self._cooldown: Dict[int, float] = {}

    def _temp(self, guild_id: int) -> Dict[str, int]:
        return self.bot.store.guild(guild_id)["temp_channels"]

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        for guild in self.bot.guilds:
            temp = self._temp(guild.id)
            for cid in list(temp):
                ch = guild.get_channel(int(cid))
                if ch is None:
                    temp.pop(cid, None)
                elif isinstance(ch, discord.VoiceChannel) and not ch.members:
                    await self._delete_room(ch)
        self.bot.store.save_soon()

    async def _delete_room(self, channel: discord.VoiceChannel) -> None:
        self._temp(channel.guild.id).pop(str(channel.id), None)
        self.bot.store.save_soon()
        try:
            await channel.delete(reason="Geçici oda boşaldı")
        except Exception:
            pass

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState) -> None:
        if member.bot or before.channel == after.channel:
            return
        try:
            if after.channel is not None and after.channel.name == VC_MASTER:
                await self._create_room(member, after.channel)
            if before.channel is not None and str(before.channel.id) in self._temp(member.guild.id):
                if not before.channel.members:
                    await self._delete_room(before.channel)
        except Exception:
            log.exception("Dinamik ses kanalı hatası")

    async def _create_room(self, member: discord.Member, master: discord.VoiceChannel) -> None:
        guild = member.guild
        lock = self._user_locks.setdefault(member.id, asyncio.Lock())
        async with lock:
            temp = self._temp(guild.id)
            for cid, owner in list(temp.items()):
                if owner == member.id:
                    existing = guild.get_channel(int(cid))
                    if isinstance(existing, discord.VoiceChannel):
                        try:
                            await member.move_to(existing)
                        except discord.HTTPException:
                            pass
                        return
                    temp.pop(cid, None)

            now = time.monotonic()
            if now - self._cooldown.get(member.id, 0.0) < 5.0:
                try:
                    await member.move_to(None)
                except discord.HTTPException:
                    pass
                return
            self._cooldown[member.id] = now

            overwrites: Dict[Any, discord.PermissionOverwrite] = dict(master.overwrites)
            overwrites[member] = discord.PermissionOverwrite(
                view_channel=True, connect=True, speak=True, stream=True, manage_channels=True,
                move_members=True, mute_members=True, deafen_members=True, priority_speaker=True)
            overwrites[guild.me] = discord.PermissionOverwrite(
                view_channel=True, connect=True, speak=True, manage_channels=True, move_members=True)
            try:
                room = await guild.create_voice_channel(
                    f"[🔊] {member.display_name}'s Room"[:100], category=master.category, overwrites=overwrites,
                    bitrate=min(int(guild.bitrate_limit), 128000), reason=f"Geçici oda: {member}")
            except Exception as exc:
                log.warning("Geçici oda oluşturulamadı: %s", exc)
                return
            temp[str(room.id)] = member.id
            self.bot.store.save_soon()
            try:
                await member.move_to(room, reason="Geçici odaya taşındı")
            except discord.HTTPException:
                await self._delete_room(room)

    async def _need_room(self, interaction: discord.Interaction) -> Optional[discord.VoiceChannel]:
        member = interaction.user
        if not isinstance(member, discord.Member) or not member.voice or not member.voice.channel:
            await respond(interaction, "❌ Bir ses kanalında olmalısın.", ephemeral=True)
            return None
        ch = member.voice.channel
        owner = self._temp(interaction.guild_id).get(str(ch.id))
        if owner != member.id and staff_level(member) < 1:
            await respond(interaction, "❌ Bu oda sana ait değil.", ephemeral=True)
            return None
        return ch

    @app_commands.command(name="oda-kilitle", description="Odanı kilitler.")
    @app_commands.guild_only()
    async def room_lock(self, interaction: discord.Interaction) -> None:
        room = await self._need_room(interaction)
        if not room:
            return
        for key in ("member", "gamer", "vip"):
            role = find_role(room.guild, ROLE_BY_KEY[key])
            if role:
                ow = room.overwrites_for(role)
                ow.connect = False
                await room.set_permissions(role, overwrite=ow)
        await respond(interaction, "🔒 Oda kilitlendi.", ephemeral=True)

    @app_commands.command(name="oda-ac", description="Odanın kilidini açar.")
    @app_commands.guild_only()
    async def room_unlock(self, interaction: discord.Interaction) -> None:
        room = await self._need_room(interaction)
        if not room:
            return
        for key in ("member", "gamer", "vip"):
            role = find_role(room.guild, ROLE_BY_KEY[key])
            if role:
                ow = room.overwrites_for(role)
                ow.connect = True
                await room.set_permissions(role, overwrite=ow)
        await respond(interaction, "🔓 Oda herkese açıldı.", ephemeral=True)

    @app_commands.command(name="oda-limit", description="Odanın kişi limitini ayarlar (0 = limitsiz).")
    @app_commands.guild_only()
    async def room_limit(self, interaction: discord.Interaction, limit: app_commands.Range[int, 0, 99]) -> None:
        room = await self._need_room(interaction)
        if not room:
            return
        await room.edit(user_limit=limit)
        await respond(interaction, f"👥 Limit: **{limit or 'limitsiz'}**", ephemeral=True)

    @app_commands.command(name="oda-isim", description="Odanın adını değiştirir.")
    @app_commands.guild_only()
    async def room_rename(self, interaction: discord.Interaction, name: app_commands.Range[str, 1, 80]) -> None:
        room = await self._need_room(interaction)
        if not room:
            return
        await room.edit(name=f"[🔊] {name}"[:100])
        await respond(interaction, "✏️ Oda adı güncellendi.", ephemeral=True)

    @app_commands.command(name="oda-davet", description="Kilitli odana birine giriş izni verir.")
    @app_commands.guild_only()
    async def room_invite(self, interaction: discord.Interaction, user: discord.Member) -> None:
        room = await self._need_room(interaction)
        if not room:
            return
        await room.set_permissions(user, connect=True, view_channel=True, speak=True)
        await respond(interaction, f"✅ {user.mention} odaya davet edildi.", ephemeral=True)

# ════════════════════════════════════════════════════════════════════════════
#  YOUTUBE / RSS MONITOR
# ════════════════════════════════════════════════════════════════════════════
YT_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}

def feed_url_from_source(source: str) -> Optional[str]:
    source = source.strip()
    if URL_RE.match(source) and "youtube.com/channel/" not in source:
        return source
    m = re.search(r"UC[0-9A-Za-z_-]{22}", source)
    if m:
        return f"https://www.youtube.com/feeds/videos.xml?channel_id={m.group(0)}"
    return None

def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            return email.utils.parsedate_to_datetime(value)
        except Exception:
            return None

def parse_feed(text: str) -> List[Dict[str, Any]]:
    root = ET.fromstring(text)
    out = []
    if root.tag.lower().endswith("feed"):
        for e in root.findall("atom:entry", YT_NS):
            vid = e.findtext("yt:videoId", default=None, namespaces=YT_NS)
            link = None
            for ln in e.findall("atom:link", YT_NS):
                if ln.get("rel", "alternate") == "alternate":
                    link = ln.get("href")
                    break
            if not link and vid:
                link = f"https://www.youtube.com/watch?v={vid}"
            ident = vid or e.findtext("atom:id", default=None, namespaces=YT_NS) or link
            if not ident:
                continue
            thumb = None
            mt = e.find("media:group/media:thumbnail", YT_NS)
            if mt is not None:
                thumb = mt.get("url")
            if not thumb and vid:
                thumb = f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"
            out.append({
                "id": ident,
                "title": e.findtext("atom:title", default="Yeni video", namespaces=YT_NS),
                "link": link,
                "thumb": thumb,
                "author": e.findtext("atom:author/atom:name", default=None, namespaces=YT_NS),
                "published": _parse_dt(e.findtext("atom:published", default=None, namespaces=YT_NS)),
            })
    return out

class YouTubeCog(commands.Cog, name="YouTube"):
    def __init__(self, bot: "AllInOneBot") -> None:
        self.bot = bot
        self.session: Optional[aiohttp.ClientSession] = None

    async def cog_load(self) -> None:
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
        self.monitor.start()

    async def cog_unload(self) -> None:
        self.monitor.cancel()
        if self.session:
            await self.session.close()

    async def _fetch(self, url: str, cache: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
        if url in cache:
            return cache[url]
        entries = None
        try:
            async with self.session.get(url) as resp:
                if resp.status == 200:
                    raw = await resp.content.read(3_000_000)
                    entries = parse_feed(raw.decode("utf-8", errors="replace"))
        except Exception:
            pass
        cache[url] = entries
        return entries

    async def _check_guild(self, guild: discord.Guild, cache: Dict[str, Any]) -> int:
        g = self.bot.store.guild(guild.id)["youtube"]
        source = g.get("source") or DEFAULT_YT_SOURCE
        if not source:
            return 0
        url = feed_url_from_source(source)
        if not url:
            return 0
        entries = await self._fetch(url, cache)
        if not entries:
            return 0

        if not g.get("initialized"):
            g["seen"] = [e["id"] for e in entries][:100]
            g["initialized"] = True
            self.bot.store.save_soon()
            return 0

        seen = set(g["seen"])
        new = [e for e in entries if e["id"] not in seen]
        if not new:
            return 0
        new.reverse()
        new = new[-3:]
        channel = discord.utils.get(guild.text_channels, name=CH_VIDEO)
        sent = 0
        for entry in new:
            g["seen"].append(entry["id"])
            if channel:
                embed = discord.Embed(
                    title=entry["title"], url=entry["link"], color=discord.Color.red(),
                    description="Hemen izle! 🔔", timestamp=entry["published"] or discord.utils.utcnow())
                if entry["thumb"]:
                    embed.set_image(url=entry["thumb"])
                try:
                    await channel.send(content="📢 **Yeni video yüklendi!**", embed=embed)
                    sent += 1
                except Exception:
                    pass
        g["seen"] = g["seen"][-200:]
        self.bot.store.save_soon()
        return sent

    @tasks.loop(minutes=5)
    async def monitor(self) -> None:
        cache: Dict[str, Any] = {}
        for guild in list(self.bot.guilds):
            try:
                await self._check_guild(guild, cache)
            except Exception:
                pass

    @monitor.before_loop
    async def _before_monitor(self) -> None:
        await self.bot.wait_until_ready()

    @app_commands.command(name="youtube-ayarla", description="İzlenecek YouTube kanalını ayarlar.")
    @app_commands.guild_only()
    @require_staff(2)
    async def yt_set(self, interaction: discord.Interaction, source: str) -> None:
        url = feed_url_from_source(source)
        if not url:
            await respond(interaction, "❌ Geçersiz YouTube kaynak adresi / Kanal ID.", ephemeral=True)
            return
        g = self.bot.store.guild(interaction.guild_id)["youtube"]
        g.update({"source": source.strip(), "seen": [], "initialized": False})
        self.bot.store.save_soon()
        await respond(interaction, f"✅ YouTube takip kaynağı ayarlandı: `{source}`", ephemeral=True)

# ════════════════════════════════════════════════════════════════════════════
#  WELCOME / AUDIT COG
# ════════════════════════════════════════════════════════════════════════════
class WelcomeCog(commands.Cog, name="Karşılama"):
    def __init__(self, bot: "AllInOneBot") -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        guild = member.guild
        role = find_role(guild, ROLE_BY_KEY["bot" if member.bot else "member"])
        if role:
            try:
                await member.add_roles(role)
            except Exception:
                pass

        channel = discord.utils.get(guild.text_channels, name=CH_WELCOME)
        if channel:
            e = discord.Embed(title="🎉 Aramıza Hoş Geldin!",
                              description=f"{member.mention}, **{guild.name}** sunucusuna katıldı!\nKurallara göz atmayı unutma.",
                              color=C_OK, timestamp=discord.utils.utcnow())
            e.set_thumbnail(url=member.display_avatar.url)
            e.add_field(name="👥 Toplam üye", value=f"**{guild.member_count}**")
            try:
                await channel.send(content=member.mention, embed=e)
            except Exception:
                pass

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        guild = member.guild
        channel = discord.utils.get(guild.text_channels, name=CH_WELCOME)
        if channel:
            e = discord.Embed(title="👋 Bir üye ayrıldı", description=f"**{member}** sunucudan ayrıldı.",
                              color=C_ERR, timestamp=discord.utils.utcnow())
            e.set_thumbnail(url=member.display_avatar.url)
            e.add_field(name="👥 Kalan üye", value=f"**{guild.member_count}**")
            try:
                await channel.send(embed=e)
            except Exception:
                pass

# ════════════════════════════════════════════════════════════════════════════
#  ANNOUNCEMENT COG
# ════════════════════════════════════════════════════════════════════════════
class AnnounceModal(discord.ui.Modal):
    def __init__(self, channel: discord.TextChannel, embed_mode: bool, ping: str) -> None:
        super().__init__(title="Resmî Duyuru", timeout=600)
        self.channel = channel
        self.embed_mode = embed_mode
        self.ping = ping
        self.title_in = discord.ui.TextInput(label="Başlık", max_length=256, required=True) if embed_mode else None
        if self.title_in:
            self.add_item(self.title_in)
        self.body_in = discord.ui.TextInput(label="Mesaj", style=discord.TextStyle.paragraph, max_length=3800 if embed_mode else 1900, required=True)
        self.add_item(self.body_in)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        content = {"everyone": "@everyone", "here": "@here"}.get(self.ping)
        mentions = discord.AllowedMentions(everyone=bool(content), roles=False, users=False)
        try:
            if self.embed_mode:
                embed = discord.Embed(title=self.title_in.value, description=self.body_in.value, color=C_INFO, timestamp=discord.utils.utcnow())
                embed.set_footer(text=f"{interaction.guild.name} • Resmî Duyuru")
                await self.channel.send(content=content, embed=embed, allowed_mentions=mentions)
            else:
                text = self.body_in.value if not content else f"{content}\n{self.body_in.value}"
                await self.channel.send(text, allowed_mentions=mentions)
            await interaction.response.send_message(f"✅ Duyuru {self.channel.mention} kanalına gönderildi.", ephemeral=True)
            e = discord.Embed(title="📢 Duyuru gönderildi", color=C_INFO, timestamp=discord.utils.utcnow())
            e.add_field(name="Yetkili", value=interaction.user.mention)
            e.add_field(name="Kanal", value=self.channel.mention)
            await send_log(interaction.guild, e)
        except Exception as exc:
            await respond(interaction, f"❌ Gönderilemedi: {short_err(exc)}", ephemeral=True)

class AnnounceCog(commands.Cog, name="Duyuru"):
    def __init__(self, bot: "AllInOneBot") -> None:
        self.bot = bot

    @app_commands.command(name="announce", description="Botun kimliğiyle duyuru gönderir (Yönetici+).")
    @app_commands.guild_only()
    @require_staff(2)
    @app_commands.describe(channel="Kanal", mode="Mod", ping="Etiket")
    @app_commands.choices(
        mode=[app_commands.Choice(name="Embed", value="embed"), app_commands.Choice(name="Düz Metin", value="plain")],
        ping=[app_commands.Choice(name="Etiket Yok", value="none"), app_commands.Choice(name="@everyone", value="everyone"), app_commands.Choice(name="@here", value="here")])
    async def announce(self, interaction: discord.Interaction, channel: discord.TextChannel,
                       mode: Optional[app_commands.Choice[str]] = None,
                       ping: Optional[app_commands.Choice[str]] = None) -> None:
        embed_mode = (mode.value if mode else "embed") == "embed"
        await interaction.response.send_modal(AnnounceModal(channel, embed_mode, ping.value if ping else "none"))

# ════════════════════════════════════════════════════════════════════════════
#  BOT CLASS
# ════════════════════════════════════════════════════════════════════════════
class AllInOneBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        intents.voice_states = True
        super().__init__(
            command_prefix=commands.when_mentioned_or(PREFIX),
            intents=intents,
            help_command=None,
            allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=True),
            activity=discord.Activity(type=discord.ActivityType.listening, name="/yardim"),
        )
        self.store = Store(DATA_FILE)
        self.db = EconomyDB(ECONOMY_DB_FILE)
        self.dictionary: Set[str] = load_local_dictionary()

    async def setup_hook(self) -> None:
        # Render Uptime Server
        async def handle_ping(request: web.Request) -> web.Response:
            return web.Response(text="Bot aktif ve çalışıyor!")

        app = web.Application()
        app.router.add_get("/", handle_ping)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", PORT)
        await site.start()

        # Cog Kayıtları
        for cog_cls in (
            AdminCog, StatsCog, ChannelGuardCog, AntiSpamCog, GamesCog,
            MusicCog, EconomyCog, VoiceRoomsCog, YouTubeCog, WelcomeCog, AnnounceCog,
        ):
            try:
                await self.add_cog(cog_cls(self))
            except Exception:
                log.exception("Cog yüklenemedi: %s", cog_cls.__name__)

        self.tree.on_error = on_app_command_error
        try:
            if DEV_GUILD_ID:
                guild = discord.Object(id=DEV_GUILD_ID)
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)
            else:
                await self.tree.sync()
        except Exception:
            log.exception("Slash komutları senkronize edilemedi.")

    async def on_ready(self) -> None:
        log.info("Bot Aktif: %s (ID: %s)", self.user, self.user.id)

    async def close(self) -> None:
        self.store.flush()
        self.db.close()
        await super().close()

async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
    original = error.original if isinstance(error, app_commands.CommandInvokeError) else error
    if isinstance(original, NotAuthorized):
        msg = f"⛔ {original}"
    elif isinstance(original, discord.Forbidden):
        msg = "⚠️ Botun yetkisi veya rol sırası yetersiz."
    else:
        msg = "❌ Beklenmeyen bir hata oluştu."
    await respond(interaction, msg, ephemeral=True)

def main() -> None:
    setup_logging()
    if not TOKEN:
        log.error("DISCORD_TOKEN ortam değişkeni tanımlanmamış!")
        sys.exit(1)
    bot = AllInOneBot()
    bot.run(TOKEN, log_handler=None)

if __name__ == "__main__":
    main()
