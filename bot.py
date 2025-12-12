# bot.py (Flask + Remind unified command)
# --- imports ---
import os
import datetime
import asyncio
import discord
from discord.ext import commands, tasks
from discord import app_commands
from flask import Flask
from threading import Thread

# ---------------------
# Flask keep_alive
# ---------------------
app = Flask('')

@app.route('/')
def home():
    return "I'm alive"

def keep_alive():
    Thread(target=lambda: app.run(host='0.0.0.0', port=8080)).start()

# ---------------------
# Bot settings
# ---------------------
intents = discord.Intents.default()
intents.message_content = False

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# ---------------------
# In-memory reminder storage (Method A)
# ---------------------
# r = { "time": timestamp, "user_id": int, "channel_id": int or None,
#       "message": str, "repeat": "once" | "weekly", "weekday": int or None }
reminders = []

# ---------------------
# Utils
# ---------------------
def parse_datetime_input(text: str) -> datetime.datetime:
    try:
        return datetime.datetime.strptime(text, "%Y/%m/%d %H:%M")
    except:
        raise ValueError("日時は YYYY/MM/DD HH:MM の形式で入力して下さい")

async def send_reminder(r):
    channel = None
    if r["channel_id"]:
        channel = bot.get_channel(r["channel_id"])
    else:
        # DM
        user = await bot.fetch_user(r["user_id"])
        channel = await user.create_dm()

    await channel.send(f"🔔 **リマインダー**\n{r['message']}")

# ---------------------
# Background Loop
# ---------------------
@tasks.loop(seconds=5)
async def reminder_loop():
    global reminders
    now = datetime.datetime.now(datetime.UTC).timestamp()

    remaining = []

    for r in reminders:
        if r["time"] <= now:
            try:
                await send_reminder(r)
            except Exception as e:
                print(f"Failed to send: {e}")

            # weekly repeat
            if r.get("repeat") == "weekly":
                r["time"] += 7 * 24 * 60 * 60
                remaining.append(r)
        else:
            remaining.append(r)

    reminders = remaining

# ---------------------
# Unified command: /remind
# → オプションで選択
#   - 通知形式: "at", "after", "weekly"
#   - DM or チャンネル
# ---------------------
@tree.command(name="remind", description="リマインダーを設定します")
@app_commands.describe(
    mode="通知方法を選択",
    datetime_text="YYYY/MM/DD HH:MM の形式",
    minutes="今から何分後か",
    weekday="毎週の曜日",
    message="リマインド内容",
    deliver="DM または このチャンネル"
)
@app_commands.choices(mode=[
    app_commands.Choice(name="日時指定", value="at"),
    app_commands.Choice(name="○分後", value="after"),
    app_commands.Choice(name="毎週", value="weekly"),
])
@app_commands.choices(deliver=[
    app_commands.Choice(name="DM", value="dm"),
    app_commands.Choice(name="このチャンネル", value="channel"),
])
@app_commands.choices(weekday=[
    app_commands.Choice(name="月", value=0),
    app_commands.Choice(name="火", value=1),
    app_commands.Choice(name="水", value=2),
    app_commands.Choice(name="木", value=3),
    app_commands.Choice(name="金", value=4),
    app_commands.Choice(name="土", value=5),
    app_commands.Choice(name="日", value=6),
])
async def remind(
    interaction: discord.Interaction,
    mode: app_commands.Choice[str],
    message: str,
    deliver: app_commands.Choice[str],
    datetime_text: str | None = None,
    minutes: int | None = None,
    weekday: app_commands.Choice[int] | None = None,
):
    await interaction.response.defer(ephemeral=True)

    # ------------------
    # Parse notification time
    # ------------------
    if mode.value == "at":
        if not datetime_text:
            return await interaction.followup.send("❌ 日時を入力してください")
        dt = parse_datetime_input(datetime_text).replace(tzinfo=datetime.UTC)
        timestamp = dt.timestamp()
        repeat = "once"
        weekday_val = None

    elif mode.value == "after":
        if minutes is None:
            return await interaction.followup.send("❌ 分数を入力してください")
        dt = datetime.datetime.now(datetime.UTC) + datetime.timedelta(minutes=minutes)
        timestamp = dt.timestamp()
        repeat = "once"
        weekday_val = None

    elif mode.value == "weekly":
        if weekday is None or not datetime_text:
            return await interaction.followup.send("❌ 曜日と時刻を入力してください")

        # datetime_text → HH:MM のみ利用
        try:
            t = datetime.datetime.strptime(datetime_text, "%H:%M")
        except:
            return await interaction.followup.send("❌ 時刻は HH:MM 形式")

        now = datetime.datetime.now(datetime.UTC)
        target = now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)

        # 曜日調整
        diff = (weekday.value - target.weekday()) % 7
        if diff == 0 and target.timestamp() < now.timestamp():
            diff = 7
        target = target + datetime.timedelta(days=diff)

        timestamp = target.timestamp()
        repeat = "weekly"
        weekday_val = weekday.value

    # ------------------
    # Where to send
    # ------------------
    if deliver.value == "dm":
        channel_id = None
    else:
        channel_id = interaction.channel.id

    # ------------------
    # Save reminder
    # ------------------
    reminders.append({
        "time": timestamp,
        "user_id": interaction.user.id,
        "channel_id": channel_id,
        "message": message,
        "repeat": repeat,
        "weekday": weekday_val,
    })

    await interaction.followup.send("✅ リマインドを設定しました！", ephemeral=True)

# ---------------------
# /remindlist
# ---------------------
@tree.command(name="remindlist", description="設定中のリマインダーを表示")
async def remindlist(interaction: discord.Interaction):
    if not reminders:
        return await interaction.response.send_message("リマインダーはありません", ephemeral=True)

    embed = discord.Embed(title="⏰ リマインダー一覧", color=0x00ffcc)

    for i, r in enumerate(reminders):
        t = datetime.datetime.fromtimestamp(r["time"], datetime.UTC)
        repeat = "毎週" if r["repeat"] == "weekly" else "1回"
        wd = ["月","火","水","木","金","土","日"]
        wd_text = f"（{wd[r['weekday']]}）" if r.get("weekday") is not None else ""
        place = "DM" if r["channel_id"] is None else "このチャンネル"

        embed.add_field(
            name=f"#{i+1}",
            value=f"**内容:** {r['message']}\n**日時:** {t} {wd_text}\n**繰り返し:** {repeat}\n**送信先:** {place}",
            inline=False
        )

    await interaction.response.send_message(embed=embed, ephemeral=True)

# ---------------------
# Startup
# ---------------------
@bot.event\async def on_ready():
    print(f"Logged in as {bot.user}")
    reminder_loop.start()
    try:
        synced = await tree.sync()
        print(f"Synced {len(synced)} commands")
    except Exception as e:
        print(e)

keep_alive()
bot.run(os.getenv("TOKEN"))