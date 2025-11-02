import discord
from discord.ext import commands, tasks
from discord import app_commands
import os
import asyncio
import json
from datetime import datetime, timedelta
from flask import Flask
import threading

# -------------------------------
# 🔧 CONFIG
# -------------------------------
TOKEN = os.getenv("BOT_TOKEN")
GUILD_ID = 1424815111541096530
LOG_CHANNEL_ID = 1434241829733404692
COOLDOWN_HOURS = 48
INACTIVITY_CLOSE_MINUTES = 15
YOUTUBE_CHANNEL_URL = "https://www.youtube.com/@RASH-TECH"
APP_FILE = "apps.json"
EMBED_COLOR = 0x00BFFF  # Electric blue for Premium Tech look

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# -------------------------------
# 🌐 FLASK KEEP-ALIVE
# -------------------------------
app = Flask(__name__)

@app.route('/')
def home():
    return "TICKET BOT V2 is running!"

threading.Thread(target=lambda: app.run(host='0.0.0.0', port=8080)).start()

# -------------------------------
# 🧩 EMBED HELPER
# -------------------------------
def make_embed(title=None, description=None, color=EMBED_COLOR):
    embed = discord.Embed(title=title, description=description, color=color)
    embed.set_footer(text="⚡ RASH TECH | Ticket Bot V2")
    return embed

# -------------------------------
# ⏱️ COOLDOWN SYSTEM
# -------------------------------
cooldowns = {}  # user_id -> datetime
tickets = {}  # user_id -> channel_id
last_activity = {}  # channel_id -> datetime

def set_cooldown(user_id):
    cooldowns[user_id] = datetime.utcnow() + timedelta(hours=COOLDOWN_HOURS)

def check_cooldown(user_id):
    if user_id not in cooldowns:
        return False, 0
    remaining = (cooldowns[user_id] - datetime.utcnow()).total_seconds()
    if remaining <= 0:
        del cooldowns[user_id]
        return False, 0
    return True, round(remaining / 3600, 1)

def remove_cooldown_user(user_id):
    if user_id in cooldowns:
        del cooldowns[user_id]

# -------------------------------
# 🕓 ACTIVITY TRACKING
# -------------------------------
def update_ticket_activity(channel_id):
    last_activity[channel_id] = datetime.utcnow()

@tasks.loop(minutes=5)
async def check_inactivity():
    now = datetime.utcnow()
    for channel_id, last_time in list(last_activity.items()):
        if (now - last_time).total_seconds() > INACTIVITY_CLOSE_MINUTES * 60:
            channel = bot.get_channel(channel_id)
            if channel:
                await send_transcript(channel)
                await channel.delete(reason="Inactive ticket")
            del last_activity[channel_id]

# -------------------------------
# 📜 APP MANAGEMENT
# -------------------------------
def load_apps():
    if os.path.exists(APP_FILE):
        with open(APP_FILE, "r") as f:
            return json.load(f)
    else:
        apps = {
            "🎬 KINEMASTER premium": "https://link-target.net/1425230/7EdG6nu9eJ1G",
            "🎵 Spotify Premium": "https://direct-link.net/1425230/Ihg8hRfZw09V",
            "📞 Truecaller premium": "https://link-target.net/1425230/uT1uGZ0lP8MW",
            "🎥 CineTV premium": "https://link-target.net/1425230/Efpk9wEmqABS"
        }
        with open(APP_FILE, "w") as f:
            json.dump(apps, f)
        return apps

def save_apps(apps):
    with open(APP_FILE, "w") as f:
        json.dump(apps, f)

apps = load_apps()

# -------------------------------
# 📜 TRANSCRIPT
# -------------------------------
async def send_transcript(channel: discord.TextChannel):
    try:
        log_channel = channel.guild.get_channel(LOG_CHANNEL_ID)
        if not log_channel:
            return
        messages = []
        async for msg in channel.history(limit=200, oldest_first=True):
            ts = msg.created_at.strftime("%Y-%m-%d %H:%M:%S")
            author = f"{msg.author} ({msg.author.id})"
            content = msg.clean_content or ""
            messages.append(f"[{ts}] {author}: {content}")
        transcript_text = "\n".join(messages)
        file_name = f"transcript_{channel.name}_{channel.id}.txt"
        with open(file_name, "w", encoding="utf-8") as f:
            f.write(transcript_text)
        await log_channel.send(
            embed=make_embed("Ticket Closed", f"Transcript from {channel.mention}"),
            file=discord.File(file_name)
        )
        os.remove(file_name)
    except Exception as e:
        print("Transcript error:", e)

# -------------------------------
# 🎫 CLOSE BUTTON VIEW
# -------------------------------
class CloseTicketView(discord.ui.View):
    def __init__(self, channel_id, timeout=None):
        super().__init__(timeout=timeout)
        self.channel_id = channel_id

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.danger)
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = interaction.guild.get_channel(self.channel_id)
        if channel:
            await send_transcript(channel)
            await channel.delete(reason="Ticket closed by user")
        await interaction.response.send_message("✅ Ticket closed.", ephemeral=True)

# -------------------------------
# 🚀 BOT READY
# -------------------------------
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    try:
        guild = discord.Object(id=GUILD_ID)
        await bot.tree.sync(guild=guild)
        print("✅ Slash commands synced.")
    except Exception as e:
        print("❌ Command sync failed:", e)
    check_inactivity.start()

# -------------------------------
# 🎟️ /ticket COMMAND
# -------------------------------
@tree.command(name="ticket", description="Create a private ticket.")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def ticket(interaction: discord.Interaction):
    user = interaction.user
    guild = interaction.guild

    existing_ticket = next((c for c in guild.text_channels if c.topic == str(user.id)), None)
    if existing_ticket:
        await interaction.response.send_message(
            embed=make_embed("⚠️ You already have a ticket!",
                             f"{user.mention}, please use your existing ticket: {existing_ticket.mention}."),
            ephemeral=True
        )
        return

    cooldown_active, remaining = check_cooldown(user.id)
    if cooldown_active:
        await interaction.response.send_message(
            embed=make_embed("⏳ Cooldown Active",
                             f"You can create a new ticket in **{remaining} hours**."),
            ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True, thinking=True)

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False),
        user: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True),
        guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True)
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

    # List apps in embed
    apps_list = "\n".join([f"{name}" for name in apps.keys()])

    welcome_embed = make_embed(
        title="🎟️ Welcome to Your Ticket!",
        description=(
            f"Hello {user.mention}, thank you for opening a ticket!\n\n"
            "**Here are the premium apps we currently provide:**\n"
            f"{apps_list}\n\n"
            "🕓 *New apps coming soon!*\n\n"
            "💡 **How It Works:**\n"
            "1️⃣ Type the app name you want below.\n"
            "2️⃣ Complete YouTube verification.\n"
            "3️⃣ Receive your download link in DM.\n\n"
            "⚠️ You can only create one ticket every 48 hours."
        )
    )
    await channel.send(embed=welcome_embed)
    set_cooldown(user.id)
    update_ticket_activity(channel.id)
    await interaction.followup.send(
        embed=make_embed("✅ Ticket Created",
                         f"Your private ticket has been created: {channel.mention}"),
        ephemeral=True
    )

# -------------------------------
# 📨 MESSAGE HANDLER FOR APP REQUEST
# -------------------------------
@bot.event
async def on_message(message):
    if message.author.bot:
        return
    if not message.channel.topic:
        return
    update_ticket_activity(message.channel.id)

    content = message.content.strip()
    app_link = apps.get(content)
    if not app_link:
        return

    member = message.author
    ask_embed = make_embed(
        "🎥 YouTube Subscription Verification",
        f"{member.mention}, before we can send your premium app link, please **subscribe to our YouTube channel**:\n\n"
        f"🔗 [👉 Click here to Subscribe]({YOUTUBE_CHANNEL_URL})\n\n"
        f"After subscribing, upload a **screenshot here** ({message.channel.mention}) as proof.\n"
        "⏳ You have 2 minutes to send it."
    )
    await message.channel.send(embed=ask_embed)

    def check(m):
        return (
            m.author == member and
            m.channel == message.channel and
            (m.attachments and m.attachments[0].content_type.startswith("image/"))
        )

    try:
        reply = await bot.wait_for("message", timeout=120, check=check)
        screenshot = reply.attachments[0]

        confirm_embed = make_embed(
            "✅ Verification Successful",
            f"Thanks {member.mention}! Screenshot received. Sending your download link..."
        )
        await message.channel.send(embed=confirm_embed)

        download_embed = make_embed(
            title=f"{content} — Download Link",
            description=f"Here is your download link:\n{app_link}"
        )
        view = CloseTicketView(message.channel.id)
        await member.send(embed=download_embed)
        await message.channel.send(
            embed=make_embed(
                "📥 Download Sent",
                f"I've sent the download link in DM.\n"
                f"If you are satisfied, you can close this ticket using the button below, "
                f"or it will auto-close after {INACTIVITY_CLOSE_MINUTES} minutes of inactivity."
            ),
            view=view
        )
        update_ticket_activity(message.channel.id)
    except asyncio.TimeoutError:
        timeout_embed = make_embed(
            "⌛ Verification Timed Out",
            "You did not upload a valid screenshot in time. Please type the app name again to retry."
        )
        await message.channel.send(embed=timeout_embed)

# -------------------------------
# ⚙️ OWNER ADMIN COMMANDS
# -------------------------------
@tree.command(name="addapp", description="Add a premium app with download link (Admin Only)")
@app_commands.describe(app_name="Name of the app (with emoji)", link="Download link")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def add_app(interaction: discord.Interaction, app_name: str, link: str):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ You do not have permission.", ephemeral=True)
        return
    apps[app_name] = link
    save_apps(apps)
    await interaction.response.send_message(f"✅ App **{app_name}** added successfully.", ephemeral=True)

@tree.command(name="removeapp", description="Remove a premium app (Admin Only)")
@app_commands.describe(app_name="Name of the app to remove (with emoji)")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def remove_app(interaction: discord.Interaction, app_name: str):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ You do not have permission.", ephemeral=True)
        return
    if app_name in apps:
        del apps[app_name]
        save_apps(apps)
        await interaction.response.send_message(f"✅ App **{app_name}** removed successfully.", ephemeral=True)
    else:
        await interaction.response.send_message("❌ App not found.", ephemeral=True)

@tree.command(name="listapps", description="List all premium apps (Admin Only)")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def list_apps(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ You do not have permission.", ephemeral=True)
        return
    if not apps:
        await interaction.response.send_message("No apps available.", ephemeral=True)
        return
    app_list = "\n".join([f"{name}" for name in apps.keys()])
    await interaction.response.send_message(embed=make_embed("📦 Premium Apps", app_list), ephemeral=True)

@tree.command(
    name="remove_cooldown",
    description="Remove a user's ticket cooldown (Admin Only)"
)
@app_commands.describe(user="Select the user to remove cooldown from")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def remove_cooldown_command(interaction: discord.Interaction, user: discord.Member):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ You do not have permission.", ephemeral=True)
        return

    if user.id in cooldowns:
        del cooldowns[user.id]
        await interaction.response.send_message(f"✅ Cooldown removed for {user.mention}", ephemeral=True)
    else:
        await interaction.response.send_message(f"ℹ️ {user.mention} does not have a cooldown.", ephemeral=True)

# -------------------------------
# 🚀 RUN BOT
# -------------------------------
bot.run(TOKEN)
