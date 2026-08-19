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

# --- Render Environment Variable'dan cookies.txt oluşturma ---
YOUTUBE_COOKIES_ENV = os.getenv("YOUTUBE_COOKIES")
if YOUTUBE_COOKIES_ENV:
    with open("cookies.txt", "w", encoding="utf-8") as f:
        f.write(YOUTUBE_COOKIES_ENV)
    print("✅ cookies.txt çevre değişkeninden başarıyla yüklendi!")

# FFmpeg Otomatik Yükleyici Entegrasyonu
try:
    import static_ffmpeg
    static_ffmpeg.add_paths()
except Exception as e:
    print(f"⚠️ FFmpeg uyarısı: {e}")

# --- PO Token Provider Kurulumu (bgutil-ytdlp-pot-provider) ---
# build.sh betiği proje kök dizininde bgutil-ytdlp-pot-provider klasörünü hazırlıyor.
BGUTIL_SERVER_HOME = os.path.join(os.getcwd(), "bgutil-ytdlp-pot-provider", "server")
if os.path.exists(os.path.join(BGUTIL_SERVER_HOME, "build", "main.js")):
    print(f"✅ PO Token provider (bgutil) bulundu: {BGUTIL_SERVER_HOME}")
else:
    print(f"⚠️ PO Token provider (bgutil) bulunamadı ({BGUTIL_SERVER_HOME}), yt-dlp PO token olmadan devam edecek.")

# --- 1. WEB SUNUCUSU (7/24 Uptime) ---
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

# --- 2. VERİTABANI İŞLEMLERİ ---
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

# --- 3. TÜRKÇE KELİME LİSTESİ ---
TURKISH_WORDS = set()

async def fetch_turkish_words():
    global TURKISH_WORDS
    url = "https://raw.githubusercontent.com/mertcuruk/turkce-kelimeler/master/kelimeler.txt"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    text = await response.text()
                    TURKISH_WORDS = set(w.strip().lower() for w in text.splitlines() if w.strip())
                    print(f"✅ {len(TURKISH_WORDS)} adet Türkçe kelime yüklendi!")
                else:
                    print(f"⚠️ Kelime listesi çekilemedi, kod: {response.status}")
    except Exception as e:
        print(f"⚠️ Kelime listesi hatası: {e}")

TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True
intents.voice_states = True

# Dinamik Geçici Ses Kanalları Takibi
temp_channels = []

# Destek (Ticket) Arayüzleri
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
        
        close_view = TicketCloseView()
        await ticket_channel.send(
            f"Merhaba {member.mention}, yetkililer kısa süre içinde burada olacaktır.\nTalebi kapatmak için aşağıdaki butona basabilirsiniz.",
            view=close_view
        )
        await interaction.response.send_message(f"Destek kanalınız oluşturuldu: {ticket_channel.mention}", ephemeral=True)

class TicketCloseView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🔒 Talebi Kapat", style=discord.ButtonStyle.danger, custom_id="close_ticket_btn")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Kanal 5 saniye içinde siliniyor...")
        await asyncio.sleep(5)
        await interaction.channel.delete()

class SetupBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        self.add_view(TicketView())
        self.add_view(TicketCloseView())
        await self.tree.sync()
        youtube_loop.start()
        stats_loop.start()

bot = SetupBot()

# --- 4. MÜZİK AYARLARI VE KOMUTLARI (Cookies Entegreli) ---
YTDL_OPTIONS = {
    'format': 'best',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'ytsearch',
    'source_address': '0.0.0.0',
    'cookiefile': 'cookies.txt' if os.path.exists("cookies.txt") else None,
    'extractor_args': {
        'youtube': {
            'player_client': ['web', 'android'],
        },
        'youtubepot-bgutilscript': {
            'server_home': [BGUTIL_SERVER_HOME],
        },
    }
}

FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -probesize 20000000 -analyzeduration 0',
    'options': '-vn -b:a 192k -loglevel panic'
}

ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)

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
            voice_client = await voice_channel.connect(self_deaf=True)
        elif voice_client.channel != voice_channel:
            await voice_client.move_to(voice_channel)
    except Exception as e:
        await interaction.followup.send(f"❌ Ses kanalına bağlanırken hata oluştu: `{e}`")
        return

    query = sarkici_veya_url

    try:
        loop = asyncio.get_event_loop()
        search_query = query if query.startswith("http") else f"ytsearch:{query}"

        try:
            data = await loop.run_in_executor(None, lambda: ytdl.extract_info(search_query, download=False))
        except yt_dlp.utils.DownloadError as e:
            print(f"⚠️ İlk deneme başarısız: {e}")
            if "Requested format is not available" in str(e):
                fallback_opts = dict(YTDL_OPTIONS)
                fallback_opts['format'] = 'best'
                fallback_ytdl = yt_dlp.YoutubeDL(fallback_opts)
                data = await loop.run_in_executor(None, lambda: fallback_ytdl.extract_info(search_query, download=False))
            else:
                raise

        if 'entries' in data and len(data['entries']) > 0:
            video_data = data['entries'][0]
        else:
            video_data = data

        print(f"🔎 video_data anahtarları: {list(video_data.keys())}")
        print(f"🔎 url alanı: {video_data.get('url')}")
        print(f"🔎 http_headers: {video_data.get('http_headers')}")
        print(f"🔎 formats sayısı: {len(video_data.get('formats', []))}")

        audio_url = video_data['url']
        title = video_data.get('title', 'Bilinmeyen Şarkı')
        http_headers = video_data.get('http_headers', {}) or {}

        if voice_client.is_playing():
            voice_client.stop()

        headers_str = "".join(f"{k}: {v}\r\n" for k, v in http_headers.items())
        ffmpeg_before_options = FFMPEG_OPTIONS['before_options']
        if headers_str:
            ffmpeg_before_options = f'-headers "{headers_str}" ' + ffmpeg_before_options

        try:
            source = discord.FFmpegOpusAudio(
                audio_url,
                before_options=ffmpeg_before_options,
                options=FFMPEG_OPTIONS['options']
            )
        except Exception as probe_err:
            print(f"⚠️ FFmpegOpusAudio oluşturma hatası: {probe_err}")
            raise

        voice_client.play(source)

        await interaction.followup.send(f"🎵 **Şu an çalıyor:** `{title}`")

    except Exception as e:
        print(f"⚠️ Genel oynatma hatası: {e}")
        await interaction.followup.send(f"❌ Şarkı oynatılırken hata oluştu: `{e}`")

@bot.tree.command(name="dur", description="Çalan müziği durdurur ve kanaldan ayrılır.")
async def dur(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client if interaction.guild else None
    if voice_client and voice_client.is_connected():
        await voice_client.disconnect()
        await interaction.response.send_message("⏹️ Müzik durduruldu, ses kanalından ayrıldım.")
    else:
        await interaction.response.send_message("❌ Zaten bir ses kanalında değilim.")

# --- 5. TICKET KURULUM KOMUTU ---
@bot.tree.command(name="ticket-kur", description="Destek talebi panelini bulunduğunuz kanala kurar.")
@app_commands.checks.has_permissions(administrator=True)
async def ticket_kur(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🎫 Destek Sistemi",
        description="Bir sorununuz veya sorunuz varsa aşağıdaki **Destek Talebi Aç** butonuna basarak yetkililerle iletişime geçebilirsiniz.",
        color=discord.Color.blue()
    )
    await interaction.channel.send(embed=embed, view=TicketView())
    await interaction.response.send_message("✅ Destek paneli başarıyla oluşturuldu!", ephemeral=True)

# --- 6. KURULUM / AYAR KOMUTLARI ---
@bot.tree.command(name="kurulum-hosgeldin", description="Hoş geldin ve görüşürüz kanalını ayarlar.")
@app_commands.checks.has_permissions(administrator=True)
async def kurulum_hosgeldin(interaction: discord.Interaction, kanal: discord.TextChannel):
    SERVER_DATA["welcome_channel_id"] = kanal.id
    save_db(SERVER_DATA)
    await interaction.response.send_message(f"✅ Hoş geldin kanalı {kanal.mention} olarak ayarlandı.")

@bot.tree.command(name="kurulum-istatistik", description="Sunucu üye sayısının yazılacağı ses kanalını ayarlar.")
@app_commands.checks.has_permissions(administrator=True)
async def kurulum_istatistik(interaction: discord.Interaction, kanal: discord.VoiceChannel):
    SERVER_DATA["stats_channel_id"] = kanal.id
    save_db(SERVER_DATA)
    await interaction.response.send_message(f"✅ İstatistik kanalı {kanal.mention} olarak ayarlandı.")

@bot.tree.command(name="kurulum-gecici-ses", description="Geçici ses odası oluşturma kanalını ve kategorisini ayarlar.")
@app_commands.checks.has_permissions(administrator=True)
async def kurulum_gecici_ses(interaction: discord.Interaction, giris_kanali: discord.VoiceChannel, kategori: discord.CategoryChannel):
    SERVER_DATA["join_to_create_id"] = giris_kanali.id
    SERVER_DATA["temp_category_id"] = kategori.id
    save_db(SERVER_DATA)
    await interaction.response.send_message(f"✅ Geçici ses sistemi ayarlandı! Giriş Kanalı: {giris_kanali.mention}, Hedef Kategori: **{kategori.name}**")

@bot.tree.command(name="kurulum-youtube", description="YouTube bildirim kanalını ve RSS URL adresini ayarlar.")
@app_commands.checks.has_permissions(administrator=True)
async def kurulum_youtube(interaction: discord.Interaction, kanal: discord.TextChannel, rss_url: str):
    SERVER_DATA["yt_channel_id"] = kanal.id
    SERVER_DATA["yt_rss_url"] = rss_url
    save_db(SERVER_DATA)
    await interaction.response.send_message(f"✅ YouTube bildirim kanalı {kanal.mention} olarak ayarlandı!")

# --- 7. ARKA PLAN GÖREVLERİ (LOOPS) ---
@tasks.loop(minutes=5)
async def youtube_loop():
    rss_url = SERVER_DATA.get("yt_rss_url")
    channel_id = SERVER_DATA.get("yt_channel_id")

    if not rss_url or not channel_id:
        return

    try:
        feed = feedparser.parse(rss_url)
        if feed.entries:
            latest_entry = feed.entries[0]
            video_id = latest_entry.yt_videoid if hasattr(latest_entry, 'yt_videoid') else latest_entry.link
            video_url = latest_entry.link
            video_title = latest_entry.title

            if SERVER_DATA.get("last_video_id") != video_id:
                SERVER_DATA["last_video_id"] = video_id
                save_db(SERVER_DATA)

                target_channel = bot.get_channel(channel_id)
                if target_channel:
                    await target_channel.send(f"📢 **Yeni YouTube Videosu Yayında!**\n\n🎥 **{video_title}**\n🔗 {video_url}")
    except Exception as e:
        print(f"⚠️ YouTube kontrol hatası: {e}")

@tasks.loop(minutes=10)
async def stats_loop():
    stats_channel_id = SERVER_DATA.get("stats_channel_id")
    if not stats_channel_id:
        return

    channel = bot.get_channel(stats_channel_id)
    if channel and isinstance(channel, discord.VoiceChannel):
        total_members = channel.guild.member_count
        try:
            await channel.edit(name=f"👥 Üyeler: {total_members}")
        except Exception as e:
            print(f"⚠️ İstatistik güncellenemedi: {e}")

# --- 8. GEÇİCİ SES KANALI VE ÜYE EVENT'LERİ ---
@bot.event
async def on_voice_state_update(member, before, after):
    join_to_create_id = SERVER_DATA.get("join_to_create_id")
    temp_category_id = SERVER_DATA.get("temp_category_id")

    # Geçici Odaya Katılınca Yeni Oda Açma
    if after.channel and after.channel.id == join_to_create_id and temp_category_id:
        guild = member.guild
        category = guild.get_channel(temp_category_id)
        if category:
            temp_channel = await guild.create_voice_channel(
                name=f"🔊 {member.display_name}'in Odası",
                category=category
            )
            temp_channels.append(temp_channel.id)
            await member.move_to(temp_channel)

    # Boşalan Geçici Odayı Otomatik Silme
    if before.channel and before.channel.id in temp_channels:
        if len(before.channel.members) == 0:
            temp_channels.remove(before.channel.id)
            try:
                await before.channel.delete()
            except Exception:
                pass

@bot.event
async def on_member_join(member):
    welcome_channel_id = SERVER_DATA.get("welcome_channel_id")
    if welcome_channel_id:
        channel = bot.get_channel(welcome_channel_id)
        if channel:
            await channel.send(f"🎉 Hoş geldin {member.mention}! Sunucumuza katıldığın için mutluyuz.")

@bot.event
async def on_member_remove(member):
    welcome_channel_id = SERVER_DATA.get("welcome_channel_id")
    if welcome_channel_id:
        channel = bot.get_channel(welcome_channel_id)
        if channel:
            await channel.send(f"👋 **{member.display_name}** aramızdan ayrıldı.")

# --- 9. MESAJ EVENT'LERİ VE OYUNLAR ---
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
