#!/usr/bin/env python3
import os
import json
import datetime
import uuid
import discord
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
    """
    入力はローカル（JST）として解釈する関数。
    受け付けるフォーマットの例:
      2025-11-08T09:30
      2025-11-08 09:30
      2025/11/08 09:30
      11/08 09:30   -> 年は現在年を使う
      09:30         -> 今日の 09:30 として扱う
    """
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
    """
    dt は UTC の datetime（tzinfo 付きでも無しでも可）を想定。
    表示は日本標準時（JST: UTC+9）で行う。
    """
    if dt.tzinfo is None:
        # treat as UTC timestamp naive -> convert
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
            # malformed entry -> skip (or you might want to remove)
            continue

        if r_time <= now_ts:
            try:
                # リマインダー送信
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
                        # チャンネルが見つからない場合はスキップして残す or 削除の選択肢あり
                        # 今回は削除せず remaining に入れない（=消さない） -> 次回も試す
                else:
                    user = await client.fetch_user(r["user_id"])
                    embed = discord.Embed(title="🔔 リマインダー", color=discord.Color.green())
                    embed.add_field(name="🕒 時刻", value=formatted_time, inline=False)
                    embed.add_field(name="💬 内容", value=r.get("message", "（内容なし）"), inline=False)
                    await user.send(embed=embed)

                # repeat handling
                if r.get("repeat") == "weekly":
                    # 次の週にスケジュールを移動
                    next_time = datetime.datetime.fromtimestamp(r_time, datetime.timezone.utc) + datetime.timedelta(days=7)
                    r["time"] = next_time.timestamp()
                    remaining.append(r)
                else:
                    # one-shot は消す（何もしない）
                    pass

            except Exception as e:
                print(f"❌ Failed to send reminder {r.get('uid')}: {e}")
                # エラーが起きたリマインダーは念のため remaining に戻す（retry）
                remaining.append(r)
        else:
            remaining.append(r)

    save_reminders(remaining)


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
        remind_time = parse_datetime_input(time_str)  # JST naive datetime
    except ValueError as e:
        await interaction.response.send_message(f"❌ 日時の解析に失敗しました: {e}", ephemeral=True)
        return

    # 保存は UTC に変換（JST -> UTC）
    remind_time_utc = remind_time - datetime.timedelta(hours=9)

    reminders = load_reminders()
    reminders.append({
        "uid": str(uuid.uuid4()),
        "user_id": interaction.user.id,
        "time": remind_time_utc.timestamp(),
        "message": message,
        "type": "dm"
    })
    save_reminders(reminders)

    await interaction.response.send_message("⏰ リマインダーを設定しました！ /remindlist で確認できます。", ephemeral=True)


# === /remindhere ===
@tree.command(name="remindhere", description="このチャンネルにリマインドします")
async def remindhere(interaction: discord.Interaction, time_str: str, message: str):
    try:
        remind_time = parse_datetime_input(time_str)
    except ValueError as e:
        await interaction.response.send_message(f"❌ 日時の解析に失敗しました: {e}", ephemeral=True)
        return

    remind_time_utc = remind_time - datetime.timedelta(hours=9)

    reminders = load_reminders()
    reminders.append({
        "uid": str(uuid.uuid4()),
        "user_id": interaction.user.id,
        "channel_id": interaction.channel.id,
        "time": remind_time_utc.timestamp(),
        "message": message,
        "type": "channel"
    })
    save_reminders(reminders)

    await interaction.response.send_message("📌 このチャンネルにリマインドを設定しました。", ephemeral=True)


# === /remindeveryweek ===
@tree.command(name="remindeveryweek", description="毎週リマインドします（日本語の曜日指定OK）")
async def remindeveryweek(
    interaction: discord.Interaction,
    weekday: str,
    time_str: str,
    message: str,
    here: bool = False
):
    # 日本語 → 英語曜日マップ
    jp_weekdays = {
        "月": "mon", "月曜": "mon", "月曜日": "mon",
        "火": "tue", "火曜": "tue", "火曜日": "tue",
        "水": "wed", "水曜": "wed", "水曜日": "wed",
        "木": "thu", "木曜": "thu", "木曜日": "thu",
        "金": "fri", "金曜": "fri", "金曜日": "fri",
        "土": "sat", "土曜": "sat", "土曜日": "sat",
        "日": "sun", "日曜": "sun", "日曜日": "sun",
    }

    en_weekdays = {"mon","tue","wed","thu","fri","sat","sun"}

    w = weekday.lower()
    if w in jp_weekdays:
        w = jp_weekdays[w]
    elif w not in en_weekdays:
        await interaction.response.send_message(
            "⚠️ 曜日は「月 / 月曜 / 月曜日 / mon」などで指定してください。",
            ephemeral=True
        )
        return

    weekday_num = {"mon":0,"tue":1,"wed":2,"thu":3,"fri":4,"sat":5,"sun":6}[w]

    try:
        base_time = parse_datetime_input(time_str)
    except ValueError as e:
        await interaction.response.send_message(f"❌ 日時の解析に失敗しました: {e}", ephemeral=True)
        return

    # 現在のローカル時刻（JST）を基準に target を作る（JST naive）
    now_jst = datetime.datetime.now()
    target = now_jst.replace(
        hour=base_time.hour,
        minute=base_time.minute,
        second=0,
        microsecond=0
    )

    # 今日の指定時刻が未来かどうかで振り分け
    if target.weekday() != weekday_num:
        # 曜日が違う → 次に来る該当曜日へ進める
        days_to_add = (weekday_num - target.weekday()) % 7
        if days_to_add == 0:
            days_to_add = 7
        target = target + datetime.timedelta(days=days_to_add)
    else:
        # 同じ曜日なら、時間が未来なら今日、過去なら来週
        if target <= now_jst:
            target = target + datetime.timedelta(days=7)

    # 保存は UTC に変換（JST -> UTC）
    remind_time_utc = target - datetime.timedelta(hours=9)

    reminders = load_reminders()
    data = {
        "uid": str(uuid.uuid4()),
        "user_id": interaction.user.id,
        "time": remind_time_utc.timestamp(),
        "message": message,
        "repeat": "weekly",
        "type": "channel" if here else "dm"
    }
    if here:
        data["channel_id"] = interaction.channel.id

    reminders.append(data)
    save_reminders(reminders)

    formatted = format_jst_datetime(remind_time_utc if isinstance(remind_time_utc, datetime.datetime) else datetime.datetime.fromtimestamp(remind_time_utc, datetime.timezone.utc))
    # (format_jst_datetime は UTC を期待するので上記の変換で安全に渡す)

    embed = discord.Embed(
        title="⏳ 毎週リマインダーを設定しました！",
        color=discord.Color.green()
    )
    embed.add_field(name="📅 曜日", value=weekday, inline=False)
    embed.add_field(name="🕒 時刻（JST）", value=formatted, inline=False)
    embed.add_field(name="💬 内容", value=message, inline=False)
    embed.add_field(
        name="📍 場所",
        value=("このチャンネルに投稿" if here else "DMで通知"),
        inline=False
    )
    embed.set_footer(text=f"設定者: {interaction.user.name}")

    await interaction.response.send_message(embed=embed, ephemeral=True)


# === /remindlist ===
@tree.command(name="remindlist", description="リマインド一覧を表示")
async def remindlist(interaction: discord.Interaction):
    reminders = load_reminders()
    mine = [r for r in reminders if r["user_id"] == interaction.user.id and not r.get("deleted", False)]

    if not mine:
        await interaction.response.send_message("🔍 リマインダーはありません。", ephemeral=True)
        return

    text = ""
    for r in mine:
        try:
            dt = datetime.datetime.fromtimestamp(r["time"], datetime.timezone.utc)
            text += f"UID: `{r['uid']}` | {format_jst_datetime(dt)} | {r.get('message','（内容なし）')} | {r.get('repeat','once')}\n"
        except Exception:
            text += f"UID: `{r.get('uid','?')}` | (日時不正) | {r.get('message','（内容なし）')}\n"

    await interaction.response.send_message(text, ephemeral=True)


# === /reminddelete ===
@tree.command(name="reminddelete", description="リマインドを削除する")
async def reminddelete(interaction: discord.Interaction, uid: str):
    reminders = load_reminders()
    found = False
    for r in reminders:
        if r.get("uid") == uid and r["user_id"] == interaction.user.id:
            r["deleted"] = True
            found = True

    save_reminders(reminders)

    if found:
        await interaction.response.send_message(f"🗑 削除しました: `{uid}`", ephemeral=True)
    else:
        await interaction.response.send_message("⚠️ UIDが見つかりません。/remindlist を確認してください", ephemeral=True)


# === 起動（Flask を別スレッドで立てる）===
if __name__ == "__main__":
    from threading import Thread

    def run_flask():
        port = int(os.environ.get("PORT", 5000))
        app.run(host="0.0.0.0", port=port)

    Thread(target=run_flask, daemon=True).start()

    client.run(TOKEN)
