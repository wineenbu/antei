# ======================
# /remind
# ======================
@tree.command(name="remind", description="リマインダーを設定します")
@app_commands.describe(
    mode="at=日時指定 / weekly=毎週",
    time="日時 or HH:MM",
    destination="送信先",
    channel="送信先チャンネル（destination=channel の場合）",
    role="メンションするロール（任意）",
    weekday="weekly の場合のみ選択",
    message="内容"
)
@app_commands.choices(
    mode=[
        app_commands.Choice(name="日時指定", value="at"),
        app_commands.Choice(name="毎週", value="weekly"),
    ],
    destination=[
        app_commands.Choice(name="DM", value="dm"),
        app_commands.Choice(name="チャンネル", value="channel"),
    ],
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
async def remind(
    interaction: discord.Interaction,
    mode: app_commands.Choice[str],
    time: str,
    destination: app_commands.Choice[str],
    message: str,
    channel: discord.TextChannel | None = None,
    role: discord.Role | None = None,
    weekday: app_commands.Choice[str] | None = None,  # ← Choice に変更
):
    # チャンネル必須チェック
    if destination.value == "channel" and channel is None:
        await interaction.response.send_message(
            "❌ destination=channel の場合は channel を指定してください。", ephemeral=True
        )
        return

    # weekly の曜日チェック
    selected_weekday = weekday.value if weekday else None
    if mode.value == "weekly" and not selected_weekday:
        await interaction.response.send_message(
            "❌ 毎週モードの場合は曜日を選択してください", ephemeral=True
        )
        return

    # 時刻計算
    try:
        if mode.value == "at":
            dt = parse_datetime_input(time)
        else:
            # weekly
            hhmm = datetime.datetime.strptime(time, "%H:%M")
            now = datetime.datetime.now()
            target = now.replace(hour=hhmm.hour, minute=hhmm.minute, second=0, microsecond=0)

            weekday_map = {"mon":0,"tue":1,"wed":2,"thu":3,"fri":4,"sat":5,"sun":6}
            wd = weekday_map.get(selected_weekday)
            if wd is None:
                raise ValueError("曜日選択が不正です")

            days_ahead = (wd - target.weekday()) % 7
            if days_ahead == 0 and target <= now:
                days_ahead = 7
            dt = target + datetime.timedelta(days=days_ahead)

        remind_ts = (dt - datetime.timedelta(hours=9)).timestamp()
    except Exception as e:
        await interaction.response.send_message(f"❌ {e}", ephemeral=True)
        return

    # 保存
    entry = {
        "uid": str(uuid.uuid4()),
        "user_id": interaction.user.id,
        "time": remind_ts,
        "message": message,
        "destination": destination.value
    }
    if destination.value == "channel":
        entry["channel_id"] = channel.id
    if role:
        entry["role_id"] = role.id
    if mode.value == "weekly":
        entry["repeat"] = "weekly"
        entry["weekday"] = selected_weekday

    reminders = load_reminders()
    reminders.append(entry)
    save_reminders(reminders)

    # 設定完了メッセージ
    content = f"✅ リマインダー設定完了\n🕒 {format_jst(dt)}\n💬 {message}"
    if role:
        content = f"<@&{role.id}> " + content
    content += f"\n📍 {'DM' if destination.value=='dm' else f'#{channel.name}'}"

    try:
        if destination.value == "channel":
            await channel.send(content=content)
        else:
            user = await client.fetch_user(interaction.user.id)
            await user.send(content=content)
    except Exception as e:
        print("設定完了送信失敗:", e)

    await interaction.response.send_message(content="リマインダーを設定しました！", ephemeral=True)
