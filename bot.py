import discord
from discord.ext import commands, tasks
from discord import app_commands
import os
import asyncio
from datetime import datetime, timedelta
from flask import Flask
import threading

# -------------------------------
# 🔧 CONFIGURATION
# -------------------------------
TOKEN = os.getenv("BOT_TOKEN")
GUILD_ID = 1424815111541096530
LOG_CHANNEL_ID = 1434241829733404692
COOLDOWN_HOURS = 48
INACTIVITY_CLOSE_MINUTES = 15

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# -------------------------------
# 🧩 SIMPLE IN-MEMORY DATABASE
# -------------------------------
tickets = {}  # user_id -> channel_id
cooldowns = {}  # user_id -> datetime
last_activity = {}  # channel_id -> datetime

# -------------------------------
# 🌐 FLASK KEEP-ALIVE SERVER
# -------------------------------
app = Flask(__name__)

@app.route('/')
def home():
    return "TICKET BOT V2 is running!"

def run_flask():
    app.run(host='0.0.0.0', port=8080)

threading.Thread(target=run_flask).start()

# -------------------------------
# 📦 EMBED HELPER
# -------------------------------
def make_embed(title=None, description=None, color=0x5865F2):
    embed = discord.Embed(title=title, description=description, color=color)
    return embed

# -------------------------------
# ⏱️ COOLDOWN SYSTEM
# -------------------------------
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

def remove_cooldown(user_id):
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
                await channel.delete(reason="Inactive for too long")
            del last_activity[channel_id]

# -------------------------------
# 📜 TRANSCRIPT SYSTEM
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
# 🚀 BOT READY EVENT
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

    welcome_embed = make_embed(
        title="🎟️ Welcome to Your Ticket!",
        description=(
            f"Hello {user.mention}, thank you for opening a ticket!\n\n"
            "**Here are the premium apps we currently provide:**\n"
            "• Spotify Premium\n• Netflix Premium\n• YouTube Premium\n\n"
            "🕓 *New apps coming soon!*\n\n"
            "Please type the **app name** below to continue."
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

    apps = {
        "spotify": {"name": "Spotify Premium", "link": "https://example.com/spotify"},
        "netflix": {"name": "Netflix Premium", "link": "https://example.com/netflix"},
        "youtube": {"name": "YouTube Premium", "link": "https://example.com/youtube"}
    }

    content = message.content.lower().strip()
    app = apps.get(content)
    if not app:
        return

    member = message.author
    ask_embed = make_embed(
        "📸 YouTube Subscription Verification",
        f"{member.mention}, please upload a **screenshot** showing you are subscribed to our YouTube channel.\n"
        "You have **2 minutes** to send it here."
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

        embed = make_embed(
            title=f"{app['name']} — Download",
            description=f"Here is your download link:\n{app['link']}"
        )
        view = CloseTicketView(message.channel.id)
        await member.send(embed=embed)
        await message.channel.send(
            embed=make_embed(
                "📥 Download Sent",
                f"I've sent you the download link in DM.\n"
                "If you are satisfied, you can close this ticket below, "
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
# ⚙️ OWNER COMMANDS
# -------------------------------
@tree.command(name="ticket_close", description="Close a user's ticket.")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def ticket_close(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ You don't have permission.", ephemeral=True)
        return
    await interaction.response.send_message("Closing ticket...", ephemeral=True)
    await send_transcript(interaction.channel)
    await interaction.channel.delete(reason=f"Closed by {interaction.user}")

@tree.command(name="remove_cooldown", description="Remove cooldown from a user.")
@app_commands.describe(user="The user to remove cooldown from.")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def remove_cd(interaction: discord.Interaction, user: discord.Member):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ You don't have permission.", ephemeral=True)
        return
    remove_cooldown(user.id)
    await interaction.response.send_message(
        embed=make_embed("✅ Cooldown Removed", f"Cooldown removed for {user.mention}."),
        ephemeral=True
    )

# -------------------------------
# 🚀 RUN BOT
# -------------------------------
bot.run(TOKEN)
