import discord
from discord.ext import commands, tasks
from discord import app_commands
import os
import asyncio
import json
from datetime import datetime, timedelta, timezone
from flask import Flask
import threading

# -------------------------------
# CONFIGURATION
# -------------------------------
TOKEN = os.getenv("BOT_TOKEN")
GUILD_ID = 1424815111541096530
LOG_CHANNEL_ID = 1434241829733404692
COOLDOWN_HOURS = 48
INACTIVITY_CLOSE_MINUTES = 15
YOUTUBE_CHANNEL_URL = "https://www.youtube.com/@RASH-TECH"
APP_FILE = "apps.json"
EMBED_COLOR = 0x00BFFF

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# -------------------------------
# KEEP-ALIVE FLASK APP
# -------------------------------
app = Flask(__name__)

@app.route('/')
def home():
    return "TICKET BOT V2 is running!"

threading.Thread(target=lambda: app.run(host='0.0.0.0', port=8080)).start()

# -------------------------------
# UTILITIES
# -------------------------------
def make_embed(title=None, description=None, color=EMBED_COLOR):
    embed = discord.Embed(title=title, description=description, color=color)
    embed.set_footer(text="⚡ RASH TECH | Ticket Bot V2")
    return embed

def format_time(seconds):
    days, remainder = divmod(int(seconds), 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, secs = divmod(remainder, 60)
    parts = []
    if days: parts.append(f"{days} day{'s' if days != 1 else ''}")
    if hours: parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if minutes: parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    if secs: parts.append(f"{secs} second{'s' if secs != 1 else ''}")
    return " ".join(parts)

# -------------------------------
# COOLDOWN MANAGEMENT
# -------------------------------
cooldowns = {}
last_activity = {}

def set_cooldown(uid):
    cooldowns[uid] = datetime.utcnow() + timedelta(hours=COOLDOWN_HOURS)

def check_cooldown(uid):
    if uid not in cooldowns:
        return False, ""
    remaining = (cooldowns[uid] - datetime.utcnow()).total_seconds()
    if remaining <= 0:
        del cooldowns[uid]
        return False, ""
    return True, format_time(remaining)

def remove_cooldown(uid):
    if uid in cooldowns:
        del cooldowns[uid]

def update_ticket_activity(cid):
    last_activity[cid] = datetime.utcnow()

# -------------------------------
# INACTIVITY AUTO-CLOSE
# -------------------------------
@tasks.loop(minutes=5)
async def check_inactivity():
    now = datetime.utcnow()
    for cid, last_time in list(last_activity.items()):
        if (now - last_time).total_seconds() > INACTIVITY_CLOSE_MINUTES * 60:
            channel = bot.get_channel(cid)
            if channel:
                await send_transcript(channel)
                await channel.delete(reason="Inactive ticket")
            del last_activity[cid]

# -------------------------------
# APP MANAGEMENT
# -------------------------------
def load_apps():
    if os.path.exists(APP_FILE):
        with open(APP_FILE, "r") as f:
            return json.load(f)
    else:
        apps = {
            "🎬 KINEMASTER Premium": "https://placeholder.link/kinemaster",
            "🎵 Spotify Premium": "https://placeholder.link/spotify",
            "📞 Truecaller Premium": "https://placeholder.link/truecaller",
            "🎥 CineTV Premium": "https://placeholder.link/cinetv"
        }
        with open(APP_FILE, "w") as f:
            json.dump(apps, f)
        return apps

def save_apps(apps):
    with open(APP_FILE, "w") as f:
        json.dump(apps, f)

apps = load_apps()

# -------------------------------
# TRANSCRIPT (EMBED VERSION)
# -------------------------------
async def send_transcript(channel: discord.TextChannel):
    try:
        log_channel = channel.guild.get_channel(LOG_CHANNEL_ID)
        if not log_channel:
            return
        messages = []
        async for m in channel.history(limit=50, oldest_first=True):
            if m.author.bot:
                continue
            content = m.clean_content or "[No text]"
            if len(content) > 150:
                content = content[:147] + "..."
            messages.append(f"**{m.author.display_name}:** {content}")

        transcript_preview = "\n".join(messages[-20:]) if messages else "_No messages found._"
        owner_id = channel.topic if channel.topic and channel.topic.isdigit() else "Unknown"
        owner_mention = f"<@{owner_id}>" if owner_id != "Unknown" else "Unknown"
        created_at = channel.created_at.strftime("%Y-%m-%d %H:%M UTC")
        closed_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        embed = discord.Embed(
            title=f"📜 Ticket Transcript — #{channel.name}",
            color=EMBED_COLOR,
            description=(
                f"**Opened by:** {owner_mention}\n"
                f"**Created:** {created_at}\n"
                f"**Closed:** {closed_at}\n\n"
                f"**Recent Messages:**\n{transcript_preview}"
            ),
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_footer(text="⚡ RASH TECH | Ticket Transcript")
        await log_channel.send(embed=embed)
    except Exception as e:
        print("send_transcript error:", e)

# -------------------------------
# CLOSE BUTTON
# -------------------------------
class CloseTicketView(discord.ui.View):
    def __init__(self, channel_id):
        super().__init__(timeout=None)
        self.channel_id = channel_id

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.danger)
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = interaction.guild.get_channel(self.channel_id)
        if channel:
            await send_transcript(channel)
            await channel.delete(reason="Closed by user")
        await interaction.response.send_message("✅ Ticket closed.", ephemeral=True)

# -------------------------------
# BOT READY
# -------------------------------
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    try:
        await tree.sync(guild=discord.Object(id=GUILD_ID))
        print("✅ Commands synced.")
    except Exception as e:
        print("❌ Sync error:", e)
    check_inactivity.start()

# -------------------------------
# /TICKET COMMAND
# -------------------------------
@tree.command(name="ticket", description="Create a private ticket.")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def ticket(interaction: discord.Interaction):
    user = interaction.user
    guild = interaction.guild

    existing_ticket = next((c for c in guild.text_channels if c.topic == str(user.id)), None)
    if existing_ticket:
        await interaction.response.send_message(
            embed=make_embed("⚠️ Ticket Already Exists",
                             f"You already have an open ticket: {existing_ticket.mention}"),
            ephemeral=True
        )
        return

    cooldown_active, remaining = check_cooldown(user.id)
    if cooldown_active:
        await interaction.response.send_message(
            embed=make_embed("⏳ Cooldown Active",
                             f"You can create a new ticket in **{remaining}**."),
            ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False),
        user: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True),
        guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
    }

    category = discord.utils.get(guild.categories, name="Tickets")
    if category is None:
        category = await guild.create_category("Tickets")

    channel = await guild.create_text_channel(
        name=f"ticket-{user.name}",
        topic=str(user.id),
        overwrites=overwrites,
        category=category
    )

    apps_list = (
        "🎬 **KINEMASTER Premium** — Best video editor for mobile\n"
        "🎵 **Spotify Premium** — Ad-free music experience\n"
        "📞 **Truecaller Premium** — Identify unknown callers\n"
        "🎥 **CineTV Premium** — Stream movies and TV shows\n\n"
        "✨ *More premium apps coming soon!*"
    )

    welcome = make_embed(
        "🎟️ Welcome to Your Ticket",
        f"Hello {user.mention}, thanks for opening a ticket!\n\n"
        f"Here are the premium apps we currently provide:\n\n{apps_list}\n\n"
        "💡 **How It Works:**\n"
        "1️⃣ Type the app name below.\n"
        "2️⃣ Complete YouTube verification.\n"
        "3️⃣ Receive your app link in DM.\n\n"
        f"📺 [Subscribe to our YouTube Channel]({YOUTUBE_CHANNEL_URL})"
    )
    await channel.send(embed=welcome)
    set_cooldown(user.id)
    update_ticket_activity(channel.id)

    await interaction.followup.send(
        embed=make_embed("✅ Ticket Created",
                         f"Your private ticket has been created: {channel.mention}"),
        ephemeral=True
    )

# -------------------------------
# MESSAGE HANDLER
# -------------------------------
@bot.event
async def on_message(message):
    if message.author.bot or not message.channel.topic:
        return
    update_ticket_activity(message.channel.id)
    content = message.content.strip().lower()
    app_name = next((name for name in apps if name.lower().startswith(content)), None)
    if not app_name:
        return

    member = message.author
    await message.channel.send(embed=make_embed(
        "📺 YouTube Verification",
        f"{member.mention}, please **subscribe** to our YouTube channel:\n"
        f"[👉 Click here to Subscribe]({YOUTUBE_CHANNEL_URL})\n\n"
        "Upload a **screenshot** here as proof within 2 minutes."
    ))

    def check(m):
        return m.author == member and m.channel == message.channel and m.attachments

    try:
        reply = await bot.wait_for("message", timeout=120, check=check)
        await message.channel.send(embed=make_embed(
            "✅ Verification Successful",
            f"Thanks {member.mention}! Sending your app link..."
        ))

        download_embed = make_embed(
            title=f"{app_name} — Download Link",
            description=f"Here’s your secure download link:\n{apps[app_name]}"
        )
        try:
            await member.send(embed=download_embed)
            await message.channel.send(embed=make_embed(
                "📥 Download Sent",
                "Check your DM for the link.\nIf you’re satisfied, you can close this ticket using the button below."
            ), view=CloseTicketView(message.channel.id))
        except:
            await message.channel.send(embed=make_embed(
                "⚠️ Couldn’t send DM",
                "Please enable DMs to receive your app link."
            ))
    except asyncio.TimeoutError:
        await message.channel.send(embed=make_embed(
            "⌛ Verification Timed Out",
            "You didn’t upload a screenshot in time. Type the app name again to retry."
        ))

# -------------------------------
# ADMIN COMMANDS
# -------------------------------
@tree.command(name="addapp", description="Add a new app (Admin Only)")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def add_app(interaction: discord.Interaction, app_name: str, link: str):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ No permission.", ephemeral=True)
        return
    apps[app_name] = link
    save_apps(apps)
    await interaction.response.send_message(f"✅ Added **{app_name}**.", ephemeral=True)

@tree.command(name="removeapp", description="Remove an app (Admin Only)")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def remove_app(interaction: discord.Interaction, app_name: str):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ No permission.", ephemeral=True)
        return
    if app_name in apps:
        del apps[app_name]
        save_apps(apps)
        await interaction.response.send_message(f"✅ Removed **{app_name}**.", ephemeral=True)
    else:
        await interaction.response.send_message("App not found.", ephemeral=True)

@tree.command(name="listapps", description="List all apps (Admin Only)")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def list_apps(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ No permission.", ephemeral=True)
        return
    if not apps:
        await interaction.response.send_message("No apps available.", ephemeral=True)
        return
    app_list = "\n".join(apps.keys())
    await interaction.response.send_message(embed=make_embed("📦 Available Apps", app_list), ephemeral=True)

@tree.command(name="remove_cooldown", description="Remove a user's cooldown (Admin Only)")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def remove_cd(interaction: discord.Interaction, user: discord.Member):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ No permission.", ephemeral=True)
        return
    remove_cooldown(user.id)
    await interaction.response.send_message(f"✅ Cooldown removed for {user.mention}", ephemeral=True)

@tree.command(name="close_ticket", description="Manually close a ticket (Admin Only)")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def close_ticket(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ No permission.", ephemeral=True)
        return
    channel = interaction.channel
    await send_transcript(channel)
    await channel.delete(reason="Closed manually by admin")

# -------------------------------
# RUN
# -------------------------------
bot.run(TOKEN)
