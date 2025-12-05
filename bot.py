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

# === 日時パース ===
def parse_datetime_input(time_str: str) -> datetime.datetime:
    formats = [
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M",
        "%Y/%m/%d %H:%M",
        "%m/%d %H:%M",
        "%H:%M",
    ]

    now = datetime.datetime.now()
    for fmt in formats:
        try:
            dt = datetime.datetime.strptime(time_str, fmt)
            if fmt == "%m/%d %H:%M":
                dt = dt.replace(year=now.year)
            elif fmt == "%H:%M":
                dt = dt.replace(year=now.year, month=now.month, day=now.day)
            return dt
        except ValueError:
            continue

    raise ValueError("対応していない日時形式です。例: 2025-11-08T09:30")

# === JST表示 ===
def format_jst_datetime(dt: datetime.datetime) -> str:
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
                        print(f"⚠️ Channel not found: {r}")

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

                # --- weeklyリマインダーの再設定 ---
                if r.get("repeat") == "weekly":
                    next_time = datetime.datetime.fromtimestamp(r["time"], datetime.UTC) + datetime.timedelta(days=7)
                    r["time"] = next_time.timestamp()
                    remaining.append(r)

            except Exception as e:
                print(f"❌ Failed to send reminder: {e}")

        else:
            # まだ時間前のものは残す
            remaining.append(r)

    save_reminders(remaining)

# === Bot起動時イベント ===
@client.event
async def on_ready():
    print(f"✅ Logged in as {client.user}")
    await tree.sync()
    print("🌐 Slash commands synced.")
    check_reminders.start()


# === /remindat コマンド（DMに送信） ===
@tree.command(
    name="remindat",
    description="指定時刻にリマインドを設定します (例: 2025-11-08T09:30 リハーサル)"
)
async def remindat(interaction: discord.Interaction, time_str: str, message: str):
    try:
        # ユーザー入力をパース
        remind_time = parse_datetime_input(time_str)
        remind_time_utc = remind_time - datetime.timedelta(hours=9)  # JST→UTC

        # JSONに保存
        reminders = load_reminders()
        reminders.append({
            "user_id": interaction.user.id,
            "time": remind_time_utc.timestamp(),
            "message": message,
            "type": "dm"
        })
        save_reminders(reminders)

        formatted_time = format_jst_datetime(remind_time_utc)

        # DM用Embedを作成
        embed = discord.Embed(
            title="⏰ リマインダーを設定しました！",
            color=discord.Color.green(),
            description=f"{interaction.user.mention} さんのリマインドです。"
        )
        embed.add_field(name="🕒 時刻", value=formatted_time, inline=False)
        embed.add_field(name="💬 内容", value=message, inline=False)

        # DM送信
        user = await client.fetch_user(interaction.user.id)
        await user.send(embed=embed)

        # 確認メッセージ（チャンネルには表示しない、ephemeral）
        await interaction.response.send_message(
            f"✅ DMにリマインダーを設定しました！",
            ephemeral=True
        )

    except Exception as e:
        await interaction.response.send_message(
            f"⚠️ 時刻形式が正しくありません: z{e}", ephemeral=True
        )


# === /remindhere（チャンネルに送る） ===
@tree.command(name="remindhere", description="このチャンネルにリマインダーを設定します")
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
        await interaction.response.send_message(
            f"⚠️ 時刻形式が正しくありません: {e}", ephemeral=True
        )

# === 追加: /remindeveryweek ===
@tree.command(
    name="remindeveryweek",
    description="毎週リマインドを設定します (例: fri 18:00 ジム)"
)
async def remindeveryweek(interaction: discord.Interaction, weekday: str, time_str: str, message: str):
    try:
        weekdays = {"mon":0,"tue":1,"wed":2,"thu":3,"fri":4,"sat":5,"sun":6}
        if weekday.lower() not in weekdays:
            await interaction.response.send_message("⚠️ 曜日は mon,tue,wed,thu,fri,sat,sun から選んでください", ephemeral=True)
            return

        # 時刻解析
        base_time = parse_datetime_input(time_str)
        now = datetime.datetime.now()
        target = now.replace(hour=base_time.hour, minute=base_time.minute, second=0, microsecond=0)

        # 次の該当曜日に調整
        while target.weekday() != weekdays[weekday.lower()] or target <= now:
            target += datetime.timedelta(days=1)

        remind_time_utc = target - datetime.timedelta(hours=9)

        reminders = load_reminders()
        reminders.append({
            "user_id": interaction.user.id,
            "time": remind_time_utc.timestamp(),
            "message": message,
            "type": "weekly",
            "weekday": weekday.lower()
        })
        save_reminders(reminders)

        formatted_time = format_jst_datetime(remind_time_utc)
        await interaction.response.send_message(
            f"📅 毎週リマインドを設定しました！ ({weekday} {formatted_time})",
            ephemeral=True
        )

    except Exception as e:
        await interaction.response.send_message(f"⚠️ エラー: {e}", ephemeral=True)


# === 追加: リスト表示 ===
@tree.command(name="remindlist", description="自分のリマインド一覧を表示します")
async def remindlist(interaction: discord.Interaction):
    reminders = load_reminders()
    user_reminders = [r for r in reminders if r["user_id"] == interaction.user.id]

    if not user_reminders:
        await interaction.response.send_message("🔍 リマインドはありません。", ephemeral=True)
        return

    text = ""
    for i, r in enumerate(user_reminders):
        dt = datetime.datetime.fromtimestamp(r["time"], datetime.UTC)
        text += f"ID: `{i}` | {format_jst_datetime(dt)} | {r['message']} | type: {r['type']}\n"

    await interaction.response.send_message(text, ephemeral=True)


# === 追加: リマインド削除 ===
@tree.command(name="reminddelete", description="リマインドを削除します (IDは /remindlist で確認)")
async def reminddelete(interaction: discord.Interaction, reminder_id: int):
    reminders = load_reminders()
    user_reminders = [r for r in reminders if r["user_id"] == interaction.user.id]

    try:
        target = user_reminders[reminder_id]
        reminders.remove(target)
        save_reminders(reminders)
        await interaction.response.send_message(
            f"🗑 リマインドを削除しました: `{target['message']}`",
            ephemeral=True
        )
    except:
        await interaction.response.send_message("⚠️ IDが無効です。`/remindlist` で確認してください。", ephemeral=True)

# === メイン処理 ===
if __name__ == "__main__":
    from threading import Thread

    def run_flask():
        app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

    Thread(target=run_flask).start()

    client.run(TOKEN)
