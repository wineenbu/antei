import os
import json
import datetime
import discord
from discord.ext import commands, tasks

# === Discord Bot Setup ===
TOKEN = os.environ.get("DISCORD_TOKEN")

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

DATA_FILE = "reminders.json"

def load_reminders():
    try:
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return []

def save_reminders(reminders):
    with open(DATA_FILE, "w") as f:
        json.dump(reminders, f)

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    check_reminders.start()

@tasks.loop(seconds=30)
async def check_reminders():
    now = datetime.datetime.utcnow().timestamp()
    reminders = load_reminders()
    remaining = []
    for r in reminders:
        if r["time"] <= now:
            try:
                user = await bot.fetch_user(r["user_id"])
                await user.send(f"🔔 リマインド: {r['message']}")
            except Exception as e:
                print(f"❌ Failed to send reminder: {e}")
        else:
            remaining.append(r)
    save_reminders(remaining)

@bot.command()
async def remindat(ctx, time_str: str, *, message: str):
    """
    例: !remindat 2025-10-28T08:30 リハーサル
    """
    try:
        # ISO形式で受け取り、JSTからUTCに変換
        remind_time = datetime.datetime.fromisoformat(time_str)
        remind_time_utc = remind_time - datetime.timedelta(hours=9)
        reminders = load_reminders()
        reminders.append({
            "user_id": ctx.author.id,
            "time": remind_time_utc.timestamp(),
            "message": message
        })
        save_reminders(reminders)
        await ctx.send(f"⏰ {time_str} にリマインドを設定しました！")
    except Exception as e:
        await ctx.send(f"⚠️ 時刻形式が正しくありません: {e}")

if __name__ == "__main__":
    # Flaskサーバー不要なので keep_alive() は削除
    bot.run(TOKEN)
