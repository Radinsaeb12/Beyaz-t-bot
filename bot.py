import discord
from discord.ext import commands, tasks
from discord import app_commands
import asyncio
import yt_dlp
import feedparser
import json
import os
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

# --- 2. KALICI VERİTABANI (JSON) SİSTEMİ ---
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
        with open(DB_FILE, "w") as f:
            json.dump(default_data, f)
        return default_data
    with open(DB_FILE, "r") as f:
        return json.load(f)

def save_db(data):
    with open(DB_FILE, "w") as f:
        json.dump(data, f, indent=4)

SERVER_DATA = load_db()

# --- DISCORD AYARLARI ---
TOKEN = os.getenv("DISCORD_TOKEN")  # Token'ı çevre değişkeninden çeker

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True
intents.voice_states = True

class SetupBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        # Komutları doğrudan senin sunucuna anında kaydeder
        guild = discord.Object(id=1536741477508714541)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)

bot = SetupBot()

# Müzik Ayarları (Yüksek Ses Kalitesi & Optimize FFmpeg)
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

# --- TICKET (DESTEK) SİSTEMİ VIEW ---
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
        await ticket_channel.send(f"Merhaba {member.mention}, yetkililer kısa süre içinde burada olacaktır. Talebi kapatmak için `/ticket-kapat` komutunu kullanabilirsiniz.")
        await interaction.response.send_message(f"Destek kanalınız oluşturuldu: {ticket_channel.mention}", ephemeral=True)

# --- SUNUCU KURULUM KOMUTU ---
@bot.tree.command(name="sunucu-kur", description="HER ŞEYİ SİLER ve sunucuyu sıfırdan kurar.")
@app_commands.default_permissions(administrator=True)
async def sunucu_kur(interaction: discord.Interaction):
    guild = interaction.guild
    
    await interaction.response.defer(ephemeral=False)
    await interaction.followup.send("🧹 **Tüm kanallar ve roller temizleniyor, sıfırdan kurulum başlatılıyor...**")

    for channel in guild.channels:
        try:
            await channel.delete()
            await asyncio.sleep(0.1)
        except:
            pass

    for role in guild.roles:
        if role.name != "@everyone" and not role.managed:
            try:
                await role.delete()
                await asyncio.sleep(0.1)
            except:
                pass

    role_settings = [
        ("👑 | Kurucu", discord.Color.gold()),
        ("🎬 | YouTuber", discord.Color.red()),
        ("🛡️ | Yönetici", discord.Color.dark_red()),
        ("🛠️ | Moderatör", discord.Color.dark_gold()),
        ("🤖 | Botlar", discord.Color.dark_gray()),
        ("💎 | Nitro Booster", discord.Color.magenta()),
        ("⭐ | YouTube Katıl", discord.Color.dark_theme()),
        ("📺 | Twitch Abonesi", discord.Color.purple()),
        ("🌟 | VIP Üye", discord.Color.teal()),
        ("🎮 | Oyuncu", discord.Color.blue()),
    ]
    
    roles = {}
    for r_name, r_color in role_settings:
        try:
            role = await guild.create_role(name=r_name, color=r_color, hoist=True, mentionable=True)
            roles[r_name] = role
        except:
            pass

    everyone = guild.default_role

    read_only = {
        everyone: discord.PermissionOverwrite(read_messages=True, send_messages=False, connect=False),
        roles.get("🛠️ | Moderatör", everyone): discord.PermissionOverwrite(send_messages=True)
    }

    cat_admin = await guild.create_category("🛑 │ YÖNETİM ODALARI")
    await guild.create_text_channel("yönetim-sohbet", category=cat_admin)

    cat_info = await guild.create_category("📢 │ BİLGİLENDİRME")
    await guild.create_text_channel("📜│kurallar", category=cat_info, overwrites=read_only)
    await guild.create_text_channel("📢│duyurular", category=cat_info, overwrites=read_only)
    await guild.create_text_channel("🎬│video-duyuru", category=cat_info, overwrites=read_only)
    c_welcome = await guild.create_text_channel("👋│gelen-giden", category=cat_info, overwrites=read_only)
    SERVER_DATA["welcome_channel_id"] = c_welcome.id

    cat_stats = await guild.create_category("📊 │ SUNUCU PANELİ")
    c_stats = await guild.create_voice_channel(
        f"👥│Üye Sayısı: {guild.member_count}", 
        category=cat_stats, 
        overwrites={everyone: discord.PermissionOverwrite(connect=False)}
    )
    SERVER_DATA["stats_channel_id"] = c_stats.id

    cat_chat = await guild.create_category("💬 │ SOHBET & OYUN")
    await guild.create_text_channel("💬│genel-sohbet", category=cat_chat)
    await guild.create_text_channel("🤖│bot-komut", category=cat_chat)
    await guild.create_text_channel("sayi-sayma", category=cat_chat)
    await guild.create_text_channel("kelime-turetme", category=cat_chat)

    cat_voice = await guild.create_category("🔊 │ SES KANALLARI")
    await guild.create_voice_channel("Genel Sohbet 1", category=cat_voice)
    await guild.create_voice_channel("🎵 Müzik Odası", category=cat_voice)

    cat_temp = await guild.create_category("➕ │ ÖZEL ODALAR")
    c_create = await guild.create_voice_channel("➕│Oda Oluştur", category=cat_temp)
    SERVER_DATA["join_to_create_id"] = c_create.id
    SERVER_DATA["temp_category_id"] = cat_temp.id

    save_db(SERVER_DATA)

    if "👑 | Kurucu" in roles:
        try:
            await interaction.user.add_roles(roles["👑 | Kurucu"])
        except:
            pass

    await interaction.channel.send("✅ **Bütün eski kanallar ve roller silindi! Sıfırdan profesyonel sunucu yapısı kuruldu.**")

# --- YOUTUBE KURULUM KOMUTU ---
@bot.tree.command(name="youtube-kur", description="YouTube kanal bildirimi için kanal ID'si ve duyuru kanalını ayarlar.")
@app_commands.default_permissions(administrator=True)
async def youtube_kur(interaction: discord.Interaction, youtube_kanal_id: str, duyuru_kanali: discord.TextChannel):
    rss_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={youtube_kanal_id}"
    
    SERVER_DATA["yt_rss_url"] = rss_url
    SERVER_DATA["yt_channel_id"] = duyuru_kanali.id
    save_db(SERVER_DATA)

    await interaction.response.send_message(
        f"✅ **YouTube Bildirim Sistemi Aktif Edildi ve Kaydedildi!**\n"
        f"📌 **Takip Edilen Kanal ID:** `{youtube_kanal_id}`\n"
        f"📢 **Bildirim Kanalı:** {duyuru_kanali.mention}",
        ephemeral=True
    )

# --- TICKET KOMUTLARI ---
@bot.tree.command(name="ticket-kur", description="Destek talebi (Ticket) butonunu seçilen kanala kurar.")
@app_commands.default_permissions(administrator=True)
async def ticket_kur(interaction: discord.Interaction, kanal: discord.TextChannel):
    embed = discord.Embed(title="🎫 Destek Sistemi", description="Aşağıdaki butona tıklayarak yetkili ekibimizle özel bir destek kanalı açabilirsiniz.", color=discord.Color.blue())
    await kanal.send(embed=embed, view=TicketView())
    await interaction.response.send_message(f"✅ Destek paneli {kanal.mention} kanalına kuruldu.", ephemeral=True)

@bot.tree.command(name="ticket-kapat", description="Bulunduğunuz destek kanalını kapatır.")
async def ticket_kapat(interaction: discord.Interaction):
    if "ticket-" in interaction.channel.name:
        await interaction.response.send_message("🔒 Destek kanalı 5 saniye içinde siliniyor...")
        await asyncio.sleep(5)
        await interaction.channel.delete()
    else:
        await interaction.response.send_message("❌ Bu komut yalnızca ticket kanallarında kullanılabilir.", ephemeral=True)

# --- YOUTUBE DÖNGÜSÜ ---
@tasks.loop(minutes=3)
async def check_youtube():
    if not SERVER_DATA.get("yt_rss_url") or not SERVER_DATA.get("yt_channel_id"):
        return

    channel = bot.get_channel(SERVER_DATA["yt_channel_id"])
    if not channel:
        return

    try:
        feed = feedparser.parse(SERVER_DATA["yt_rss_url"])
        if feed.entries:
            latest = feed.entries[0]
            v_id = latest.yt_videoid
            v_url = latest.link
            v_title = latest.title

            if SERVER_DATA.get("last_video_id") is None:
                SERVER_DATA["last_video_id"] = v_id
                save_db(SERVER_DATA)
                return

            if SERVER_DATA["last_video_id"] != v_id:
                SERVER_DATA["last_video_id"] = v_id
                save_db(SERVER_DATA)
                await channel.send(f"🚨 **YENİ VİDEO YAYINLANDI!** 🚨\n\n**{v_title}**\n{v_url}\n\n@everyone")
    except Exception as e:
        print(f"YouTube kontrol hatası: {e}")

# --- ÜYE SAYACI CANLI GÜNCELLEME DÖNGÜSÜ ---
@tasks.loop(minutes=10)
async def update_member_count():
    if SERVER_DATA.get("stats_channel_id"):
        channel = bot.get_channel(SERVER_DATA["stats_channel_id"])
        if channel:
            await channel.edit(name=f"👥│Üye Sayısı: {channel.guild.member_count}")

# --- MÜZİK KOMUTLARI ---
@bot.tree.command(name="oynat", description="Şarkı adı veya YouTube URL'si ile yüksek kalitede müzik çalar.")
async def oynat(interaction: discord.Interaction, sarkici_veya_url: str):
    await interaction.response.defer(thinking=True)

    if not interaction.user.voice:
        await interaction.followup.send("❌ Müzik çalabilmem için önce bir **ses kanalına** girmelisin!")
        return

    voice_channel = interaction.user.voice.channel
    voice_client = interaction.guild.voice_client

    try:
        if not voice_client:
            voice_client = await voice_channel.connect()
        elif voice_client.channel != voice_channel:
            await voice_client.move_to(voice_channel)
    except Exception as e:
        await interaction.followup.send(f"❌ Ses kanalına bağlanırken hata oluştu: {e}")
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
        await interaction.followup.send(f"❌ Şarkı oynatılırken bir hata oluştu: {e}")

@bot.tree.command(name="dur", description="Çalan müziği durdurur ve bottan ayrılır.")
async def dur(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client
    if voice_client and voice_client.is_connected():
        await voice_client.disconnect()
        await interaction.response.send_message("⏹️ Müzik durduruldu, ses kanalından ayrıldım.")
    else:
        await interaction.response.send_message("❌ Zaten bir ses kanalında değilim.")

# --- EVENTLER (GELEN-GİDEN, DİNAMİK ODALAR & OYUNLAR) ---

@bot.event
async def on_member_join(member):
    # Üye Sayısını Anlık Güncelle
    if SERVER_DATA.get("stats_channel_id"):
        ch = member.guild.get_channel(SERVER_DATA["stats_channel_id"])
        if ch:
            await ch.edit(name=f"👥│Üye Sayısı: {member.guild.member_count}")

    # Gelen-Giden Kanalına Mesaj At
    if SERVER_DATA.get("welcome_channel_id"):
        welcome_ch = member.guild.get_channel(SERVER_DATA["welcome_channel_id"])
        if welcome_ch:
            await welcome_ch.send(f"👋 Hoş geldin {member.mention}! Senininle birlikte **{member.guild.member_count}** kişi olduk!")

@bot.event
async def on_member_remove(member):
    if SERVER_DATA.get("stats_channel_id"):
        ch = member.guild.get_channel(SERVER_DATA["stats_channel_id"])
        if ch:
            await ch.edit(name=f"👥│Üye Sayısı: {member.guild.member_count}")

    if SERVER_DATA.get("welcome_channel_id"):
        welcome_ch = member.guild.get_channel(SERVER_DATA["welcome_channel_id"])
        if welcome_ch:
            await welcome_ch.send(f"😢 {member.display_name} aramamızdan ayrıldı. Toplam **{member.guild.member_count}** kişi kaldık.")

temp_channels = []

@bot.event
async def on_voice_state_update(member, before, after):
    if after.channel and SERVER_DATA.get("join_to_create_id") and after.channel.id == SERVER_DATA["join_to_create_id"]:
        category = bot.get_channel(SERVER_DATA.get("temp_category_id"))
        new_ch = await member.guild.create_voice_channel(
            name=f"🎮│{member.display_name}'in Odası",
            category=category
        )
        temp_channels.append(new_ch.id)
        await member.move_to(new_ch)

    if before.channel and before.channel.id in temp_channels:
        if len(before.channel.members) == 0:
            temp_channels.remove(before.channel.id)
            await before.channel.delete()

# Oyun Değişkenleri
counting_number = 0
last_counter_user = None
last_word_letter = ""

AUTO_RESPONSES = {
    "sa": "Aleyküm selam, hoş geldin! 👋",
    "sea": "Aleyküm selam!",
    "selam": "Selam! Naber?",
    "sa eyt": "Aleyküm selam eyt!"
}

@bot.event
async def on_message(message):
    global counting_number, last_counter_user, last_word_letter

    if message.author.bot:
        return

    content_lower = message.content.lower().strip()

    # Oto Cevap
    if content_lower in AUTO_RESPONSES:
        await message.channel.send(AUTO_RESPONSES[content_lower])
        return

    # Sayı Sayma Oyunu (sayi-sayma kanalı)
    if message.channel.name == "sayi-sayma":
        if message.content.isdigit():
            val = int(message.content)
            if val == counting_number + 1 and message.author != last_counter_user:
                counting_number += 1
                last_counter_user = message.author
                await message.add_reaction("✅")
            else:
                counting_number = 0
                last_counter_user = None
                await message.add_reaction("❌")
                await message.channel.send(f"{message.author.mention} sırayı veya sayıyı bozdu! Sayac 0'landı. 1'den başlayın.")

    # Kelime Türetme Oyunu (kelime-turetme kanalı)
    elif message.channel.name == "kelime-turetme":
        word = content_lower
        if last_word_letter == "":
            last_word_letter = word[-1]
            await message.add_reaction("✅")
        elif word.startswith(last_word_letter):
            last_word_letter = word[-1]
            await message.add_reaction("✅")
            await message.channel.send(f"Yeni kelime **{last_word_letter.upper()}** harfi ile başlamalı!")
        else:
            await message.add_reaction("❌")
            await message.channel.send(f"Yanlış harf! Kelime **{last_word_letter.upper()}** ile başlamalıydı.")

    await bot.process_commands(message)

@bot.event
async def on_ready():
    print(f"[{bot.user.name}] Başarıyla başlatıldı. Tüm modüller aktif!")
    keep_alive() # Web sunucusu başlatılıyor
    check_youtube.start()
    update_member_count.start()

# Botu Çalıştır
if __name__ == "__main__":
    bot.run(TOKEN)
