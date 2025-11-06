@tree.command(name="remindhere", description="このチャンネルにリマインドを設定します (例: 2025-10-28T08:30 ミーティング)")
async def remindhere(interaction: discord.Interaction, time_str: str, message: str):
    try:
        remind_time = datetime.datetime.fromisoformat(time_str)
        remind_time_utc = remind_time - datetime.timedelta(hours=9)  # JST→UTC変換
        reminders = load_reminders()
        reminders.append({
            "user_id": interaction.user.id,
            "channel_id": interaction.channel.id,
            "time": remind_time_utc.timestamp(),
            "message": message,
            "type": "channel"
        })
        save_reminders(reminders)

        # === Embedメッセージを作成 ===
        embed = discord.Embed(
            title="📅 リマインダーを設定しました！",
            color=discord.Color.blue()
        )
        embed.add_field(name="🕒 日時", value=time_str, inline=False)
        embed.add_field(name="💬 内容", value=message, inline=False)
        embed.set_footer(text=f"設定者: {interaction.user.display_name}")

        await interaction.response.send_message(embed=embed)
    except Exception as e:
        await interaction.response.send_message(f"⚠️ 時刻形式が正しくありません: {e}", ephemeral=True)
