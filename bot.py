# bot.py
import discord
from discord.ext import commands, tasks
from discord import app_commands
import os
import asyncio
import json
from datetime import datetime, timedelta, timezone
from flask import Flask
import threading
import re

# ------------------------------
# CONFIG
# ------------------------------
TOKEN = os.getenv("BOT_TOKEN")  # set on Render
GUILD_ID = 1424815111541096530
LOG_CHANNEL_ID = 1434241829733404692
YOUTUBE_CHANNEL_URL = "https://www.youtube.com/@RASH-TECH"
APP_FILE = "apps.json"
EMBED_COLOR = 0x00BFFF  # electric blue
COOLDOWN_HOURS = 48
INACTIVITY_CLOSE_MINUTES = 15
VERIFICATION_TIMEOUT = 120  # seconds

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# ------------------------------
# FLASK KEEPALIVE
# ------------------------------
app = Flask("keepalive")

@app.route("/")
def home():
    return "TICKET BOT V2 - RASH TECH (alive)"

def run_flask():
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")))

threading.Thread(target=run_flask, daemon=True).start()

# ------------------------------
# UTILS / STATE
# ------------------------------
def make_embed(title=None, description=None, color=EMBED_COLOR):
    e = discord.Embed(title=title, description=description, color=color, timestamp=datetime.now(timezone.utc))
    e.set_footer(text="⚡ RASH TECH | Ticket Bot V2")
    return e

# persistent apps storage
def load_apps():
    if os.path.exists(APP_FILE):
        with open(APP_FILE, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except:
                return {}
    # default placeholders (replace via /addapp)
    default = {
        "🎬 KINEMASTER — Best video editor for mobile": "REPLACE_WITH_YOUR_LINK",
        "🎵 Spotify Premium — Ad-free music experience": "REPLACE_WITH_YOUR_LINK",
        "📞 Truecaller — Identify unknown callers": "REPLACE_WITH_YOUR_LINK",
        "🎥 CineTV — Stream movies and TV shows": "REPLACE_WITH_YOUR_LINK"
    }
    with open(APP_FILE, "w", encoding="utf-8") as f:
        json.dump(default, f, ensure_ascii=False, indent=2)
    return default

def save_apps(apps):
    with open(APP_FILE, "w", encoding="utf-8") as f:
        json.dump(apps, f, ensure_ascii=False, indent=2)

apps = load_apps()

# runtime state (cooldowns and last-activity)
cooldowns = {}         # user_id -> datetime (UTC) when cooldown ends
open_tickets = {}      # channel_id -> owner_id
last_activity = {}     # channel_id -> datetime UTC

def set_cooldown(user_id):
    cooldowns[user_id] = datetime.now(timezone.utc) + timedelta(hours=COOLDOWN_HOURS)

def check_cooldown(user_id):
    exp = cooldowns.get(user_id)
    if not exp:
        return False, 0
    remaining = (exp - datetime.now(timezone.utc)).total_seconds()
    if remaining <= 0:
        del cooldowns[user_id]
        return False, 0
    return True, remaining

def remove_cooldown_user(user_id):
    cooldowns.pop(user_id, None)

def update_ticket_activity(channel_id):
    last_activity[channel_id] = datetime.now(timezone.utc)

# normalize text for matching (strip emojis/spaces, lowercase)
EMOJI_PATTERN = re.compile("["
    u"\U0001F600-\U0001F64F"  # emoticons
    u"\U0001F300-\U0001F5FF"  # symbols & pictographs
    u"\U0001F680-\U0001F6FF"  # transport & map symbols
    u"\U0001F1E0-\U0001F1FF"  # flags
    "]+", flags=re.UNICODE)

def normalize(s: str) -> str:
    s = s.lower().strip()
    s = EMOJI_PATTERN.sub("", s)        # remove emojis
    s = re.sub(r"[^a-z0-9\s\-]", "", s) # remove punctuation except hyphen
    s = re.sub(r"\s+", " ", s)          # collapse spaces
    return s

def find_app_by_input(user_input: str):
    ni = normalize(user_input)
    for key in apps.keys():
        if normalize(key) == ni:
            return key, apps[key]
    # partial match: if user typed a main word
    for key in apps.keys():
        if ni in normalize(key) or normalize(key) in ni:
            return key, apps[key]
    return None, None

# ------------------------------
# TRANSCRIPT
# ------------------------------
async def send_transcript(channel: discord.TextChannel):
    try:
        log_channel = channel.guild.get_channel(LOG_CHANNEL_ID)
        if not log_channel:
            return
        lines = []
        async for m in channel.history(limit=500, oldest_first=True):
            ts = m.created_at.strftime("%Y-%m-%d %H:%M:%S")
            lines.append(f"[{ts}] {m.author} ({m.author.id}): {m.clean_content}")
        fname = f"transcript_{channel.id}.txt"
        with open(fname, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        await log_channel.send(embed=make_embed("Ticket Transcript", f"Transcript from {channel.mention}"), file=discord.File(fname))
        os.remove(fname)
    except Exception as e:
        print("send_transcript error:", e)

# ------------------------------
# CLOSE BUTTON VIEW
# ------------------------------
class CloseTicketView(discord.ui.View):
    def __init__(self, owner_id: int, channel_id: int, timeout=None):
        super().__init__(timeout=timeout)
        self.owner_id = owner_id
        self.channel_id = channel_id

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.danger)
    async def close_button(self, interaction: discord.Interaction, btn: discord.ui.Button):
        # allow owner or admins
        if interaction.user.id != self.owner_id and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(embed=make_embed("Permission denied", "Only the ticket owner or an admin can close this ticket."), ephemeral=True)
            return
        channel = interaction.guild.get_channel(self.channel_id)
        if channel:
            await send_transcript(channel)
            await channel.delete(reason=f"Closed by {interaction.user}")
        await interaction.response.send_message(embed=make_embed("Closed", "Ticket closed and transcript saved."), ephemeral=True)

# ------------------------------
# INACTIVITY TASK
# ------------------------------
@tasks.loop(minutes=1)
async def inactivity_check():
    now = datetime.now(timezone.utc)
    for ch_id, last in list(last_activity.items()):
        if (now - last) > timedelta(minutes=INACTIVITY_CLOSE_MINUTES):
            ch = bot.get_channel(ch_id)
            if ch:
                try:
                    await send_transcript(ch)
                    await ch.delete(reason="Auto-closed due to inactivity")
                except Exception:
                    pass
            last_activity.pop(ch_id, None)
            open_tickets.pop(ch_id, None)

# ------------------------------
# READY
# ------------------------------
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (id: {bot.user.id})")
    try:
        await tree.sync(guild=discord.Object(id=GUILD_ID))
        print("Slash commands synced to guild.")
    except Exception as e:
        print("Slash sync error:", e)
    inactivity_check.start()

# ------------------------------
# SLASH: /ticket
# ------------------------------
@tree.command(name="ticket", description="Create a private ticket.")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def slash_ticket(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True, thinking=True)
    user = interaction.user
    guild = interaction.guild

    # existing ticket check by topic owner id
    existing = next((c for c in guild.text_channels if c.topic == str(user.id)), None)
    if existing:
        await interaction.followup.send(embed=make_embed("Ticket exists", f"You already have a ticket: {existing.mention}"), ephemeral=True)
        return

    on_cd, rem = check_cooldown(user.id)
    if on_cd:
        hrs = int(rem // 3600)
        mins = int((rem % 3600) // 60)
        await interaction.followup.send(embed=make_embed("Cooldown active", f"You can create a new ticket in **{hrs}h {mins}m**."), ephemeral=True)
        return

    # create channel
    category = discord.utils.get(guild.categories, name="Tickets")
    if not category:
        category = await guild.create_category("Tickets")
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False),
        user: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True),
        guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
    }
    chan_name = f"ticket-{user.name}".lower().replace(" ", "-")[:90]
    channel = await category.create_text_channel(name=chan_name, topic=str(user.id), overwrites=overwrites)
    open_tickets[channel.id] = user.id
    set_cooldown(user.id)
    update_ticket_activity(channel.id)

    # prepare long welcome embed (with app list + short descriptions)
    apps_lines = []
    for k in apps.keys():
        apps_lines.append(f"• {k}")
    apps_text = "\n".join(apps_lines) if apps_lines else "No apps available."

    welcome = (
        f"Hello {user.mention} — welcome to your private ticket!\n\n"
        f"**Available Premium Apps:**\n{apps_text}\n\n"
        "💡 **How it works:**\n"
        "1️⃣ Type the **exact app name** (as shown above) in this channel to request it.\n"
        "2️⃣ You will be asked to subscribe and upload a screenshot for verification.\n"
        "3️⃣ After verification we will DM you the download link.\n\n"
        f"⚠️ You can create only one ticket every **{COOLDOWN_HOURS} hours**.\n"
        "If you need assistance, mention an admin."
    )
    await channel.send(embed=make_embed("🎟️ Welcome to Your Ticket", welcome))
    await interaction.followup.send(embed=make_embed("Ticket created", f"Your ticket has been created: {channel.mention}"), ephemeral=True)

# ------------------------------
# MESSAGE: handle app requests (case-insensitive)
# ------------------------------
@bot.event
async def on_message(message: discord.Message):
    # required for commands to work
    await bot.process_commands(message)

    if message.author.bot:
        return
    if not isinstance(message.channel, discord.TextChannel):
        return
    # only handle inside ticket channels (we store owner in topic)
    topic = message.channel.topic
    if not topic:
        return
    # update activity
    update_ticket_activity(message.channel.id)
    owner_id = open_tickets.get(message.channel.id) or (int(topic) if topic and topic.isdigit() else None)

    content = message.content.strip()
    if not content:
        return

    # try to find the app
    app_key, app_link = find_app_by_input(content)
    if not app_key:
        return  # not an app request

    # check server join time (>=24 hours)
    member = message.author
    joined_at = member.joined_at
    if not joined_at:
        await message.channel.send(embed=make_embed("Verification failed", "Could not verify how long you've been in the server."))
        return
    if (datetime.now(timezone.utc) - joined_at) < timedelta(hours=24):
        await message.channel.send(embed=make_embed("Requirement not met", "You must be in this server for at least 24 hours to request premium apps."))
        return

    # ask for YouTube subscription + channel mention
    ask = (
        f"{member.mention}, to receive **{app_key}** you must be subscribed to our YouTube channel.\n\n"
        f"🔗 [👉 Click here to Subscribe]({YOUTUBE_CHANNEL_URL})\n\n"
        f"📩 After subscribing, upload a **screenshot** here: {message.channel.mention}\n\n"
        f"⏳ You have {VERIFICATION_TIMEOUT//60} minutes to upload the screenshot."
    )
    await message.channel.send(embed=make_embed("🎥 YouTube Subscription Verification", ask))

    def check(m: discord.Message):
        return (
            m.author.id == member.id
            and m.channel.id == message.channel.id
            and m.attachments
            and m.attachments[0].content_type
            and m.attachments[0].content_type.startswith("image")
        )

    try:
        reply = await bot.wait_for("message", timeout=VERIFICATION_TIMEOUT, check=check)
        # (We do not validate screenshot content — operator will manually check)
        # Send DM with link
        dm_embed = make_embed(f"{app_key} — Download Link",
                              "✅ Verification received. Please find your download link below.")
        # If the app link is placeholder, warn admin-only
        if app_link == "REPLACE_WITH_YOUR_LINK":
            dm_embed.add_field(name="Note", value="This app's download link is a placeholder. Admins should update it with `/addapp`.", inline=False)
        dm_embed.add_field(name="Download", value=app_link, inline=False)
        try:
            await member.send(embed=dm_embed)
        except discord.Forbidden:
            await message.channel.send(embed=make_embed("DM blocked", "I couldn't DM you. Please enable DMs to receive the download link."))
            return

        # in-channel confirmation with close button
        view = CloseTicketView(owner_id, message.channel.id)
        await message.channel.send(embed=make_embed("📥 Download Sent", f"I've sent the download link to your DMs. If you're satisfied, press **Close Ticket** below. This ticket will auto-close after {INACTIVITY_CLOSE_MINUTES} minutes of inactivity."), view=view)
        update_ticket_activity(message.channel.id)
    except asyncio.TimeoutError:
        await message.channel.send(embed=make_embed("⌛ Verification timed out", "You did not upload a valid screenshot in time. Please type the app name again to retry."))

# ------------------------------
# ADMIN: add/remove/list apps
# ------------------------------
@tree.command(name="addapp", description="Add/update a premium app (Admin only)")
@app_commands.describe(app_name="Name with emoji & short desc", link="Download link")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def cmd_addapp(interaction: discord.Interaction, app_name: str, link: str):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ You don't have permission.", ephemeral=True); return
    apps[app_name] = link
    save_apps(apps)
    await interaction.response.send_message(embed=make_embed("✅ App added", f"**{app_name}** has been added/updated."), ephemeral=True)

@tree.command(name="removeapp", description="Remove a premium app (Admin only)")
@app_commands.describe(app_name="Name exactly as listed")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def cmd_removeapp(interaction: discord.Interaction, app_name: str):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ You don't have permission.", ephemeral=True); return
    if app_name in apps:
        del apps[app_name]
        save_apps(apps)
        await interaction.response.send_message(embed=make_embed("✅ Removed", f"{app_name} removed."), ephemeral=True)
    else:
        await interaction.response.send_message(embed=make_embed("Not found", "No app by that name found."), ephemeral=True)

@tree.command(name="listapps", description="List all premium apps (Admin only)")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def cmd_listapps(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ You don't have permission.", ephemeral=True); return
    if not apps:
        await interaction.response.send_message(embed=make_embed("No apps", "There are currently no apps."), ephemeral=True); return
    text = "\n".join([f"• {k}" for k in apps.keys()])
    await interaction.response.send_message(embed=make_embed("📦 Premium Apps", text), ephemeral=True)

# ------------------------------
# ADMIN: close ticket command
# ------------------------------
@tree.command(name="close_ticket", description="Close this ticket (Admin only)")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def cmd_close_ticket(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ You don't have permission.", ephemeral=True); return
    channel = interaction.channel
    if not channel or not isinstance(channel, discord.TextChannel):
        await interaction.response.send_message("This command must be used inside the ticket channel.", ephemeral=True); return
    await interaction.response.defer(ephemeral=True)
    await send_transcript(channel)
    await channel.delete(reason=f"Closed by {interaction.user}")
    await interaction.followup.send(embed=make_embed("Closed", "Ticket closed and transcript saved."), ephemeral=True)

# ------------------------------
# ADMIN: remove cooldown command
# ------------------------------
@tree.command(name="remove_cooldown", description="Remove a user's cooldown (Admin only)")
@app_commands.describe(user="User to remove cooldown for")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def cmd_remove_cooldown(interaction: discord.Interaction, user: discord.Member):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ You don't have permission.", ephemeral=True); return
    if user.id in cooldowns:
        del cooldowns[user.id]
        await interaction.response.send_message(embed=make_embed("✅ Removed", f"Cooldown removed for {user.mention}."), ephemeral=True)
    else:
        await interaction.response.send_message(embed=make_embed("No cooldown", f"{user.mention} has no cooldown."), ephemeral=True)

# ------------------------------
# RUN
# ------------------------------
if not TOKEN:
    print("ERROR: BOT_TOKEN environment variable not set.")
else:
    bot.run(TOKEN)
