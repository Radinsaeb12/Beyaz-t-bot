import discord
from discord.ext import commands, tasks
from discord import app_commands
import asyncio
import yt_dlp
import feedparser
import json
import os
import aiohttp
from flask import Flask
from threading import Thread

# --- 1. 7/24 UYKUYU ENGELLEYEN WEB SUNUCUSU ---
app = Flask('')

@app.route('/')
def home():
    return "Bot 7/24 Aktif!"

def run_flask():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()

# --- 2. TÜRKÇE KELİME LİSTESİ YÜKLEME ---
TURKISH_WORDS = set()

async def fetch_turkish_words():
    global TURKISH_WORDS
    url = "https://raw.githubusercontent.com/kelimeler/turkce-kelimeler/master/turkce-kelimeler.txt"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    text = await response.text()
                    TURKISH_WORDS = set(w.strip().lower() for w in text.splitlines() if w.strip())
                    print(f"✅ {len(TURKISH_WORDS)} adet Türkçe kelime yüklendi!")
                else:
                    print("⚠️ Kelime listesi indirilemedi, durum kodu:", response.status)
    except Exception as e:
        print(f"⚠️ Kelime listesi çekilirken hata oluştu: {e}")

# --- 3. VERİTABANI İŞLEMLERİ ---
DB_FILE = "db.json"

def load_db():
    if not os.path.exists(DB_FILE):
        default_data = {
            "welcome_channel_id": None,
            "stats_channel_id": None,
            "join_to_create_id": None,
            "temp_category_id": None,
            "yt_channel_id": None,
            "yt_rss_url": None,
            "last_video_id": None
        }
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(default_data, f, ensure_ascii=False, indent=4)
        return default_data
    with open(DB_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_db(data):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

SERVER_DATA = load_db()

TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True
intents.voice_states = True

class SetupBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        self.add_view(TicketView())
        await self.tree.sync()

bot = SetupBot()

# Müzik Ayarları
YTDL_OPTIONS = {
    'format': 'bestaudio/best',
    'extractaudio': True,
    'audioformat': 'mp3',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'ytsearch',
    'source_address': '0.0.0.0',
    'extractor_args': {'youtube': {'player_client': ['android', 'web']}}
}

FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -probesize 20000000 -analyzeduration 0',
    'options': '-vn -b:a 192k -loglevel panic'
}

ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)

# Ticket View
class TicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="📩 Destek Talebi Aç", style=discord.ButtonStyle.primary, custom_id="create_ticket_btn")
    async def create_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        member = interaction.user
        
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            member: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }
        
        channel_name = f"ticket-{member.name.lower()}"
        existing_channel = discord.utils.get(guild.text_channels, name=channel_name)
        
        if existing_channel:
            await interaction.response.send_message(f"Zaten açık bir destek talebiniz var: {existing_channel.mention}", ephemeral=True)
            return

        ticket_channel = await guild.create_text_channel(channel_name, overwrites=overwrites)
        await ticket_channel.send(f"Merhaba {member.mention}, yetkililer kısa süre içinde burada olacaktır.")
        await interaction.response.send_message(f"Destek kanalınız oluşturuldu: {ticket_channel.mention}", ephemeral=True)

# Müzik Komutları
@bot.tree.command(name="oynat", description="Şarkı adı veya YouTube URL'si ile müzik çalar.")
async def oynat(interaction: discord.Interaction, sarkici_veya_url: str):
    await interaction.response.defer(thinking=True)

    if not isinstance(interaction.user, discord.Member) or not interaction.user.voice:
        await interaction.followup.send("❌ Müzik çalabilmem için önce bir **ses kanalına** girmelisin!")
        return

    voice_channel = interaction.user.voice.channel
    voice_client = interaction.guild.voice_client if interaction.guild else None

    try:
        if not voice_client:
            voice_client = await voice_channel.connect()
        elif voice_client.channel != voice_channel:
            await voice_client.move_to(voice_channel)
    except Exception as e:
        await interaction.followup.send(f"❌ Ses kanalına bağlanırken hata oluştu: `{e}`\n*(PyNaCl kütüphanesinin yüklü olduğundan emin olun)*")
        return

    query = sarkici_veya_url

    try:
        loop = asyncio.get_event_loop()
        search_query = query if query.startswith("http") else f"ytsearch:{query}"

        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(search_query, download=False))
        
        if 'entries' in data and len(data['entries']) > 0:
            video_data = data['entries'][0]
        else:
            video_data = data

        audio_url = video_data['url']
        title = video_data.get('title', 'Bilinmeyen Şarkı')

        if voice_client.is_playing():
            voice_client.stop()

        source = await discord.FFmpegOpusAudio.from_probe(audio_url, **FFMPEG_OPTIONS)
        voice_client.play(source)

        await interaction.followup.send(f"🎵 **Şu an çalıyor:** `{title}`")

    except Exception as e:
        await interaction.followup.send(f"❌ Şarkı oynatılırken hata oluştu: {e}")

@bot.tree.command(name="dur", description="Çalan müziği durdurur ve kanaldan ayrılır.")
async def dur(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client if interaction.guild else None
    if voice_client and voice_client.is_connected():
        await voice_client.disconnect()
        await interaction.response.send_message("⏹️ Müzik durduruldu, ses kanalından ayrıldım.")
    else:
        await interaction.response.send_message("❌ Zaten bir ses kanalında değilim.")

# Oyun Değişkenleri
counting_number = 0
last_counter_user = None

last_word_letter = ""
last_word_user = None
used_words = set()

AUTO_RESPONSES = {
    "sa": "Aleyküm selam, hoş geldin! 👋",
    "sea": "Aleyküm selam!",
    "selam": "Selam! Naber?"
}

@bot.event
async def on_message(message):
    global counting_number, last_counter_user, last_word_letter, last_word_user, used_words

    if message.author.bot:
        return

    content_lower = message.content.lower().strip()

    if content_lower in AUTO_RESPONSES:
        await message.channel.send(AUTO_RESPONSES[content_lower])
        return

    # Sayı Sayma Oyunu
    if getattr(message.channel, 'name', None) == "sayi-sayma":
        if message.content.isdigit():
            val = int(message.content)
            expected = counting_number + 1
            if val == expected and message.author != last_counter_user:
                counting_number += 1
                last_counter_user = message.author
                await message.add_reaction("✅")
            else:
                try:
                    await message.delete()
                except Exception:
                    pass
                msg = await message.channel.send(f"⚠️ {message.author.mention}, yanlış sayı veya üst üste yazdın! Sıradaki sayı: **{expected}**")
                await asyncio.sleep(4)
                await msg.delete()
        else:
            try:
                await message.delete()
            except Exception:
                pass

    # Kelime Türetme Oyunu
    elif getattr(message.channel, 'name', None) == "kelime-turetme":
        word = content_lower

        if message.author == last_word_user:
            try:
                await message.delete()
            except Exception:
                pass
            msg = await message.channel.send(f"⚠️ {message.author.mention}, üst üste kelime yazamazsın!")
            await asyncio.sleep(4)
            await msg.delete()
            return

        # Sözlük Kontrolü (Sözlük yüklendiyse kontrol et)
        if TURKISH_WORDS and word not in TURKISH_WORDS:
            try:
                await message.delete()
            except Exception:
                pass
            msg = await message.channel.send(f"❌ {message.author.mention}, **'{word}'** geçerli bir Türkçe kelime değil!")
            await asyncio.sleep(4)
            await msg.delete()
            return

        if word in used_words:
            try:
                await message.delete()
            except Exception:
                pass
            msg = await message.channel.send(f"⚠️ {message.author.mention}, **'{word}'** daha önce kullanıldı!")
            await asyncio.sleep(4)
            await msg.delete()
            return

        if last_word_letter == "" or word.startswith(last_word_letter):
            last_word_letter = word[-1]
            last_word_user = message.author
            used_words.add(word)
            await message.add_reaction("✅")
        else:
            try:
                await message.delete()
            except Exception:
                pass
            msg = await message.channel.send(f"❌ {message.author.mention}, kelime **'{last_word_letter.upper()}'** harfi ile başlamalıydı.")
            await asyncio.sleep(4)
            await msg.delete()

    await bot.process_commands(message)

@bot.event
async def on_ready():
    print(f"[{bot.user.name}] Başarıyla başlatıldı!")
    await fetch_turkish_words()
    keep_alive()

if __name__ == "__main__":
    bot.run(TOKEN)
