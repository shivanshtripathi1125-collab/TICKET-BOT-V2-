import os
import sqlite3
import asyncio
from datetime import datetime, timedelta, timezone
import json
import discord
from discord.ext import commands, tasks
from discord import app_commands
from flask import Flask
import threading

# ---------------- CONFIG ----------------
TOKEN = os.getenv("BOT_TOKEN")  # Add this in Render Environment
GUILD_ID = 1424815111541096530
OWNER_ID = int(os.getenv("OWNER_ID", "0"))  # You’ll fill this too
LOG_CHANNEL_ID = 1434241829733404692
COOLDOWN_HOURS = int(os.getenv("COOLDOWN_HOURS", "48"))
INACTIVITY_CLOSE_MINUTES = int(os.getenv("INACTIVITY_CLOSE_MINUTES", "15"))
DB_PATH = os.getenv("DB_PATH", "tickets.db")
TICKET_CATEGORY_NAME = os.getenv("TICKET_CATEGORY_NAME", "Tickets")

# Apps list (JSON or fallback)
APPS_JSON = os.getenv("PREMIUM_APPS_JSON")
if APPS_JSON:
    PREMIUM_APPS = json.loads(APPS_JSON)
else:
    PREMIUM_APPS = [
        {"name": "YouTube Premium", "link": "https://example.com/youtube", "aliases": ["yt", "youtube"]},
        {"name": "Spotify Premium", "link": "https://example.com/spotify", "aliases": ["spotify"]},
    ]

# ---------------- INTENTS & BOT ----------------
intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.messages = True
intents.message_content = True
bot = commands.Bot(command_prefix="/", intents=intents)
tree = bot.tree

# ---------------- DATABASE ----------------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS cooldowns (
        user_id INTEGER PRIMARY KEY,
        last_ticket_ts TEXT
    )""")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS tickets (
        channel_id INTEGER PRIMARY KEY,
        owner_id INTEGER,
        created_ts TEXT,
        last_activity_ts TEXT,
        is_closed INTEGER DEFAULT 0
    )""")
    conn.commit()
    conn.close()

def set_cooldown(user_id, ts):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT OR REPLACE INTO cooldowns VALUES (?, ?)", (user_id, ts.isoformat()))
    conn.commit(); conn.close()

def get_cooldown(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor(); c.execute("SELECT last_ticket_ts FROM cooldowns WHERE user_id=?", (user_id,))
    r = c.fetchone(); conn.close()
    return datetime.fromisoformat(r[0]) if r else None

def remove_cooldown(user_id):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM cooldowns WHERE user_id=?", (user_id,))
    conn.commit(); conn.close()

def add_ticket(channel_id, owner_id):
    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT OR REPLACE INTO tickets VALUES (?, ?, ?, ?, 0)", (channel_id, owner_id, now, now))
    conn.commit(); conn.close()

def update_ticket_activity(channel_id):
    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE tickets SET last_activity_ts=? WHERE channel_id=?", (now, channel_id))
    conn.commit(); conn.close()

def mark_ticket_closed(channel_id):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE tickets SET is_closed=1 WHERE channel_id=?", (channel_id,))
    conn.commit(); conn.close()

def get_ticket(channel_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor(); c.execute("SELECT * FROM tickets WHERE channel_id=?", (channel_id,))
    r = c.fetchone(); conn.close(); return r

def get_all_open_tickets():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor(); c.execute("SELECT * FROM tickets WHERE is_closed=0")
    r = c.fetchall(); conn.close(); return r

# ---------------- HELPERS ----------------
def make_embed(title=None, description=None, color=0x2F3136):
    return discord.Embed(title=title, description=description, color=color, timestamp=datetime.now(timezone.utc))

def find_app_by_message(content: str):
    c = content.lower().strip()
    for app in PREMIUM_APPS:
        if c == app["name"].lower() or c in [a.lower() for a in app.get("aliases", [])]:
            return app
    return None

# ---------------- TRANSCRIPT ----------------
async def send_transcript(channel: discord.TextChannel):
    try:
        log_channel = channel.guild.get_channel(LOG_CHANNEL_ID)
        if not log_channel:
            return
        lines = []
        async for msg in channel.history(limit=None, oldest_first=True):
            ts = msg.created_at.strftime("%Y-%m-%d %H:%M:%S")
            text = msg.clean_content or ""
            lines.append(f"[{ts}] {msg.author}: {text}")
        txt = "\n".join(lines)
        name = f"transcript_{channel.name}_{channel.id}.txt"
        with open(name, "w", encoding="utf-8") as f:
            f.write(txt)
        await log_channel.send(embed=make_embed("Ticket Closed", f"Transcript for {channel.mention}"), file=discord.File(name))
        os.remove(name)
    except Exception as e:
        print("Transcript error:", e)

# ---------------- UI ----------------
class CloseTicketView(discord.ui.View):
    def __init__(self, channel_id): 
        super().__init__(timeout=None)
        self.channel_id = channel_id
    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.danger)
    async def close(self, interaction, button):
        t = get_ticket(self.channel_id)
        if not t: 
            await interaction.response.send_message(embed=make_embed("Error", "Ticket not found."), ephemeral=True)
            return
        owner = t[1]
        if interaction.user.id not in (owner, OWNER_ID):
            await interaction.response.send_message(embed=make_embed("Denied", "You cannot close this ticket."), ephemeral=True)
            return
        channel = interaction.channel
        await send_transcript(channel)
        mark_ticket_closed(channel.id)
        await channel.delete(reason="Closed by button")

class CooldownRefreshView(discord.ui.View):
    def __init__(self, uid): super().__init__(timeout=900); self.uid=uid
    @discord.ui.button(label="Refresh Countdown", style=discord.ButtonStyle.primary)
    async def refresh(self, i, b):
        if i.user.id!=self.uid: 
            await i.response.send_message(embed=make_embed("Denied","Not your cooldown."),ephemeral=True); return
        last=get_cooldown(self.uid)
        if not last: 
            await i.response.send_message(embed=make_embed("Ready","You can create ticket now."),ephemeral=True);return
        remain=last+timedelta(hours=COOLDOWN_HOURS)-datetime.now(timezone.utc)
        if remain.total_seconds()<=0:
            await i.response.send_message(embed=make_embed("Ready","You can create ticket now."),ephemeral=True);return
        await i.response.edit_message(embed=make_embed("Cooldown active",f"Wait **{str(remain).split('.')[0]}**"),view=self)

# ---------------- EVENTS ----------------
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    try:
        await tree.sync(guild=discord.Object(id=GUILD_ID))
        print("Commands synced.")
    except Exception as e:
        print("Sync error:", e)
    inactivity_check.start()

# ---------------- COMMANDS ----------------
@tree.command(name="ticket", description="Create a support ticket", guild=discord.Object(id=GUILD_ID))
async def create_ticket(i: discord.Interaction):
    await i.response.defer(ephemeral=True)
    user=i.user
    last=get_cooldown(user.id)
    if last and (datetime.now(timezone.utc)-last)<timedelta(hours=COOLDOWN_HOURS):
        remain=(last+timedelta(hours=COOLDOWN_HOURS))-datetime.now(timezone.utc)
        e=make_embed("Cooldown active",f"You can create a ticket again in **{str(remain).split('.')[0]}**.")
        try:
            await user.send(embed=e, view=CooldownRefreshView(user.id))
            await i.followup.send(embed=make_embed("Cooldown","I've DM'd you the info."),ephemeral=True)
        except:
            await i.followup.send(embed=make_embed("DM Blocked","Enable DMs from server members."),ephemeral=True)
        return
    g=i.guild
    cat=discord.utils.get(g.categories,name=TICKET_CATEGORY_NAME) or await g.create_category(TICKET_CATEGORY_NAME)
    overwrites={
        g.default_role:discord.PermissionOverwrite(read_messages=False),
        user:discord.PermissionOverwrite(read_messages=True,send_messages=True),
        g.me:discord.PermissionOverwrite(read_messages=True)
    }
    ch=await cat.create_text_channel(f"ticket-{user.name}",overwrites=overwrites)
    add_ticket(ch.id,user.id); set_cooldown(user.id,datetime.now(timezone.utc))
    apps="\n".join([f"• **{a['name']}**" for a in PREMIUM_APPS])
    desc=f"Hello {user.mention}! Welcome.\n\nHere are our premium apps:\n{apps}\n\nType the app name to request it."
    await ch.send(embed=make_embed("Welcome to your ticket",desc))
    await i.followup.send(embed=make_embed("Ticket created",f"{ch.mention}"),ephemeral=True)

@tree.command(name="ticket_close", description="Close the current ticket", guild=discord.Object(id=GUILD_ID))
async def close_ticket(i: discord.Interaction):
    ch=i.channel; t=get_ticket(ch.id)
    if not t:
        await i.response.send_message(embed=make_embed("Not a ticket","Use inside a ticket."),ephemeral=True);return
    if i.user.id not in (t[1], OWNER_ID):
        await i.response.send_message(embed=make_embed("Denied","Only owner can close."),ephemeral=True);return
    await send_transcript(ch); mark_ticket_closed(ch.id)
    await ch.delete(reason=f"Closed by {i.user}")
    await i.response.send_message(embed=make_embed("Closed","Ticket deleted."),ephemeral=True)

@tree.command(name="remove_cooldown", description="Owner removes a user's cooldown", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(user="User to clear cooldown for")
async def remove_cd(i: discord.Interaction, user: discord.Member):
    if i.user.id!=OWNER_ID:
        await i.response.send_message(embed=make_embed("Denied","Owner only."),ephemeral=True);return
    remove_cooldown(user.id)
    await i.response.send_message(embed=make_embed("Done",f"Removed cooldown for {user.mention}"),ephemeral=True)

# ---------------- MESSAGE ----------------
@bot.event
async def on_message(msg: discord.Message):
    if msg.author.bot: return
    t=get_ticket(msg.channel.id)
    if t: update_ticket_activity(msg.channel.id)
    if t:
        app=find_app_by_message(msg.content)
        if app:
            member=msg.author
            ask=make_embed("YouTube Verification",f"{member.mention}, please upload a **screenshot** proving you are subscribed to our **YouTube channel**.\n\nYou have 2 minutes.",0x5865F2)
            await msg.channel.send(embed=ask)
            def check(m): 
                return m.author==member and m.channel==msg.channel and m.attachments and m.attachments[0].content_type.startswith("image/")
            try:
                reply=await bot.wait_for("message",timeout=120,check=check)
                await msg.channel.send(embed=make_embed("Verified","Screenshot received. Sending your app link...",0x57F287))
                embed=make_embed(f"{app['name']} — Download",f"Here’s your link:\n{app['link']}")
                view=CloseTicketView(msg.channel.id)
                await member.send(embed=embed)
                await msg.channel.send(embed=make_embed("Sent","Check your DMs. Close ticket below or it will auto-close."),view=view)
                update_ticket_activity(msg.channel.id)
            except asyncio.TimeoutError:
                await msg.channel.send(embed=make_embed("Timeout","No screenshot received. Type the app name again to retry.",0xED4245))
    await bot.process_commands(msg)

# ---------------- BACKGROUND TASK ----------------
@tasks.loop(minutes=1)
async def inactivity_check():
    now=datetime.now(timezone.utc)
    for ch_id,owner,created,last,closed in get_all_open_tickets():
        if closed: continue
        if (now-datetime.fromisoformat(last))>timedelta(minutes=INACTIVITY_CLOSE_MINUTES):
            for g in bot.guilds:
                ch=g.get_channel(ch_id)
                if ch:
                    await send_transcript(ch)
                    await ch.delete(reason="Auto closed due to inactivity")
                    mark_ticket_closed(ch_id)

# ---------------- KEEP ALIVE ----------------
app=Flask("keepalive")
@app.route("/")
def home(): return "TICKET BOT V2 is running!"
def run_flask(): app.run(host="0.0.0.0", port=int(os.getenv("PORT","5000")))

# ---------------- MAIN ----------------
if __name__=="__main__":
    init_db()
    threading.Thread(target=run_flask).start()
    bot.run(TOKEN)
