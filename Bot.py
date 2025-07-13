import discord
from discord.ext import commands
import asyncio  # 處理非同步任務
import yt_dlp  # 擷取YouTube音訊

token = ""

# 設定Bot權限
intents = discord.Intents.default()
intents.message_content = True

# 建立Bot實例，設定指令前綴
bot = commands.Bot(command_prefix = "!", intents = intents)

# 同步至伺服器以使用/觸發指令
@bot.command()
@commands.has_permissions(administrator = True)
async def synccommands(ctx):
    await bot.tree.sync()
    await ctx.send("同步完成")

# 延遲測試指令
@bot.command()
async def ping(ctx):
    latency = bot.latency * 1000
    await ctx.send(f"延遲: {latency:.2f}ms")

# yt_dlp與ffmpeg播放設置
ytdlp_format_options = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'quiet': True,  # 減少不必要輸出
    'extract_flat': False,  # 支援實際音訊串流
    'source_address': '0.0.0.0'  # 避免某些網路問題
}

ffmpeg_options = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',  # 避免斷線問題
    'options': '-vn',  # 僅保留音訊
}

# 建立yt_dlp實例
ytdlp = yt_dlp.YoutubeDL(ytdlp_format_options)

# 封裝音訊來源
class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title')  # 音樂標題
        self.url = data.get('url')  # 音訊網址

    @classmethod
    async def from_url(cls, url, *, loop = None, stream = False):
        # 從YouTube擷取音訊來源
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: ytdlp.extract_info(url, download = not stream))

        if 'entries' in data:  # 若是播放清單，取第一首
            data = data['entries'][0]

        filename = data['url'] if stream else ytdlp.prepare_filename(data)
        return cls(discord.FFmpegPCMAudio(filename, **ffmpeg_options), data = data)

# 建立音樂控制面板
class MusicControlView(discord.ui.View):
    def __init__(self, ctx):
        super().__init__(timeout = 300)  # 控制面板持續時間為5分鐘
        self.ctx = ctx
        self.user_id = ctx.author.id  

    # 限制只有原操作者能控制
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("這不是你的控制面板！", ephemeral = True)
            return False
        return True

    # 暫停/繼續按鈕
    @discord.ui.button(label = "暫停/繼續", style = discord.ButtonStyle.primary)
    async def pause_resume(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client
        
        if not vc or not vc.is_connected():
            await interaction.response.send_message("我不在語音頻道", ephemeral = True)
            return

        if vc.is_playing():
            vc.pause()
            await interaction.response.send_message("音樂已暫停", ephemeral = True)
        elif vc.is_paused():
            vc.resume()
            await interaction.response.send_message("音樂已繼續", ephemeral = True)
        else:
            await interaction.response.send_message("目前沒有音樂正在播放", ephemeral = True)

    # 跳過按鈕
    @discord.ui.button(label = "跳過", style = discord.ButtonStyle.secondary)
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client
        
        if not vc or not vc.is_connected():
            await interaction.response.send_message("我不在語音頻道", ephemeral = True)
            return

        if vc.is_playing() or vc.is_paused():
            vc.stop()
            await interaction.response.send_message("已跳過音樂", ephemeral = True)
        else:
            await interaction.response.send_message("目前沒有音樂正在播放", ephemeral = True)
        
        await play_next_in_queue(self.ctx)

    # 停止按鈕
    @discord.ui.button(label = "停止", style = discord.ButtonStyle.danger)
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client
        
        if not vc or not vc.is_connected():
            await interaction.response.send_message("我不在語音頻道", ephemeral = True)
            return

        vc.stop()
        await interaction.response.send_message("音樂已停止", ephemeral = True)

        # 刪除控制面板
        global now_playing_message
        try:
            if now_playing_message:
                await now_playing_message.delete()
                now_playing_message = None
        except:
            pass

# 音樂隊列與狀態變數
music_queue = []
now_playing_message = None
now_playing_view = None
is_playing = False  # 播放鎖定狀態，避免重複觸發

# 播放下一首音樂（若有）
async def play_next_in_queue(ctx):
    global music_queue, now_playing_message, now_playing_view, is_playing

    if is_playing:  # 若已在播放中，直接退出
        return
    is_playing = True

    if ctx.voice_client is None:
        await ctx.send("我尚未加入語音頻道，請先使用 `/加入語音頻道`")
        is_playing = False
        return

    if len(music_queue) > 0:
        player = music_queue.pop(0)

        # 停止現有播放
        if ctx.voice_client.is_playing() or ctx.voice_client.is_paused():
            ctx.voice_client.stop()

        # 開始播放新音樂
        def after_playing(error):
            fut = asyncio.run_coroutine_threadsafe(play_next_in_queue(ctx), bot.loop)
            if error:
                print(f"播放時發生錯誤：{error}")
                fut.cancel()

        ctx.voice_client.play(player, after = after_playing)

        # 刪除舊控制面板
        if now_playing_message:
            try:
                await now_playing_message.delete()
            except discord.NotFound:
                pass

        # 發送新面板
        now_playing_view = MusicControlView(ctx)
        now_playing_message = await ctx.send(f"現在播放：{player.title}", view = now_playing_view)
    else:
        # 隊列空了，刪除控制面板
        if now_playing_message:
            try:
                await now_playing_message.delete()
            except discord.NotFound:
                pass
            now_playing_message = None
            now_playing_view = None
        else:
            await ctx.send("音樂隊列已播放完畢")
    
    is_playing = False

@bot.hybrid_command()
async def 播放音樂(ctx, 網址):
    """讓Bot播放音樂"""
    global music_queue

    # 檢查是否已連接語音頻道
    if ctx.voice_client is None:
        await ctx.send("我尚未加入語音頻道，請先使用 `/加入語音頻道`")
        return
    
    async with ctx.typing():
        try:
            player = await YTDLSource.from_url(網址, loop = bot.loop, stream = True)
        except Exception as e:
            await ctx.send(f"發生錯誤：{e}")
            return
        
        # 將音樂添加到隊列中
        music_queue.append(player)
        await ctx.send(f"已添加到隊列: {player.title}")

        # 如果當前沒有播放音樂且沒有音樂暫停中，則立即播放
        vc = ctx.voice_client
        if vc and not vc.is_playing() and not vc.is_paused():
            await play_next_in_queue(ctx)     

@bot.hybrid_command()
async def 加入語音頻道(ctx):
    """讓Bot加入語音頻道"""
    if ctx.author.voice:
        try:
            await ctx.author.voice.channel.connect(timeout = 10)
            await ctx.send(f"已加入語音頻道：{ctx.author.voice.channel.name}")
        except asyncio.TimeoutError:
            await ctx.send("連接語音頻道逾時")
    else:
        await ctx.send("你需要先進入語音頻道")

@bot.hybrid_command()
async def 離開語音頻道(ctx):
    """讓Bot離開語音頻道"""
    if ctx.voice_client:
        await ctx.guild.voice_client.disconnect()
        await ctx.send("再見")
    else:
        await ctx.send("我不在語音頻道裡面")

@bot.hybrid_command()
async def 音樂隊列_清空(ctx):
    """清空音樂隊列"""
    global music_queue
    music_queue.clear()
    await ctx.send("音樂隊列已清空")

@bot.hybrid_command()
async def 音樂隊列_查看(ctx):
    """查看當前的音樂隊列"""
    global music_queue
    if len(music_queue) == 0:
        await ctx.send("音樂隊列為空")
    else:
        queue_text = "\n".join(f"{i+1}. {track.title}" for i, track in enumerate(music_queue))
        await ctx.send(f"當前音樂隊列：\n{queue_text}")

bot.run(token)