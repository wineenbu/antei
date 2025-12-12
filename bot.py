# bot.py
# /remind_list 削除ボタン付き UI を含む 完成版

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
tree = app_commands.CommandTree(client)

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
    # dt は UTC timezone を想定しているので JST に調整して表示
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

        # 念のため型を揃える
        try:
            r_time = float(r["time"])
        except Exception:
            # 無効な時間データはスキップ
            continue

        if r_time <= now_ts:
            try:
                remind_dt = datetime.datetime.fromtimestamp(r_time, datetime.timezone.utc)
                formatted_time = format_jst_datetime(remind_dt)

                # --- チャンネル宛て ---
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
                # --- DM宛て ---
                else:
                    try:
                        user = await client.fetch_user(r["user_id"])
                        embed = discord.Embed(title="🔔 リマインダー", color=discord.Color.green())
                        embed.add_field(name="🕒 時刻", value=formatted_time, inline=False)
                        embed.add_field(name="💬 内容", value=r.get("message", "（内容なし）"), inline=False)
                        await user.send(embed=embed)
                    except Exception as e:
                        print(f"❌ Failed to send DM for {r.get('uid')}: {e}")

                # --- weekly の場合は次週へ再登録 ---
                if r.get("repeat") == "weekly":
                    next_time = remind_dt + datetime.timedelta(days=7)
                    r["time"] = next_time.timestamp()
                    remaining.append(r)
                else:
                    # 一回きりなら何もしない（＝削除される）
                    pass

            except Exception as e:
                print(f"❌ Failed to send reminder {r.get('uid')}: {e}")
                # 失敗しても残す（次回再挑戦）
                remaining.append(r)
        else:
            remaining.append(r)

    save_reminders(remaining)

# === 削除ボタン用の動的ボタン実装（UID 個別） ===
class DeleteButton(discord.ui.Button):
    def __init__(self, uid: str, owner_id: int):
        # custom_id を設定（メッセージをまたいだ永続化の準備。ただし簡易運用）
        super().__init__(label="❌ 削除", style=discord.ButtonStyle.danger, custom_id=f"delete_{uid}")
        self.uid = uid
        self.owner_id = owner_id

    async def callback(self, interaction: discord.Interaction):
        # 設定者のみ削除可能
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("⚠️ 操作する権限がありません。", ephemeral=True)
            return

        reminders = load_reminders()
        found = False
        for r in reminders:
            if r.get("uid") == self.uid and r.get("user_id") == interaction.user.id:
                r["deleted"] = True
                found = True
                break

        if not found:
            await interaction.response.send_message("⚠️ リマインダーが見つからないか、既に削除されています。", ephemeral=True)
            return

        save_reminders(reminders)

        # ボタンを無効化してメッセージを更新
        self.disabled = True
        try:
            embed = interaction.message.embeds[0] if interaction.message and interaction.message.embeds else None
            if embed:
                embed.set_footer(text="🗑 削除済み")
                await interaction.response.edit_message(embed=embed, view=interaction.message.components[0].to_view() if hasattr(interaction.message.components[0], 'to_view') else None)
            else:
                await interaction.response.edit_message(content="🗑 削除済み", view=None)
        except Exception:
            # 上書きに失敗しても応答を返す
            await interaction.response.send_message("🗑 削除しました。", ephemeral=True)

# ReminderDeleteView は DeleteButton を追加するだけ
class ReminderDeleteView(discord.ui.View):
    def __init__(self, uid: str, owner_id: int):
        super().__init__(timeout=None)
        self.add_item(DeleteButton(uid, owner_id))

# === イベント ===
@client.event
async def on_ready():
    print(f"Logged in as {client.user}")
    # sync は on_ready 内で安全に実行
    try:
        await tree.sync()
        print("Slash commands synced.")
    except Exception as e:
        print(f"⚠️ tree.sync failed: {e}")

    if not check_reminders.is_running():
        check_reminders.start()

# === /remind コマンド（remindat + remindhere を統合） ===
# 送信先をボタンで選択するフローにする
@tree.command(name="remind", description="リマインダーを設定します（時間指定または曜日毎）")
@app_commands.describe(
    mode="'at' = 時間指定, 'weekly' = 毎週指定",
    time_str="時刻または日時（at の場合: 例 2025-11-08T09:30 または 09:30）",
    weekday="weekly の場合の曜日（mon/tue/...）",
    message="リマインド内容"
)
@app_commands.choices(
    mode=[
        app_commands.Choice(name="時間指定 (一回)", value="at"),
        app_commands.Choice(name="毎週指定", value="weekly"),
    ]
)
async def remind(interaction: discord.Interaction, mode: app_commands.Choice[str], message: str, time_str: str, weekday: str = None):
    """
    使い方例:
    /remind mode:at time_str:2025-11-08T09:30 message:テスト
    /remind mode:weekly time_str:14:30 weekday:fri message:毎週リマインド
    """

    # 時刻パース（at と weekly で使う）
    if mode.value == "at":
        try:
            dt = parse_datetime_input(time_str)
        except ValueError as e:
            await interaction.response.send_message(f"❌ 時刻パースエラー: {e}", ephemeral=True)
            return
        # adjust to UTC for storage (we treat input as JST-naive)
        remind_time_utc = dt - datetime.timedelta(hours=9)
        timestamp = remind_time_utc.timestamp()
    else:  # weekly
        # validate weekday (allow mon/tue/... or Japanese)
        jp_map = {"月":"mon","火":"tue","水":"wed","木":"thu","金":"fri","土":"sat","日":"sun"}
        w = weekday.lower()
        if w in jp_map:
            w = jp_map[w]
        if w not in {"mon","tue","wed","thu","fri","sat","sun"}:
            await interaction.response.send_message("⚠️ 曜日は mon/tue/... または 月/火/... で指定してください。", ephemeral=True)
            return

        # parse time_str as HH:MM (local JST)
        try:
            t = datetime.datetime.strptime(time_str, "%H:%M")
        except ValueError:
            await interaction.response.send_message("❌ 時刻形式は HH:MM（例: 14:30）で指定してください。", ephemeral=True)
            return

        weekday_num = {"mon":0,"tue":1,"wed":2,"thu":3,"fri":4,"sat":5,"sun":6}[w]
        now = datetime.datetime.now()
        target = now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
        days_ahead = (weekday_num - now.weekday()) % 7
        if days_ahead == 0 and target <= now:
            days_ahead = 7
        target += datetime.timedelta(days=days_ahead)
        remind_time_utc = target - datetime.timedelta(hours=9)
        timestamp = remind_time_utc.timestamp()

    # 送信先選択ビュー（ボタン）
    class SendTargetView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=60)
            self.chosen = None  # ('dm' or 'channel')

        @discord.ui.button(label="DM に送る", style=discord.ButtonStyle.primary)
        async def send_dm(self, interaction2: discord.Interaction, button: discord.ui.Button):
            self.chosen = "dm"
            await interaction2.response.defer()
            self.stop()

        @discord.ui.button(label="このチャンネルに送る", style=discord.ButtonStyle.success)
        async def send_here(self, interaction2: discord.Interaction, button: discord.ui.Button):
            self.chosen = "channel"
            await interaction2.response.defer()
            self.stop()

    view = SendTargetView()
    await interaction.response.send_message("📩 送信先を選択してください（60秒でタイムアウト）", view=view, ephemeral=True)
    await view.wait()

    if view.chosen is None:
        await interaction.followup.send("⌛ 送信先が選ばれなかったためキャンセルしました。", ephemeral=True)
        return

    # prepare reminder record
    uid = str(uuid.uuid4())
    reminder = {
        "uid": uid,
        "user_id": interaction.user.id,
        "time": timestamp,
        "message": message,
        "type": "channel" if view.chosen == "channel" else "dm"
    }
    if view.chosen == "channel":
        reminder["channel_id"] = interaction.channel.id
    if mode.value == "weekly":
        reminder["repeat"] = "weekly"
        reminder["weekday"] = w  # mon/tue/...

    reminders = load_reminders()
    reminders.append(reminder)
    save_reminders(reminders)

    # confirm
    if mode.value == "at":
        dt_disp = format_jst_datetime(datetime.datetime.fromtimestamp(timestamp, datetime.timezone.utc))
        await interaction.followup.send(f"✅ リマインダーを設定しました（{dt_disp} に {'このチャンネル' if view.chosen=='channel' else 'DM'} に送信）", ephemeral=True)
    else:
        await interaction.followup.send(f"✅ 毎週 {w} に {'このチャンネル' if view.chosen=='channel' else 'DM'} でリマインドします。", ephemeral=True)

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
        await interaction.response.send_message("📭 現在、設定されているリマインダーはありません。", ephemeral=True)
        return

    weekday_jp = {
        "mon": "月曜日", "tue": "火曜日", "wed": "水曜日",
        "thu": "木曜日", "fri": "金曜日", "sat": "土曜日", "sun": "日曜日"
    }

    await interaction.response.send_message(f"📋 あなたのリマインダーは **{len(user_reminders)} 件** あります。", ephemeral=True)

    for r in user_reminders:
        dt = datetime.datetime.fromtimestamp(r["time"], datetime.timezone.utc)
        formatted_time = format_jst_datetime(dt)
        repeat = r.get("repeat", "なし")

        embed = discord.Embed(title="⏰ リマインダー", color=discord.Color.blurple())
        embed.add_field(name="🕒 時刻", value=formatted_time, inline=False)
        embed.add_field(name="🔁 繰り返し", value=repeat, inline=False)
        embed.add_field(name="💬 内容", value=r.get("message", "（内容なし）"), inline=False)

        # 毎週リマインドなら曜日表示
        if r.get("repeat") == "weekly":
            w = r.get("weekday", "?")
            embed.add_field(name="📅 曜日", value=weekday_jp.get(w, "不明"), inline=False)

        view = ReminderDeleteView(r["uid"], user_id)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

# === 起動（Render 互換） ===
if __name__ == "__main__":
    import threading

    # --- Discord Bot をバックグラウンドで起動 ---
    def run_bot():
        client.run(TOKEN)

    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()

    # --- Flask（Render が期待する Web サーバー）をメインスレッドで起動 ---
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
