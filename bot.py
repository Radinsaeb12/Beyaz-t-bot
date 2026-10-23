#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════════════════╗
║  ALL-IN-ONE DISCORD BOT  (discord.py 2.x · yt-dlp · ffmpeg · asyncio)    ║
╚══════════════════════════════════════════════════════════════════════════╝

Modules (each one is a Cog):
  AdminCog        /setup-server (!setup)  roles · categories · channels · /purge · /oyuncu-rolu · /yardim
  ChannelGuardCog counting channel · word-chain channel · #bot-komut enforcement
  AntiSpamCog     rapid-fire / repeated-text detection → 5 min timeout + purge
  GamesCog        /adam-asmaca (hangman) · /cf · /roll
  MusicCog        /play /pause /resume /skip /queue /stop /leave /nowplaying /volume
  VoiceRoomsCog   "+ | Oda Oluştur" join-to-create rooms + /oda-* controls
  YouTubeCog      RSS / YouTube channel monitor (every 5 minutes) → #video-duyuru
  WelcomeCog      join / leave embeds in #gelen-giden + auto roles
  AnnounceCog     /announce (modal based, embed or plain text)

Setup:
  1. Python 3.10+, FFmpeg installed and on PATH.
  2. pip install -r requirements.txt
  3. Create a .env file next to this script:
         DISCORD_TOKEN=your_bot_token
         DEV_GUILD_ID=123456789012345678        (optional – instant slash-command sync)
         YOUTUBE_CHANNEL_ID=UCxxxxxxxxxxxxxxxxxxxxxx   (optional default channel to monitor)
         YOUTUBE_RSS_URL=https://...            (optional alternative: any RSS/Atom feed)
  4. Developer Portal → Bot → enable "Server Members Intent" and "Message Content Intent".
  5. Invite the bot with the Administrator permission (needed to create/configure roles),
     move the bot's own integration role to the TOP of the role list, then run /setup-server.
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
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
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

try:  # optional .env support
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

# ════════════════════════════════════════════════════════════════════════════
#  CONFIGURATION
# ════════════════════════════════════════════════════════════════════════════
TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
PREFIX = os.getenv("BOT_PREFIX", "!")
PORT = int(os.getenv("PORT", 10000))
DATA_FILE = Path(os.getenv("DATA_FILE", "bot_data.json"))
DEFAULT_YT_SOURCE = (os.getenv("YOUTUBE_CHANNEL_ID") or os.getenv("YOUTUBE_RSS_URL") or "").strip()

try:
    DEV_GUILD_ID = int(os.getenv("DEV_GUILD_ID") or 0)
except ValueError:
    DEV_GUILD_ID = 0

# Channel / role names (the bot looks channels up by these names)
CH_WELCOME = "gelen-giden"
CH_VIDEO = "video-duyuru"
CH_ANNOUNCE = "duyurular"
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

# Anti-spam tuning
SPAM_WINDOW = 3.0           # seconds
SPAM_MAX_MESSAGES = 5       # > 5 messages inside SPAM_WINDOW
SPAM_REPEAT_WINDOW = 15.0   # seconds
SPAM_REPEAT_COUNT = 4       # same text N times inside SPAM_REPEAT_WINDOW
SPAM_TIMEOUT = timedelta(minutes=5)

# Counting game: forbid the same user counting twice in a row?
COUNT_NO_DOUBLE = False

# Music
MAX_QUEUE = 100
IDLE_TIMEOUT = 300          # leave voice after 5 min of silence
ALONE_TIMEOUT = 60          # leave if alone in voice for 60 s
STREAM_TTL = 1200           # re-resolve stream URLs older than 20 min

# Colors
C_OK = discord.Color.green()
C_ERR = discord.Color.red()
C_INFO = discord.Color.blurple()
C_WARN = discord.Color.orange()
C_GOLD = discord.Color.gold()

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
#  PERSISTENT STORE (JSON, debounced writes)
# ════════════════════════════════════════════════════════════════════════════
DEFAULT_GUILD: Dict[str, Any] = {
    "counting": {"last": 0, "last_user": None, "high": 0},
    "wordchain": {"last_word": None, "last_user": None, "used": [], "chain": 0},
    "antispam": {"global": True, "channels": {}},
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
#  GENERIC HELPERS
# ════════════════════════════════════════════════════════════════════════════
TR_ALPHABET = set("abcçdefgğhıijklmnoöprsştuüvyz")
WORD_RE = re.compile(r"[a-zçğıöşüâîû]{2,40}")
COUNT_RE = re.compile(r"[0-9]{1,15}")
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def tr_lower(text: str) -> str:
    return text.replace("I", "ı").replace("İ", "i").lower()


def tr_upper(text: str) -> str:
    return text.replace("i", "İ").replace("ı", "I").upper()


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
        log.warning("Mesaj silinemedi (HTTP hatası).")
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
    """Send a reply no matter whether the interaction was already acknowledged."""
    try:
        if interaction.response.is_done():
            await interaction.followup.send(content=content, embed=embed, ephemeral=ephemeral, **kwargs)
        else:
            await interaction.response.send_message(content=content, embed=embed, ephemeral=ephemeral, **kwargs)
    except discord.HTTPException:
        log.warning("Etkileşime yanıt verilemedi.")


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
    """0 = nobody · 1 = moderator · 2 = administrator · 3 = owner"""
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
    kind: str                 # "text" | "voice"
    access: Optional[str] = None
    topic: Optional[str] = None
    user_limit: int = 0


@dataclass(frozen=True)
class CategorySpec:
    name: str
    access: str               # public | readonly | gamer | vip | staff
    channels: Tuple[ChannelSpec, ...]


LAYOUT: List[CategorySpec] = [
    CategorySpec("📢・BİLGİ", "readonly", (
        ChannelSpec(CH_WELCOME, "text", "readonly", "Giren çıkan üyeler burada görünür."),
        ChannelSpec(CH_VIDEO, "text", "readonly", "Yeni YouTube videoları otomatik duyurulur."),
        ChannelSpec(CH_ANNOUNCE, "text", "readonly", "Resmî sunucu duyuruları."),
    )),
    CategorySpec("💬・SOHBET", "public", (
        ChannelSpec(CH_GENERAL, "text", "public", "Genel sohbet kanalı."),
        ChannelSpec(CH_BOTCMD, "text", "public", "Sadece bot komutları! Sohbet yasak."),
        ChannelSpec(CH_COUNT, "text", "public", "1'den başla ve sırayla say! Harf/yanlış sayı silinir."),
        ChannelSpec(CH_WORD, "text", "public", "Kelime türetme: önceki kelimenin son harfiyle başla."),
        ChannelSpec(CH_HANGMAN, "text", "public", "Adam asmaca oyunu için /adam-asmaca yaz."),
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
            d.update(send_messages=True, embed_links=True, attach_files=True, read_message_history=True,
                     add_reactions=True)
        if voice:
            d.update(connect=True, speak=True, stream=True, use_voice_activation=True)
        return po(**d)

    def muted() -> discord.PermissionOverwrite:
        d: Dict[str, bool] = {}
        if text:
            d.update(send_messages=False, create_public_threads=False, create_private_threads=False,
                     send_messages_in_threads=False)
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
#  ADMIN COG  – /setup-server, /purge, /oyuncu-rolu, /yardim
# ════════════════════════════════════════════════════════════════════════════
class AdminCog(commands.Cog, name="Yönetim"):
    def __init__(self, bot: "AllInOneBot") -> None:
        self.bot = bot
        self._running: Set[int] = set()

    # ---------- core setup routine ----------
    async def _do_setup(self, guild: discord.Guild, assign_existing: bool, fix_perms: bool) -> discord.Embed:
        if guild.id in self._running:
            return simple_embed("⏳ Kurulum zaten çalışıyor", "Lütfen mevcut kurulumun bitmesini bekle.", C_WARN)
        self._running.add(guild.id)
        try:
            return await self._setup_impl(guild, assign_existing, fix_perms)
        finally:
            self._running.discard(guild.id)

    async def _setup_impl(self, guild: discord.Guild, assign_existing: bool, fix_perms: bool) -> discord.Embed:
        me = guild.me
        gp = me.guild_permissions
        if not (gp.manage_roles and gp.manage_channels):
            return simple_embed(
                "❌ Bot yetkisi yetersiz",
                "Botun **Rolleri Yönet** ve **Kanalları Yönet** yetkisine ihtiyacı var. "
                "Botu Yönetici yetkisiyle davet etmeni öneririm.", C_ERR)

        roles_created: List[str] = []
        roles_existing: List[str] = []
        cats_created: List[str] = []
        ch_created: List[str] = []
        ch_existing = 0
        warnings: List[str] = []
        roles: Dict[str, discord.Role] = {}

        # 1) roles ---------------------------------------------------------
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

        # 2) hierarchy -----------------------------------------------------
        await self._fix_hierarchy(guild, roles, warnings)

        # 3) special assignments ------------------------------------------
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
                        warnings.append(f"`{m}` kullanıcısına Bot rolü verilemedi (rol sıralamasını kontrol et).")
                        break

        # 4) categories & channels ----------------------------------------
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
                if discord.utils.get(pool, name=spec.name) is not None:
                    ch_existing += 1
                    continue
                ow = build_overwrites(guild, roles, spec.access or cat_spec.access, spec.kind)
                try:
                    if spec.kind == "text":
                        await guild.create_text_channel(
                            spec.name, category=category, overwrites=ow, topic=spec.topic,
                            reason="Sunucu otomatik kurulumu")
                    else:
                        await guild.create_voice_channel(
                            spec.name, category=category, overwrites=ow, user_limit=spec.user_limit,
                            bitrate=min(int(guild.bitrate_limit), 128000), reason="Sunucu otomatik kurulumu")
                    ch_created.append(spec.name)
                    await asyncio.sleep(0.5)
                except discord.HTTPException as exc:
                    warnings.append(f"Kanal `{spec.name}` oluşturulamadı: {short_err(exc, 100)}")

        # 5) bulk-assign Member role in the background ---------------------
        member_role = roles.get("member")
        bulk_note = ""
        if assign_existing and member_role is not None:
            todo = sum(1 for m in guild.members if not m.bot and member_role not in m.roles)
            if todo:
                asyncio.create_task(self._bulk_assign(guild, member_role))
                bulk_note = f"\n👤 **{todo}** mevcut üyeye arka planda `{member_role.name}` rolü veriliyor."

        # 6) report --------------------------------------------------------
        embed = discord.Embed(
            title="✅ Sunucu kurulumu tamamlandı",
            description="Roller, kategoriler ve kanallar kontrol edildi; eksik olanlar oluşturuldu." + bulk_note,
            color=C_OK, timestamp=discord.utils.utcnow())
        embed.add_field(name=f"🎭 Oluşturulan roller ({len(roles_created)})",
                        value=trunc("\n".join(roles_created) or "—"), inline=True)
        embed.add_field(name=f"📌 Zaten var olan roller ({len(roles_existing)})",
                        value=trunc("\n".join(roles_existing) or "—"), inline=True)
        embed.add_field(name=f"📁 Yeni kategoriler ({len(cats_created)})",
                        value=trunc("\n".join(cats_created) or "—"), inline=False)
        embed.add_field(name=f"💬 Yeni kanallar ({len(ch_created)})  ·  mevcut: {ch_existing}",
                        value=trunc("\n".join(ch_created) or "—"), inline=False)
        if warnings:
            embed.add_field(name="⚠️ Uyarılar", value=trunc("\n".join(f"• {w}" for w in warnings)), inline=False)
            embed.color = C_WARN
        embed.set_footer(text="Rol hiyerarşisi: Kurucu > Yönetici > Moderatör > Bot > Özel Üye > Oyuncu > Üye")
        return embed

    async def _fix_hierarchy(self, guild: discord.Guild, roles: Dict[str, discord.Role], warnings: List[str]) -> None:
        order = [roles[k] for k in ("owner", "admin", "mod", "bot", "vip", "gamer", "member") if k in roles]
        if len(order) < 2:
            return
        if all(order[i].position > order[i + 1].position for i in range(len(order) - 1)):
            return  # already correct
        top = guild.me.top_role.position
        base = top - 1
        if base < len(order) or any(r.position >= top for r in order):
            warnings.append("Rol sırası düzeltilemedi: botun kendi rolünü sunucu rol listesinin EN ÜSTÜNE taşı ve "
                            "`/setup-server fix_permissions:True` komutunu tekrar çalıştır.")
            return
        positions = {role: base - i for i, role in enumerate(order)}
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
        done = 0
        for m in list(guild.members):
            if m.bot or role in m.roles:
                continue
            try:
                await m.add_roles(role, reason="Kurulum: mevcut üyelere Üye rolü")
                done += 1
            except discord.HTTPException:
                pass
            await asyncio.sleep(0.4)
        log.info("[%s] %d üyeye %s rolü verildi.", guild.name, done, role.name)

    # ---------- commands ----------
    @app_commands.command(name="setup-server", description="Rolleri, kategorileri ve kanalları otomatik kurar.")
    @app_commands.guild_only()
    @app_commands.describe(
        assign_existing="Mevcut üyelere 'Üye' rolü verilsin mi?",
        fix_permissions="Zaten var olan rollerin yetkilerini de sıfırla/onar")
    async def setup_slash(self, interaction: discord.Interaction, assign_existing: bool = True,
                          fix_permissions: bool = False) -> None:
        if staff_level(interaction.user) < 3:
            raise NotAuthorized("Bu komutu sadece sunucu sahibi / Yönetici yetkisi olanlar kullanabilir.")
        await interaction.response.defer(thinking=True)
        embed = await self._do_setup(interaction.guild, assign_existing, fix_permissions)
        await interaction.followup.send(embed=embed)

    @commands.command(name="setup", aliases=["setup-server"])
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def setup_prefix(self, ctx: commands.Context) -> None:
        msg = await ctx.send("⏳ Sunucu kurulumu başladı, bu işlem birkaç dakika sürebilir…")
        embed = await self._do_setup(ctx.guild, True, False)
        try:
            await msg.edit(content=None, embed=embed)
        except discord.HTTPException:
            await ctx.send(embed=embed)

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
            await interaction.followup.send("❌ Botun bu kanalda **Mesajları Yönet** yetkisi yok.", ephemeral=True)
            return
        except discord.HTTPException as exc:
            await interaction.followup.send(f"❌ Silme başarısız: {short_err(exc)}", ephemeral=True)
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
                await respond(interaction, f"🎮 {role.mention} rolü verildi! Oyun kanalları açıldı.", ephemeral=True)
        except discord.Forbidden:
            await respond(interaction, "❌ Rolü veremiyorum; bot rolü bu rolün üstünde olmalı.", ephemeral=True)

    @app_commands.command(name="yardim", description="Komut listesini gösterir.")
    async def help_cmd(self, interaction: discord.Interaction) -> None:
        e = discord.Embed(title="📖 Komutlar", color=C_INFO)
        e.add_field(name="🎵 Müzik", inline=False, value=(
            "`/play` `/pause` `/resume` `/skip` `/queue` `/stop` `/leave` `/nowplaying` `/volume`"))
        e.add_field(name="🎮 Oyunlar", inline=False, value=(
            "`/adam-asmaca` `/cf` `/roll` `/sayim-durum` `/kelime-durum`\n"
            f"Sayı oyunu → #{CH_COUNT} · Kelime türetme → #{CH_WORD}"))
        e.add_field(name="🔊 Özel Oda", inline=False, value=(
            f"**{VC_MASTER}** kanalına gir → kendi odan açılır.\n"
            "`/oda-kilitle` `/oda-ac` `/oda-limit` `/oda-isim` `/oda-davet`"))
        e.add_field(name="🛡️ Yetkili", inline=False, value=(
            "`/setup-server` `/announce` `/purge` `/antispam-ayarla` `/antispam-durum` "
            "`/youtube-ayarla` `/youtube-kontrol` `/sayim-sifirla` `/kelime-sifirla`"))
        await respond(interaction, embed=e, ephemeral=True)


# ════════════════════════════════════════════════════════════════════════════
#  CHANNEL GUARD COG  – counting / word-chain / #bot-komut
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

    def _lock(self, guild_id: int, name: str) -> asyncio.Lock:
        return self._locks.setdefault((guild_id, name), asyncio.Lock())

    async def _warn(self, message: discord.Message, text: str, seconds: float = 6.0) -> None:
        key = (message.guild.id, message.author.id)
        now = time.monotonic()
        if now - self._warn_cd.get(key, 0.0) < 2.5:
            return  # don't flood the channel with warnings
        self._warn_cd[key] = now
        await send_temp(message.channel, text, seconds)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
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
            await safe_delete(after)
            await self._warn(after, f"✏️ {after.author.mention} bu kanalda mesaj düzenlemek yasak!")

    # ---------- counting ----------
    async def _counting(self, message: discord.Message) -> None:
        async with self._lock(message.guild.id, "count"):
            g = self.bot.store.guild(message.guild.id)["counting"]
            expected = int(g["last"]) + 1
            content = message.content.strip()
            problem: Optional[str] = None
            if not COUNT_RE.fullmatch(content):
                problem = "Bu kanalda **sadece sayı** yazabilirsin!"
            elif int(content) != expected:
                problem = "Yanlış sayı!"
            elif COUNT_NO_DOUBLE and g.get("last_user") == message.author.id:
                problem = "Üst üste iki kez sayamazsın, başkasını bekle!"

            if problem:
                await safe_delete(message)
                await self._warn(message, f"❌ {message.author.mention} {problem} "
                                          f"Sayı sıfırlanmadı — sıradaki sayı: **{expected}**")
                return
            g["last"] = expected
            g["last_user"] = message.author.id
            g["high"] = max(int(g.get("high", 0)), expected)
            self.bot.store.save_soon()
            try:
                await message.add_reaction("🎉" if expected % 100 == 0 else "✅")
            except discord.HTTPException:
                pass

    # ---------- word chain ----------
    async def _wordchain(self, message: discord.Message) -> None:
        async with self._lock(message.guild.id, "word"):
            g = self.bot.store.guild(message.guild.id)["wordchain"]
            word = tr_lower(message.content.strip())
            problem: Optional[str] = None
            if not WORD_RE.fullmatch(word):
                problem = "Sadece **tek bir kelime** (harflerden oluşan, en az 2 harf) yazabilirsin!"
            elif g["last_word"] and word[0] != required_letter(g["last_word"]):
                problem = f"Kelime **{tr_upper(required_letter(g['last_word']))}** harfiyle başlamalı!"
            elif word in g["used"]:
                problem = "Bu kelime daha önce kullanıldı!"
            elif g["last_word"] and g.get("last_user") == message.author.id:
                problem = "Üst üste iki kelime yazamazsın, başkasını bekle!"

            if problem:
                await safe_delete(message)
                await self._warn(message, f"❌ {message.author.mention} {problem}")
                return
            g["last_word"] = word
            g["last_user"] = message.author.id
            g["used"].append(word)
            if len(g["used"]) > 20000:
                g["used"] = g["used"][-20000:]
            g["chain"] = int(g.get("chain", 0)) + 1
            self.bot.store.save_soon()
            try:
                await message.add_reaction("✅")
            except discord.HTTPException:
                pass

    # ---------- #bot-komut ----------
    async def _botcmd(self, message: discord.Message) -> None:
        ctx = await self.bot.get_context(message)
        if ctx.valid:
            return  # a real prefix command
        await safe_delete(message)
        chat = discord.utils.get(message.guild.text_channels, name=CH_GENERAL)
        where = f" Sohbet için {chat.mention} kanalını kullan." if chat else ""
        await self._warn(
            message,
            f"💬 {message.author.mention} burası **sadece bot komutları** içindir!{where} "
            f"(Slash komutları `/` ile, ön ek komutları `{PREFIX}` ile kullanılır.)", 8.0)

    # ---------- status / reset commands ----------
    @app_commands.command(name="sayim-durum", description="Sayı oyununun durumunu gösterir.")
    @app_commands.guild_only()
    async def count_status(self, interaction: discord.Interaction) -> None:
        g = self.bot.store.guild(interaction.guild_id)["counting"]
        e = discord.Embed(title="🔢 Sayım Oyunu", color=C_INFO)
        e.add_field(name="Son sayı", value=f"**{g['last']}**")
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
        await respond(interaction, "🔄 Sayım sıfırlandı. **1** ile yeniden başlayın!")

    @app_commands.command(name="kelime-durum", description="Kelime türetme oyununun durumunu gösterir.")
    @app_commands.guild_only()
    async def word_status(self, interaction: discord.Interaction) -> None:
        g = self.bot.store.guild(interaction.guild_id)["wordchain"]
        e = discord.Embed(title="🔤 Kelime Türetme", color=C_INFO)
        if g["last_word"]:
            e.add_field(name="Son kelime", value=f"**{g['last_word']}**")
            e.add_field(name="Sıradaki harf", value=f"**{tr_upper(required_letter(g['last_word']))}**")
        else:
            e.description = "Henüz kelime yok. İstediğin kelimeyle başla!"
        e.add_field(name="Zincir uzunluğu", value=str(g.get("chain", 0)))
        await respond(interaction, embed=e)

    @app_commands.command(name="kelime-sifirla", description="Kelime türetme oyununu sıfırlar (Yönetici+).")
    @app_commands.guild_only()
    @require_staff(2)
    async def word_reset(self, interaction: discord.Interaction) -> None:
        g = self.bot.store.guild(interaction.guild_id)["wordchain"]
        g.update({"last_word": None, "last_user": None, "used": [], "chain": 0})
        self.bot.store.save_soon()
        await respond(interaction, "🔄 Kelime zinciri sıfırlandı. Yeni bir kelimeyle başlayın!")


# ════════════════════════════════════════════════════════════════════════════
#  ANTI-SPAM COG
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
        try:
            # purge the spam
            by_channel: Dict[discord.abc.GuildChannel, List[discord.Message]] = collections.defaultdict(list)
            for m in messages:
                by_channel[m.channel].append(m)
            purged = 0
            for ch, msgs in by_channel.items():
                try:
                    if len(msgs) > 1 and isinstance(ch, discord.TextChannel):
                        await ch.delete_messages(msgs, reason=f"Anti-spam: {reason}")
                    else:
                        for m in msgs:
                            await safe_delete(m)
                    purged += len(msgs)
                except discord.HTTPException:
                    for m in msgs:
                        if await safe_delete(m):
                            purged += 1

            # timeout
            timed_out = False
            err = ""
            try:
                await member.timeout(SPAM_TIMEOUT, reason=f"Anti-spam: {reason}")
                timed_out = True
            except discord.Forbidden:
                err = "Botun bu kullanıcıyı susturma yetkisi yok (rol sıralaması / Üyeleri Zaman Aşımına Uğrat)."
            except discord.HTTPException as exc:
                err = short_err(exc, 120)

            if timed_out:
                await send_temp(channel, f"🚨 {member.mention} spam yaptığı için **5 dakika** susturuldu "
                                         f"ve mesajları silindi.", 10)
            else:
                await send_temp(channel, f"🚨 {member.mention} spam yapmayı bırak! Mesajların silindi.", 10)

            e = discord.Embed(title="🚨 Anti-Spam", color=C_ERR, timestamp=discord.utils.utcnow())
            e.add_field(name="Kullanıcı", value=f"{member.mention} (`{member.id}`)")
            e.add_field(name="Sebep", value=reason)
            e.add_field(name="Silinen mesaj", value=str(purged))
            e.add_field(name="İşlem", value="5 dk timeout" if timed_out else f"Timeout başarısız: {err}", inline=False)
            await send_log(member.guild, e)
        except Exception:
            log.exception("Anti-spam cezası uygulanırken hata")
        finally:
            self.punishing.discard(key)

    @app_commands.command(name="antispam-ayarla", description="Anti-spam filtresini aç/kapat (Yönetici+).")
    @app_commands.guild_only()
    @require_staff(2)
    @app_commands.describe(enabled="Açık mı?", channel="Boş bırakırsan tüm sunucu için geçerli olur.")
    async def antispam_set(self, interaction: discord.Interaction, enabled: bool,
                           channel: Optional[discord.TextChannel] = None) -> None:
        cfg = self.bot.store.guild(interaction.guild_id)["antispam"]
        if channel is None:
            cfg["global"] = enabled
            scope = "tüm sunucu"
        else:
            cfg["channels"][str(channel.id)] = enabled
            scope = channel.mention
        self.bot.store.save_soon()
        await respond(interaction, f"🛡️ Anti-spam **{'açıldı' if enabled else 'kapatıldı'}** → {scope}")

    @app_commands.command(name="antispam-durum", description="Anti-spam ayarlarını gösterir.")
    @app_commands.guild_only()
    @require_staff(1)
    async def antispam_status(self, interaction: discord.Interaction) -> None:
        cfg = self.bot.store.guild(interaction.guild_id)["antispam"]
        lines = [f"**Genel:** {'🟢 Açık' if cfg['global'] else '🔴 Kapalı'}"]
        for cid, state in cfg["channels"].items():
            ch = interaction.guild.get_channel(int(cid))
            lines.append(f"{ch.mention if ch else cid}: {'🟢 Açık' if state else '🔴 Kapalı'}")
        lines.append(f"\nKural: >{SPAM_MAX_MESSAGES} mesaj / {SPAM_WINDOW:.0f} sn  veya  "
                     f"{SPAM_REPEAT_COUNT}× aynı mesaj / {SPAM_REPEAT_WINDOW:.0f} sn → 5 dk timeout")
        await respond(interaction, embed=simple_embed("🛡️ Anti-Spam", "\n".join(lines)), ephemeral=True)


# ════════════════════════════════════════════════════════════════════════════
#  GAMES COG  – hangman · coinflip · dice
# ════════════════════════════════════════════════════════════════════════════
HANGMAN_WORDS: Dict[str, List[str]] = {
    "Hayvan": ["aslan", "kaplan", "kartal", "penguen", "zürafa", "kaplumbağa", "papağan", "yunus", "tilki",
               "geyik", "leylek", "karınca", "yarasa", "flamingo"],
    "Şehir / Ülke": ["istanbul", "ankara", "izmir", "trabzon", "almanya", "japonya", "brezilya", "kanada",
                     "norveç", "mısır", "portekiz", "eskişehir"],
    "Teknoloji": ["klavye", "bilgisayar", "yazılım", "internet", "telefon", "sunucu", "algoritma", "monitör",
                  "işlemci", "tarayıcı", "şifreleme", "robot"],
    "Yiyecek": ["baklava", "lahmacun", "mercimek", "karnıyarık", "simit", "künefe", "pastırma", "dondurma",
                "mantı", "börek", "çorba"],
    "Genel": ["kütüphane", "gökyüzü", "arkadaş", "orman", "deniz", "pencere", "uçurtma", "bahçe", "yıldız",
              "gökkuşağı", "kahraman", "macera"],
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

    def __init__(self, word: str, category: str, owner_id: int) -> None:
        self.word = word
        self.category = category
        self.owner_id = owner_id
        self.correct: Set[str] = set()
        self.wrong: List[str] = []
        self.wrong_words: List[str] = []
        self.mistakes = 0
        self.status = "playing"      # playing | won | lost | timeout | quit
        self.last_action = "Oyun başladı! Harf tahmin et."

    @property
    def masked(self) -> str:
        return " ".join(tr_upper(c) if c in self.correct else "_" for c in self.word)

    def _check_end(self) -> None:
        if all(c in self.correct for c in self.word):
            self.status = "won"
        elif self.mistakes >= self.MAX_WRONG:
            self.status = "lost"

    def guess_letter(self, letter: str, who: str) -> str:
        if letter in self.correct or letter in self.wrong:
            return "dup"
        if letter in self.word:
            self.correct.add(letter)
            self.last_action = f"✅ {who}, **{tr_upper(letter)}** harfini buldu!"
            self._check_end()
            return "hit"
        self.wrong.append(letter)
        self.mistakes += 1
        self.last_action = f"❌ {who}, **{tr_upper(letter)}** harfi yok!"
        self._check_end()
        return "miss"

    def guess_word(self, word: str, who: str) -> str:
        if word == self.word:
            self.correct.update(self.word)
            self.last_action = f"🎯 {who} kelimeyi bildi!"
            self._check_end()
            return "hit"
        self.mistakes += 1
        self.wrong_words.append(word)
        self.last_action = f"❌ {who}, **{tr_upper(word)}** yanlış tahmin!"
        self._check_end()
        return "miss"


class LetterModal(discord.ui.Modal):
    def __init__(self, game_view: "HangmanView") -> None:
        super().__init__(title="Harf Tahmin Et", timeout=120)
        self.game_view = game_view
        self.letter = discord.ui.TextInput(label="Bir harf yaz", min_length=1, max_length=1, required=True)
        self.add_item(self.letter)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        letter = tr_lower(self.letter.value.strip())
        if letter not in TR_ALPHABET:
            await interaction.response.send_message("⚠️ Geçerli bir Türkçe harf yaz.", ephemeral=True)
            return
        game = self.game_view.game
        if game.status != "playing":
            await interaction.response.send_message("Oyun bitti.", ephemeral=True)
            return
        result = game.guess_letter(letter, interaction.user.display_name)
        if result == "dup":
            await interaction.response.send_message("Bu harfi zaten denediniz.", ephemeral=True)
            return
        await self.game_view.refresh(interaction)


class WordModal(discord.ui.Modal):
    def __init__(self, game_view: "HangmanView") -> None:
        super().__init__(title="Kelimeyi Tahmin Et", timeout=120)
        self.game_view = game_view
        self.word = discord.ui.TextInput(label="Kelime", min_length=2, max_length=40, required=True)
        self.add_item(self.word)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        guess = tr_lower(self.word.value.strip())
        game = self.game_view.game
        if game.status != "playing":
            await interaction.response.send_message("Oyun bitti.", ephemeral=True)
            return
        if not WORD_RE.fullmatch(guess):
            await interaction.response.send_message("⚠️ Geçerli bir kelime yaz.", ephemeral=True)
            return
        game.guess_word(guess, interaction.user.display_name)
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
        titles = {"playing": "🪢 Adam Asmaca", "won": "🎉 Kazandınız!", "lost": "💀 Kaybettiniz!",
                  "timeout": "⌛ Süre doldu", "quit": "🏳️ Oyun bırakıldı"}
        e = discord.Embed(title=titles[g.status], color=color)
        e.description = f"```\n{HANGMAN_ART[min(g.mistakes, 6)]}\n```\n# `{g.masked}`"
        e.add_field(name="Kategori", value=g.category)
        e.add_field(name="Kalan hak", value=f"{'❤️' * (g.MAX_WRONG - g.mistakes)}{'🖤' * g.mistakes}")
        wrong = ", ".join(tr_upper(w) for w in g.wrong) or "—"
        e.add_field(name="Yanlış harfler", value=wrong, inline=False)
        if g.wrong_words:
            e.add_field(name="Yanlış kelimeler", value=trunc(", ".join(tr_upper(w) for w in g.wrong_words), 500),
                        inline=False)
        if g.status != "playing":
            e.add_field(name="Kelime", value=f"**{tr_upper(g.word)}**", inline=False)
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
        await interaction.response.send_modal(LetterModal(self))

    @discord.ui.button(label="Kelimeyi Tahmin Et", emoji="🎯", style=discord.ButtonStyle.success)
    async def word_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(WordModal(self))

    @discord.ui.button(label="Bırak", emoji="🏳️", style=discord.ButtonStyle.danger)
    async def quit_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != self.game.owner_id and staff_level(interaction.user) < 1:
            await interaction.response.send_message("Oyunu sadece başlatan kişi veya yetkililer bırakabilir.",
                                                    ephemeral=True)
            return
        self.game.status = "quit"
        self.game.last_action = f"{interaction.user.display_name} oyunu bıraktı."
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

    @app_commands.command(name="adam-asmaca", description="Adam asmaca oyunu başlatır.")
    @app_commands.guild_only()
    @app_commands.describe(kategori="Kelime kategorisi (boş = rastgele)")
    @app_commands.choices(kategori=[app_commands.Choice(name=k, value=k) for k in HANGMAN_WORDS])
    async def hangman(self, interaction: discord.Interaction,
                      kategori: Optional[app_commands.Choice[str]] = None) -> None:
        if interaction.channel_id in self.active:
            await respond(interaction, "⚠️ Bu kanalda zaten devam eden bir adam asmaca var!", ephemeral=True)
            return
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
#  MUSIC COG  – yt-dlp + FFmpeg
# ════════════════════════════════════════════════════════════════════════════
YTDL_OPTS: Dict[str, Any] = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "default_search": "ytsearch",
    "quiet": True,
    "no_warnings": True,
    "skip_download": True,
    "socket_timeout": 15,
    "retries": 5,
    "cachedir": False,
    "source_address": "0.0.0.0",
    "extractor_args": {
        "youtube": {
            "player_client": ["android", "web_creator"]
        }
    },
}
FFMPEG_BEFORE = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -nostdin"
FFMPEG_OPTIONS = "-vn"
URL_RE = re.compile(r"^https?://", re.I)


def _ytdl_extract(query: str) -> Dict[str, Any]:
    target = query if URL_RE.match(query) else f"ytsearch1:{query}"
    with yt_dlp.YoutubeDL(YTDL_OPTS) as ydl:
        info = ydl.extract_info(target, download=False)
    if info is None:
        raise RuntimeError("Sonuç bulunamadı.")
    if "entries" in info:
        entries = [e for e in info["entries"] if e]
        if not entries:
            raise RuntimeError("Sonuç bulunamadı.")
        info = entries[0]
    if not info.get("url"):
        raise RuntimeError("Ses akışı çözümlenemedi.")
    return info


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
        if error:
            log.error("Oynatma hatası: %s", error)
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
        e.add_field(name="İsteyen", value=track.requester.mention)
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
                        discord.FFmpegPCMAudio(track.stream_url, before_options=FFMPEG_BEFORE,
                                               options=FFMPEG_OPTIONS),
                        volume=self.volume)
                except Exception as exc:  # extraction / ffmpeg problems
                    log.warning("Şarkı hazırlanamadı: %s", exc)
                    await self.say(f"⚠️ **{track.title}** çalınamadı: `{short_err(exc, 180)}`")
                    continue

                self.current = track
                self.next_evt.clear()
                try:
                    vc.play(source, after=self._after)
                except discord.ClientException as exc:
                    await self.say(f"⚠️ Oynatma başlatılamadı: `{short_err(exc)}`")
                    continue
                await self.say(embed=self.now_playing_embed(track))
                await self.next_evt.wait()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Müzik döngüsü çöktü")
            await self.destroy("⚠️ Beklenmeyen bir hata oluştu, müzik oynatıcı kapatıldı.")

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

    # ---------- helpers ----------
    async def _require_same_channel(self, interaction: discord.Interaction) -> Optional[discord.VoiceClient]:
        vc = interaction.guild.voice_client
        member = interaction.user
        if vc is None or not vc.is_connected():
            await respond(interaction, "❌ Şu an bir ses kanalında değilim.", ephemeral=True)
            return None
        if not member.voice or member.voice.channel != vc.channel:
            await respond(interaction, "❌ Bu komut için benimle **aynı ses kanalında** olmalısın.", ephemeral=True)
            return None
        return vc

    # ---------- commands ----------
    @app_commands.command(name="play", description="YouTube linki veya arama ile müzik çalar.")
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
        await interaction.response.defer(thinking=True)

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
                    await interaction.followup.send("❌ Ben başka bir ses kanalında müzik çalıyorum.")
                    return
                await vc.move_to(channel)
        except asyncio.TimeoutError:
            await interaction.followup.send("❌ Ses kanalına bağlanma zaman aşımına uğradı.")
            return
        except (discord.ClientException, discord.HTTPException, RuntimeError) as exc:
            await interaction.followup.send(f"❌ Ses kanalına bağlanılamadı: `{short_err(exc)}`")
            return

        if player is None:
            player = GuildPlayer(self, interaction.guild, interaction.channel)
            self.players[interaction.guild_id] = player
        else:
            player.text_channel = interaction.channel

        if len(player.queue) >= MAX_QUEUE:
            await interaction.followup.send(f"❌ Kuyruk dolu (maks. {MAX_QUEUE}).")
            return
        try:
            info = await ytdl_extract(query.strip())
        except asyncio.TimeoutError:
            await interaction.followup.send("❌ Arama zaman aşımına uğradı, tekrar dene.")
            return
        except Exception as exc:
            log.warning("yt-dlp hatası: %s", exc)
            await interaction.followup.send(f"❌ Şarkı bulunamadı/çözümlenemedi: `{short_err(exc, 200)}`")
            return

        track = Track.from_info(info, member)
        was_idle = player.current is None and not player.queue
        player.queue.append(track)
        player.wakeup.set()
        if was_idle:
            await interaction.followup.send(embed=simple_embed("🔎 Bulundu", f"[{track.title}]({track.webpage_url})"))
        else:
            e = player.now_playing_embed(track, "➕ Kuyruğa eklendi")
            e.add_field(name="Sıra", value=f"#{len(player.queue)}")
            await interaction.followup.send(embed=e)

    @app_commands.command(name="pause", description="Müziği duraklatır.")
    @app_commands.guild_only()
    async def pause(self, interaction: discord.Interaction) -> None:
        vc = await self._require_same_channel(interaction)
        if vc is None:
            return
        if vc.is_playing():
            vc.pause()
            await respond(interaction, "⏸️ Duraklatıldı.")
        else:
            await respond(interaction, "❌ Şu an çalan bir şey yok.", ephemeral=True)

    @app_commands.command(name="resume", description="Duraklatılan müziği devam ettirir.")
    @app_commands.guild_only()
    async def resume(self, interaction: discord.Interaction) -> None:
        vc = await self._require_same_channel(interaction)
        if vc is None:
            return
        if vc.is_paused():
            vc.resume()
            await respond(interaction, "▶️ Devam ediyor.")
        else:
            await respond(interaction, "❌ Müzik duraklatılmış değil.", ephemeral=True)

    @app_commands.command(name="skip", description="Şarkıyı atlar.")
    @app_commands.guild_only()
    async def skip(self, interaction: discord.Interaction) -> None:
        vc = await self._require_same_channel(interaction)
        if vc is None:
            return
        player = self.players.get(interaction.guild_id)
        if vc.is_playing() or vc.is_paused():
            title = player.current.title if player and player.current else "şarkı"
            vc.stop()
            await respond(interaction, f"⏭️ **{title}** atlandı.")
        else:
            await respond(interaction, "❌ Atlanacak bir şey yok.", ephemeral=True)

    @app_commands.command(name="queue", description="Şarkı kuyruğunu gösterir.")
    @app_commands.guild_only()
    @app_commands.describe(page="Sayfa numarası")
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
                        value=f"[{player.current.title}]({player.current.webpage_url}) "
                              f"`{fmt_duration(player.current.duration)}`")
        if chunk:
            start = (page - 1) * per
            lines = [f"`{start + i + 1}.` [{t.title}]({t.webpage_url}) `{fmt_duration(t.duration)}` — "
                     f"{t.requester.display_name}" for i, t in enumerate(chunk)]
            e.add_field(name="Sıradakiler", value=trunc("\n".join(lines), 1000), inline=False)
        total = sum(t.duration or 0 for t in items)
        e.set_footer(text=f"Sayfa {page}/{pages} • {len(items)} şarkı • Toplam {fmt_duration(total) if total else '—'}")
        await respond(interaction, embed=e)

    @app_commands.command(name="nowplaying", description="Çalan şarkıyı gösterir.")
    @app_commands.guild_only()
    async def nowplaying(self, interaction: discord.Interaction) -> None:
        player = self.players.get(interaction.guild_id)
        if player is None or player.current is None:
            await respond(interaction, "❌ Şu an çalan bir şey yok.", ephemeral=True)
            return
        e = player.now_playing_embed(player.current)
        e.add_field(name="Ses", value=f"%{int(player.volume * 100)}")
        await respond(interaction, embed=e)

    @app_commands.command(name="volume", description="Ses seviyesini ayarlar (1-150).")
    @app_commands.guild_only()
    async def volume(self, interaction: discord.Interaction, level: app_commands.Range[int, 1, 150]) -> None:
        vc = await self._require_same_channel(interaction)
        if vc is None:
            return
        player = self.players.get(interaction.guild_id)
        if player is None:
            await respond(interaction, "❌ Aktif oynatıcı yok.", ephemeral=True)
            return
        player.volume = level / 100
        if isinstance(vc.source, discord.PCMVolumeTransformer):
            vc.source.volume = player.volume
        await respond(interaction, f"🔊 Ses seviyesi **%{level}**")

    @app_commands.command(name="stop", description="Müziği durdurur ve kuyruğu temizler.")
    @app_commands.guild_only()
    async def stop(self, interaction: discord.Interaction) -> None:
        vc = await self._require_same_channel(interaction)
        if vc is None:
            return
        player = self.players.get(interaction.guild_id)
        if player:
            player.queue.clear()
        if vc.is_playing() or vc.is_paused():
            vc.stop()
        await respond(interaction, "⏹️ Müzik durduruldu, kuyruk temizlendi.")

    @app_commands.command(name="leave", description="Ses kanalından ayrılır.")
    @app_commands.guild_only()
    async def leave(self, interaction: discord.Interaction) -> None:
        vc = await self._require_same_channel(interaction)
        if vc is None:
            return
        player = self.players.get(interaction.guild_id)
        if player:
            await player.destroy()
        else:
            await vc.disconnect(force=True)
        await respond(interaction, "👋 Kanaldan ayrıldım.")

    # ---------- voice events ----------
    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState,
                                    after: discord.VoiceState) -> None:
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
        """Clean up empty rooms that were left over from a previous run."""
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
        except (discord.NotFound, discord.Forbidden):
            pass
        except discord.HTTPException:
            log.warning("Geçici oda silinemedi: %s", channel.id)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState,
                                    after: discord.VoiceState) -> None:
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
            # already owns a room? → move them there
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
            except discord.Forbidden:
                log.warning("[%s] Geçici oda oluşturma yetkisi yok.", guild.name)
                return
            except discord.HTTPException as exc:
                log.warning("Geçici oda oluşturulamadı: %s", exc)
                return
            temp[str(room.id)] = member.id
            self.bot.store.save_soon()
            try:
                await member.move_to(room, reason="Geçici odaya taşındı")
            except discord.HTTPException:
                # user left in the meantime
                await self._delete_room(room)

    # ---------- room controls ----------
    def _room_of(self, interaction: discord.Interaction) -> Optional[discord.VoiceChannel]:
        member = interaction.user
        if not isinstance(member, discord.Member) or not member.voice or not member.voice.channel:
            return None
        ch = member.voice.channel
        owner = self._temp(interaction.guild_id).get(str(ch.id))
        if owner is None:
            return None
        if owner != member.id and staff_level(member) < 1:
            return None
        return ch

    async def _need_room(self, interaction: discord.Interaction) -> Optional[discord.VoiceChannel]:
        room = self._room_of(interaction)
        if room is None:
            await respond(interaction, "❌ Bu komutu **kendi geçici odanın içindeyken** kullanabilirsin.",
                          ephemeral=True)
        return room

    async def _set_lock(self, room: discord.VoiceChannel, locked: bool) -> None:
        for key in ("member", "gamer", "vip"):
            role = find_role(room.guild, ROLE_BY_KEY[key])
            if role is not None:
                ow = room.overwrites_for(role)
                ow.connect = (not locked)
                await room.set_permissions(role, overwrite=ow, reason="Oda kilidi")

    @app_commands.command(name="oda-kilitle", description="Odanı kilitler (kimse katılamaz).")
    @app_commands.guild_only()
    async def room_lock(self, interaction: discord.Interaction) -> None:
        room = await self._need_room(interaction)
        if room is None:
            return
        try:
            await self._set_lock(room, True)
            await respond(interaction, "🔒 Oda kilitlendi. `/oda-davet` ile birini davet edebilirsin.", ephemeral=True)
        except discord.HTTPException as exc:
            await respond(interaction, f"❌ Başarısız: {short_err(exc)}", ephemeral=True)

    @app_commands.command(name="oda-ac", description="Odanın kilidini açar.")
    @app_commands.guild_only()
    async def room_unlock(self, interaction: discord.Interaction) -> None:
        room = await self._need_room(interaction)
        if room is None:
            return
        try:
            await self._set_lock(room, False)
            await respond(interaction, "🔓 Oda herkese açıldı.", ephemeral=True)
        except discord.HTTPException as exc:
            await respond(interaction, f"❌ Başarısız: {short_err(exc)}", ephemeral=True)

    @app_commands.command(name="oda-limit", description="Odanın kişi limitini ayarlar (0 = limitsiz).")
    @app_commands.guild_only()
    async def room_limit(self, interaction: discord.Interaction, limit: app_commands.Range[int, 0, 99]) -> None:
        room = await self._need_room(interaction)
        if room is None:
            return
        try:
            await room.edit(user_limit=limit)
            await respond(interaction, f"👥 Limit: **{limit or 'limitsiz'}**", ephemeral=True)
        except discord.HTTPException as exc:
            await respond(interaction, f"❌ Başarısız: {short_err(exc)}", ephemeral=True)

    @app_commands.command(name="oda-isim", description="Odanın adını değiştirir.")
    @app_commands.guild_only()
    async def room_rename(self, interaction: discord.Interaction, name: app_commands.Range[str, 1, 80]) -> None:
        room = await self._need_room(interaction)
        if room is None:
            return
        try:
            await room.edit(name=f"[🔊] {name}"[:100])
            await respond(interaction, "✏️ Oda adı güncellendi.", ephemeral=True)
        except discord.HTTPException as exc:
            await respond(interaction, f"❌ Başarısız (çok sık isim değiştirme limiti olabilir): {short_err(exc)}",
                          ephemeral=True)

    @app_commands.command(name="oda-davet", description="Kilitli odana birine giriş izni verir.")
    @app_commands.guild_only()
    async def room_invite(self, interaction: discord.Interaction, user: discord.Member) -> None:
        room = await self._need_room(interaction)
        if room is None:
            return
        try:
            await room.set_permissions(user, connect=True, view_channel=True, speak=True, reason="Oda daveti")
            await respond(interaction, f"✅ {user.mention} odana girebilir.", ephemeral=True)
        except discord.HTTPException as exc:
            await respond(interaction, f"❌ Başarısız: {short_err(exc)}", ephemeral=True)


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
        except (TypeError, ValueError):
            return None


def parse_feed(text: str) -> List[Dict[str, Any]]:
    root = ET.fromstring(text)
    out: List[Dict[str, Any]] = []
    if root.tag.lower().endswith("feed"):  # Atom / YouTube
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
    else:  # RSS 2.0
        for item in root.iterfind("./channel/item"):
            link = item.findtext("link")
            ident = item.findtext("guid") or link
            if not ident:
                continue
            thumb = None
            enc = item.find("enclosure")
            if enc is not None and (enc.get("type") or "").startswith("image"):
                thumb = enc.get("url")
            mt = item.find("{http://search.yahoo.com/mrss/}thumbnail")
            if mt is not None:
                thumb = mt.get("url")
            out.append({
                "id": ident,
                "title": item.findtext("title") or "Yeni içerik",
                "link": link,
                "thumb": thumb,
                "author": item.findtext("author") or root.findtext("./channel/title"),
                "published": _parse_dt(item.findtext("pubDate")),
            })
    return out


class YouTubeCog(commands.Cog, name="YouTube"):
    def __init__(self, bot: "AllInOneBot") -> None:
        self.bot = bot
        self.session: Optional[aiohttp.ClientSession] = None

    async def cog_load(self) -> None:
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=20),
            headers={"User-Agent": "Mozilla/5.0 (compatible; DiscordAllInOneBot/1.0)"})
        self.monitor.start()

    async def cog_unload(self) -> None:
        self.monitor.cancel()
        if self.session:
            await self.session.close()

    async def _fetch(self, url: str, cache: Dict[str, Optional[List[Dict[str, Any]]]]) -> Optional[List[Dict[str, Any]]]:
        if url in cache:
            return cache[url]
        entries: Optional[List[Dict[str, Any]]] = None
        try:
            async with self.session.get(url) as resp:
                if resp.status != 200:
                    log.warning("RSS isteği başarısız (%s): %s", resp.status, url)
                else:
                    raw = await resp.content.read(3_000_000)
                    entries = parse_feed(raw.decode("utf-8", errors="replace"))
        except asyncio.TimeoutError:
            log.warning("RSS zaman aşımı: %s", url)
        except aiohttp.ClientError as exc:
            log.warning("RSS bağlantı hatası: %s", exc)
        except ET.ParseError:
            log.warning("RSS XML çözümlenemedi: %s", url)
        cache[url] = entries
        return entries

    async def _check_guild(self, guild: discord.Guild, cache: Dict[str, Optional[List[Dict[str, Any]]]]) -> int:
        g = self.bot.store.guild(guild.id)["youtube"]
        source = g.get("source") or DEFAULT_YT_SOURCE
        if not source:
            return 0
        url = feed_url_from_source(source)
        if not url:
            return 0
        entries = await self._fetch(url, cache)
        if entries is None:
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
        new.reverse()            # oldest first
        new = new[-3:]           # never flood the channel
        channel = discord.utils.get(guild.text_channels, name=CH_VIDEO)
        sent = 0
        for entry in new:
            g["seen"].append(entry["id"])
            if channel is None:
                continue
            embed = discord.Embed(
                title=entry["title"], url=entry["link"], color=discord.Color.red(),
                description="Hemen izle, beğenmeyi ve yorum yapmayı unutma! 🔔",
                timestamp=entry["published"] or discord.utils.utcnow())
            if entry["author"]:
                embed.set_author(name=entry["author"])
            if entry["thumb"]:
                embed.set_image(url=entry["thumb"])
            embed.set_footer(text="Yeni video")
            try:
                await channel.send(content="📢 **Al kardeşim yeni video!** 🎬", embed=embed)
                sent += 1
            except discord.HTTPException as exc:
                log.warning("Video duyurusu gönderilemedi: %s", exc)
        g["seen"] = g["seen"][-200:]
        self.bot.store.save_soon()
        return sent

    @tasks.loop(minutes=5)
    async def monitor(self) -> None:
        cache: Dict[str, Optional[List[Dict[str, Any]]]] = {}
        for guild in list(self.bot.guilds):
            try:
                await self._check_guild(guild, cache)
            except Exception:
                log.exception("YouTube kontrol hatası (%s)", guild.name)

    @monitor.before_loop
    async def _before_monitor(self) -> None:
        await self.bot.wait_until_ready()

    @monitor.error
    async def _monitor_error(self, exc: BaseException) -> None:
        log.error("YouTube döngüsü hata verdi: %s", exc)

    @app_commands.command(name="youtube-ayarla", description="İzlenecek YouTube kanalını / RSS adresini ayarlar.")
    @app_commands.guild_only()
    @require_staff(2)
    @app_commands.describe(source="YouTube Kanal ID (UC…), kanal linki veya RSS/Atom adresi")
    async def yt_set(self, interaction: discord.Interaction, source: str) -> None:
        url = feed_url_from_source(source)
        if url is None:
            await respond(interaction, "❌ Geçersiz kaynak. `UC…` ile başlayan Kanal ID'si veya RSS URL'si ver.",
                          ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        g = self.bot.store.guild(interaction.guild_id)["youtube"]
        g.update({"source": source.strip(), "seen": [], "initialized": False})
        self.bot.store.save_soon()
        cache: Dict[str, Optional[List[Dict[str, Any]]]] = {}
        await self._check_guild(interaction.guild, cache)
        ok = cache.get(url) is not None
        await interaction.followup.send(
            f"✅ Kaynak ayarlandı: `{source.strip()}`\n"
            + ("Mevcut videolar işaretlendi; bundan sonra yüklenenler **#video-duyuru** kanalında duyurulacak."
               if ok else "⚠️ Feed şu an okunamadı; 5 dakikada bir tekrar denenecek."), ephemeral=True)

    @app_commands.command(name="youtube-kontrol", description="Yeni video var mı hemen kontrol eder.")
    @app_commands.guild_only()
    @require_staff(2)
    async def yt_check(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        sent = await self._check_guild(interaction.guild, {})
        await interaction.followup.send(f"🔎 Kontrol tamamlandı, **{sent}** yeni video duyuruldu.", ephemeral=True)


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
        if role is not None:
            try:
                await member.add_roles(role, reason="Otomatik rol")
            except discord.HTTPException:
                log.warning("[%s] %s kullanıcısına otomatik rol verilemedi.", guild.name, member)

        channel = discord.utils.get(guild.text_channels, name=CH_WELCOME)
        if channel is None:
            return
        e = discord.Embed(
            title="🎉 Aramıza Hoş Geldin!",
            description=f"{member.mention}, **{guild.name}** sunucusuna katıldı!\n"
                        f"Kuralları oku ve keyfine bak. 💜",
            color=C_OK, timestamp=discord.utils.utcnow())
        e.set_thumbnail(url=member.display_avatar.url)
        e.add_field(name="👥 Toplam üye", value=f"**{guild.member_count}**")
        e.add_field(name="📅 Hesap oluşturma", value=discord.utils.format_dt(member.created_at, "R"))
        e.set_footer(text=f"ID: {member.id}")
        try:
            await channel.send(content=member.mention, embed=e)
        except discord.HTTPException as exc:
            log.warning("Karşılama mesajı gönderilemedi: %s", exc)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        guild = member.guild
        channel = discord.utils.get(guild.text_channels, name=CH_WELCOME)
        if channel is None:
            return
        e = discord.Embed(
            title="👋 Bir üye ayrıldı",
            description=f"**{member}** sunucudan ayrıldı.",
            color=C_ERR, timestamp=discord.utils.utcnow())
        e.set_thumbnail(url=member.display_avatar.url)
        e.add_field(name="👥 Kalan üye", value=f"**{guild.member_count}**")
        if member.joined_at:
            e.add_field(name="📥 Katılma", value=discord.utils.format_dt(member.joined_at, "R"))
        roles = [r.mention for r in member.roles if r != guild.default_role]
        if roles:
            e.add_field(name="🎭 Roller", value=trunc(" ".join(roles), 1000), inline=False)
        e.set_footer(text=f"ID: {member.id}")
        try:
            await channel.send(embed=e)
        except discord.HTTPException as exc:
            log.warning("Ayrılma kaydı gönderilemedi: %s", exc)


# ════════════════════════════════════════════════════════════════════════════
#  ANNOUNCEMENT COG
# ════════════════════════════════════════════════════════════════════════════
class AnnounceModal(discord.ui.Modal):
    def __init__(self, channel: discord.TextChannel, embed_mode: bool, ping: str) -> None:
        super().__init__(title="Resmî Duyuru", timeout=600)
        self.channel = channel
        self.embed_mode = embed_mode
        self.ping = ping
        self.title_in: Optional[discord.ui.TextInput] = None
        self.color_in: Optional[discord.ui.TextInput] = None
        self.image_in: Optional[discord.ui.TextInput] = None
        if embed_mode:
            self.title_in = discord.ui.TextInput(label="Başlık", max_length=256, required=True)
            self.add_item(self.title_in)
        self.body_in = discord.ui.TextInput(label="Mesaj", style=discord.TextStyle.paragraph,
                                            max_length=3800 if embed_mode else 1900, required=True)
        self.add_item(self.body_in)
        if embed_mode:
            self.color_in = discord.ui.TextInput(label="Renk (hex, örn. #5865F2)", max_length=7, required=False)
            self.image_in = discord.ui.TextInput(label="Görsel URL (isteğe bağlı)", max_length=500, required=False)
            self.add_item(self.color_in)
            self.add_item(self.image_in)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        content = {"everyone": "@everyone", "here": "@here"}.get(self.ping)
        mentions = discord.AllowedMentions(everyone=bool(content), roles=False, users=False)
        try:
            if self.embed_mode:
                color = C_INFO
                raw = (self.color_in.value or "").strip().lstrip("#") if self.color_in else ""
                if raw:
                    try:
                        color = discord.Color(int(raw, 16))
                    except ValueError:
                        pass
                embed = discord.Embed(title=self.title_in.value, description=self.body_in.value, color=color,
                                      timestamp=discord.utils.utcnow())
                img = (self.image_in.value or "").strip() if self.image_in else ""
                if img.lower().startswith(("http://", "https://")):
                    embed.set_image(url=img)
                if interaction.guild.icon:
                    embed.set_thumbnail(url=interaction.guild.icon.url)
                embed.set_footer(text=f"{interaction.guild.name} • Resmî Duyuru")
                await self.channel.send(content=content, embed=embed, allowed_mentions=mentions)
            else:
                text = self.body_in.value if not content else f"{content}\n{self.body_in.value}"
                await self.channel.send(text, allowed_mentions=mentions)
        except discord.Forbidden:
            await interaction.response.send_message(
                f"❌ {self.channel.mention} kanalına yazma yetkim yok (veya etiket yetkisi eksik).", ephemeral=True)
            return
        except discord.HTTPException as exc:
            await interaction.response.send_message(f"❌ Gönderilemedi: {short_err(exc)}", ephemeral=True)
            return
        await interaction.response.send_message(f"✅ Duyuru {self.channel.mention} kanalına gönderildi.",
                                                ephemeral=True)
        e = discord.Embed(title="📢 Duyuru gönderildi", color=C_INFO, timestamp=discord.utils.utcnow())
        e.add_field(name="Yetkili", value=interaction.user.mention)
        e.add_field(name="Kanal", value=self.channel.mention)
        await send_log(interaction.guild, e)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        log.exception("Duyuru modalı hatası", exc_info=error)
        await respond(interaction, "❌ Duyuru gönderilirken beklenmeyen bir hata oluştu.", ephemeral=True)


class AnnounceCog(commands.Cog, name="Duyuru"):
    def __init__(self, bot: "AllInOneBot") -> None:
        self.bot = bot

    @app_commands.command(name="announce", description="Botun kimliğiyle resmî duyuru gönderir (Yönetici+).")
    @app_commands.guild_only()
    @require_staff(2)
    @app_commands.describe(channel="Duyurunun gönderileceği kanal", mode="Gömülü (embed) veya düz metin",
                           ping="Etiketleme")
    @app_commands.choices(
        mode=[app_commands.Choice(name="Embed (gömülü)", value="embed"),
              app_commands.Choice(name="Düz metin", value="plain")],
        ping=[app_commands.Choice(name="Etiket yok", value="none"),
              app_commands.Choice(name="@everyone", value="everyone"),
              app_commands.Choice(name="@here", value="here")])
    async def announce(self, interaction: discord.Interaction, channel: discord.TextChannel,
                       mode: Optional[app_commands.Choice[str]] = None,
                       ping: Optional[app_commands.Choice[str]] = None) -> None:
        perms = channel.permissions_for(interaction.guild.me)
        if not (perms.view_channel and perms.send_messages):
            await respond(interaction, f"❌ {channel.mention} kanalına yazma yetkim yok.", ephemeral=True)
            return
        embed_mode = (mode.value if mode else "embed") == "embed"
        if embed_mode and not perms.embed_links:
            await respond(interaction, f"❌ {channel.mention} kanalında **Bağlantı Yerleştir** yetkim yok.",
                          ephemeral=True)
            return
        await interaction.response.send_modal(AnnounceModal(channel, embed_mode, ping.value if ping else "none"))


# ════════════════════════════════════════════════════════════════════════════
#  BOT CLASS + GLOBAL ERROR HANDLING
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

    async def setup_hook(self) -> None:
        # --- RENDER / BULUT ICIN HTTP PING SUNUCUSU ---
        async def handle_ping(request: web.Request) -> web.Response:
            return web.Response(text="Bot aktif ve calisiyor!")

        app = web.Application()
        app.router.add_get("/", handle_ping)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", PORT)
        await site.start()
        log.info("Render HTTP sunucusu port %d uzerinde acildi.", PORT)
        # ----------------------------------------------

        for cog_cls in (AdminCog, ChannelGuardCog, AntiSpamCog, GamesCog, MusicCog, VoiceRoomsCog,
                        YouTubeCog, WelcomeCog, AnnounceCog):
            try:
                await self.add_cog(cog_cls(self))
                log.info("Cog yüklendi: %s", cog_cls.__name__)
            except Exception:
                log.exception("Cog yüklenemedi: %s", cog_cls.__name__)
        self.tree.on_error = on_app_command_error
        try:
            if DEV_GUILD_ID:
                guild = discord.Object(id=DEV_GUILD_ID)
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
            else:
                synced = await self.tree.sync()
            log.info("%d slash komutu senkronize edildi.", len(synced))
        except discord.HTTPException:
            log.exception("Slash komutları senkronize edilemedi.")

    async def on_ready(self) -> None:
        log.info("Giriş yapıldı: %s (ID: %s) • %d sunucu", self.user, self.user.id, len(self.guilds))

    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        if isinstance(error, commands.CommandNotFound):
            return
        if isinstance(error, commands.CommandInvokeError):
            error = error.original  # type: ignore[assignment]
        if isinstance(error, commands.MissingPermissions):
            msg = "⛔ Bu komut için yetkin yok."
        elif isinstance(error, commands.BotMissingPermissions):
            msg = "⚠️ Botun şu yetkileri eksik: " + ", ".join(error.missing_permissions)
        elif isinstance(error, commands.NoPrivateMessage):
            msg = "Bu komut sadece sunucularda kullanılabilir."
        elif isinstance(error, commands.CommandOnCooldown):
            msg = f"⏳ Lütfen {error.retry_after:.0f} sn bekle."
        elif isinstance(error, (commands.CheckFailure, commands.BadArgument, commands.MissingRequiredArgument)):
            msg = f"⚠️ {error}"
        elif isinstance(error, discord.Forbidden):
            msg = "⚠️ Discord bu işlemi reddetti (botun yetkisi/rol sırası yetersiz)."
        else:
            log.error("Komut hatası (%s)", ctx.command, exc_info=error)
            msg = "❌ Beklenmeyen bir hata oluştu."
        try:
            await ctx.send(msg, delete_after=10)
        except discord.HTTPException:
            pass

    async def close(self) -> None:
        self.store.flush()
        await super().close()


async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
    original = error.original if isinstance(error, app_commands.CommandInvokeError) else error
    if isinstance(original, NotAuthorized):
        msg = f"⛔ {original}"
    elif isinstance(original, app_commands.MissingPermissions):
        msg = "⛔ Bu komut için yetkin yok."
    elif isinstance(original, app_commands.BotMissingPermissions):
        msg = "⚠️ Botun şu yetkileri eksik: " + ", ".join(original.missing_permissions)
    elif isinstance(original, app_commands.NoPrivateMessage):
        msg = "Bu komut sadece sunucularda kullanılabilir."
    elif isinstance(original, app_commands.CommandOnCooldown):
        msg = f"⏳ Lütfen {original.retry_after:.0f} sn bekle."
    elif isinstance(original, app_commands.CheckFailure):
        msg = "⛔ Bu komutu kullanamazsın."
    elif isinstance(original, discord.Forbidden):
        msg = "⚠️ Discord bu işlemi reddetti (botun yetkisi veya rol sırası yetersiz)."
    elif isinstance(original, discord.NotFound):
        msg = "⚠️ İstenen öğe bulunamadı (süre dolmuş olabilir)."
    else:
        cmd = interaction.command.qualified_name if interaction.command else "?"
        log.error("Slash komut hatası (/%s)", cmd, exc_info=original)
        msg = "❌ Beklenmeyen bir hata oluştu. Lütfen tekrar dene."
    await respond(interaction, msg, ephemeral=True)


def main() -> None:
    setup_logging()
    if not TOKEN:
        log.critical("DISCORD_TOKEN bulunamadı. .env dosyasına DISCORD_TOKEN=... ekle.")
        sys.exit(1)
    bot = AllInOneBot()
    try:
        bot.run(TOKEN, log_handler=None)
    except discord.LoginFailure:
        log.critical("Geçersiz bot token'ı.")
        sys.exit(1)
    except discord.PrivilegedIntentsRequired:
        log.critical("Developer Portal'dan 'Server Members' ve 'Message Content' intent'lerini aç.")
        sys.exit(1)


if __name__ == "__main__":
    main()
