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
    updated = []

    for r in reminders:
        # 時間になった
        if r["time"] <= now:
            try:
                remind_dt = datetime.datetime.fromtimestamp(r["time"], datetime.UTC)
                formatted_time = format_jst_datetime(remind_dt)

                # 送信（DM or channel）
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

            # 🔁 繰り返し処理
            if "repeat" in r:
                next_time = datetime.datetime.fromtimestamp(r["time"], datetime.UTC)

                if r["repeat"] == "daily":
                    next_time += datetime.timedelta(days=1)
                elif r["repeat"] == "weekly":
                    next_time += datetime.timedelta(weeks=1)
                elif r["repeat"] == "monthly":
                    # 月を +1
                    y = next_time.year
                    m = next_time.month + 1
                    if m > 12:
                        y += 1
                        m = 1
                    next_time = next_time.replace(year=y, month=m)

                r["time"] = next_time.timestamp()
                updated.append(r)  # 繰り返しなので残す

        else:
            updated.append(r)  # 時間前のものは残す

    save_reminders(updated)

# === Slash Commands ===

# === リマインダー削除用 Select メニュー ===
class ReminderDeleteSelect(discord.ui.Select):
    def __init__(self, user, reminders):
        self.user = user
        self.reminders = reminders

        options = []
        for i, r in enumerate(reminders, start=1):
            dt = datetime.datetime.fromtimestamp(r["time"], datetime.UTC)
            label = f"#{i} {format_jst_datetime(dt)}"
            description = r["message"][:50]
            options.append(discord.SelectOption(label=label, description=description, value=str(i-1)))

        super().__init__(
            placeholder="削除するリマインダーを選択してください",
            min_values=1,
            max_values=len(options),  # 複数選択可能にする
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        selected_indices = [int(i) for i in self.values]
        selected_reminders = [self.reminders[i] for i in selected_indices]

        # JSON から削除
        all_data = load_reminders()
        for reminder in selected_reminders:
            all_data.remove(reminder)
        save_reminders(all_data)

        embed = discord.Embed(
            title="🗑️ リマインダー削除完了",
            color=discord.Color.red()
        )

        for reminder in selected_reminders:
            dt = datetime.datetime.fromtimestamp(reminder["time"], datetime.UTC)
            embed.add_field(name=f"🕒 時刻: {format_jst_datetime(dt)}", value=reminder["message"], inline=False)

        await interaction.response.edit_message(
            content="選択したリマインダーが削除されました。",
            embed=embed,
            view=None
        )


class ReminderDeleteView(discord.ui.View):
    def __init__(self, user, reminders):
        super().__init__(timeout=60)
        self.add_item(ReminderDeleteSelect(user, reminders))

# === /reminddelete（複数選択削除） ===
@tree.command(name="reminddelete", description="選択メニューでリマインダーを削除します")
async def reminddelete(interaction: discord.Interaction):
    reminders = [r for r in load_reminders() if r["user_id"] == interaction.user.id]

    if not reminders:
        await interaction.response.send_message(
            "📭 現在あなたのリマインダーはありません。",
            ephemeral=True
        )
        return

    view = ReminderDeleteView(interaction.user, reminders)

    embed = discord.Embed(
        title="🗑️ リマインダー削除",
        description="下のメニューから削除するリマインダーを選択してください。",
        color=discord.Color.orange()
    )

    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

# === 繰り返しリマインダー設定 ===
@tree.command(
    name="remindrepeat",
    description="繰り返しリマインダーを設定します（daily/weekly/monthly）"
)
async def remindrepeat(
    interaction: discord.Interaction,
    repeat_type: str,
    time_str: str,
    message: str
):
    """
    repeat_type: "daily", "weekly", "monthly"
    time_str: "09:00" / "2025-12-10T09:00" など
    """

    repeat_type = repeat_type.lower()
    if repeat_type not in ["daily", "weekly", "monthly"]:
        await interaction.response.send_message(
            "⚠️ repeat_type は daily / weekly / monthly のいずれかです。",
            ephemeral=True
        )
        return

    try:
        base_time = parse_datetime_input(time_str)
    except Exception as e:
        await interaction.response.send_message(f"❌ 時刻形式エラー: {e}", ephemeral=True)
        return

    base_time_utc = base_time - datetime.timedelta(hours=9)

    reminders = load_reminders()
    reminders.append({
        "user_id": interaction.user.id,
        "time": base_time_utc.timestamp(),
        "message": message,
        "type": "dm",       # repeat は DM 送信とする
        "repeat": repeat_type
    })
    save_reminders(reminders)

    formatted = format_jst_datetime(base_time_utc)

    await interaction.response.send_message(
        f"🔁 {repeat_type} リマインダーを設定しました！\n⏰ {formatted}\n💬 {message}",
        ephemeral=True
    )

# === メイン処理 ===
if __name__ == "__main__":
    from threading import Thread

    def run_flask():
        app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

    Thread(target=run_flask).start()

    client.run(TOKEN)
