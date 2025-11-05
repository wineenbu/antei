import os
import json
import datetime
import asyncio
import discord
from discord.ext import tasks
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
client = discord.Client(intents=intents)
tree = discord.app_commands.CommandTree(client)

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
    now = datetime.datetime.now(datetime.UTC).timestamp()
    reminders = load_reminders()
    remaining = []
    for r in reminders:
        if r["time"] <= now:
            try:
                user = await client.fetch_user(r["user_id"])
                await user.send(f"🔔 リマインド: {r['message']}")
            except Exception as e:
                print(f"❌ Failed to send reminder: {e}")
        else:
            remaining.append(r)
    save_reminders(remaining)

@client.event
async def on_ready():
    print(f"✅ Logged in as {client.user}")
    await tree.sync()  # スラッシュコマンドをDiscordに同期
    print("🌐 Slash commands synced.")
    check_reminders.start()

# === /remindat コマンド ===
@tree.command(name="remindat", description="指定時刻にリマインドを設定します (例: 2025-10-28T08:30 リハーサル)")
async def remindat(interaction: discord.Interaction, time_str: str, message: str):
    try:
        remind_time = datetime.datetime.fromisoformat(time_str)
        remind_time_utc = remind_time - datetime.timedelta(hours=9)  # JST→UTC変換
        reminders = load_reminders()
        reminders.append({
            "user_id": interaction.user.id,
            "time": remind_time_utc.timestamp(),
            "message": message
        })
        save_reminders(reminders)
        await interaction.response.send_message(f"⏰ {time_str} にリマインドを設定しました！", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"⚠️ 時刻形式が正しくありません: {e}", ephemeral=True)

# === /remindhere コマンド ===
@tree.command(name="remindhere", description="このチャンネルにリマインドを設定します (例: 2025-10-28T08:30 ミーティング)")
async def remindhere(interaction: discord.Interaction, time_str: str, message: str):
    try:
        remind_time = datetime.datetime.fromisoformat(time_str)
        remind_time_utc = remind_time - datetime.timedelta(hours=9)  # JST→UTC変換
        reminders = load_reminders()
        reminders.append({
            "user_id": interaction.user.id,
            "channel_id": interaction.channel.id,  # チャンネルIDも保存
            "time": remind_time_utc.timestamp(),
            "message": message,
            "type": "channel"  # 種別を追加
        })
        save_reminders(reminders)
        await interaction.response.send_message(f"📢 {time_str} にこのチャンネルでリマインドを設定しました！", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"⚠️ 時刻形式が正しくありません: {e}", ephemeral=True)

# === メイン処理 ===
if __name__ == "__main__":
    from threading import Thread

    def run_flask():
        app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

    Thread(target=run_flask).start()

    client.run(TOKEN)
