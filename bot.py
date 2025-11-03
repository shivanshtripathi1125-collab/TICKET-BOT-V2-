import discord
from discord import app_commands
from discord.ext import commands, tasks
import asyncio
from datetime import datetime, timezone, timedelta
from flask import Flask
import threading

# -----------------------------
# BASIC CONFIG
# -----------------------------
GUILD_ID = 1424815111541096530         # your server id
LOG_CHANNEL_ID = 1434241829733404692   # log channel
EMBED_COLOR = 0x00FFAA                 # embed color
COOLDOWN_HOURS = 48

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# -----------------------------
# DATABASES
# -----------------------------
cooldowns = {}   # user_id: datetime
apps = {
    "🎬 KINEMASTER Premium": "https://placeholder.link/app1",
    "🎵 Spotify Premium": "https://placeholder.link/app2",
    "📞 Truecaller Premium": "https://placeholder.link/app3",
    "🎥 CineTV Premium": "https://placeholder.link/app4",
}

# -----------------------------
# HELPER: TIME FORMATTING
# -----------------------------
def format_time(seconds):
    days, remainder = divmod(int(seconds), 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, secs = divmod(remainder, 60)
    parts = []
    if days: parts.append(f"{days} day{'s' if days!=1 else ''}")
    if hours: parts.append(f"{hours} hour{'s' if hours!=1 else ''}")
    if minutes: parts.append(f"{minutes} minute{'s' if minutes!=1 else ''}")
    if secs: parts.append(f"{secs} second{'s' if secs!=1 else ''}")
    return " ".join(parts)

def check_cooldown(uid):
    if uid not in cooldowns: return False, ""
    remaining = (cooldowns[uid] - datetime.utcnow()).total_seconds()
    if remaining <= 0:
        del cooldowns[uid]
        return False, ""
    return True, format_time(remaining)

def make_embed(title, desc, color=EMBED_COLOR):
    return discord.Embed(title=title, description=desc, color=color)

# -----------------------------
# CLOSE TICKET BUTTON
# -----------------------------
class CloseTicketView(discord.ui.View):
    def __init__(self, channel_id=None):
        super().__init__(timeout=None)
        self.channel_id = channel_id

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.danger,
                       custom_id="close_ticket_button")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = interaction.channel
        await send_transcript(channel, interaction.user)
        await interaction.response.send_message("✅ Ticket closed.", ephemeral=True)
        await asyncio.sleep(1)
        await channel.delete(reason="Closed by user")

# -----------------------------
# TRANSCRIPT (embed style)
# -----------------------------
async def send_transcript(channel, closed_by):
    try:
        log_channel = channel.guild.get_channel(LOG_CHANNEL_ID)
        if not log_channel:
            return

        msgs = []
        async for m in channel.history(limit=50, oldest_first=True):
            if m.author.bot:
                continue
            txt = m.clean_content or "[No text]"
            if len(txt) > 150:
                txt = txt[:147] + "..."
            msgs.append(f"**{m.author.display_name}:** {txt}")

        preview = "\n".join(msgs[-20:]) if msgs else "_No messages found._"

        opener = f"<@{channel.topic}>" if channel.topic else "Unknown"
        embed = discord.Embed(
            title=f"📜 Ticket Transcript — #{channel.name}",
            description=(
                f"**Opened by:** {opener}\n"
                f"**Closed by:** {closed_by.mention}\n"
                f"**Created:** {channel.created_at.strftime('%Y-%m-%d %H:%M UTC')}\n"
                f"**Closed:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n\n"
                f"**Recent Messages:**\n{preview}"
            ),
            color=EMBED_COLOR,
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_footer(text="⚡ RASH TECH | Ticket System")

        view = None
        if len(msgs) > 20:
            view = discord.ui.View()
            view.add_item(
                discord.ui.Button(
                    label="View Full History",
                    style=discord.ButtonStyle.link,
                    url=f"https://discord.com/channels/{channel.guild.id}/{channel.id}"
                )
            )

        await log_channel.send(embed=embed, view=view)
    except Exception as e:
        print("Transcript Error:", e)

# -----------------------------
# TICKET COMMAND
# -----------------------------
@tree.command(name="ticket", description="Create a private support ticket")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def ticket(inter: discord.Interaction):
    active, remaining = check_cooldown(inter.user.id)
    if active:
        await inter.response.send_message(
            embed=make_embed("⏳ Cooldown Active",
                             f"You can create a new ticket in **{remaining}**."),
            ephemeral=True
        )
        return

    guild = inter.guild
    category = discord.utils.get(guild.categories, name="🎫 Tickets")
    if category is None:
        category = await guild.create_category("🎫 Tickets")

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False),
        inter.user: discord.PermissionOverwrite(read_messages=True,
                                                send_messages=True,
                                                attach_files=True)
    }
    channel = await guild.create_text_channel(
        f"ticket-{inter.user.name}", category=category,
        topic=str(inter.user.id), overwrites=overwrites)

    cooldowns[inter.user.id] = datetime.utcnow() + timedelta(hours=COOLDOWN_HOURS)

    embed = discord.Embed(
        title="🎟️ Welcome to Your Ticket!",
        color=EMBED_COLOR,
        description=(
            f"Hello {inter.user.mention}!\n\n"
            "Please send a screenshot showing you’ve **subscribed** to our "
            "[YouTube channel](https://www.youtube.com/@RASH-TECH).\n\n"
            "**Available apps:**\n"
            "🎬 KINEMASTER — Best video editor for mobile\n"
            "🎵 Spotify Premium — Ad-free music experience\n"
            "📞 Truecaller — Identify unknown callers\n"
            "🎥 CineTV — Stream movies and TV shows\n\n"
            "_More premium apps coming soon!_"
        )
    )
    await inter.response.send_message(
        embed=make_embed("✅ Ticket Created",
                         f"Your ticket has been created: {channel.mention}."),
        ephemeral=True
    )
    await channel.send(embed=embed)

# -----------------------------
# CLOSE TICKET (admin)
# -----------------------------
@tree.command(name="close_ticket", description="Close a ticket (Admin Only)")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def close_ticket_cmd(inter: discord.Interaction):
    if not inter.user.guild_permissions.manage_channels:
        await inter.response.send_message("❌ You lack permission.", ephemeral=True)
        return
    if not inter.channel.name.startswith("ticket-"):
        await inter.response.send_message("❌ Use this inside a ticket.", ephemeral=True)
        return
    await send_transcript(inter.channel, inter.user)
    await inter.response.send_message("✅ Ticket closing…", ephemeral=True)
    await asyncio.sleep(1)
    await inter.channel.delete(reason="Closed by admin")

# -----------------------------
# REMOVE COOLDOWN (admin)
# -----------------------------
@tree.command(name="remove_cooldown",
              description="Remove a user’s ticket cooldown (Admin Only)")
@app_commands.describe(user="Select the user")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def remove_cd(inter: discord.Interaction, user: discord.Member):
    if not inter.user.guild_permissions.manage_channels:
        await inter.response.send_message("❌ You lack permission.", ephemeral=True)
        return
    if user.id in cooldowns:
        del cooldowns[user.id]
        await inter.response.send_message(f"✅ Cooldown removed for {user.mention}", ephemeral=True)
    else:
        await inter.response.send_message(f"ℹ️ {user.mention} has no cooldown.", ephemeral=True)

# -----------------------------
# DYNAMIC APP MANAGEMENT
# -----------------------------
@tree.command(name="addapp", description="Add a new app (Admin Only)")
@app_commands.describe(name="App name with emoji", link="App link")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def addapp(inter, name: str, link: str):
    if not inter.user.guild_permissions.manage_channels:
        await inter.response.send_message("❌ Permission denied.", ephemeral=True)
        return
    apps[name] = link
    await inter.response.send_message(f"✅ Added **{name}**", ephemeral=True)

@tree.command(name="removeapp", description="Remove an app (Admin Only)")
@app_commands.describe(name="Exact app name")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def removeapp(inter, name: str):
    if not inter.user.guild_permissions.manage_channels:
        await inter.response.send_message("❌ Permission denied.", ephemeral=True)
        return
    if name in apps:
        del apps[name]
        await inter.response.send_message(f"🗑️ Removed **{name}**", ephemeral=True)
    else:
        await inter.response.send_message("❌ App not found.", ephemeral=True)

@tree.command(name="listapps", description="List current apps")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def listapps(inter):
    if not apps:
        await inter.response.send_message("No apps available.", ephemeral=True)
        return
    msg = "\n".join(f"{n}" for n in apps.keys())
    await inter.response.send_message(embed=make_embed("📱 Available Apps", msg), ephemeral=True)

# -----------------------------
# MESSAGE LISTENER
# -----------------------------
@bot.event
async def on_message(message):
    if message.author.bot or not message.channel.name.startswith("ticket-"):
        return
    content = message.content.strip().lower()
    for app_name, app_link in apps.items():
        if content == app_name.lower() or content in app_name.lower():
            async with message.channel.typing():
                await asyncio.sleep(1)
            await message.channel.send(
                embed=make_embed(
                    f"✅ Access Approved — {app_name}",
                    f"Here’s your resource link for **{app_name}**:\n{app_link}\n\n"
                    "If you’re satisfied, press the button below to close this ticket."
                ),
                view=CloseTicketView()
            )
            return
    await message.channel.send(embed=make_embed("❌ Invalid App Name",
        "Please type the exact name from the list above."))

# -----------------------------
# BACKGROUND TASKS
# -----------------------------
@tasks.loop(minutes=10)
async def check_inactivity():
    for guild in bot.guilds:
        for ch in guild.text_channels:
            if ch.name.startswith("ticket-"):
                last = await ch.history(limit=1).flatten()
                if last:
                    diff = (datetime.utcnow() - last[0].created_at).total_seconds()
                    if diff > 900:   # 15 min
                        await send_transcript(ch, bot.user)
                        await ch.delete(reason="Auto closed after inactivity")

# -----------------------------
# BOT READY
# -----------------------------
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    try:
        await tree.sync(guild=discord.Object(id=GUILD_ID))
        print("✅ Slash commands synced.")
    except Exception as e:
        print("Sync error:", e)
    bot.add_view(CloseTicketView())     # persistent buttons
    check_inactivity.start()

# -----------------------------
# KEEP-ALIVE SERVER (Render)
# -----------------------------
app = Flask("")

@app.route('/')
def home():
    return "TICKET BOT V2 is running!"

def run_flask():
    app.run(host="0.0.0.0", port=8080)

def keep_alive():
    threading.Thread(target=run_flask).start()

keep_alive()

# -----------------------------
# RUN BOT
# -----------------------------
bot.run(os.getenv("TOKEN"))
