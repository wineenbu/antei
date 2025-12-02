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

# === 日時パース関数（柔軟対応）===
def parse_datetime_input(time_str: str) -> datetime.datetime:
    """ユーザー入力の日時文字列を自動判定してdatetimeに変換"""
    formats = [
        "%Y-%m-%dT%H:%M",   # 例: 2025-11-08T09:30
        "%Y-%m-%d %H:%M",   # 例: 2025-11-08 09:30
        "%Y/%m/%d %H:%M",   # 例: 2025/11/08 09:30
        "%m/%d %H:%M",      # 例: 11/08 09:30（今年として扱う）
        "%H:%M",            # 例: 09:30（今日として扱う）
    ]

    now = datetime.datetime.now()
    for fmt in formats:
        try:
            dt = datetime.datetime.strptime(time_str, fmt)
            # 年月日補完
            if fmt == "%m/%d %H:%M":
                dt = dt.replace(year=now.year)
            elif fmt == "%H:%M":
                dt = dt.replace(year=now.year, month=now.month, day=now.day)
            return dt
        except ValueError:
            continue

    raise ValueError("対応していない日時形式です。例: 2025-11-08T09:30 または 11/08 09:30")

# === 日付フォーマット関数（見やすい表示）===
def format_jst_datetime(dt: datetime.datetime) -> str:
    """UTC日時をJSTに変換して日本語フォーマットで返す"""
    jst = dt + datetime.timedelta(hours=9)
    return jst.strftime("%Y年%m月%d日 %H時%M分")

# === リマインダー処理 ===
@tasks.loop(seconds=30)
async def check_reminders():
    now = datetime.datetime.now(datetime.UTC).timestamp()
    reminders = load_reminders()
    remaining = []

    for r in reminders:
        if r["time"] <= now:
            try:
                # --- 必要な処理（これが無いと try が不完全扱いで SyntaxError） ---
                remind_dt = datetime.datetime.fromtimestamp(r["time"], datetime.UTC)
                formatted_time = format_jst_datetime(remind_dt)

                # --- チャンネル宛て ---
                if r.get("type") == "channel":
                    channel = client.get_channel(r["channel_id"])
                    if channel:
                        embed = discord.Embed(
                            title="🔔 リマインダー",
                            color=discord.Color.green()
                        )
                        embed.add_field(name="🕒 時刻", value=formatted_time, inline=False)
                        embed.add_field(name="💬 内容", value=r["message"], inline=False)
                        embed.set_footer(text=f"設定者: <@{r['user_id']}>")

                        await channel.send(embed=embed)
                    else:
                        print(f"⚠️ Channel not found for reminder: {r}")

                # --- DM宛て ---
                else:
                    user = await client.fetch_user(r["user_id"])

                    embed = discord.Embed(
                        title="🔔 リマインダー",
                        description=f"<@{r['user_id']}> さんへのリマインドです！",
                        color=discord.Color.green()
                    )
                    embed.add_field(name="🕒 時刻", value=formatted_time, inline=False)
                    embed.add_field(name="💬 内容", value=r["message"], inline=False)

                    await user.send(embed=embed)

            except Exception as e:
                print(f"❌ Failed to send reminder: {e}")

        else:
            remaining.append(r)

    save_reminders(remaining)

# === Bot起動時イベント ===
@client.event
async def on_ready():
    print(f"✅ Logged in as {client.user}")
    await tree.sync()  # スラッシュコマンド同期
    print("🌐 Slash commands synced.")
    check_reminders.start()

# === /remindat コマンド（DMに送信） ===
@tree.command(name="remindat", description="指定時刻にリマインドを設定します (例: 2025-11-08T09:30 リハーサル)")
async def remindat(interaction: discord.Interaction, time_str: str, message: str):
    try:
        remind_time = parse_datetime_input(time_str)
        remind_time_utc = remind_time - datetime.timedelta(hours=9)  # JST→UTC変換

        reminders = load_reminders()
        reminders.append({
            "user_id": interaction.user.id,
            "time": remind_time_utc.timestamp(),
            "message": message,
            "type": "dm"
        })
        save_reminders(reminders)

        formatted_time = format_jst_datetime(remind_time_utc)

        await interaction.response.send_message(
            f"⏰ {formatted_time} に以下の内容でDMリマインドを設定しました！\n\n💬 {message}",
            ephemeral=True
        )

    except Exception as e:
        await interaction.response.send_message(f"⚠️ 時刻形式が正しくありません: {e}", ephemeral=True)

# === /remindhere コマンド（チャンネルに送信） ===
@tree.command(name="remindhere", description="このチャンネルにリマインドを設定します (例: 2025-11-08T09:30 ミーティング)")
async def remindhere(interaction: discord.Interaction, time_str: str, message: str):
    try:
        remind_time = parse_datetime_input(time_str)
        remind_time_utc = remind_time - datetime.timedelta(hours=9)

        reminders = load_reminders()
        reminders.append({
            "user_id": interaction.user.id,
            "channel_id": interaction.channel.id,
            "time": remind_time_utc.timestamp(),
            "message": message,
            "type": "channel"
        })
        save_reminders(reminders)

        formatted_time = format_jst_datetime(remind_time_utc)

        embed = discord.Embed(
            title="📅 リマインダーを設定しました！",
            color=discord.Color.blue()
        )
        embed.add_field(name="🕒 日時", value=formatted_time, inline=False)
        embed.add_field(name="💬 内容", value=message, inline=False)
        embed.set_footer(text=f"設定者: {interaction.user.display_name}")

        await interaction.response.send_message(embed=embed)
    except Exception as e:
        await interaction.response.send_message(f"⚠️ 時刻形式が正しくありません: {e}", ephemeral=True)

# === メイン処理 ===
if __name__ == "__main__":
    from threading import Thread

    def run_flask():
        app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

    Thread(target=run_flask).start()

    client.run(TOKEN)
