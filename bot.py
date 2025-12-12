# /remindlist 削除ボタン付き UI を含む bot.py
# 完全版コード

import os
import json
import datetime
import uuid
import discord
from discord import app_commands
from discord.ext import tasks
from flask import Flask

# === Flaskサーバー（Render動作用）===
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is running!"

# === Discord Bot ===
TOKEN = os.environ.get("DISCORD_TOKEN")
if TOKEN is None:
    raise ValueError("DISCORD_TOKEN が設定されていません。Render環境変数を確認してください。")

intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = discord.app_commands.CommandTree(client)

DATA_FILE = "reminders.json"


# === JSON読み書き ===
def load_reminders():
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return []

def save_reminders(reminders):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(reminders, f, ensure_ascii=False, indent=2)


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


# === JST 表示形式 ===
def format_jst_datetime(dt: datetime.datetime) -> str:
    if dt.tzinfo is None:
        dt_utc = dt.replace(tzinfo=datetime.timezone.utc)
    else:
        dt_utc = dt.astimezone(datetime.timezone.utc)
    jst = dt_utc + datetime.timedelta(hours=9)
    return jst.strftime("%Y年%m月%d日 %H時%M分")


# === リマインダー監視タスク ===
@tasks.loop(seconds=30)
async def check_reminders():
    now_ts = datetime.datetime.now(datetime.timezone.utc).timestamp()
    reminders = load_reminders()
    remaining = []

    for r in reminders:
        if r.get("deleted", False):
            continue

        try:
            r_time = float(r["time"])
        except Exception:
            continue

        if r_time <= now_ts:
            try:
                remind_dt = datetime.datetime.fromtimestamp(r_time, datetime.timezone.utc)
                formatted_time = format_jst_datetime(remind_dt)

                if r.get("type") == "channel" and r.get("channel_id"):
                    channel = client.get_channel(r["channel_id"])
                    if channel:
                        embed = discord.Embed(title="🔔 リマインダー", color=discord.Color.green())
                        embed.add_field(name="🕒 時刻", value=formatted_time, inline=False)
                        embed.add_field(name="💬 内容", value=r.get("message", "（内容なし）"), inline=False)
                        embed.set_footer(text=f"設定者: <@{r['user_id']}>")
                        await channel.send(embed=embed)
                    else:
                        print(f"⚠️ Channel not found for reminder: {r.get('uid')}")
                else:
                    user = await client.fetch_user(r["user_id"])
                    embed = discord.Embed(title="🔔 リマインダー", color=discord.Color.green())
                    embed.add_field(name="🕒 時刻", value=formatted_time, inline=False)
                    embed.add_field(name="💬 内容", value=r.get("message", "（内容なし）"), inline=False)
                    await user.send(embed=embed)

                if r.get("repeat") == "weekly":
                    next_time = remind_dt + datetime.timedelta(days=7)
                    r["time"] = next_time.timestamp()
                    remaining.append(r)
                else:
                    pass

            except Exception as e:
                print(f"❌ Failed to send reminder {r.get('uid')}: {e}")
                remaining.append(r)
        else:
            remaining.append(r)

    save_reminders(remaining)


# === 削除ボタン用 View ===
class ReminderDeleteView(discord.ui.View):
    def __init__(self, uid: str, owner_id: int):
        # timeout=None にしてボタンが無期限で残るようにする（必要であれば調整）
        super().__init__(timeout=None)
        self.uid = uid
        self.owner_id = owner_id

    @discord.ui.button(label="❌ 削除", style=discord.ButtonStyle.danger)
    async def delete_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # ボタンを押した人が設定者であることを確認
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("⚠️ 操作する権限がありません。", ephemeral=True)
            return

        reminders = load_reminders()
        found = False
        for r in reminders:
            if r.get("uid") == self.uid and r["user_id"] == interaction.user.id:
                r["deleted"] = True
                found = True
                break

        if not found:
            await interaction.response.send_message("⚠️ リマインダーが見つからないか、既に削除されています。", ephemeral=True)
            return

        save_reminders(reminders)

        # ボタンを無効化してメッセージを更新
        button.disabled = True
        # 編集用の embed 表示を変える（元の embed を取得して上書き）
        try:
            embed = interaction.message.embeds[0] if interaction.message and interaction.message.embeds else None
            if embed:
                embed.set_footer(text="🗑 削除済み")
                await interaction.response.edit_message(embed=embed, view=self)
            else:
                await interaction.response.edit_message(content="🗑 削除済み", view=self)
        except Exception:
            # 編集に失敗しても削除は完了
            await interaction.response.send_message("🗑 削除しました。", ephemeral=True)


# === イベント ===
@client.event
async def on_ready():
    print(f"Logged in as {client.user}")
    await tree.sync()
    print("Slash commands synced.")
    if not check_reminders.is_running():
        check_reminders.start()


# === /remindat ===
@tree.command(name="remindat", description="指定時刻にDMでリマインドを設定します")
async def remindat(interaction: discord.Interaction, time_str: str, message: str):
    try:
        remind_time = parse_datetime_input(time_str)
    except ValueError as e:
        embed = discord.Embed(
            title="❌ 日時エラー",
            description=f"{e}",
            color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    remind_time_utc = remind_time - datetime.timedelta(hours=9)

    reminders = load_reminders()
    uid = str(uuid.uuid4())
    reminders.append({
        "uid": uid,
        "user_id": interaction.user.id,
        "time": remind_time_utc.timestamp(),
        "message": message,
        "type": "dm"
    })
    save_reminders(reminders)

    embed = discord.Embed(title="⏰ リマインダー設定完了", color=discord.Color.green())
    embed.add_field(name="📅 リマインド日時", value=f"{remind_time.strftime('%Y/%m/%d %H:%M')} (JST)", inline=False)
    embed.add_field(name="💬 メッセージ", value=message, inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


# === /remindhere ===
@tree.command(name="remindhere", description="このチャンネルにリマインドを設定します")
async def remindhere(interaction: discord.Interaction, time_str: str, message: str):
    try:
        remind_time = parse_datetime_input(time_str)
    except ValueError as e:
        embed = discord.Embed(
            title="❌ 日時エラー",
            description=f"{e}",
            color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    remind_time_utc = remind_time - datetime.timedelta(hours=9)

    reminders = load_reminders()
    uid = str(uuid.uuid4())
    reminders.append({
        "uid": uid,
        "user_id": interaction.user.id,
        "channel_id": interaction.channel.id,
        "time": remind_time_utc.timestamp(),
        "message": message,
        "type": "channel"
    })
    save_reminders(reminders)

    embed = discord.Embed(title="📌 チャンネルリマインダー設定完了", color=discord.Color.green())
    embed.add_field(name="📅 リマインド日時", value=f"{remind_time.strftime('%Y/%m/%d %H:%M')} (JST)", inline=False)
    embed.add_field(name="💬 メッセージ", value=message, inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


# === /remindeveryweek ===
@tree.command(name="remindeveryweek", description="毎週決まった曜日と時刻にリマインドします")
@app_commands.describe(
    weekday="曜日を選択してください",
    time_str="時刻（例: 14:30）",
    message="リマインド内容",
    here="このチャンネルに送る場合は true"
)
@app_commands.choices(
    weekday=[
        app_commands.Choice(name="月曜日", value="mon"),
        app_commands.Choice(name="火曜日", value="tue"),
        app_commands.Choice(name="水曜日", value="wed"),
        app_commands.Choice(name="木曜日", value="thu"),
        app_commands.Choice(name="金曜日", value="fri"),
        app_commands.Choice(name="土曜日", value="sat"),
        app_commands.Choice(name="日曜日", value="sun"),
    ]
)
async def remindeveryweek(
    interaction: discord.Interaction,
    weekday: app_commands.Choice[str],
    time_str: str,
    message: str,
    here: bool = False
):

    # 曜日テーブル
    weekday_map = {
        "mon": 0, "tue": 1, "wed": 2, "thu": 3,
        "fri": 4, "sat": 5, "sun": 6
    }

    # 今日の日付
    now = datetime.datetime.now()
    target_weekday = weekday_map[weekday.value]

    # 入力された時刻を datetime に変換
    try:
        t = datetime.datetime.strptime(time_str, "%H:%M")
    except ValueError:
        await interaction.response.send_message(
            "❌ 時刻の形式が正しくありません。（例: 14:30）",
            ephemeral=True
        )
        return

    # 初回の実行時間を計算
    first_time = now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)

    # 目標曜日までの日数を計算
    days_ahead = (target_weekday - now.weekday()) % 7
    if days_ahead == 0 and first_time <= now:
        days_ahead = 7

    first_time += datetime.timedelta(days=days_ahead)

    # UTC へ変換
    first_time_utc = first_time - datetime.timedelta(hours=9)

    uid = str(uuid.uuid4())
    reminders = load_reminders()
    reminders.append({
        "uid": uid,
        "user_id": interaction.user.id,
        "channel_id": interaction.channel.id if here else None,
        "time": first_time_utc.timestamp(),
        "message": message,
        "repeat": "weekly",
        "weekday": weekday.value,
        "type": "channel" if here else "dm"
    })
    save_reminders(reminders)

    embed = discord.Embed(title="🔁 毎週リマインド設定", color=discord.Color.green())
    embed.add_field(name="📅 曜日", value=weekday.name, inline=False)
    embed.add_field(name="🕒 時刻", value=time_str, inline=False)
    embed.add_field(name="💬 内容", value=message, inline=False)
    embed.add_field(name="📍 送信先", value="このチャンネル" if here else "DM", inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)

# === /remind_list コマンド ===
@tree.command(name="remind_list", description="設定されているリマインダーを一覧表示します")
async def remind_list(interaction: discord.Interaction):
    reminders = load_reminders()
    user_id = interaction.user.id

    user_reminders = [
        r for r in reminders
        if r.get("user_id") == user_id and not r.get("deleted", False)
    ]

    if not user_reminders:
        await interaction.response.send_message(
            "📭 現在、設定されているリマインダーはありません。",
            ephemeral=True
        )
        return

    weekday_jp = {
        "mon": "月曜日", "tue": "火曜日", "wed": "水曜日",
        "thu": "木曜日", "fri": "金曜日", "sat": "土曜日", "sun": "日曜日"
    }

    await interaction.response.send_message(
        f"📋 あなたのリマインダーは **{len(user_reminders)} 件** あります。",
        ephemeral=True
    )

    for r in user_reminders:
        dt = datetime.datetime.fromtimestamp(r["time"], datetime.UTC)
        formatted_time = format_jst_datetime(dt)
        repeat = r.get("repeat", "なし")

        embed = discord.Embed(title="⏰ リマインダー", color=discord.Color.blurple())
        embed.add_field(name="🕒 時刻", value=formatted_time, inline=False)
        embed.add_field(name="🔁 繰り返し", value=repeat, inline=False)
        embed.add_field(name="💬 内容", value=r["message"], inline=False)

        # 毎週リマインドなら曜日表示
        if r.get("repeat") == "weekly":
            w = r.get("weekday", "?")
            embed.add_field(name="📅 曜日", value=weekday_jp.get(w, "不明"), inline=False)

        view = ReminderDeleteView(r["uid"], user_id)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)


# === 起動（Render 互換）===
if __name__ == "__main__":
    import threading

    # --- Discord Bot をバックグラウンドで起動 ---
    def run_bot():
        client.run(TOKEN)

    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()

    # --- Flask（Render が必須とする Web サーバー）をメインスレッドで起動 ---
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
