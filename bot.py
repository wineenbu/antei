import os
import json
import datetime
import asyncio
import discord
from discord.ext import commands, tasks
from flask import Flask

# === Flaskサーバー（Render用）===
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is running!"

# === Discord Bot Setup ===
TOKEN = os.environ.get("DISCORD_TOKEN")
if TOKEN is None:
    raise ValueError("DISCORD_TOKEN が設定されていません。Renderの環境変数を確認してください。")

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

DATA_FILE = "reminders.json"

# === JSONファイルの読み書き ===
def load_reminders():
    try:
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return []

def save_reminders(reminders):
    with open(DATA_FILE, "w") as f:
        json.dump(reminders, f)

# === リマインダー処理 ===
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

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    check_reminders.start()

@bot.command()
async def remindat(ctx, time_str: str, *, message: str):
    """
    例: !remindat 2025-10-28T08:30 リハーサル
    """
    try:
        remind_time = datetime.datetime.fromisoformat(time_str)
        remind_time_utc = remind_time - datetime.timedelta(hours=9)  # JST→UTC
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

# === メイン処理 ===
if __name__ == "__main__":
    # FlaskとBotを同時に動かす
    from threading import Thread

    def run_flask():
        app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

    Thread(target=run_flask).start()

    bot.run(TOKEN)
