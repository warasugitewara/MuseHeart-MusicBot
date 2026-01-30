# -*- coding: utf-8 -*-
import asyncio
import contextlib
import datetime
import itertools
import os.path
import pickle
import pprint
import re
import sys
import traceback
import zlib
from base64 import b64decode
from contextlib import suppress
from copy import deepcopy
from io import BytesIO
from random import shuffle
from typing import Union, Optional
from urllib.parse import urlparse, parse_qs, quote

import aiofiles
import aiohttp
import disnake
from async_timeout import timeout
from disnake.ext import commands
from yt_dlp import YoutubeDL

import wavelink
from utils.client import BotCore
from utils.db import DBModel
from utils.music.audio_sources.deezer import deezer_regex
from utils.music.audio_sources.spotify import spotify_regex_w_user
from utils.music.checks import check_voice, has_player, has_source, is_requester, is_dj, \
    can_send_message_check, check_requester_channel, can_send_message, can_connect, check_deafen, check_pool_bots, \
    check_channel_limit, check_stage_topic, check_queue_loading, check_player_perm, check_yt_cooldown
from utils.music.converters import time_format, fix_characters, string_to_seconds, URL_REG, \
    YOUTUBE_VIDEO_REG, google_search, percentage, music_source_image
from utils.music.errors import GenericError, MissingVoicePerms, NoVoice, PoolException, parse_error, \
    EmptyFavIntegration, DiffVoiceChannel, NoPlayer
from utils.music.interactions import VolumeInteraction, QueueInteraction, SelectInteraction, FavMenuView, ViewMode, \
    SetStageTitle, SelectBotVoice, youtube_regex, ButtonInteraction
from utils.music.models import LavalinkPlayer, LavalinkTrack, LavalinkPlaylist, PartialTrack, PartialPlaylist, \
    native_sources, CustomYTDL
from utils.others import check_cmd, send_idle_embed, CustomContext, PlayerControls, queue_track_index, \
    pool_command, string_to_file, CommandArgparse, music_source_emoji_url, song_request_buttons, \
    select_bot_pool, ProgressBar, update_inter, get_source_emoji_cfg, music_source_emoji

sc_recommended = re.compile(r"https://soundcloud\.com/.*/recommended$")
sc_profile_regex = re.compile(r"<?https://soundcloud\.com/[a-zA-Z0-9_-]+>?$")

class Music(commands.Cog):

    emoji = "🎶"
    name = "音楽"
    desc_prefix = f"[{emoji} {name}] | "

    playlist_opts = [
        disnake.OptionChoice("プレイリストをシャッフル", "shuffle"),
        disnake.OptionChoice("プレイリストを逆順", "reversed"),
    ]

    audio_formats = ("audio/mpeg", "audio/ogg", "audio/mp4", "audio/aac")

    providers_info = {
        "youtube": "ytsearch",
        "soundcloud": "scsearch",
        "spotify": "spsearch",
        "tidal": "tdsearch",
        "bandcamp": "bcsearch",
        "applemusic": "amsearch",
        "deezer": "dzsearch",
        "jiosaavn": "jssearch",
    }

    def __init__(self, bot: BotCore):

        self.bot = bot

        self.modules = [
                "utils.music.models",
                "utils.music.audio_sources.spotify",
                "utils.music.audio_sources.deezer",
                "utils.music.filters",
                "utils.music.local_lavalink",
                "utils.music.skin_utils",
                "utils.music.errors",
                "utils.music.interactions",
            ]

        self.extra_hints = bot.config["EXTRA_HINTS"].split("||")

        self.song_request_concurrency = commands.MaxConcurrency(1, per=commands.BucketType.member, wait=False)

        self.player_interaction_concurrency = commands.MaxConcurrency(1, per=commands.BucketType.member, wait=False)

        self.song_request_cooldown = commands.CooldownMapping.from_cooldown(rate=1, per=300,
                                                                            type=commands.BucketType.member)

        self.music_settings_cooldown = commands.CooldownMapping.from_cooldown(rate=3, per=15,
                                                                              type=commands.BucketType.guild)

        if self.bot.config["AUTO_ERROR_REPORT_WEBHOOK"]:
            self.error_report_queue = asyncio.Queue()
            self.error_report_task = bot.loop.create_task(self.error_report_loop())
        else:
            self.error_report_queue = None

    stage_cd = commands.CooldownMapping.from_cooldown(2, 45, commands.BucketType.guild)
    stage_mc = commands.MaxConcurrency(1, per=commands.BucketType.guild, wait=False)

    @commands.has_guild_permissions(manage_guild=True)
    @pool_command(
        only_voiced=True, name="setvoicestatus", aliases=["stagevc", "togglestageannounce", "announce", "vcannounce", "setstatus",
                                                         "voicestatus", "setvcstatus", "statusvc", "vcstatus", "stageannounce"],
        description="曲名でチャンネルの自動アナウンス/ステータスシステムを有効にします。",
        cooldown=stage_cd, max_concurrency=stage_mc, extras={"exclusive_cooldown": True},
        usage="{prefix}{cmd} <placeholders>\nEx: {track.author} - {track.title}"
    )
    async def setvoicestatus_legacy(self, ctx: CustomContext, *, template = ""):
        await self.set_voice_status.callback(self=self, inter=ctx, template=template)

    @commands.slash_command(
        description=f"{desc_prefix}曲名でチャンネルの自動アナウンス/ステータスシステムを有効化/編集します。",
        extras={"only_voiced": True, "exclusive_cooldown": True}, cooldown=stage_cd, max_concurrency=stage_mc,
        default_member_permissions=disnake.Permissions(manage_guild=True)
    )
    @commands.contexts(guild=True)
    async def set_voice_status(
            self, inter: disnake.ApplicationCommandInteraction,
            template: str = commands.Param(
                name="modelo", default="",
                description="ステータステンプレートを手動で指定してください（プレースホルダーを含めてください）。"
            )
    ):

        if isinstance(template, commands.ParamInfo):
            template = ""

        try:
            bot = inter.music_bot
            guild = inter.music_guild
            author = guild.get_member(inter.author.id)
        except AttributeError:
            bot = inter.bot
            guild = inter.guild
            author = inter.author

        if not author.guild_permissions.manage_guild and not (await bot.is_owner(author)):
            raise GenericError("**このシステムを有効化/無効化するにはサーバー管理権限が必要です。**")

        if not template:
            await inter.response.defer(ephemeral=True, with_message=True)
            global_data = await self.bot.get_global_data(inter.guild_id, db_name=DBModel.guilds)
            view = SetStageTitle(ctx=inter, bot=bot, data=global_data, guild=guild)
            view.message = await inter.send(view=view, embeds=view.build_embeds(), ephemeral=True)
            await view.wait()
        else:
            if not any(p in template for p in SetStageTitle.placeholders):
                raise GenericError(f"**少なくとも1つの有効なプレースホルダーを使用する必要があります:** {SetStageTitle.placeholder_text}")

            try:
                player = bot.music.players[inter.guild_id]
            except KeyError:
                raise NoPlayer()

            if not author.voice:
                raise NoVoice()

            if author.id not in guild.me.voice.channel.voice_states:
                raise DiffVoiceChannel()

            await inter.response.defer()

            player.stage_title_event = True
            player.stage_title_template = template
            player.start_time = disnake.utils.utcnow()

            await player.update_stage_topic()

            await player.process_save_queue()

            player.set_command_log(text="自動ステータスを有効にしました", emoji="📢")

            player.update = True

            if isinstance(inter, CustomContext):
                await inter.send("**自動ステータスが正常に設定されました！**")
            else:
                await inter.edit_original_message("**自動ステータスが正常に設定されました！**")


    @set_voice_status.autocomplete("modelo")
    async def default_models(self, inter: disnake.Interaction, query: str):
        return [
            "{track.title} - By: {track.author} | {track.timestamp}",
            "{track.emoji} | {track.title}",
            "{track.title} ( {track.playlist} )",
            "{track.title}  リクエスト者: {requester.name}",
        ]

    play_cd = commands.CooldownMapping.from_cooldown(3, 12, commands.BucketType.member)
    play_mc = commands.MaxConcurrency(1, per=commands.BucketType.member, wait=False)

    @check_voice()
    @can_send_message_check()
    @commands.message_command(name="add to queue", extras={"check_player": False},
                              cooldown=play_cd, max_concurrency=play_mc)
    async def message_play(self, inter: disnake.MessageCommandInteraction):

        if not inter.target.content:
            emb = disnake.Embed(description=f"選択された[メッセージ]({inter.target.jump_url})にテキストがありません...",
                                color=disnake.Colour.red())
            await inter.send(embed=emb, ephemeral=True)
            return

        await self.play.callback(
            self=self,
            inter=inter,
            query=inter.target.content,
            position=0,
            options="",
            manual_selection=False,
            force_play="no",
        )

    @check_voice()
    @can_send_message_check()
    @commands.slash_command(name="search", extras={"check_player": False}, cooldown=play_cd, max_concurrency=play_mc,
                            description=f"{desc_prefix}曲を検索し、結果から選んで再生します。")
    @commands.contexts(guild=True)
    async def search(
            self,
            inter: disnake.ApplicationCommandInteraction,
            query: str = commands.Param(name="busca", desc="曲名またはリンク。"),
            *,
            position: int = commands.Param(name="posição", description="曲を特定の位置に配置します",
                                           default=0),
            force_play: str = commands.Param(
                name="tocar_agora",
                description="曲をすぐに再生します（キューに追加する代わりに）。",
                default="no",
                choices=[
                    disnake.OptionChoice(disnake.Localized("Yes", data={disnake.Locale.pt_BR: "Sim"}), "yes"),
                ]
            ),
            options: str = commands.Param(name="opções", description="プレイリスト処理オプション",
                                          choices=playlist_opts, default=False),
            server: str = commands.Param(name="server", desc="検索に特定の音楽サーバーを使用します。",
                                         default=None),
            manual_bot_choice: str = commands.Param(
                name="selecionar_bot",
                description="利用可能なボットを手動で選択します。",
                default="no",
                choices=[
                    disnake.OptionChoice(disnake.Localized("Yes", data={disnake.Locale.pt_BR: "Sim"}), "yes"),
                ]
            ),
    ):

        await self.play.callback(
            self=self,
            inter=inter,
            query=query,
            position=position,
            force_play=force_play,
            options=options,
            manual_selection=True,
            server=server,
            manual_bot_choice=manual_bot_choice
        )

    @search.autocomplete("busca")
    async def search_autocomplete(self, inter: disnake.Interaction, current: str):

        if not current:
            return []

        if not self.bot.bot_ready or not self.bot.is_ready() or URL_REG.match(current):
            return [current] if len(current) < 100 else []

        try:
            bot, guild = await check_pool_bots(inter, only_voiced=True)
        except GenericError:
            return [current[:99]]
        except:
            bot = inter.bot

        try:
            if not inter.author.voice:
                return []
        except AttributeError:
            return [current[:99]]

        return await google_search(bot, current)

    @is_dj()
    @has_player()
    @can_send_message_check()
    @commands.max_concurrency(1, commands.BucketType.guild)
    @commands.slash_command(
        extras={"only_voiced": True},
        description=f"{desc_prefix}ボイスチャンネルに接続します（または移動します）。"
    )
    @commands.contexts(guild=True)
    async def connect(
            self,
            inter: disnake.ApplicationCommandInteraction,
            channel: Union[disnake.VoiceChannel, disnake.StageChannel] = commands.Param(
                name="canal",
                description="接続するチャンネル"
            )
    ):
        try:
            channel = inter.music_bot.get_channel(channel.id)
        except AttributeError:
            pass

        await self.do_connect(inter, channel)

    async def do_connect(
            self,
            ctx: Union[disnake.ApplicationCommandInteraction, commands.Context, disnake.Message],
            channel: Union[disnake.VoiceChannel, disnake.StageChannel] = None,
            check_other_bots_in_vc: bool = False,
            bot: BotCore = None,
            me: disnake.Member = None,
    ):

        if not channel:
            try:
                channel = ctx.music_bot.get_channel(ctx.author.voice.channel.id) or ctx.author.voice.channel
            except AttributeError:
                channel = ctx.author.voice.channel

        if not bot:
            try:
                bot = ctx.music_bot
            except AttributeError:
                try:
                    bot = ctx.bot
                except:
                    bot = self.bot

        if not me:
            try:
                me = ctx.music_guild.me
            except AttributeError:
                me = channel.guild.me

        try:
            guild_id = ctx.guild_id
        except AttributeError:
            guild_id = ctx.guild.id

        try:
            text_channel = ctx.music_bot.get_channel(ctx.channel.id)
        except AttributeError:
            text_channel = ctx.channel

        try:
            player = bot.music.players[guild_id]
        except KeyError:
            print(f"Player debug test 20: {bot.user} | {self.bot.user}")
            raise GenericError(
                f"**ボット {bot.user.mention} のプレイヤーがボイスチャンネルに接続する前に終了しました"
                f"（またはプレイヤーが初期化されませんでした）...\n念のため、もう一度お試しください。**"
            )

        can_connect(channel, me.guild, check_other_bots_in_vc=check_other_bots_in_vc, bot=bot)

        deafen_check = True

        if isinstance(ctx, disnake.ApplicationCommandInteraction) and ctx.application_command.name == self.connect.name:

            perms = channel.permissions_for(me)

            if not perms.connect or not perms.speak:
                raise MissingVoicePerms(channel)

            await player.connect(channel.id, self_deaf=True)

            if channel != me.voice and me.voice.channel:
                txt = [
                    f"チャンネル <#{channel.id}> に移動しました",
                    f"**チャンネル** <#{channel.id}> **に正常に移動しました**"
                ]

                deafen_check = False


            else:
                txt = [
                    f"チャンネル <#{channel.id}> に接続しました",
                    f"**チャンネル** <#{channel.id}> **に接続しました**"
                ]

            await self.interaction_message(ctx, txt, emoji="🔈", rpc_update=True)

        else:
            await player.connect(channel.id, self_deaf=True)

        try:
            player.members_timeout_task.cancel()
        except:
            pass

        if deafen_check and bot.config["GUILD_DEAFEN_WARN"]:

            retries = 0

            while retries < 5:

                if me.voice:
                    break

                await asyncio.sleep(1)
                retries += 1

            if not await check_deafen(me):
                await text_channel.send(
                    embed=disnake.Embed(
                        title="注意:",
                        description="プライバシーを守り、リソースを節約するために、"
                                    "私を右クリックして「サーバーでミュート」を選択し、"
                                    "チャンネルでの私の音声を無効にすることをお勧めします。",
                        color=self.bot.get_color(me),
                    ).set_image(
                        url="https://cdn.discordapp.com/attachments/554468640942981147/1012533546386210956/unknown.png"
                    ), delete_after=20
                )

        if isinstance(channel, disnake.StageChannel):

            stage_perms = channel.permissions_for(me)

            if stage_perms.mute_members:

                retries = 5

                while retries > 0:
                    await asyncio.sleep(1)
                    if not me.voice:
                        retries -= 1
                        continue
                    break
                await asyncio.sleep(1.5)
                await me.edit(suppress=False)
            else:
                embed = disnake.Embed(color=self.bot.get_color(me))

                embed.description = f"**スタッフの方がステージで話すよう招待してください: " \
                                    f"[{channel.name}]({channel.jump_url})。**"

                embed.set_footer(
                    text="💡 ヒント: 自動的にステージで話せるようにするには、"
                         "メンバーをミュートする権限を付与してください（サーバー全体または選択したステージチャンネルのみ）。")

                await text_channel.send(ctx.author.mention, embed=embed, delete_after=45)

    @can_send_message_check()
    @check_voice()
    @commands.bot_has_guild_permissions(send_messages=True)
    @commands.max_concurrency(1, commands.BucketType.member)
    @pool_command(name="addposition", description="曲をキューの特定の位置に追加します。",
                  aliases=["adp", "addpos"], check_player=False, cooldown=play_cd, max_concurrency=play_mc,
                  usage="{prefix}{cmd} [位置(番号)] [曲名|リンク]\nEx: {prefix}{cmd} 2 sekai - burn me down")
    async def addpos_legacy(self, ctx: CustomContext, position: int, *, query: str):

        if position < 1:
            raise GenericError("**キューの位置番号は1以上である必要があります。**")

        await self.play.callback(self=self, inter=ctx, query=query, position=position, options=False,
                                 force_play="no", manual_selection=False, server=None)

    stage_flags = CommandArgparse()
    stage_flags.add_argument('query', nargs='*', help="曲名またはリンク")
    stage_flags.add_argument('-position', '-pos', '-p', type=int, default=0, help='曲をキューの特定の位置に配置します（-next等を使用する場合は無視されます）。\nEx: -p 10')
    stage_flags.add_argument('-next', '-proximo', action='store_true', help='曲/プレイリストをキューの先頭に追加します（-pos 1と同等）')
    stage_flags.add_argument('-reverse', '-r', action='store_true', help='追加された曲の順序を逆にします（プレイリスト追加時のみ有効）。')
    stage_flags.add_argument('-shuffle', '-sl', action='store_true', help='追加された曲をシャッフルします（プレイリスト追加時のみ有効）。')
    stage_flags.add_argument('-select', '-s', action='store_true', help='検索結果から曲を選択します。')
    stage_flags.add_argument('-mix', '-rec', '-recommended', action="store_true", help="指定したアーティスト名-曲名に基づいておすすめの曲を追加/再生します。")
    stage_flags.add_argument('-force', '-now', '-n', '-f', action='store_true', help='追加した曲をすぐに再生します（現在曲が再生中の場合のみ有効）。')
    stage_flags.add_argument('-server', '-sv', type=str, default=None, help='特定の音楽サーバーを使用します。')
    stage_flags.add_argument('-selectbot', '-sb', action="store_true", help="利用可能なボットを手動で選択します。")

    @can_send_message_check()
    @commands.bot_has_guild_permissions(send_messages=True)
    @check_voice()
    @commands.max_concurrency(1, commands.BucketType.member)
    @pool_command(name="play", description="ボイスチャンネルで曲を再生します。", aliases=["p"], check_player=False,
                  cooldown=play_cd, max_concurrency=play_mc, extras={"flags": stage_flags},
                  usage="{prefix}{cmd} [曲名|リンク]\nEx: {prefix}{cmd} sekai - burn me down")
    async def play_legacy(self, ctx: CustomContext, *, flags: str = ""):

        args, unknown = ctx.command.extras['flags'].parse_known_args(flags.split())

        await self.play.callback(
            self = self,
            inter = ctx,
            query = " ".join(args.query + unknown),
            position= 1 if args.next else args.position if args.position > 0 else 0,
            options = "shuffle" if args.shuffle else "reversed" if args.reverse else None,
            force_play = "yes" if args.force else "no",
            manual_selection = args.select,
            server = args.server,
            manual_bot_choice = "yes" if args.selectbot else "no",
            mix = args.mix,
        )

    @can_send_message_check()
    @commands.bot_has_guild_permissions(send_messages=True)
    @check_voice()
    @pool_command(name="search", description="曲を検索し、結果から選んで再生します。",
                  aliases=["sc"], check_player=False, cooldown=play_cd, max_concurrency=play_mc,
                  usage="{prefix}{cmd} [曲名]\nEx: {prefix}{cmd} sekai - burn me down")
    async def search_legacy(self, ctx: CustomContext, *, query):

        await self.play.callback(self=self, inter=ctx, query=query, position=0, options=False, force_play="no",
                                 manual_selection=True, server=None)

    @can_send_message_check()
    @check_voice()
    @commands.slash_command(
        name="play_music_file",
        description=f"{desc_prefix}ボイスチャンネルで音楽ファイルを再生します。",
        extras={"check_player": False}, cooldown=play_cd, max_concurrency=play_mc
    )
    @commands.contexts(guild=True)
    async def play_file(
            self,
            inter: Union[disnake.ApplicationCommandInteraction, CustomContext],
            file: disnake.Attachment = commands.Param(
                name="arquivo", description="再生またはキューに追加する音声ファイル"
            ),
            position: int = commands.Param(name="posição", description="曲を特定の位置に配置します",
                                           default=0),
            force_play: str = commands.Param(
                name="tocar_agora",
                description="曲をすぐに再生します（キューに追加する代わりに）。",
                default="no",
                choices=[
                    disnake.OptionChoice(disnake.Localized("Yes", data={disnake.Locale.pt_BR: "Sim"}), "yes"),
                ]
            ),
            server: str = commands.Param(name="server", desc="検索に特定の音楽サーバーを使用します。",
                                         default=None),
            manual_bot_choice: str = commands.Param(
                name="selecionar_bot",
                description="利用可能なボットを手動で選択します。",
                default="no",
                choices=[
                    disnake.OptionChoice(disnake.Localized("Yes", data={disnake.Locale.pt_BR: "Sim"}), "yes"),
                ]
            ),
    ):

        class DummyMessage:
            attachments = [file]

        try:
            thread = inter.message.thread
        except:
            thread = None
        inter.message = DummyMessage()
        inter.message.thread = thread

        await self.play.callback(self=self, inter=inter, query="", position=position, options=False, force_play=force_play,
                                 manual_selection=False, server=server,
                                 manual_bot_choice=manual_bot_choice)

    async def check_player_queue(self, user: disnake.User, bot: BotCore, guild_id: int, tracks: Union[list, LavalinkPlaylist] = None):

        count = self.bot.config["QUEUE_MAX_ENTRIES"]

        try:
            player: LavalinkPlayer = bot.music.players[guild_id]
        except KeyError:
            if count < 1:
                return tracks
            count += 1
        else:
            if count < 1:
                return tracks
            if len(player.queue) >= count and not (await bot.is_owner(user)):
                raise GenericError(f"**キューがいっぱいです（{self.bot.config['QUEUE_MAX_ENTRIES']}曲）。**")

        if tracks:

            if isinstance(tracks, list):
                if not await bot.is_owner(user):
                    tracks = tracks[:count]
            else:
                if not await bot.is_owner(user):
                    tracks.tracks = tracks.tracks[:count]

        return tracks

    @can_send_message_check()
    @check_voice()
    @commands.slash_command(
        description=f"{desc_prefix}ボイスチャンネルで曲を再生します。",
        extras={"check_player": False}, cooldown=play_cd, max_concurrency=play_mc
    )
    @commands.contexts(guild=True)
    async def play(
            self,
            inter: Union[disnake.ApplicationCommandInteraction, CustomContext],
            query: str = commands.Param(name="busca", desc="曲名またはリンク。"), *,
            position: int = commands.Param(name="posição", description="曲を特定の位置に配置します",
                                           default=0),
            force_play: str = commands.Param(
                name="tocar_agora",
                description="曲をすぐに再生します（キューに追加する代わりに）。",
                default="no",
                choices=[
                    disnake.OptionChoice(disnake.Localized("Yes", data={disnake.Locale.pt_BR: "Sim"}), "yes"),
                ]
            ),
            mix: str = commands.Param(
                name="recomendadas",
                description="指定したアーティスト名-曲名に基づいておすすめの曲を再生します",
                default=False,
                choices=[
                    disnake.OptionChoice(disnake.Localized("Yes", data={disnake.Locale.pt_BR: "Sim"}), "yes"),
                ]
            ),
            manual_selection: bool = commands.Param(name="selecionar_manualmente",
                                                    description="検索結果から曲を手動で選択します",
                                                    default=False),
            options: str = commands.Param(name="opções", description="プレイリスト処理オプション",
                                          choices=playlist_opts, default=False),
            server: str = commands.Param(name="server", desc="検索に特定の音楽サーバーを使用します。",
                                         default=None),
            manual_bot_choice: str = commands.Param(
                name="selecionar_bot",
                description="利用可能なボットを手動で選択します。",
                default="no",
                choices=[
                    disnake.OptionChoice(disnake.Localized("Yes", data={disnake.Locale.pt_BR: "Sim"}), "yes"),
                ]
            ),
    ):

        try:
            bot = inter.music_bot
            guild = inter.music_guild
            author = guild.get_member(inter.author.id)
        except AttributeError:
            bot = inter.bot
            guild = inter.guild
            author = inter.author

        original_bot = bot

        mix = mix == "yes" or mix is True

        msg = None
        guild_data = await bot.get_data(inter.author.id, db_name=DBModel.guilds)
        ephemeral = None

        if not inter.response.is_done():
            try:
                async with timeout(1.5):
                    ephemeral = await self.is_request_channel(inter, data=guild_data, ignore_thread=True)
            except asyncio.TimeoutError:
                ephemeral = True
            await inter.response.defer(ephemeral=ephemeral, with_message=True)

        """if not inter.author.voice:
            raise NoVoice()

            if not (c for c in guild.channels if c.permissions_for(inter.author).connect):
                raise GenericError(f"**ボイスチャンネルに接続していません。また、サーバーには "
                                   "接続権限のあるボイスチャンネル/ステージがありません。**")

            color = self.bot.get_color(guild.me)

            if isinstance(inter, CustomContext):
                func = inter.send
            else:
                func = inter.edit_original_message

            msg = await func(
                embed=disnake.Embed(
                    description=f"**{inter.author.mention} 曲を再生するにはボイスチャンネルに参加してください。**\n"
                                f"**25秒以内にチャンネルに接続しない場合、この操作はキャンセルされます。**",
                    color=color
                )
            )

            if msg:
                inter.store_message = msg

            try:
                await bot.wait_for("voice_state_update", timeout=25, check=lambda m, b, a: m.id == inter.author.id and m.voice)
            except asyncio.TimeoutError:
                try:
                    func = msg.edit
                except:
                    func = inter.edit_original_message
                await func(
                    embed=disnake.Embed(
                        description=f"**{inter.author.mention} operação cancelada.**\n"
                                    f"**ボイスチャンネル/ステージへの接続に時間がかかりすぎました。**", color=color
                    )
                )
                return

            await asyncio.sleep(1)

        else:
            channel = bot.get_channel(inter.channel.id)
            if not channel:
                raise GenericError(f"**チャンネル <#{inter.channel.id}> が見つかりません（または削除されました）。**")
            await check_pool_bots(inter, check_player=False, bypass_prefix=True)"""

        if guild.me.voice and bot.user.id not in author.voice.channel.voice_states:

            if str(inter.channel.id) == guild_data['player_controller']['channel']:

                try:
                    if inter.author.id not in bot.music.players[guild.id].last_channel.voice_states:
                        raise DiffVoiceChannel()
                except (KeyError, AttributeError):
                    pass

            else:

                free_bots = await self.check_available_bot(inter=inter, guild=guild, bot=bot, message=msg)

                if len(free_bots) > 1 and manual_bot_choice == "yes":

                    v = SelectBotVoice(inter, guild, free_bots)

                    try:
                        func = msg.edit
                    except AttributeError:
                        try:
                            func = inter.edit_original_message
                        except AttributeError:
                            func = inter.send

                    newmsg = await func(
                        embed=disnake.Embed(
                            description=f"**{author.voice.channel.mention} で使用するボットを選択してください**",
                            color=self.bot.get_color(guild.me)), view=v
                    )
                    await v.wait()

                    if newmsg:
                        msg = newmsg

                    if v.status is None:
                        try:
                            func = msg.edit
                        except AttributeError:
                            func = inter.edit_original_message
                        try:
                            await func(embed=disnake.Embed(description="### タイムアウトしました...", color=self.bot.get_color(guild.me)), view=None)
                        except:
                            traceback.print_exc()
                        return

                    if v.status is False:
                        try:
                            func = msg.edit
                        except AttributeError:
                            func = inter.edit_original_message
                        await func(embed=disnake.Embed(description="### 操作がキャンセルされました。",
                                                       color=self.bot.get_color(guild.me)), view=None)
                        return

                    if not author.voice:
                        try:
                            func = msg.edit
                        except AttributeError:
                            func = inter.edit_original_message
                        await func(embed=disnake.Embed(description="### ボイスチャンネルに接続していません...",
                                                       color=self.bot.get_color(guild.me)), view=None)
                        return

                    update_inter(inter, v.inter)

                    current_bot = v.bot
                    inter = v.inter
                    guild = v.guild

                    await inter.response.defer()

                else:
                    try:
                        current_bot = free_bots.pop(0)
                    except:
                        return

                if bot != current_bot:
                    guild_data = await current_bot.get_data(guild.id, db_name=DBModel.guilds)

                bot = current_bot

        channel = bot.get_channel(inter.channel.id)

        can_send_message(channel, bot.user)

        await check_player_perm(inter=inter, bot=bot, channel=channel, guild_data=guild_data)

        if not guild.voice_client and not check_channel_limit(guild.me, author.voice.channel):
            raise GenericError(f"**チャンネル {author.voice.channel.mention} は満員です！**")

        await self.check_player_queue(inter.author, bot, guild.id)

        query = query.replace("\n", " ").strip()
        warn_message = None
        queue_loaded = False
        reg_query = None
        image_file = None

        try:
            if isinstance(inter.message, disnake.Message):
                message_inter = inter.message
            else:
                message_inter = None
        except AttributeError:
            message_inter = None

        try:
            modal_message_id = int(inter.data.custom_id[15:])
        except:
            modal_message_id = None

        attachment: Optional[disnake.Attachment] = None

        try:
            voice_channel: disnake.VoiceChannel = bot.get_channel(author.voice.channel.id)
        except AttributeError:
            raise NoVoice()

        try:
            player = bot.music.players[guild.id]

            if not server:
                node = player.node
            else:
                node = bot.music.get_node(server) or player.node

            guild_data = {}

        except KeyError:

            node = bot.music.get_node(server)

            if not node:
                node = await self.get_best_node(bot)

            guild_data = await bot.get_data(inter.guild_id, db_name=DBModel.guilds)

            if not guild.me.voice:
                can_connect(voice_channel, guild, guild_data["check_other_bots_in_vc"], bot=bot)

            static_player = guild_data['player_controller']

            if not inter.response.is_done():
                ephemeral = await self.is_request_channel(inter, data=guild_data, ignore_thread=True)
                await inter.response.defer(ephemeral=ephemeral)

            if static_player['channel']:
                channel, warn_message, message = await self.check_channel(guild_data, inter, channel, guild, bot)

        if ephemeral is None:
            ephemeral = await self.is_request_channel(inter, data=guild_data, ignore_thread=True)

        is_pin = None

        original_query = query or ""

        if not query:

            if self.bot.config["ENABLE_DISCORD_URLS_PLAYBACK"]:

                try:
                    attachment = inter.message.attachments[0]

                    if attachment.size > 18000000:
                        raise GenericError("**送信されたファイルは18MB以下である必要があります。**")

                    if attachment.content_type not in self.audio_formats:
                        raise GenericError("**送信されたファイルは有効な音楽ファイルではありません...**")

                    query = attachment.url

                except IndexError:
                    pass

        user_data = await self.bot.get_global_data(inter.author.id, db_name=DBModel.users)

        try:
            fav_slashcmd = f"</fav_manager:" + str(self.bot.get_global_command_named("fav_manager",
                                                                                     cmd_type=disnake.ApplicationCommandType.chat_input).id) + ">"
        except AttributeError:
            fav_slashcmd = "/fav_manager"

        try:
            savequeue_slashcmd = f"</save_queue:" + str(self.bot.get_global_command_named("save_queue",
                                                                                          cmd_type=disnake.ApplicationCommandType.chat_input).id) + ">"
        except AttributeError:
            savequeue_slashcmd = "/save_queue"

        if not query:

            opts = []

            txt = "### `[⭐] お気に入り [⭐]`\n"

            if user_data["fav_links"]:
                opts.append(disnake.SelectOption(label="お気に入りを使用", value=">> [⭐ Favoritos ⭐] <<", emoji="⭐"))
                txt += f"`お気に入りに追加した曲やプレイリストを再生します。`\n"

            else:
                txt += f"`お気に入りがありません...`\n"

            txt += f"-# お気に入りはコマンド {fav_slashcmd} で管理できます。\n" \
                   f"### `[💠] 連携 [💠]`\n"

            if user_data["integration_links"]:
                opts.append(disnake.SelectOption(label="連携を使用", value=">> [💠 Integrações 💠] <<", emoji="💠"))
                txt += f"`連携リストからYouTubeチャンネル（または音楽プラットフォームのユーザープロフィール）の公開プレイリストを再生します。`\n"

            else:
                txt += f"`連携が追加されていません...` " \
                       f"`連携を使用してYouTubeチャンネルのリンク等を追加すると公開プレイリストに簡単にアクセスできます。`\n"

            txt += f"-# 連携を管理するには、コマンド {fav_slashcmd} を使用して「連携」オプションを選択してください。\n" \
                    f"### `[💾] 保存済みキュー [💾]`\n"

            if os.path.isfile(f"./local_database/saved_queues_v1/users/{inter.author.id}.pkl"):
                txt += f"`コマンド` {savequeue_slashcmd} `で保存した曲のキューを使用します。`\n"
                opts.append(disnake.SelectOption(label="保存済みキューを使用", value=">> [💾 Fila Salva 💾] <<", emoji="💾"))

            else:
                txt += "`保存済みの曲のキューがありません`\n" \
                        f"-# キューを保存するには、プレイヤーに最低3曲が追加されている状態でコマンド {savequeue_slashcmd} を使用してください。"

            if user_data["last_tracks"]:
                txt += "### `[📑] 最近の曲 [📑]`\n" \
                    "`最近聴いた/追加した曲を再生します。`\n"
                opts.append(disnake.SelectOption(label="最近の曲を追加", value=">> [📑 Músicas recentes 📑] <<", emoji="📑"))
                
            if isinstance(inter, disnake.MessageInteraction) and not inter.response.is_done():
                await inter.response.defer(ephemeral=ephemeral)

            if not guild_data:
                guild_data = await bot.get_data(inter.guild_id, db_name=DBModel.guilds)

            if guild_data["player_controller"]["fav_links"]:
                txt += "### `[📌] サーバーのお気に入り [📌]`\n" \
                        "`サーバーのお気に入りを使用します（サーバースタッフが追加）。`\n"
                opts.append(disnake.SelectOption(label="サーバーのお気に入りを使用", value=">> [📌 Favoritos do servidor 📌] <<", emoji="📌"))

            if not opts:
                raise EmptyFavIntegration()

            embed = disnake.Embed(
                color=self.bot.get_color(guild.me),
                description=f"{txt}## 以下からオプションを選択してください:"
                            f"\n-# 注意: 以下のオプションを選択しない場合、このリクエストは <t:{int((disnake.utils.utcnow() + datetime.timedelta(seconds=180)).timestamp())}:R> に自動的にキャンセルされます。"
            )

            kwargs = {
                "content": "",
                "embed": embed
            }

            try:
                if inter.message.author.bot:
                    kwargs["content"] = inter.author.mention
            except AttributeError:
                pass

            view = SelectInteraction(user=inter.author, timeout=180, opts=opts)

            try:
                await msg.edit(view=view, **kwargs)
            except AttributeError:
                try:
                    await inter.edit_original_message(view=view, **kwargs)
                except AttributeError:
                    msg = await inter.send(view=view, **kwargs)

            await view.wait()

            select_interaction = view.inter

            try:
                func = inter.edit_original_message
            except AttributeError:
                func = msg.edit

            if not select_interaction or view.selected is False:

                embed.set_footer(text="⚠️ " + ("選択がタイムアウトしました！" if view.selected is not False else "ユーザーによりキャンセルされました。"))

                try:
                    await func(embed=embed, components=song_request_buttons)
                except AttributeError:
                    traceback.print_exc()
                    pass
                return

            if select_interaction.data.values[0] == "cancel":
                await func(
                    embed=disnake.Embed(
                        description="**選択がキャンセルされました！**",
                        color=self.bot.get_color(guild.me)
                    ),
                    components=None
                )
                return

            try:
                inter.store_message = msg
            except AttributeError:
                pass

            inter.token = select_interaction.token
            inter.id = select_interaction.id
            inter.response = select_interaction.response
            query = select_interaction.data.values[0]
            await inter.response.defer()

        fav_opts = []

        menu = None
        selected_title = ""

        if query.startswith(">> [💠 Integrações 💠] <<"):
            query = ""
            menu = "integrations"
            for k, v in user_data["integration_links"].items():

                update = False

                if not isinstance(v, dict):
                    v = {"url": v, "avatar": None}
                    user_data["integration_links"][k] = v
                    update = True

                if update:
                    await self.bot.update_global_data(inter.author.id, user_data, db_name=DBModel.users)

                emoji, platform = music_source_emoji_url(v["url"])

                fav_opts.append({"url": v["url"], "option": disnake.SelectOption(label=fix_characters(k[6:], 45), value=f"> itg: {k}", description=f"[💠 Integração 💠] -> {platform}", emoji=emoji)})

        elif query.startswith(">> [⭐ Favoritos ⭐] <<"):
            query = ""
            menu = "favs"
            for k, v in user_data["fav_links"].items():
                emoji, platform = music_source_emoji_url(v)
                fav_opts.append({"url": v, "option": disnake.SelectOption(label=fix_characters(k, 45), value=f"> fav: {k}", description=f"[⭐ Favorito ⭐] -> {platform}", emoji=emoji)})

        elif query.startswith(">> [📑 Músicas recentes 📑] <<"):

            if not user_data["last_tracks"]:
                raise GenericError("**履歴に曲が登録されていません...**\n"
                                   "検索やリンクで曲を追加すると表示されるようになります。")

            query = ""
            menu = "latest"
            for i, d in enumerate(user_data["last_tracks"]):
                fav_opts.append({"url": d["url"], "option": disnake.SelectOption(label=d["name"], value=f"> lst: {i}",
                                                                                 description="[📑 Músicas recentes 📑]",
                                                     emoji=music_source_emoji_url(d["url"])[0])})

        elif query.startswith(">> [📌 Favoritos do servidor 📌] <<"):

            if not guild_data:
                guild_data = await bot.get_data(guild.id, db_name=DBModel.guilds)

            if not guild_data["player_controller"]["fav_links"]:
                raise GenericError("**サーバーには固定リンク/お気に入りがありません。**")

            menu = "guild_favs"
            
            for name, v in guild_data["player_controller"]["fav_links"].items():
                fav_opts.append({"url": v["url"], "option": disnake.SelectOption(label=fix_characters(name, 45), value=f"> pin: {name}", description="[📌 Favorito do servidor 📌]", emoji=music_source_emoji_url(v['url'])[0])})

            is_pin = False

        if fav_opts:

            if len(fav_opts) == 1:
                query = list(fav_opts)[0]["option"].value

            else:

                check_url = (lambda i: f"{i}/playlists" if (".spotify." in i or '.deezer.' in i) else i)

                embed = disnake.Embed(
                    color=self.bot.get_color(guild.me),
                    description="\n".join(f"{get_source_emoji_cfg(bot, i['url']) or ''} [`{fix_characters(i['option'].label, 45)}`]({check_url(i['url'])})" for i in fav_opts)
                )

                if menu == "favs":
                    embed.description = '### `[⭐] ⠂お気に入り ⠂[⭐]`\n' \
                                        '`お気に入りリストに追加した曲やプレイリストを再生します。`\n' \
                                        f'-# お気に入りはコマンド {fav_slashcmd} で管理できます。\n\n' \
                                         f'{embed.description}\n\n**以下からお気に入りを選択してください:**'

                elif menu == "integrations":
                    embed.description = '### `[💠] ⠂連携 ⠂[💠]`\n' \
                                        '`連携リストからYouTubeチャンネル（または音楽プラットフォームのユーザープロフィール）の公開プレイリストを再生します。`\n' \
                                        f'-# 連携を管理するには、コマンド {fav_slashcmd} を使用して「連携」オプションを選択してください。\n\n' \
                                         f'{embed.description}\n\n**以下から連携を選択してください:**'

                elif menu == "guild_favs":
                    embed.description = f'### `[📌] ⠂サーバーのお気に入り ⠂[📌]\n' \
                                        '`サーバーのお気に入りを使用します（サーバースタッフが追加）。`\n\n'\
                                         f'{embed.description}\n\n**以下からお気に入りを選択してください:**'

                elif menu == "latest":
                    embed.description = f'### 📑 ⠂最近の曲/プレイリストを再生:\n{embed.description}\n\n**以下から項目を選択してください:**'

                embed.description += f'\n-# 注意: 以下のオプションを選択しない場合、このリクエストは <t:{int((disnake.utils.utcnow() + datetime.timedelta(seconds=75)).timestamp())}:R> に自動的にキャンセルされます。'

                try:
                    if bot.user.id != self.bot.user.id:
                        embed.set_footer(text=f"選択されたBot: {bot.user.display_name}", icon_url=bot.user.display_avatar.url)
                except AttributeError:
                    pass

                kwargs = {
                    "content": "",
                    "embed": embed
                }

                try:
                    if inter.message.author.bot:
                        kwargs["content"] = inter.author.mention
                except AttributeError:
                    pass

                view = SelectInteraction(
                    user=inter.author,  timeout=75, opts=[i["option"] for i in fav_opts]
                )

                if isinstance(inter, disnake.MessageInteraction) and not inter.response.is_done():
                    await inter.response.defer(ephemeral=ephemeral)

                try:
                    func = msg.edit
                except AttributeError:
                    try:
                        if inter.response.is_done():
                            func = inter.edit_original_message
                        else:
                            func = inter.response.send_message
                            kwargs["ephemeral"] = ephemeral
                    except AttributeError:
                        kwargs["ephemeral"] = ephemeral
                        try:
                            func = inter.followup.send
                        except AttributeError:
                            func = inter.send

                msg = await func(view=view, **kwargs)

                await view.wait()

                select_interaction = view.inter

                if not select_interaction or view.selected is False:

                    embed.description = "\n".join(embed.description.split("\n")[:-3])
                    embed.set_footer(text="⚠️ タイムアウトしました！" if not view.selected is False else "⚠️ ユーザーによりキャンセルされました。")

                    try:
                        await msg.edit(embed=embed, components=song_request_buttons)
                    except AttributeError:
                        try:
                            await select_interaction.response.edit_message(embed=embed, components=song_request_buttons)
                        except AttributeError:
                            traceback.print_exc()
                    return

                if select_interaction.data.values[0] == "cancel":
                    embed.description = "\n".join(embed.description.split("\n")[:-3])
                    embed.set_footer(text="⚠️ 選択がキャンセルされました！")
                    await msg.edit(embed=embed, components=None)
                    return

                try:
                    inter.store_message = msg
                except AttributeError:
                    pass

                inter.token = select_interaction.token
                inter.id = select_interaction.id
                inter.response = select_interaction.response
                query = select_interaction.data.values[0]
                selected_title = ":".join(query.split(":")[2:])

        elif not query:
            raise EmptyFavIntegration()

        loadtype = None
        tracks = []

        source = None

        if query.startswith("> pin: "):
            if is_pin is None:
                is_pin = True
            if not guild_data:
                guild_data = await bot.get_data(inter.guild_id, db_name=DBModel.guilds)
            query = guild_data["player_controller"]["fav_links"][query[7:]]['url']
            source = False

        elif query.startswith("> lst: "):
            query = user_data["last_tracks"][int(query[7:])]["url"]
            source = False

        if not user_data:
            user_data = await self.bot.get_global_data(inter.author.id, db_name=DBModel.users)

        yt_match = None
        sc_match = None
        profile_avatar = None
        info = {"entries": []}
        node: Optional[wavelink.Node] = None

        if query.startswith("> fav:"):
            query = user_data["fav_links"][query[7:]]

        elif query.startswith("> itg:"):
            integration_data = user_data["integration_links"][query[7:]]

            if not isinstance(integration_data, dict):
                integration_data = {"url": integration_data, "avatar": None}
                user_data["integration_links"][query[7:]] = integration_data
                await self.bot.update_global_data(inter.author.id, user_data, db_name=DBModel.users)

            query = integration_data["url"]

            profile_avatar = integration_data.get("avatar")

        if (matches := spotify_regex_w_user.match(query)):

            if not self.bot.spotify:
                raise GenericError("**現在Spotifyのサポートは利用できません...**")

            url_type, user_id = matches.groups()

            if url_type == "user":

                try:
                    await inter.response.defer(ephemeral=True)
                except:
                    pass

                cache_key = f"partial:spotify:{url_type}:{user_id}"

                if not (info := self.bot.pool.integration_cache.get(cache_key)):
                    result = await self.bot.spotify.get_user_playlists(user_id)
                    info = {"entries": [{"title": t["name"], "url": f'{t["external_urls"]["spotify"]}'} for t in result["items"]]}
                    self.bot.pool.integration_cache[cache_key] = info

        elif (matches := deezer_regex.match(query)):

            url_type, user_id = matches.groups()[-2:]

            if url_type == "profile":

                try:
                    await inter.response.defer(ephemeral=True)
                except:
                    pass

                cache_key = f"partial:deezer:{url_type}:{user_id}"

                if not (info := self.bot.pool.integration_cache.get(cache_key)):
                    result = await bot.deezer.get_user_playlists(user_id)
                    info = {"entries": [{"title": t['title'], "url": f"{t['link']}"} for t in result]}
                    self.bot.pool.integration_cache[cache_key] = info

        elif matches:=(yt_match:=youtube_regex.search(query)) or (sc_match:=sc_profile_regex.match(query)):

            if yt_match:
                query = f"{yt_match.group()}/playlists"
                remove_chars = 12
            else:
                query = f"{sc_match.group(0).strip('<>')}/sets"
                remove_chars = 6

            try:
                await inter.response.defer(ephemeral=True)
            except:
                pass

            if not (info := self.bot.pool.integration_cache.get(query)):

                info = await self.bot.loop.run_in_executor(None, lambda: self.bot.pool.ytdl.extract_info(query.split("\n")[0],
                                                                                                download=False))

                try:
                    if not info["entries"]:
                        pprint.pprint(info)
                        raise GenericError(f"**コンテンツが利用できません（または非公開です）:**\n{query}")
                except KeyError:
                    raise GenericError("**選択されたオプションの結果を取得中にエラーが発生しました...**")

                self.bot.pool.integration_cache[query] = info

            try:
                profile_avatar = [a['url'] for a in info["thumbnails"] if a["id"] == "avatar_uncropped"][0]
            except (KeyError, IndexError):
                pass

            try:
                selected_title = info["channel"]
            except KeyError:
                selected_title = info["title"][:-remove_chars]

            info = {"entries": [{"title": t['title'], "url": f"{t['url']}"} for t in info["entries"]], "thumbnails": info.get("thumbnails")}

        if matches and info["entries"]:

            if len(info["entries"]) == 1:
                query = info["entries"][0]['url']

            else:

                emoji, platform = music_source_emoji_url(query)

                view = SelectInteraction(
                    user=inter.author, max_itens=15,
                    opts=[
                        disnake.SelectOption(label=e['title'][:90], value=f"entrie_select_{c}",
                                             emoji=emoji) for c, e in enumerate(info['entries'])
                    ], timeout=120)

                embed_title = f"チャンネル: {(info.get('title') or selected_title)}" if platform == "youtube" else f"ユーザー: {info.get('title') or selected_title}"

                embeds = []

                for page_index, page in enumerate(disnake.utils.as_chunks(info['entries'], 15)):

                    embed = disnake.Embed(
                        description="\n".join(f'-# ` {(15*page_index)+n+1}. `[`{i["title"]}`]({i["url"]})' for n, i in enumerate(page)) + "\n\n**以下からプレイリストを選択してください:**\n"
                                    f'-# 以下のオプションを選択しない場合、このリクエストは <t:{int((disnake.utils.utcnow() + datetime.timedelta(seconds=120)).timestamp())}:R> に自動的にキャンセルされます。',
                        color=self.bot.get_color(guild.me)
                    ).set_author(name=f"公開プレイリストを再生 {embed_title}", icon_url=music_source_image(platform), url=query)

                    if profile_avatar:
                        embed.set_thumbnail(profile_avatar)
                        try:
                            if len(info["thumbnails"]) > 2:
                                embed.set_image(info["thumbnails"][0]['url'])
                        except:
                            pass

                    embeds.append(embed)

                kwargs = {}

                view.embeds = embeds

                try:
                    func = msg.edit
                except AttributeError:
                    try:
                        func = inter.edit_original_message
                    except AttributeError:
                        kwargs["ephemeral"] = True
                        try:
                            func = inter.followup.send
                        except AttributeError:
                            func = inter.send

                msg = await func(embed=embeds[0], view=view, **kwargs)

                await view.wait()

                if not view.inter or view.selected is False:

                    try:
                        func = msg.edit
                    except:
                        func = view.inter.response.edit_message

                    try:
                        embed = view.embeds[view.current_page]
                    except:
                        embed = embeds[0]

                    embed.description = "\n".join(embed.description.split("\n")[:-3])
                    embed.set_footer(text="⚠️ タイムアウトしました！" if not view.selected is False else "⚠️ ユーザーによりキャンセルされました。")

                    try:
                        await func(embed=embed,components=song_request_buttons)
                    except:
                        traceback.print_exc()
                    return

                query = info["entries"][int(view.selected[14:])]["url"]

                if not isinstance(inter, disnake.ModalInteraction):
                    inter.token = view.inter.token
                    inter.id = view.inter.id
                    inter.response = view.inter.response
                else:
                    inter = view.inter

            source = False

        if query.startswith(">> [💾 Fila Salva 💾] <<"):

            try:
                async with aiofiles.open(f"./local_database/saved_queues_v1/users/{inter.author.id}.pkl", 'rb') as f:
                    f_content = await f.read()
                    try:
                        f_content = zlib.decompress(f_content)
                    except zlib.error:
                        pass
                    data = pickle.loads(f_content)
            except FileNotFoundError:
                raise GenericError("**保存したキューは既に削除されています...**")

            tracks = await self.check_player_queue(inter.author, bot, guild.id, self.bot.pool.process_track_cls(data["tracks"])[0])
            node = await self.get_best_node(bot)
            queue_loaded = True
            source = False

        else:

            query = query.strip("<>")

            urls = URL_REG.findall(query)

            reg_query = {}

            if urls:
                query = urls[0]
                source = False

                if not self.bot.config["ENABLE_DISCORD_URLS_PLAYBACK"] and "cdn.discordapp.com/attachments/" in query:
                    raise GenericError("**Discordリンクのサポートは無効になっています。**")

                if query.startswith("https://www.youtube.com/results"):
                    try:
                        query = f"ytsearch:{parse_qs(urlparse(query).query)['search_query'][0]}"
                    except:
                        raise GenericError(f"**指定されたリンクはサポートされていません:** {query}")
                    manual_selection = True

                elif "&list=" in query and (link_re := YOUTUBE_VIDEO_REG.search(query)):

                    view = ButtonInteraction(
                        user=inter.author, timeout=45,
                        buttons=[
                            disnake.ui.Button(label="曲のみを読み込む", emoji="🎵", custom_id="music"),
                            disnake.ui.Button(label="プレイリストを読み込む", emoji="🎶", custom_id="playlist"),
                        ]
                    )

                    embed = disnake.Embed(
                        description='**このリンクにはプレイリスト付きの動画が含まれています。**\n'
                                    f'続行するには <t:{int((disnake.utils.utcnow() + datetime.timedelta(seconds=45)).timestamp())}:R> 以内にオプションを選択してください。\n'
                                    f'-# 注意: プライベートプレイリストの場合、現在のリンクの動画のみが読み込まれます。',
                        color=self.bot.get_color(guild.me)
                    )

                    try:
                        if bot.user.id != self.bot.user.id:
                            embed.set_footer(text=f"選択されたBot: {bot.user.display_name}",
                                             icon_url=bot.user.display_avatar.url)
                    except AttributeError:
                        pass

                    try:
                        func = inter.edit_original_message
                        kwargs = {}
                    except AttributeError:
                        func = inter.send
                        kwargs = {"ephemeral": ephemeral}

                    msg = await func(embed=embed, view=view, **kwargs)

                    await view.wait()

                    if not view.inter or view.selected is False:

                        try:
                            func = inter.edit_original_message
                        except AttributeError:
                            func = msg.edit

                        embed.description = "\n".join(embed.description.split("\n")[:-3])
                        embed.set_footer(text="⚠️ タイムアウトしました！" if not view.selected is False else "⚠️ ユーザーによりキャンセルされました。")

                        try:
                            await func(embed=embed, components=song_request_buttons)
                        except:
                            traceback.print_exc()
                        return

                    if view.selected == "music":
                        query = link_re.group()

                    try:
                        inter.store_message = msg
                    except AttributeError:
                        pass

                    if not isinstance(inter, disnake.ModalInteraction):
                        inter.token = view.inter.token
                        inter.id = view.inter.id
                        inter.response = view.inter.response
                    else:
                        inter = view.inter

            else:

                music_sources = {"deezer", "spotify"}

                for b in self.bot.pool.get_guild_bots(inter.guild_id):

                    if not b.get_guild(inter.guild_id):
                        continue

                    for n in b.music.nodes.values():
                        for s in n.info["sourceManagers"]:
                            if s in self.providers_info:
                                music_sources.add(s)

                view = ButtonInteraction(
                    user=inter.author, timeout=45,
                    buttons=[
                        disnake.ui.Button(label=ms.title(), custom_id=ms, emoji=music_source_emoji(ms)) for ms in sorted(music_sources)
                    ]
                )

                embed = disnake.Embed(
                    color=inter.bot.get_color(guild.me),
                    description="**曲の検索を優先するサービスを選択してください**\n"
                                "-# 注意: 選択したサービスで希望の曲が見つからない場合、別のサービスが自動的に使用されます。\n"
                                f'-# 注意2: 以下のオプションを選択しない場合、<t:{int((disnake.utils.utcnow() + datetime.timedelta(seconds=45)).timestamp())}:R> にデフォルトのサービスが自動的に使用されます。'
                )

                if inter.response.is_done():
                    await inter.edit_original_message(embed=embed, view=view)
                else:
                    msg = await inter.send(embed=embed, view=view)

                await view.wait()

                if view.selected:
                    inter = view.inter
                    source = self.providers_info[view.selected]

                elif view.selected is False:
                    for c in view.children:
                        c.disabled = True
                    embed.description = "\n".join(embed.description.split("\n")[:-1])
                    embed.set_footer(text="操作がキャンセルされました。")
                    func = view.inter.response.edit_message
                    await func(view=view, embed=embed)
                    return

                update_inter(inter, view.inter)

        if not inter.response.is_done():
            await inter.response.defer(ephemeral=ephemeral)

        if not queue_loaded:
            tracks, node = await self.get_tracks(query, inter, inter.author, node=node, source=source, bot=bot, mix=mix)
            tracks = await self.check_player_queue(inter.author, bot, guild.id, tracks)

        try:
            player = bot.music.players[guild.id]
        except KeyError:
            new_bot, guild = await check_pool_bots(inter, check_player=False, bypass_prefix=True, bypass_attribute=True)
            channel = bot.get_channel(inter.channel.id)

            try:
                new_bot.music.players[guild.id]
            except KeyError:
                if new_bot != bot or not guild_data:
                    guild_data = await new_bot.get_data(guild.id, db_name=DBModel.guilds)

                static_player = guild_data['player_controller']

                if static_player['channel']:
                    channel, warn_message, message = await self.check_channel(guild_data, inter, channel, guild, new_bot)

            bot = new_bot

        channel = bot.get_channel(inter.channel.id)

        can_send_message(channel, bot.user)

        pos_txt = ""

        embed = disnake.Embed(color=disnake.Colour.red())

        embed.colour = self.bot.get_color(guild.me)

        position -= 1

        embed_description = ""

        track_url = ""

        if isinstance(tracks, list):

            if self.bot.pool.song_select_cooldown.get_bucket(inter).get_retry_after() > 0:
                manual_selection = True

            if not queue_loaded and len(tracks) > 1 and manual_selection:

                embed.description = f"**以下から希望の曲を選択してください:**"

                try:
                    func = inter.edit_original_message
                except AttributeError:
                    func = inter.send

                try:
                    add_id = f"_{inter.id}"
                except AttributeError:
                    add_id = ""

                tracks = tracks[:25]

                msg = await func(
                    embed=embed,
                    components=[
                        disnake.ui.Select(
                            placeholder='検索結果:',
                            custom_id=f"track_selection{add_id}",
                            min_values=1,
                            max_values=len(tracks),

                            options=[
                                disnake.SelectOption(
                                    label=f"{n+1}. {t.title[:96]}",
                                    value=f"track_select_{n}",
                                    description=f"{t.author[:70]} [{time_format(t.duration)}]")
                                for n, t in enumerate(tracks)
                            ]
                        )
                    ]
                )

                def check_song_selection(i: Union[CustomContext, disnake.MessageInteraction]):

                    try:
                        return i.data.custom_id == f"track_selection_{inter.id}" and i.author == inter.author
                    except AttributeError:
                        return i.author == inter.author and i.message.id == msg.id

                try:
                    select_interaction: disnake.MessageInteraction = await self.bot.wait_for(
                        "dropdown",
                        timeout=45,
                        check=check_song_selection
                    )
                except asyncio.TimeoutError:
                    try:
                        func = inter.edit_original_message
                    except AttributeError:
                        func = msg.edit
                    try:
                        await func(embed=disnake.Embed(color=disnake.Colour.red(), description="**タイムアウトしました！**"), view=None)
                    except disnake.NotFound:
                        pass
                    return

                update_inter(inter, select_interaction)

                if len(select_interaction.data.values) > 1:

                    indexes = set(int(v[13:]) for v in select_interaction.data.values)

                    selected_tracks = []

                    for i in indexes:
                        for n, t in enumerate(tracks):
                            if i == n:
                                selected_tracks.append(t)
                                break

                    tracks = selected_tracks

                else:

                    tracks = tracks[int(select_interaction.data.values[0][13:])]

                if isinstance(inter, CustomContext):
                    inter.message = msg

                if reg_query is not None:
                    try:
                        reg_query = {"name": tracks.title, "url": tracks.uri}
                    except AttributeError:
                        reg_query = {"name": tracks[0].title, "url": tracks[0].uri}

                    if not reg_query["url"]:
                        reg_query = None

                await select_interaction.response.defer()

                inter = select_interaction

            elif not queue_loaded:

                tracks = tracks[0]

                if tracks.info.get("sourceName") == "http":

                    if tracks.title == "Unknown title":
                        if attachment:
                            tracks.info["title"] = attachment.filename
                        else:
                            tracks.info["title"] = tracks.uri.split("/")[-1]
                        tracks.title = tracks.info["title"]

                    tracks.info["uri"] = ""

                elif url_check:=URL_REG.match(original_query.strip("<>")):
                    track_url = url_check.group()

            if not author.voice:
                raise NoVoice()

            if inter.author.id not in voice_channel.voice_states and bot.user.id not in voice_channel.voice_states:

                if not (free_bots := await self.check_available_bot(inter=inter, guild=guild, bot=bot, message=msg)):
                    return

                if free_bots[0] != bot:
                    try:
                        voice_channel = bot.get_channel(author.voice.channel.id)
                    except AttributeError:
                        raise NoVoice()
                    bot = free_bots.pop(0)
                    channel = bot.get_channel(channel.id)
                    guild = bot.get_guild(guild.id)
                    guild_data = await bot.get_data(guild.id, db_name=DBModel.guilds)
                    node = None

            await check_player_perm(inter=inter, bot=bot, channel=channel, guild_data=guild_data)

            try:
                player = bot.music.players[guild.id]
            except KeyError:
                player = await self.create_player(
                    inter=inter, bot=bot, guild=guild, guild_data=guild_data, channel=channel,
                    message_inter=message_inter, node=node, modal_message_id=modal_message_id
                )

            if not isinstance(tracks, list):

                if force_play == "yes":
                    player.queue.insert(0, tracks)
                elif position < 0:
                    player.queue.append(tracks)
                else:
                    player.queue.insert(position, tracks)
                    pos_txt = f" キューの位置 {position + 1}"

                duration = time_format(tracks.duration) if not tracks.is_stream else '🔴 Livestream'

                if not track_url:
                    track_url = tracks.uri or tracks.search_uri

                log_text = f"{inter.author.mention} は [`{fix_characters(tracks.title, 20)}`](<{track_url}>){pos_txt} `({duration})` を追加しました。"

                loadtype = "track"

                embed.set_author(
                    name="⠂" + fix_characters(tracks.single_title, 35),
                    url=track_url,
                    icon_url=music_source_image(tracks.info['sourceName'])
                )
                embed.set_thumbnail(url=tracks.thumb)
                embed.description = f"`{fix_characters(tracks.author, 15)}`**┃**`{time_format(tracks.duration) if not tracks.is_stream else '🔴 Livestream'}`**┃**{inter.author.mention}"
                emoji = "🎵"
                if reg_query is not None and tracks.uri:
                    reg_query = {"name": tracks.title, "url": tracks.uri}

            else:

                if options == "shuffle":
                    shuffle(tracks)

                if position < 0 or len(tracks) < 2:

                    if options == "reversed":
                        tracks.reverse()
                    for track in tracks:
                        player.queue.append(track)
                else:
                    if options != "reversed":
                        tracks.reverse()
                    for track in tracks:
                        player.queue.insert(position, track)

                    pos_txt = f" (Pos. {position + 1})"

                total_duration = 0

                for t in tracks:
                    if not t.is_stream:
                        total_duration += t.duration

                if queue_loaded:
                    log_text = f"{inter.author.mention} は {query[7:]} 経由で `{len(tracks)} 曲` を追加しました。"
                    title = f"{inter.author.display_name} の保存済みキューを使用"
                    icon_url = "https://i.ibb.co/51yMNPw/floppydisk.png"

                    desc = ""

                    tracks_playlists = {}

                    for t in tracks:
                        if t.playlist_name:
                            try:
                                tracks_playlists[t.playlist_url]["count"] += 1
                            except KeyError:
                                tracks_playlists[t.playlist_url] = {"name": t.playlist_name, "count": 1}

                    if tracks_playlists:
                        embed_description += "\n### 読み込まれたプレイリスト:\n" + "\n".join(f"[`{info['name']}`]({url}) `- {info['count']} 曲` " for url, info in tracks_playlists.items()) + "\n"

                else:
                    query = fix_characters(query.replace(f"{source}:", '', 1), 25)
                    title = "曲を追加しました:"
                    icon_url = music_source_image(tracks[0].info['sourceName'])
                    log_text = f"{inter.author.mention} は検索で `{len(tracks)} 曲` を追加しました: `{query}`{pos_txt}。"
                    desc = "\n".join(f"` {c+1}. ` [`{fix_characters(t.title, 75)}`](<{t.uri}>) `{time_format(t.duration)}`" for c, t in enumerate(tracks))

                embed.set_author(name="⠂" + title, icon_url=icon_url)
                embed.set_thumbnail(url=tracks[0].thumb)
                embed.description = desc or f"`{(tcount:=len(tracks))} 曲`**┃**`{time_format(total_duration)}`**┃**{inter.author.mention}"
                emoji = "🎶"

        else:

            if not author.voice:
                raise NoVoice()

            if inter.author.id not in voice_channel.voice_states and bot.user.id not in voice_channel.voice_states:

                if not (free_bots := await self.check_available_bot(inter=inter, guild=guild, bot=bot, message=msg)):
                    return

                if free_bots[0] != bot:
                    try:
                        voice_channel = bot.get_channel(author.voice.channel.id)
                    except AttributeError:
                        raise NoVoice()
                    bot = free_bots.pop(0)
                    channel = bot.get_channel(channel.id)
                    guild = bot.get_guild(guild.id)
                    guild_data = await bot.get_data(guild.id, db_name=DBModel.guilds)
                    node = None

            await check_player_perm(inter=inter, bot=bot, channel=channel, guild_data=guild_data)

            try:
                player = bot.music.players[guild.id]
            except KeyError:
                player = await self.create_player(
                    inter=inter, bot=bot, guild=guild, guild_data=guild_data, channel=channel,
                    message_inter=message_inter, node=node, modal_message_id=modal_message_id
                )

            if options == "shuffle":
                shuffle(tracks.tracks)

            if position < 0 or len(tracks.tracks) < 2:

                if options == "reversed":
                    tracks.tracks.reverse()
                for track in tracks.tracks:
                    player.queue.append(track)
            else:
                if options != "reversed":
                    tracks.tracks.reverse()
                for track in tracks.tracks:
                    player.queue.insert(position, track)

                pos_txt = f" (Pos. {position + 1})"

            if tracks.tracks[0].info["sourceName"] == "youtube":

                try:
                    q = f"https://www.youtube.com/playlist?list={query.split('&list=')[1]}"
                except:
                    q = query

                if not await bot.is_owner(inter.author):
                    try:
                        async with bot.session.get((oembed_url:=f"https://www.youtube.com/oembed?url={q}")) as r:
                            try:
                                playlist_data = await r.json()
                            except:
                                raise Exception(f"{r.status} | {await r.text()}")
                            else:
                                tracks.data["playlistInfo"]["thumb"] = playlist_data["thumbnail_url"]
                    except Exception as e:
                        print(f"プレイリストのアートワークの取得に失敗しました (oembed): {oembed_url} | {repr(e)}")

                else:

                    try:
                        with YoutubeDL(
                            {
                                'extract_flat': True,
                                'quiet': True,
                                'no_warnings': True,
                                'lazy_playlist': True,
                                'simulate': True,
                                'playlistend': 0,
                                'cachedir': "./.ytdl_cache",
                                'allowed_extractors': [
                                    r'.*youtube.*',
                                ],
                                'extractor_args': {
                                    'youtubetab': {
                                        "skip": ["webpage"]
                                    }
                                }
                            }
                        ) as ydl:
                            playlist_data = await bot.loop.run_in_executor(None, lambda: ydl.extract_info(q, download=False))

                        async with aiohttp.ClientSession() as session:
                            async with session.get(playlist_data["thumbnails"][0]['url']) as response:
                                if response.status != 200:
                                    response.raise_for_status()

                                image_file = disnake.File(fp=BytesIO(await response.read()), filename=f'{playlist_data["id"]}.jpg')

                        tracks.data["playlistInfo"]["thumb"] = playlist_data["thumbnails"][0]['url']
                    except Exception as e:
                        print(f"プレイリストのアートワークの取得に失敗しました: {q} | {repr(e)}")

            loadtype = "playlist"

            log_text = f"{inter.author.mention} はプレイリスト [`{fix_characters(tracks.name, 20)}`](<{tracks.url}>){pos_txt} `({len(tracks.tracks)})` を追加しました。"

            total_duration = 0

            for t in tracks.tracks:
                if not t.is_stream:
                    total_duration += t.duration

            try:
                embed.set_author(
                    name="⠂" + fix_characters(tracks.name, 35),
                    url=tracks.url,
                    icon_url=music_source_image(tracks.tracks[0].info['sourceName'])
                )
            except KeyError:
                embed.set_author(
                    name="⠂ Spotify Playlist",
                    icon_url=music_source_image(tracks.tracks[0].info['sourceName'])
                )

            if image_file:
                embed.set_thumbnail(f"attachment://{image_file.filename}")
            else:
                embed.set_thumbnail(url=tracks.thumb)
            embed.description = f"`{(tcount:=len(tracks.tracks))} 曲`**┃**`{time_format(total_duration)}`**┃**{inter.author.mention}"
            emoji = "🎶"

            if reg_query is not None and tracks.uri:
                reg_query = {"name": tracks.name, "url": tracks.uri}

        embed.description += player.controller_link

        player.queue_autoplay.clear()

        if not is_pin:

            if not player.is_connected:
                try:
                    embed.description += f"\n`ボイスチャンネル:` {voice_channel.mention}"
                except AttributeError:
                    pass

            embed.description += embed_description

            try:
                func = inter.edit_original_message
            except AttributeError:
                if msg:
                    func = msg.edit
                elif inter.message.author.id == bot.user.id:
                    func = inter.message.edit
                else:
                    func = inter.send

            footer_txt = f"`♾️` [`{user_data['lastfm']['username']}`](https://www.last.fm/user/{user_data['lastfm']['username']})" if user_data["lastfm"]["sessionkey"] and user_data["lastfm"]["scrobble"] else ""

            try:
                if original_bot.user.id != self.bot.user.id:
                    embed.description += f"\n-# **Via:** {bot.user.mention}" + (f" ⠂{footer_txt}" if footer_txt else "")
                elif footer_txt:
                    embed.description += f"\n-# {footer_txt}"
            except AttributeError:
                if footer_txt:
                    embed.description += f"\n-# {footer_txt}"

            if mix:
                components = []

            elif loadtype == "track":
                components = [
                    disnake.ui.Button(emoji="💗", label="お気に入り", custom_id=PlayerControls.embed_add_fav),
                    disnake.ui.Button(emoji="▶️", label="再生" + ("する（今すぐ）" if (player.current and player.current.autoplay) else ""), custom_id=PlayerControls.embed_forceplay),
                    disnake.ui.Button(emoji="<:add_music:588172015760965654>", label="キューに追加",
                                      custom_id=PlayerControls.embed_enqueue_track),
                ]

            elif loadtype == "playlist":
                try:
                    self.bot.pool.enqueue_playlist_embed_cooldown.get_bucket(inter).update_rate_limit()
                except:
                    pass
                components = [
                    disnake.ui.Button(emoji="💗", label="お気に入り", custom_id=PlayerControls.embed_add_fav),
                    disnake.ui.Button(emoji="<:add_music:588172015760965654>", label="キューに追加",
                                      custom_id=PlayerControls.embed_enqueue_playlist)
                ]
            else:
                components = None

            kw_embed = {"components": components} if components else {"view": None}
            if image_file:
                kw_embed["file"] = image_file
            await func(embed=embed, **kw_embed)

        if not player.is_connected:

            try:
                guild_data["check_other_bots_in_vc"]
            except KeyError:
                guild_data = await bot.get_data(guild.id, db_name=DBModel.guilds)

            if isinstance(voice_channel, disnake.StageChannel):
                player.stage_title_event = False

            await self.do_connect(
                inter, channel=voice_channel,
                check_other_bots_in_vc=guild_data["check_other_bots_in_vc"],
                bot=bot, me=player.guild.me
            )

        await self.process_music(inter=inter, force_play=force_play, ephemeral=ephemeral, user_data=user_data, player=player,
                                 log_text=log_text, emoji=emoji, warn_message=warn_message, reg_query=reg_query)

    @play.autocomplete("busca")
    async def fav_add_autocomplete(self, inter: disnake.Interaction, query: str):

        if not self.bot.is_ready() or URL_REG.match(query) or URL_REG.match(query):
            return [query] if len(query) < 100 else []

        favs = [">> [⭐ Favoritos ⭐] <<", ">> [💠 Integrações 💠] <<", ">> [📌 Favoritos do servidor 📌] <<"]

        if os.path.isfile(f"./local_database/saved_queues_v1/users/{inter.author.id}.pkl"):
            favs.append(">> [💾 Fila Salva 💾] <<")

        if not inter.guild_id:
            try:
                await check_pool_bots(inter, return_first=True)
            except:
                return [query] if len(query) < 100 else []

        try:
            vc = inter.author.voice
        except AttributeError:
            vc = True

        user_data = await self.bot.get_global_data(inter.author.id, db_name=DBModel.users)

        favs.extend(reversed([(f"{rec['url']} || {rec['name']}"[:100] if len(rec['url']) < 101 else rec['name'][:100]) for rec in user_data["last_tracks"] if rec.get("url")]))

        if not vc or not query:
            return favs[:20]

        return await google_search(self.bot, query, max_entries=20) or favs[:20]

    skip_back_cd = commands.CooldownMapping.from_cooldown(4, 13, commands.BucketType.member)
    skip_back_mc = commands.MaxConcurrency(1, per=commands.BucketType.member, wait=False)

    case_sensitive_args = CommandArgparse()
    case_sensitive_args.add_argument('-casesensitive', '-cs', action='store_true',
                             help="単語ごとではなく、曲名の完全一致フレーズで検索します。")
    @check_stage_topic()
    @check_yt_cooldown()
    @is_requester()
    @check_queue_loading()
    @check_voice()
    @pool_command(name="skip", aliases=["next", "n", "s", "pular", "skipto"], cooldown=skip_back_cd,
                  max_concurrency=skip_back_mc, description=f"現在再生中の曲をスキップします。",
                  extras={"flags": case_sensitive_args}, only_voiced=True,
                  usage="{prefix}{cmd} <検索語>\nEx: {prefix}{cmd} sekai")
    async def skip_legacy(self, ctx: CustomContext, *, flags: str = ""):

        args, unknown = ctx.command.extras['flags'].parse_known_args(flags.split())

        if ctx.invoked_with == "skipto" and not unknown:
            raise GenericError("**skiptoを使用するには曲名を追加する必要があります。**")

        await self.skip.callback(self=self, inter=ctx, query=" ".join(unknown), case_sensitive=args.casesensitive)

    @check_stage_topic()
    @check_yt_cooldown()
    @is_requester()
    @check_queue_loading()
    @has_source()
    @check_voice()
    @commands.slash_command(
        description=f"{desc_prefix}キューの特定の曲にスキップします。",
        extras={"only_voiced": True}, cooldown=skip_back_cd, max_concurrency=skip_back_mc
    )
    @commands.contexts(guild=True)
    async def skipto(
            self,
            inter: disnake.ApplicationCommandInteraction,
            query: str = commands.Param(
                name="nome",
                description="曲名（完全または一部）。"
            ),
            case_sensitive: bool = commands.Param(
                name="nome_exato", default=False,
                description="単語ごとではなく、曲名の完全一致フレーズで検索します。",

            )
    ):

        await self.skip.callback(self=self, inter=inter, query=query, case_sensitive=case_sensitive)

    @check_stage_topic()
    @check_yt_cooldown()
    @is_requester()
    @check_queue_loading()
    @has_player()
    @check_voice()
    @commands.slash_command(
        description=f"{desc_prefix}現在再生中の曲をスキップします。",
        extras={"only_voiced": True}, cooldown=skip_back_cd, max_concurrency=skip_back_mc
    )
    @commands.contexts(guild=True)
    async def skip(
            self,
            inter: disnake.ApplicationCommandInteraction, *,
            query: str = commands.Param(
                name="nome",
                description="曲名（完全または一部）。",
                default=None,
            ),
            play_only: str = commands.Param(
                name=disnake.Localized("play_only", data={disnake.Locale.pt_BR: "tocar_apenas"}),
                choices=[
                    disnake.OptionChoice(
                        disnake.Localized("Yes", data={disnake.Locale.pt_BR: "Sim"}), "yes"
                    )
                ],
                description="曲をすぐに再生します（キューを回転させずに）",
                default="no"
            ),
            case_sensitive: bool = commands.Param(
                name="nome_exato", default=False,
                description="単語ごとではなく、曲名の完全一致フレーズで検索します。",

            )
    ):

        try:
            bot = inter.music_bot
            guild = inter.music_guild
        except AttributeError:
            bot = inter.bot
            guild = bot.get_guild(inter.guild_id)

        player: LavalinkPlayer = bot.music.players[inter.guild_id]

        ephemeral = await self.is_request_channel(inter)

        interaction = None

        if query:

            try:
                index = queue_track_index(inter, bot, query, case_sensitive=case_sensitive)[0][0]
            except IndexError:
                raise GenericError(f"**キューに曲名: {query}**")

            if player.queue:
                track: LavalinkTrack = player.queue[index]
                player.queue.append(player.last_track or player.current)
            else:
                track: LavalinkTrack = player.queue_autoplay[index]
                index += 1
                player.queue_autoplay.appendleft(player.last_track or player.current)

            player.last_track = None

            if player.loop == "current":
                player.loop = False

            if play_only == "yes":
                if track.autoplay:
                    del player.queue_autoplay[index]
                    player.queue_autoplay.appendleft(track)
                else:
                    del player.queue[index]
                    player.queue.appendleft(track)

            elif index > 0:
                if track.autoplay:
                    player.queue_autoplay.rotate(0 - index)
                else:
                    player.queue.rotate(0 - index)

            player.set_command_log(emoji="⤵️", text=f"{inter.author.mention} が現在の曲にスキップしました。")

            embed = disnake.Embed(
                color=self.bot.get_color(guild.me),
                description= f"⤵️ **⠂{inter.author.mention} が曲にスキップしました:**\n"
                             f"╰[`{fix_characters(track.title, 43)}`](<{track.uri or track.search_uri}>){player.controller_link}"
            )

            try:
                if bot.user.id != self.bot.user.id:
                    embed.set_footer(text=f"選択されたBot: {bot.user.display_name}", icon_url=bot.user.display_avatar.url)
            except AttributeError:
                pass

            if isinstance(inter, disnake.MessageInteraction) and inter.data.custom_id == "queue_track_selection":
                await inter.response.edit_message(embed=embed, view=None)
            elif not isinstance(inter, (CustomContext, disnake.ApplicationCommandInteraction)) and inter.data.custom_id == "musicplayer_queue_dropdown":
                await inter.response.defer()
            else:
                await inter.send(embed=embed, ephemeral=ephemeral)

        else:

            if isinstance(inter, disnake.MessageInteraction):
                player.set_command_log(text=f"{inter.author.mention} が曲をスキップしました。", emoji="⏭️")
                if not inter.response.is_done():
                    try:
                        await inter.response.defer()
                    except:
                        pass
                interaction = inter
            else:

                player.set_command_log(emoji="⏭️", text=f"{inter.author.mention} が曲をスキップしました。")

                embed = disnake.Embed(
                    color=self.bot.get_color(guild.me),
                    description=f"⏭️ **⠂{inter.author.mention} が曲をスキップしました:\n"
                                f"╰[`{fix_characters(player.current.title, 43)}`](<{player.current.uri or player.current.search_uri}>)**"
                                f"{player.controller_link}"
                )

                try:
                    if bot.user.id != self.bot.user.id:
                        embed.set_footer(text=f"選択されたBot: {bot.user.display_name}", icon_url=bot.user.display_avatar.url)
                except AttributeError:
                    pass

                await inter.send(embed=embed, ephemeral=ephemeral)

            if player.loop == "current":
                player.loop = False

        try:
            (player.current or player.last_track).info["extra"]["track_loops"] = 0
        except AttributeError:
            pass

        await player.track_end(ignore_track_loop=True)
        player.ignore_np_once = True
        await player.process_next(inter=interaction)

    @check_yt_cooldown()
    @check_stage_topic()
    @is_dj()
    @check_queue_loading()
    @has_player()
    @check_voice()
    @pool_command(name="back", aliases=["b", "voltar"], description="前の曲に戻ります。", only_voiced=True,
                  cooldown=skip_back_cd, max_concurrency=skip_back_mc)
    async def back_legacy(self, ctx: CustomContext):
        await self.back.callback(self=self, inter=ctx)

    @check_stage_topic()
    @is_dj()
    @has_player()
    @check_queue_loading()
    @check_voice()
    @commands.max_concurrency(1, commands.BucketType.member)
    @commands.slash_command(
        description=f"{desc_prefix}前の曲に戻ります。",
        extras={"only_voiced": True}, cooldown=skip_back_cd, max_concurrency=skip_back_mc
    )
    @commands.contexts(guild=True)
    async def back(self, inter: disnake.ApplicationCommandInteraction):

        try:
            bot = inter.music_bot
        except AttributeError:
            bot = inter.bot

        player: LavalinkPlayer = bot.music.players[inter.guild_id]

        if not len(player.queue) and (player.keep_connected or not len(player.played)):
            await player.seek(0)
            await self.interaction_message(inter, "曲の最初に戻りました。", emoji="⏪")
            return

        try:
            track = player.played.pop()
        except:
            track = player.queue.pop()

        if not track and player.autoplay:
            try:
                track = player.queue_autoplay.pop()
            except:
                pass

        if player.current:
            if player.current.autoplay:
                if player.autoplay:
                    player.queue_autoplay.appendleft(player.current)
            else:
                player.queue.appendleft(player.current)

        player.last_track = None

        player.queue.appendleft(track)

        if isinstance(inter, disnake.MessageInteraction):
            interaction = inter
            player.set_command_log(text=f"{inter.author.mention} が現在の曲に戻りました。", emoji="⏮️")
            await inter.response.defer()
        else:

            interaction = None

            t = player.queue[0]

            txt = [
                "現在の曲に戻りました。",
                f"⏮️ **⠂{inter.author.mention} が曲に戻りました:\n╰[`{fix_characters(t.title, 43)}`](<{t.uri or t.search_uri}>)**"
            ]

            await self.interaction_message(inter, txt, emoji="⏮️", store_embed=True)

        if player.loop == "current":
            player.loop = False

        player.ignore_np_once = True

        if not player.current:
            await player.process_next(inter=interaction)
        else:
            player.is_previows_music = True
            await player.track_end()
            await player.process_next(inter=interaction, force_np=True)

    @check_stage_topic()
    @check_queue_loading()
    @has_source()
    @check_voice()
    @commands.slash_command(
        description=f"{desc_prefix}現在の曲をスキップするために投票します。",
        extras={"only_voiced": True}
    )
    @commands.contexts(guild=True)
    async def voteskip(self, inter: disnake.ApplicationCommandInteraction):

        try:
            bot = inter.music_bot
            guild = inter.music_guild
        except AttributeError:
            bot = inter.bot
            guild = inter.guild

        player: LavalinkPlayer = bot.music.players[inter.guild_id]

        embed = disnake.Embed()

        if inter.author.id in player.votes:
            raise GenericError("**現在の曲をスキップするために既に投票しています。**")

        embed.colour = self.bot.get_color(guild.me)

        txt = [
            f"現在の曲をスキップするために投票しました（投票数: {len(player.votes) + 1}/{self.bot.config['VOTE_SKIP_AMOUNT']}）。",
            f"{inter.author.mention} が現在の曲をスキップするために投票しました（投票数: {len(player.votes) + 1}/{self.bot.config['VOTE_SKIP_AMOUNT']}）。",
        ]

        if len(player.votes) < self.bot.config.get('VOTE_SKIP_AMOUNT', 3):
            embed.description = txt
            player.votes.add(inter.author.id)
            await self.interaction_message(inter, txt, emoji="✋")
            return

        await self.interaction_message(inter, txt, emoji="✋")
        await player.track_end()
        await player.process_next()

    volume_cd = commands.CooldownMapping.from_cooldown(1, 7, commands.BucketType.member)
    volume_mc = commands.MaxConcurrency(1, per=commands.BucketType.member, wait=False)

    @is_dj()
    @has_source()
    @check_voice()
    @pool_command(name="volume", description="音量を調整します。", aliases=["vol", "v"], only_voiced=True,
                  cooldown=volume_cd, max_concurrency=volume_mc, usage="{prefix}{cmd} [レベル]\nEx: {prefix}{cmd} 50")
    async def volume_legacy(self, ctx: CustomContext, level: int):

        if not 4 < level < 151:
            raise GenericError("**無効な音量です！5から150の間で選択してください**", self_delete=7)

        await self.volume.callback(self=self, inter=ctx, value=int(level))

    @is_dj()
    @has_source()
    @check_voice()
    @commands.slash_command(description=f"{desc_prefix}音量を調整します。", extras={"only_voiced": True},
                            cooldown=volume_cd, max_concurrency=volume_mc)
    @commands.contexts(guild=True)
    async def volume(
            self,
            inter: disnake.ApplicationCommandInteraction, *,
            value: int = commands.Param(name="nível", description="5から150の間のレベル", min_value=5.0, max_value=150.0)
    ):

        try:
            bot = inter.music_bot
            guild = inter.music_guild
        except AttributeError:
            bot = inter.bot
            guild = inter.guild

        player: LavalinkPlayer = bot.music.players[inter.guild_id]

        embed = disnake.Embed(color=disnake.Colour.red())

        if value is None:

            view = VolumeInteraction(inter)

            embed.colour = self.bot.get_color(guild.me)
            embed.description = "**以下から音量レベルを選択してください:**"

            try:
                if bot.user.id != self.bot.user.id:
                    embed.set_footer(text=f"選択されたBot: {bot.user.display_name}", icon_url=bot.user.display_avatar.url)
            except AttributeError:
                pass

            await inter.send(embed=embed, ephemeral=await self.is_request_channel(inter), view=view)
            await view.wait()
            if view.volume is None:
                return

            value = view.volume

        elif not 4 < value < 151:
            raise GenericError("音量は**5**から**150**の間である必要があります。")

        await player.set_volume(value)

        txt = [f"音量を **{value}%** に調整しました", f"🔊 **⠂{inter.author.mention} が音量を {value}% に調整しました**"]
        await self.interaction_message(inter, txt, emoji="🔊")

    pause_resume_cd = commands.CooldownMapping.from_cooldown(2, 7, commands.BucketType.member)
    pause_resume_mc =commands.MaxConcurrency(1, per=commands.BucketType.member, wait=False)

    @is_dj()
    @has_source()
    @check_voice()
    @pool_command(name="pause", aliases=["pausar"], description="曲を一時停止します。", only_voiced=True,
                  cooldown=pause_resume_cd, max_concurrency=pause_resume_mc)
    async def pause_legacy(self, ctx: CustomContext):
        await self.pause.callback(self=self, inter=ctx)

    @is_dj()
    @has_source()
    @check_voice()
    @commands.slash_command(
        description=f"{desc_prefix}曲を一時停止します。", extras={"only_voiced": True},
        cooldown=pause_resume_cd, max_concurrency=pause_resume_mc
    )
    @commands.contexts(guild=True)
    async def pause(self, inter: disnake.ApplicationCommandInteraction):

        try:
            bot = inter.music_bot
        except AttributeError:
            bot = inter.bot

        player: LavalinkPlayer = bot.music.players[inter.guild_id]

        if player.paused:
            raise GenericError("**曲は既に一時停止中です。**")

        await player.set_pause(True)

        txt = ["曲を一時停止しました。", f"⏸️ **⠂{inter.author.mention} が曲を一時停止しました。**"]

        await self.interaction_message(inter, txt, rpc_update=True, emoji="⏸️")
        await player.update_stage_topic()

    @is_dj()
    @has_source()
    @check_voice()
    @pool_command(name="resume", aliases=["unpause"], description="曲を再開します。", only_voiced=True,
                  cooldown=pause_resume_cd, max_concurrency=pause_resume_mc)
    async def resume_legacy(self, ctx: CustomContext):
        await self.resume.callback(self=self, inter=ctx)

    @is_dj()
    @has_source()
    @check_voice()
    @commands.slash_command(
        description=f"{desc_prefix}曲を再開します。",
        extras={"only_voiced": True}, cooldown=pause_resume_cd, max_concurrency=pause_resume_mc
    )
    @commands.contexts(guild=True)
    async def resume(self, inter: disnake.ApplicationCommandInteraction):

        try:
            bot = inter.music_bot
        except AttributeError:
            bot = inter.bot

        player: LavalinkPlayer = bot.music.players[inter.guild_id]

        if not player.paused:
            raise GenericError("**曲は一時停止していません。**")

        await player.set_pause(False)

        txt = ["曲を再開しました。", f"▶️ **⠂{inter.author.mention} が曲を再開しました。**"]
        await self.interaction_message(inter, txt, rpc_update=True, emoji="▶️")
        await player.update_stage_topic()

    seek_cd = commands.CooldownMapping.from_cooldown(2, 10, commands.BucketType.member)
    seek_mc =commands.MaxConcurrency(1, per=commands.BucketType.member, wait=False)

    @check_stage_topic()
    @is_dj()
    @check_queue_loading()
    @has_source()
    @check_voice()
    @pool_command(name="seek", aliases=["sk"], description="曲を特定の時間に進める/戻します。",
                  only_voiced=True, cooldown=seek_cd, max_concurrency=seek_mc,
                  usage="{prefix}{cmd} [時間]\n"
                        "Ex 1: {prefix}{cmd} 10 (時間 0:10)\n"
                        "Ex 2: {prefix}{cmd} 1:45 (時間 1:45)")
    async def seek_legacy(self, ctx: CustomContext, *, position: str):
        await self.seek.callback(self=self, inter=ctx, position=position)

    @check_stage_topic()
    @is_dj()
    @check_queue_loading()
    @has_source()
    @check_voice()
    @commands.slash_command(
        description=f"{desc_prefix}曲を特定の時間に進める/戻します。",
        extras={"only_voiced": True}, cooldown=seek_cd, max_concurrency=seek_mc
    )
    @commands.contexts(guild=True)
    async def seek(
            self,
            inter: disnake.ApplicationCommandInteraction,
            position: str = commands.Param(name="tempo", description="進める/戻す時間（例: 1:45 / 40 / 0:30）")
    ):

        try:
            bot = inter.music_bot
        except AttributeError:
            bot = inter.bot

        player: LavalinkPlayer = bot.music.players[inter.guild_id]

        if player.current.is_stream:
            raise GenericError("**ライブストリームではこのコマンドを使用できません。**")

        position = position.split(" | ")[0].replace(" ", ":")

        seconds = string_to_seconds(position)

        if seconds is None:
            raise GenericError(
                "**無効な時間を使用しました！秒（1桁または2桁）または（分）:（秒）の形式を使用してください**")

        milliseconds = seconds * 1000

        if milliseconds < 0:
            milliseconds = 0

        if milliseconds > player.position:

            emoji = "⏩"

            txt = [
                f"曲の時間を: `{time_format(milliseconds)}` に進めました",
                f"{emoji} **⠂{inter.author.mention} が曲の時間を進めました:** `{time_format(milliseconds)}`"
            ]

        else:

            emoji = "⏪"

            txt = [
                f"曲の時間を: `{time_format(milliseconds)}` に戻しました",
                f"{emoji} **⠂{inter.author.mention} が曲の時間を戻しました:** `{time_format(milliseconds)}`"
            ]

        await player.seek(milliseconds)

        if player.paused:
            await player.set_pause(False)

        await self.interaction_message(inter, txt, emoji=emoji)

        await asyncio.sleep(2)
        await player.update_stage_topic()
        await player.process_rpc()

    @seek.autocomplete("tempo")
    async def seek_suggestions(self, inter: disnake.Interaction, query: str):

        try:
            if not inter.author.voice:
                return
        except AttributeError:
            pass

        if not self.bot.bot_ready or not self.bot.is_ready():
            return []

        if query:
            return [time_format(string_to_seconds(query)*1000)]

        try:
            await check_pool_bots(inter, only_voiced=True)
            bot = inter.music_bot
        except:
            return

        try:
            player: LavalinkPlayer = bot.music.players[inter.guild_id]
        except KeyError:
            return

        if not player.current or player.current.is_stream:
            return

        seeks = []

        if player.current.duration >= 90000:
            times = [int(n * 0.5 * 10) for n in range(20)]
        else:
            times = [int(n * 1 * 10) for n in range(20)]

        for p in times:
            percent = percentage(p, player.current.duration)
            seeks.append(f"{time_format(percent)} | {p}%")

        return seeks

    loop_cd = commands.CooldownMapping.from_cooldown(3, 5, commands.BucketType.member)
    loop_mc =commands.MaxConcurrency(1, per=commands.BucketType.member, wait=False)

    @is_dj()
    @has_source()
    @check_voice()
    @pool_command(
        description=f"リピートモードを選択: 現在の曲 / キュー / 無効 / 回数（数字を使用）。",
        only_voiced=True, cooldown=loop_cd, max_concurrency=loop_mc,
        usage="{prefix}{cmd} <回数|モード>\nEx 1: {prefix}{cmd} 1\nEx 2: {prefix}{cmd} queue")
    async def loop(self, ctx: CustomContext, mode: str = None):

        if not mode:

            embed = disnake.Embed(
                description="**リピートモードを選択してください:**",
                color=self.bot.get_color(ctx.guild.me)
            )

            msg = await ctx.send(
                ctx.author.mention,
                embed=embed,
                components=[
                    disnake.ui.Select(
                        placeholder="オプションを選択:",
                        custom_id="loop_mode_legacy",
                        options=[
                            disnake.SelectOption(label="現在の曲", value="current"),
                            disnake.SelectOption(label="キュー", value="queue"),
                            disnake.SelectOption(label="リピートを無効化", value="off")
                        ]
                    )
                ]
            )

            try:
                select: disnake.MessageInteraction = await self.bot.wait_for(
                    "dropdown", timeout=30,
                    check=lambda i: i.message.id == msg.id and i.author == ctx.author
                )
            except asyncio.TimeoutError:
                embed.description = "選択がタイムアウトしました！"
                try:
                    await msg.edit(embed=embed, view=None)
                except:
                    pass
                return

            mode = select.data.values[0]
            ctx.store_message = msg

        if mode.isdigit():

            if len(mode) > 2 or int(mode) > 10:
                raise GenericError(f"**無効な回数: {mode}**\n"
                                   "`許可される最大回数: 10`")

            await self.loop_amount.callback(self=self, inter=ctx, value=int(mode))
            return

        if mode not in ('current', 'queue', 'off'):
            raise GenericError("無効なモードです！current/queue/off から選択してください")

        await self.loop_mode.callback(self=self, inter=ctx, mode=mode)

    @is_dj()
    @has_source()
    @check_voice()
    @commands.slash_command(
        description=f"{desc_prefix}リピートモードを選択: 現在の曲 / キュー / 無効。",
        extras={"only_voiced": True}, cooldown=loop_cd, max_concurrency=loop_mc
    )
    @commands.contexts(guild=True)
    async def loop_mode(
            self,
            inter: disnake.ApplicationCommandInteraction,
            mode: str = commands.Param(
                name="modo",
                choices=[
                    disnake.OptionChoice(
                        disnake.Localized("Current", data={disnake.Locale.pt_BR: "Música Atual"}), "current"
                    ),
                    disnake.OptionChoice(
                        disnake.Localized("Queue", data={disnake.Locale.pt_BR: "Fila"}), "queue"
                    ),
                    disnake.OptionChoice(
                        disnake.Localized("Off", data={disnake.Locale.pt_BR: "Desativar"}), "off"
                    ),
                ]
            )
    ):

        try:
            bot = inter.music_bot
        except AttributeError:
            bot = inter.bot

        player: LavalinkPlayer = bot.music.players[inter.guild_id]

        if mode == player.loop:
            raise GenericError("**選択されたリピートモードは既に有効です...**")

        if mode == 'off':
            mode = False
            player.current.info["extra"]["track_loops"] = 0
            emoji = "⭕"
            txt = ['リピートを無効にしました。', f"{emoji} **⠂{inter.author.mention}がリピートを無効にしました。**"]

        elif mode == "current":
            player.current.info["extra"]["track_loops"] = 0
            emoji = "🔂"
            txt = ["現在の曲のリピートを有効にしました。",
                   f"{emoji} **⠂{inter.author.mention} が現在の曲のリピートを有効にしました。**"]

        else:  # queue
            emoji = "🔁"
            txt = ["キューのリピートを有効にしました。", f"{emoji} **⠂{inter.author.mention} がキューのリピートを有効にしました。**"]

        player.loop = mode

        bot.loop.create_task(player.process_rpc())

        await self.interaction_message(inter, txt, emoji=emoji)

    @is_dj()
    @has_source()
    @check_voice()
    @commands.slash_command(
        description=f"{desc_prefix}現在の曲のリピート回数を設定します。",
        extras={"only_voiced": True}, cooldown=loop_cd, max_concurrency=loop_mc
    )
    @commands.contexts(guild=True)
    async def loop_amount(
            self,
            inter: disnake.ApplicationCommandInteraction,
            value: int = commands.Param(name="valor", description="リピート回数。")
    ):

        try:
            bot = inter.music_bot
        except AttributeError:
            bot = inter.bot

        player: LavalinkPlayer = bot.music.players[inter.guild_id]

        player.current.info["extra"]["track_loops"] = value

        txt = [
            f"曲のリピート回数を "
            f"[`{(fix_characters(player.current.title, 25))}`](<{player.current.uri or player.current.search_uri}>) **{value}** に設定しました。",
            f"🔄 **⠂{inter.author.mention} が曲のリピート回数を [{value}] に設定しました:**\n"
            f"╰[`{player.current.title}`](<{player.current.uri or player.current.search_uri}>)"
        ]

        await self.interaction_message(inter, txt, rpc_update=True, emoji="🔄")

    remove_mc = commands.MaxConcurrency(1, per=commands.BucketType.guild, wait=False)

    @is_dj()
    @has_player()
    @check_voice()
    @pool_command(name="remove", aliases=["r", "del"], description="キューから特定の曲を削除します。",
                  only_voiced=True, max_concurrency=remove_mc, extras={"flags": case_sensitive_args},
                  usage="{prefix}{cmd} [曲名]\nEx: {prefix}{cmd} sekai")
    async def remove_legacy(self, ctx: CustomContext, *, flags: str = ""):

        args, unknown = ctx.command.extras['flags'].parse_known_args(flags.split())

        if not unknown:
            raise GenericError("**曲の名前を追加していません。**")

        await self.remove.callback(self=self, inter=ctx, query=" ".join(unknown), case_sensitive=args.casesensitive)

    @is_dj()
    @has_player()
    @check_voice()
    @commands.slash_command(
        description=f"{desc_prefix}キューから特定の曲を削除します。",
        extras={"only_voiced": True}, max_concurrency=remove_mc
    )
    @commands.contexts(guild=True)
    async def remove(
            self,
            inter: disnake.ApplicationCommandInteraction,
            query: str = commands.Param(name="nome", description="曲の完全な名前。"),
            case_sensitive: bool = commands.Param(
                name="nome_exato", default=False,
                description="単語ごとではなく、曲名の完全一致フレーズで検索します。",

            )
    ):

        try:
            bot = inter.music_bot
        except AttributeError:
            bot = inter.bot

        try:
            index = queue_track_index(inter, bot, query, case_sensitive=case_sensitive)[0][0]
        except IndexError:
            raise GenericError(f"**キューに曲名: {query} がありません**")

        player: LavalinkPlayer = bot.music.players[inter.guild_id]

        track = player.queue[index]

        player.queue.remove(track)

        txt = [
            f"曲 [`{(fix_characters(track.title, 25))}`](<{track.uri or track.search_uri}>) をキューから削除しました。",
            f"♻️ **⠂{inter.author.mention} が曲をキューから削除しました:**\n╰[`{track.title}`](<{track.uri or track.search_uri}>)"
        ]

        await self.interaction_message(inter, txt, emoji="♻️")

        await player.update_message()

    queue_manipulation_cd = commands.CooldownMapping.from_cooldown(2, 15, commands.BucketType.guild)

    @is_dj()
    @has_player()
    @check_voice()
    @pool_command(name="readd", aliases=["readicionar", "rdd"], only_voiced=True, cooldown=queue_manipulation_cd,
                  max_concurrency=remove_mc, description="再生済みの曲をキューに再追加します。")
    async def readd_legacy(self, ctx: CustomContext):
        await self.readd_songs.callback(self=self, inter=ctx)

    @is_dj()
    @has_player()
    @check_voice()
    @commands.slash_command(
        description=f"{desc_prefix}再生済みの曲をキューに再追加します。",
        extras={"only_voiced": True}, cooldown=queue_manipulation_cd, max_concurrency=remove_mc
    )
    @commands.contexts(guild=True)
    async def readd_songs(self, inter: disnake.ApplicationCommandInteraction):

        try:
            bot = inter.music_bot
        except AttributeError:
            bot = inter.bot

        player: LavalinkPlayer = bot.music.players[inter.guild_id]

        if not player.played and not player.failed_tracks:
            raise GenericError("**再生済みの曲がありません。**")

        qsize = len(player.played) + len(player.failed_tracks)

        player.played.reverse()
        player.failed_tracks.reverse()
        player.queue.extend(player.failed_tracks)
        player.queue.extend(player.played)
        player.played.clear()
        player.failed_tracks.clear()

        txt = [
            f"再生済みの [{qsize}] 曲をキューに再追加しました。",
            f"🎶 **⠂{inter.author.mention} が {qsize} 曲をキューに再追加しました。**"
        ]

        await self.interaction_message(inter, txt, emoji="🎶")

        await player.update_message()

        if not player.current:
            await player.process_next()
        else:
            await player.update_message()

    @is_dj()
    @has_player()
    @check_voice()
    @pool_command(name="rotate", aliases=["rt", "rotacionar"], only_voiced=True,
                  description="キューを指定した曲まで回転させます。",
                  cooldown=queue_manipulation_cd, max_concurrency=remove_mc, extras={"flags": case_sensitive_args},
                  usage="{prefix}{cmd} [曲名]\nEx: {prefix}{cmd} sekai")
    async def rotate_legacy(self, ctx: CustomContext, *, flags: str = ""):

        args, unknown = ctx.command.extras['flags'].parse_known_args(flags.split())

        if not unknown:
            raise GenericError("**曲の名前を追加していません。**")

        await self.rotate.callback(self=self, inter=ctx, query=" ".join(unknown), case_sensitive=args.casesensitive)

    @is_dj()
    @check_queue_loading()
    @has_player()
    @check_voice()
    @commands.slash_command(
        description=f"{desc_prefix}キューを指定した曲まで回転させます。",
        extras={"only_voiced": True}, cooldown=queue_manipulation_cd, max_concurrency=remove_mc
    )
    @commands.contexts(guild=True)
    async def rotate(
            self,
            inter: disnake.ApplicationCommandInteraction,
            query: str = commands.Param(name="nome", description="曲の完全な名前。"),
            case_sensitive: bool = commands.Param(
                name="nome_exato", default=False,
                description="単語ごとではなく、曲名の完全一致フレーズで検索します。",
            )
    ):

        try:
            bot = inter.music_bot
        except AttributeError:
            bot = inter.bot

        index = queue_track_index(inter, bot, query, case_sensitive=case_sensitive)

        if not index:
            raise GenericError(f"**キューに曲名: {query}**")

        index = index[0][0]

        player: LavalinkPlayer = bot.music.players[inter.guild_id]

        track = (player.queue + player.queue_autoplay)[index]

        if index <= 0:
            raise GenericError(f"**曲 **[`{track.title}`](<{track.uri or track.search_uri}>) は既にキューの次です。")

        if track.autoplay:
            player.queue_autoplay.rotate(0 - (index - len(player.queue)))
        else:
            player.queue.rotate(0 - (index))

        txt = [
            f"キューを曲 [`{(fix_characters(track.title, limit=25))}`](<{track.uri or track.search_uri}>) まで回転させました。",
            f"🔃 **⠂{inter.author.mention} がキューを曲まで回転させました:**\n╰[`{track.title}`](<{track.uri or track.search_uri}>)。"
        ]

        if isinstance(inter, disnake.MessageInteraction):
            player.set_command_log(text=f"{inter.author.mention} " + txt[0], emoji="🔃")
        else:
            await self.interaction_message(inter, txt, emoji="🔃", components=[
                disnake.ui.Button(emoji="▶️", label="今すぐ再生", custom_id=PlayerControls.embed_forceplay),
            ])

        await player.update_message()

    song_request_thread_cd = commands.CooldownMapping.from_cooldown(1, 120, commands.BucketType.guild)

    @is_dj()
    @has_player()
    @check_voice()
    @commands.bot_has_guild_permissions(manage_threads=True)
    @pool_command(name="songrequestthread", aliases=["songrequest", "srt"], only_voiced=True,
                  description="曲リクエスト用の一時的なスレッド/会話を作成します")
    async def song_request_thread_legacy(self, ctx: CustomContext):

        await self.song_request_thread.callback(self=self, inter=ctx)

    @is_dj()
    @has_player()
    @check_voice()
    @commands.slash_command(extras={"only_voiced": True}, cooldown=song_request_thread_cd,
                            description=f"{desc_prefix}曲リクエスト用の一時的なスレッド/会話を作成します")
    @commands.contexts(guild=True)
    async def song_request_thread(self, inter: disnake.ApplicationCommandInteraction):

        try:
            bot = inter.music_bot
            guild = inter.music_guild
        except AttributeError:
            bot = inter.bot
            guild = inter.guild

        if not self.bot.intents.message_content:
            raise GenericError("**現在メッセージコンテンツインテントがないため、"
                               "メッセージの内容を確認できません**")

        player: LavalinkPlayer = bot.music.players[guild.id]

        if player.static:
            raise GenericError("**曲リクエストチャンネルが設定されている場合、このコマンドは使用できません。**")

        if player.has_thread:
            raise GenericError("**プレイヤーに既にアクティブなスレッド/会話があります。**")

        if not isinstance(player.text_channel, disnake.TextChannel):
            raise GenericError("**プレイヤーコントローラーがスレッド/会話の作成に"
                               "対応していないチャンネルでアクティブです。**")

        if not player.controller_mode:
            raise GenericError("**現在のスキン/外観はスレッド/会話経由の曲リクエスト"
                               "システムに対応していません\n\n"
                               "注意:** `このシステムはボタンを使用するスキンが必要です。`")

        if not player.text_channel.permissions_for(guild.me).send_messages:
            raise GenericError(f"**{bot.user.mention} はチャンネル {player.text_channel.mention} でメッセージを送信する権限がありません。**")

        if not player.text_channel.permissions_for(guild.me).create_public_threads:
            raise GenericError(f"**{bot.user.mention} はパブリックスレッドを作成する権限がありません。**")

        if not [m for m in player.guild.me.voice.channel.members if not m.bot and
                player.text_channel.permissions_for(m).send_messages_in_threads]:
            raise GenericError(f"**チャンネル <#{player.channel_id}> にチャンネル {player.text_channel.mention} で"
                               f"スレッドにメッセージを送信する権限を持つメンバーがいません**")

        await inter.response.defer(ephemeral=True)

        thread = await player.message.create_thread(name=f"{bot.user.name} temp. song-request", auto_archive_duration=10080)

        txt = [
            "曲リクエスト用の一時的なスレッド/会話システムを有効にしました。",
            f"💬 **⠂{inter.author.mention} が曲リクエスト用の一時的な[スレッド/会話]({thread.jump_url})を作成しました。**"
        ]

        await self.interaction_message(inter, txt, emoji="💬", defered=True, force=True)

    nightcore_cd = commands.CooldownMapping.from_cooldown(1, 7, commands.BucketType.guild)
    nightcore_mc = commands.MaxConcurrency(1, per=commands.BucketType.guild, wait=False)

    @is_dj()
    @has_source()
    @check_voice()
    @pool_command(name="nightcore", aliases=["nc"], only_voiced=True, cooldown=nightcore_cd, max_concurrency=nightcore_mc,
                  description="ナイトコアエフェクトを有効/無効にします（高速再生＋高音）。")
    async def nightcore_legacy(self, ctx: CustomContext):

        await self.nightcore.callback(self=self, inter=ctx)

    @is_dj()
    @has_source()
    @check_voice()
    @commands.slash_command(
        description=f"{desc_prefix}ナイトコアエフェクトを有効/無効にします（高速再生＋高音）。",
        extras={"only_voiced": True}, cooldown=nightcore_cd, max_concurrency=nightcore_mc,
    )
    @commands.contexts(guild=True)
    async def nightcore(self, inter: disnake.ApplicationCommandInteraction):

        try:
            bot = inter.music_bot
        except AttributeError:
            bot = inter.bot

        player: LavalinkPlayer = bot.music.players[inter.guild_id]

        player.nightcore = not player.nightcore

        if player.nightcore:
            await player.set_timescale(pitch=1.2, speed=1.1)
            txt = "有効にしました"
        else:
            await player.set_timescale(enabled=False)
            await player.update_filters()
            txt = "無効にしました"

        txt = [f"ナイトコアエフェクトを{txt}。", f"🇳 **⠂{inter.author.mention} がナイトコアエフェクトを{txt}。**"]

        await self.interaction_message(inter, txt, emoji="🇳")


    np_cd = commands.CooldownMapping.from_cooldown(1, 7, commands.BucketType.member)

    @commands.command(name="nowplaying", aliases=["np", "npl", "current", "tocando", "playing"],
                 description="現在聴いている曲の情報を表示します。", cooldown=np_cd)
    async def now_playing_legacy(self, ctx: CustomContext):
        await self.now_playing.callback(self=self, inter=ctx)

    @commands.slash_command(description=f"{desc_prefix}現在聴いている曲の情報を表示します（任意のサーバー）。",
                            cooldown=np_cd, extras={"allow_private": True})
    @commands.contexts(guild=True)
    async def now_playing(self, inter: disnake.ApplicationCommandInteraction):

        player: Optional[LavalinkPlayer] = None

        for bot in self.bot.pool.get_guild_bots(inter.guild_id):

            try:
                p = bot.music.players[inter.guild_id]
            except KeyError:
                continue

            if not p.last_channel:
                continue

            if inter.author.id in p.last_channel.voice_states:
                player = p
                break

        if not player:

            if isinstance(inter, CustomContext) and not (await self.bot.is_owner(inter.author)):

                try:
                    slashcmd = f"</now_playing:" + str(self.bot.get_global_command_named("now_playing",
                                                                                                      cmd_type=disnake.ApplicationCommandType.chat_input).id) + ">"
                except AttributeError:
                    slashcmd = "/now_playing"

                raise GenericError("**アクティブなプレイヤーがあるこのサーバーのボイスチャンネルに接続する必要があります...**\n"
                                   f"`注意: 他のサーバーで聴いている場合はコマンド` {slashcmd} `を使用できます`")

            for bot in self.bot.pool.get_guild_bots(inter.guild_id):

                for player_id in bot.music.players:

                    if player_id == inter.guild_id:
                        continue

                    if inter.author.id in (p := bot.music.players[player_id]).last_channel.voice_states:
                        player = p
                        break

        if not player:
            raise GenericError("**アクティブなプレイヤーがあるボイスチャンネルに接続する必要があります...**")

        if not player.current:
            raise GenericError(f"**現在チャンネル {player.last_channel.mention} で何も再生していません**")

        guild_data = await player.bot.get_data(inter.guild_id, db_name=DBModel.guilds)

        ephemeral = (player.guild.id != inter.guild_id and not await player.bot.is_owner(inter.author)) or await self.is_request_channel(inter, data=guild_data)

        url = player.current.uri or player.current.search_uri

        if player.current.info["sourceName"] == "youtube":
            url += f"&t={int(player.position/1000)}s"

        txt = f"### [{player.current.title}](<{url}>)\n"

        footer_kw = {}

        if player.current.is_stream:
            txt += "> 🔴 **⠂ライブ配信**\n"
        else:
            progress = ProgressBar(
                player.position,
                player.current.duration,
                bar_count=8
            )

            txt += f"```ansi\n[34;1m[{time_format(player.position)}] {('=' * progress.start)}[0m🔴️[36;1m{'-' * progress.end} " \
                   f"[{time_format(player.current.duration)}][0m```\n"

        txt += f"> 👤 **⠂アップローダー:** {player.current.authors_md}\n"

        if player.current.album_name:
            txt += f"> 💽 **⠂アルバム:** [`{fix_characters(player.current.album_name, limit=20)}`]({player.current.album_url})\n"

        if not player.current.autoplay:
            txt += f"> ✋ **⠂リクエスト者:** <@{player.current.requester}>\n"
        else:
            try:
                mode = f" [`おすすめ`]({player.current.info['extra']['related']['uri']})"
            except:
                mode = "`おすすめ`"
            txt += f"> 👍 **⠂追加経由:** {mode}\n"

        if player.current.playlist_name:
            txt += f"> 📑 **⠂プレイリスト:** [`{fix_characters(player.current.playlist_name, limit=20)}`]({player.current.playlist_url})\n"

        try:
            txt += f"> *️⃣ **⠂ボイスチャンネル:** {player.guild.me.voice.channel.jump_url}\n"
        except AttributeError:
            pass

        txt += f"> 🔊 **⠂音量:** `{player.volume}%`\n"

        components = [disnake.ui.Button(custom_id=f"np_{inter.author.id}", label="更新", emoji="🔄")]

        if player.guild_id != inter.guild_id:

            if player.current and not player.paused and (listeners:=len([m for m in player.last_channel.members if not m.bot and (not m.voice.self_deaf or not m.voice.deaf)])) > 1:
                txt += f"> 🎧 **⠂現在のリスナー:** `{listeners}`\n"

            txt += f"> ⏱️ **⠂プレイヤー稼働中:** <t:{player.uptime}:R>\n"

            try:
                footer_kw = {"icon_url": player.guild.icon.with_static_format("png").url}
            except AttributeError:
                pass

            footer_kw["text"] = f"サーバー: {player.guild.name} [ ID: {player.guild.id} ]"

        else:
            try:
                if player.bot.user.id != self.bot.user.id:
                    footer_kw["text"] = f"選択されたBot: {player.bot.user.display_name}"
                    footer_kw["icon_url"] = player.bot.user.display_avatar.url
            except AttributeError:
                pass

        if player.keep_connected:
            txt += "> ♾️ **⠂24/7モード:** `有効`\n"

        if player.queue or player.queue_autoplay:

            if player.guild_id == inter.guild_id:

                txt += f"### 🎶 ⠂次の曲 ({(qsize := len(player.queue + player.queue_autoplay))}):\n" + (
                            "\n").join(
                    f"> `{n + 1})` [`{fix_characters(t.title, limit=28)}`](<{t.uri}>)\n" \
                    f"> `⏲️ {time_format(t.duration) if not t.is_stream else '🔴 ライブ'}`" + (
                        f" - `リピート: {t.track_loops}`" if t.track_loops else "") + \
                    f" **|** " + (f"`✋` <@{t.requester}>" if not t.autoplay else f"`👍⠂おすすめ`") for n, t in
                    enumerate(itertools.islice(player.queue + player.queue_autoplay, 3))
                )

                if qsize > 3:
                    components.append(
                        disnake.ui.Button(custom_id=PlayerControls.queue, label="完全なリストを表示",
                                          emoji="<:music_queue:703761160679194734>"))

            elif player.queue:
                txt += f"> 🎶 **⠂キューの曲数:** `{len(player.queue)}`\n"

        if player.static and player.guild_id == inter.guild_id:
            if player.message:
                components.append(
                    disnake.ui.Button(url=player.message.jump_url, label="プレイヤーコントローラーへ",
                                      emoji="🔳"))
            elif player.text_channel:
                txt += f"\n\n`プレイヤーコントローラーにアクセス:` {player.text_channel.mention}"

        embed = disnake.Embed(description=txt, color=self.bot.get_color(player.guild.me))

        embed.set_author(name=("⠂再生中:" if inter.guild_id == player.guild_id else "現在聴いています:") if not player.paused else "⠂現在の曲:",
                         icon_url=music_source_image(player.current.info["sourceName"]))

        embed.set_thumbnail(url=player.current.thumb)

        if footer_kw:
            embed.set_footer(**footer_kw)

        if isinstance(inter, disnake.MessageInteraction):
            await inter.response.edit_message(inter.author.mention, embed=embed, components=components)
        else:
            await inter.send(inter.author.mention, embed=embed, ephemeral=ephemeral, components=components)

    @commands.Cog.listener("on_button_click")
    async def reload_np(self, inter: disnake.MessageInteraction):

        if not inter.data.custom_id.startswith("np_"):
            return

        if inter.data.custom_id != f"np_{inter.author.id}":
            await inter.send("このボタンをクリックすることはできません...", ephemeral=True)
            return

        try:
            inter.application_command = self.now_playing_legacy
            await check_cmd(self.now_playing_legacy, inter)
            await self.now_playing_legacy(inter)
        except Exception as e:
            self.bot.dispatch('interaction_player_error', inter, e)

    controller_cd = commands.CooldownMapping.from_cooldown(1, 10, commands.BucketType.member)
    controller_mc = commands.MaxConcurrency(1, per=commands.BucketType.member, wait=False)

    @has_source()
    @check_voice()
    @pool_command(name="controller", aliases=["ctl"], only_voiced=True, cooldown=controller_cd,
                  max_concurrency=controller_mc, description="プレイヤーコントローラーを指定/現在のチャンネルに送信します。")
    async def controller_legacy(self, ctx: CustomContext):
        await self.controller.callback(self=self, inter=ctx)

    @has_source()
    @check_voice()
    @commands.slash_command(description=f"{desc_prefix}プレイヤーコントローラーを指定/現在のチャンネルに送信します。",
                            extras={"only_voiced": True}, cooldown=controller_cd, max_concurrency=controller_mc)
    @commands.contexts(guild=True)
    async def controller(self, inter: disnake.ApplicationCommandInteraction):

        try:
            bot = inter.music_bot
            guild = inter.music_guild
            channel = bot.get_channel(inter.channel.id)
        except AttributeError:
            bot = inter.bot
            guild = inter.guild
            channel = inter.channel

        player: LavalinkPlayer = bot.music.players[guild.id]

        if player.static:
            raise GenericError("このコマンドはプレイヤーの固定モードでは使用できません。")

        if player.has_thread:
            raise GenericError("**プレイヤーの[メッセージ]({player.message.jump_url})でアクティブな会話がある場合、"
                               "このコマンドは使用できません。**")

        if not inter.response.is_done():
            await inter.response.defer(ephemeral=True)

        if channel != player.text_channel:

            await is_dj().predicate(inter)

            try:

                player.set_command_log(
                    text=f"{inter.author.mention} がプレイヤーコントローラーをチャンネル {inter.channel.mention} に移動しました。",
                    emoji="💠"
                )

                embed = disnake.Embed(
                    description=f"💠 **⠂{inter.author.mention} がプレイヤーコントローラーをチャンネルに移動しました:** {channel.mention}",
                    color=self.bot.get_color(guild.me)
                )

                try:
                    if bot.user.id != self.bot.user.id:
                        embed.set_footer(text=f"選択されたBot: {bot.user.display_name}", icon_url=bot.user.display_avatar.url)
                except AttributeError:
                    pass

                await player.text_channel.send(embed=embed)

            except:
                pass

        await player.destroy_message()

        player.text_channel = channel

        await player.invoke_np()

        if not isinstance(inter, CustomContext):
            await inter.edit_original_message("**プレイヤーが正常に再送信されました！**")

    @is_dj()
    @has_player()
    @check_voice()
    @commands.user_command(name=disnake.Localized("Add DJ", data={disnake.Locale.pt_BR: "Adicionar DJ"}),
                           extras={"only_voiced": True})
    async def adddj_u(self, inter: disnake.UserCommandInteraction):
        await self.add_dj(interaction=inter, user=inter.target)

    @is_dj()
    @has_player()
    @check_voice()
    @pool_command(name="adddj", aliases=["adj"], only_voiced=True,
                  description="現在のプレイヤーセッションのDJリストにメンバーを追加します。",
                  usage="{prefix}{cmd} [id|名前|@user]\nEx: {prefix}{cmd} @メンバー")
    async def add_dj_legacy(self, ctx: CustomContext, user: disnake.Member):
        await self.add_dj.callback(self=self, inter=ctx, user=user)

    @is_dj()
    @has_player()
    @check_voice()
    @commands.slash_command(
        description=f"{desc_prefix}現在のプレイヤーセッションのDJリストにメンバーを追加します。",
        extras={"only_voiced": True}
    )
    @commands.contexts(guild=True)
    async def add_dj(
            self,
            inter: disnake.ApplicationCommandInteraction, *,
            user: disnake.User = commands.Param(name="membro", description="追加するメンバー。")
    ):

        error_text = None

        try:
            bot = inter.music_bot
            guild = inter.music_guild
            channel = bot.get_channel(inter.channel.id)
        except AttributeError:
            bot = inter.bot
            guild = inter.guild
            channel = inter.channel

        player: LavalinkPlayer = bot.music.players[guild.id]

        user = guild.get_member(user.id)

        if user.bot:
            error_text = "**ボットをDJリストに追加することはできません。**"
        elif user == inter.author:
            error_text = "**自分自身をDJリストに追加することはできません。**"
        elif user.guild_permissions.manage_channels:
            error_text = f"メンバー {user.mention} をDJリストに追加できません（**チャンネル管理**権限を持っています）。"
        elif user.id == player.player_creator:
            error_text = f"**メンバー {user.mention} はプレイヤーの作成者です...**"
        elif user.id in player.dj:
            error_text = f"**メンバー {user.mention} は既にDJリストに含まれています**"

        if error_text:
            raise GenericError(error_text)

        player.dj.add(user.id)

        text = [f"{user.mention} をDJリストに追加しました。",
                f"🎧 **⠂{inter.author.mention} が {user.mention} をDJリストに追加しました。**"]

        if (player.static and channel == player.text_channel) or isinstance(inter.application_command,
                                                                            commands.InvokableApplicationCommand):
            await inter.send(f"{user.mention} がDJリストに追加されました！{player.controller_link}")

        await self.interaction_message(inter, txt=text, emoji="🎧")

    @is_dj()
    @has_player()
    @check_voice()
    @commands.slash_command(
        description=f"{desc_prefix}現在のプレイヤーセッションのDJリストからメンバーを削除します。",
        extras={"only_voiced": True}
    )
    @commands.contexts(guild=True)
    async def remove_dj(
            self,
            inter: disnake.ApplicationCommandInteraction, *,
            user: disnake.User = commands.Param(name="membro", description="削除するメンバー。")
    ):

        try:
            bot = inter.music_bot
            guild = inter.music_guild
            channel = bot.get_channel(inter.channel.id)
        except AttributeError:
            bot = inter.bot
            guild = inter.guild
            channel = inter.channel

        player: LavalinkPlayer = bot.music.players[guild.id]

        user = guild.get_member(user.id)

        if user.id == player.player_creator:
            if inter.author.guild_permissions.manage_guild:
                player.player_creator = None
            else:
                raise GenericError(f"**メンバー {user.mention} はプレイヤーの作成者です。**")

        elif user.id not in player.dj:
            GenericError(f"メンバー {user.mention} はDJリストに含まれていません")

        else:
            player.dj.remove(user.id)

        text = [f"{user.mention} をDJリストから削除しました。",
                f"🎧 **⠂{inter.author.mention} が {user.mention} をDJリストから削除しました。**"]

        if (player.static and channel == player.text_channel) or isinstance(inter.application_command,
                                                                            commands.InvokableApplicationCommand):
            await inter.send(f"{user.mention} がDJリストから削除されました！{player.controller_link}")

        await self.interaction_message(inter, txt=text, emoji="🎧")

    @has_player()
    @check_voice()
    @pool_command(name="commandlog", aliases=["cmdlog", "clog", "cl"], only_voiced=True,
                  description="コマンド使用ログを表示します。")
    async def command_log_legacy(self, ctx: CustomContext):
        await self.command_log.callback(self=self, inter=ctx)

    @has_player(check_node=False)
    @check_voice()
    @commands.slash_command(
        description=f"{desc_prefix}コマンド使用ログを表示します。",
        extras={"only_voiced": True}
    )
    @commands.contexts(guild=True)
    async def command_log(self, inter: disnake.ApplicationCommandInteraction):

        try:
            bot = inter.music_bot
        except AttributeError:
            bot = inter.bot

        player: LavalinkPlayer = bot.music.players[inter.guild_id]

        if not player.command_log_list:
            raise GenericError("**コマンドログは空です...**")

        embed = disnake.Embed(
            description="### コマンドログ:\n" + "\n\n".join(f"{i['emoji']} ⠂{i['text']}\n<t:{int(i['timestamp'])}:R>" for i in player.command_log_list),
            color=player.guild.me.color
        )

        if isinstance(inter, CustomContext):
            await inter.reply(embed=embed)
        else:
            await inter.send(embed=embed, ephemeral=True)

    @is_dj()
    @has_player()
    @check_voice()
    @pool_command(name="stop", aliases=["leave", "parar"], only_voiced=True,
                  description="プレイヤーを停止し、ボイスチャンネルから切断します。")
    async def stop_legacy(self, ctx: CustomContext):
        await self.stop.callback(self=self, inter=ctx)

    @is_dj()
    @has_player(check_node=False)
    @check_voice()
    @commands.slash_command(
        description=f"{desc_prefix}プレイヤーを停止し、ボイスチャンネルから切断します。",
        extras={"only_voiced": True}
    )
    @commands.contexts(guild=True)
    async def stop(self, inter: disnake.ApplicationCommandInteraction):

        try:
            bot = inter.music_bot
            guild = inter.music_guild
            inter_destroy = inter if bot.user.id == self.bot.user.id else None
        except AttributeError:
            bot = inter.bot
            guild = inter.guild
            inter_destroy = inter

        player: LavalinkPlayer = bot.music.players[inter.guild_id]
        player.set_command_log(text=f"{inter.author.mention} **がプレイヤーを停止しました！**", emoji="🛑", controller=True)

        self.bot.pool.song_select_cooldown.get_bucket(inter).update_rate_limit()

        if isinstance(inter, disnake.MessageInteraction):
            await player.destroy(inter=inter_destroy)
        else:

            embed = disnake.Embed(
                color=self.bot.get_color(guild.me),
                description=f"🛑 **⠂{inter.author.mention} がプレイヤーを停止しました。**"
            )

            try:
                if bot.user.id != self.bot.user.id:
                    embed.set_footer(text=f"選択されたBot: {bot.user.display_name}", icon_url=bot.user.display_avatar.url)
            except AttributeError:
                pass

            try:
                ephemeral = player.text_channel.id == inter.channel_id and player.static
            except:
                ephemeral = player.static

            await inter.send(
                embed=embed,
                ephemeral=ephemeral
            )
            await player.destroy()

    @check_queue_loading()
    @has_player()
    @check_voice()
    @pool_command(
        name="savequeue", aliases=["sq", "svq"],
        only_voiced=True, cooldown=queue_manipulation_cd, max_concurrency=remove_mc,
        description="実験的: 現在の曲とキューを保存し、いつでも再利用できるようにします。"
    )
    async def savequeue_legacy(self, ctx: CustomContext):
        await self.save_queue.callback(self=self, inter=ctx)

    @check_queue_loading()
    @has_player()
    @check_voice()
    @commands.slash_command(
        description=f"{desc_prefix}実験的: 現在の曲とキューを保存し、いつでも再利用できるようにします。",
        extras={"only_voiced": True}, cooldown=queue_manipulation_cd, max_concurrency=remove_mc
    )
    @commands.contexts(guild=True)
    async def save_queue(self, inter: disnake.ApplicationCommandInteraction):

        try:
            bot = inter.music_bot
            guild = inter.music_guild
        except AttributeError:
            bot = inter.bot
            guild = bot.get_guild(inter.guild_id)

        player: LavalinkPlayer = bot.music.players[inter.guild_id]

        tracks = []

        if player.current:
            player.current.info["id"] = player.current.id
            if player.current.playlist:
                player.current.info["playlist"] = {"name": player.current.playlist_name, "url": player.current.playlist_url}
            tracks.append(player.current.info)

        for t in player.queue:
            t.info["id"] = t.id
            if t.playlist:
                t.info["playlist"] = {"name": t.playlist_name, "url": t.playlist_url}
            tracks.append(t.info)

        if len(tracks) < 3:
            raise GenericError(f"**保存するには最低3曲が必要です（現在の曲および/またはキュー内）**")

        if not os.path.isdir(f"./local_database/saved_queues_v1/users"):
            os.makedirs(f"./local_database/saved_queues_v1/users")

        async with aiofiles.open(f"./local_database/saved_queues_v1/users/{inter.author.id}.pkl", "wb") as f:
            await f.write(
                zlib.compress(
                    pickle.dumps(
                        {
                            "tracks": tracks, "created_at": disnake.utils.utcnow(), "guild_id": inter.guild_id
                        }
                    )
                )
            )

        await inter.response.defer(ephemeral=True)

        global_data = await self.bot.get_global_data(guild.id, db_name=DBModel.guilds)

        try:
            slashcmd = f"</play:" + str(self.bot.get_global_command_named("play", cmd_type=disnake.ApplicationCommandType.chat_input).id) + ">"
        except AttributeError:
            slashcmd = "/play"

        embed = disnake.Embed(
            color=bot.get_color(guild.me),
            description=f"### {inter.author.mention}: キューが正常に保存されました！！\n"
                        f"**保存された曲数:** `{len(tracks)}`\n"
                        "### 使用方法\n"
                        f"* コマンド {slashcmd} を使用（検索の自動補完で選択）\n"
                        "* プレイヤーのお気に入り/連携再生ボタン/セレクトをクリック。\n"
                        f"* コマンド {global_data['prefix'] or self.bot.default_prefix}{self.play_legacy.name} "
                        "を曲/動画の名前やリンクなしで使用。"
        )

        embed.set_footer(text="注意: これは非常に実験的な機能です。保存されたキューは将来の"
                              "アップデートで変更または削除される可能性があります")

        if isinstance(inter, CustomContext):
            await inter.reply(embed=embed)
        else:
            await inter.edit_original_response(embed=embed)


    @has_player()
    @check_voice()
    @commands.slash_command(name="queue", extras={"only_voiced": True})
    @commands.contexts(guild=True)
    async def q(self, inter):
        pass

    @is_dj()
    @has_player()
    @check_voice()
    @pool_command(name="shuffle", aliases=["sf", "shf", "sff", "misturar"], only_voiced=True,
                  description="キューの曲をシャッフルします", cooldown=queue_manipulation_cd, max_concurrency=remove_mc)
    async def shuffle_legacy(self, ctx: CustomContext):
        await self.shuffle_.callback(self, inter=ctx)

    @is_dj()
    @q.sub_command(
        name="shuffle",
        description=f"{desc_prefix}キューの曲をシャッフルします",
        extras={"only_voiced": True}, cooldown=queue_manipulation_cd, max_concurrency=remove_mc)
    async def shuffle_(self, inter: disnake.ApplicationCommandInteraction):

        try:
            bot = inter.music_bot
        except AttributeError:
            bot = inter.bot

        player: LavalinkPlayer = bot.music.players[inter.guild_id]

        if len(player.queue) < 3:
            raise GenericError("**キューをシャッフルするには最低3曲が必要です。**")

        shuffle(player.queue)

        await self.interaction_message(
            inter,
            ["キューの曲をシャッフルしました。",
             f"🔀 **⠂{inter.author.mention} がキューの曲をシャッフルしました。**"],
            emoji="🔀"
        )

    @is_dj()
    @has_player()
    @check_voice()
    @pool_command(name="reverse", aliases=["invert", "inverter", "rv"], only_voiced=True,
                  description="キューの曲の順序を逆にします", cooldown=queue_manipulation_cd, max_concurrency=remove_mc)
    async def reverse_legacy(self, ctx: CustomContext):
        await self.reverse.callback(self=self, inter=ctx)

    @is_dj()
    @q.sub_command(
        description=f"{desc_prefix}キューの曲の順序を逆にします",
        extras={"only_voiced": True}, cooldown=queue_manipulation_cd, max_concurrency=remove_mc
    )
    async def reverse(self, inter: disnake.ApplicationCommandInteraction):

        try:
            bot = inter.music_bot
        except AttributeError:
            bot = inter.bot

        player: LavalinkPlayer = bot.music.players[inter.guild_id]

        if len(player.queue) < 2:
            raise GenericError("**キューの順序を逆にするには最低2曲が必要です。**")

        player.queue.reverse()
        await self.interaction_message(
            inter,
            txt=["キューの曲の順序を逆にしました。",
                 f"🔄 **⠂{inter.author.mention} がキューの曲の順序を逆にしました。**"],
            emoji="🔄"
        )

    queue_show_mc = commands.MaxConcurrency(1, per=commands.BucketType.member, wait=False)

    @has_player()
    @check_voice()
    @pool_command(name="queue", aliases=["q", "fila"], description="キューの曲を表示します。",
                  only_voiced=True, max_concurrency=queue_show_mc)
    async def queue_show_legacy(self, ctx: CustomContext):
        await self.display.callback(self=self, inter=ctx)

    @commands.max_concurrency(1, commands.BucketType.member)
    @q.sub_command(
        description=f"{desc_prefix}キューの曲を表示します。", max_concurrency=queue_show_mc
    )
    async def display(self, inter: disnake.ApplicationCommandInteraction):

        try:
            bot = inter.music_bot
        except AttributeError:
            bot = inter.bot

        player: LavalinkPlayer = bot.music.players[inter.guild_id]

        if not player.queue and not player.queue_autoplay:
            raise GenericError("**キューに曲がありません。**")

        view = QueueInteraction(bot, inter.author)
        embed = view.embed

        try:
            if bot.user.id != self.bot.user.id:
                embed.set_footer(text=f"選択されたBot: {bot.user.display_name}", icon_url=bot.user.display_avatar.url)
        except AttributeError:
            pass

        await inter.response.defer(ephemeral=True)

        kwargs = {
            "embed": embed,
            "view": view
        }

        try:
            func = inter.followup.send
            kwargs["ephemeral"] = True
        except AttributeError:
            try:
                func = inter.edit_original_message
            except AttributeError:
                func = inter.send
                kwargs["ephemeral"] = True

        view.message = await func(**kwargs)

        await view.wait()

    adv_queue_flags = CommandArgparse()

    adv_queue_flags.add_argument('-songtitle', '-name', '-title', '-songname', nargs='+',
                                 help="曲名に含まれる名前を指定。\n例: -name NCS", default=[])
    adv_queue_flags.add_argument('-uploader', '-author', '-artist', nargs='+', default=[],
                                 help="指定したアップローダー/アーティスト名を含む曲を削除。\n例: -uploader sekai")
    adv_queue_flags.add_argument('-member', '-user', '-u', nargs='+', default=[],
                                 help="指定したユーザーがリクエストした曲を削除。\n例: -user @user")
    adv_queue_flags.add_argument('-duplicates', '-dupes', '-duplicate', action='store_true',
                                 help="重複した曲を削除。")
    adv_queue_flags.add_argument('-playlist', '-list', '-pl', nargs='+', default=[],
                                 help="関連付けられたプレイリスト名を含む曲を削除。\n例: -playlist myplaylist")
    adv_queue_flags.add_argument('-minimaltime', '-mintime', '-min', '-minduration', '-minduration', default=None,
                                 help="指定した最小再生時間の曲を削除。\n例: -min 1:23")
    adv_queue_flags.add_argument('-maxduration', '-maxtime', '-max', default=None,
                                 help="指定した最大再生時間の曲を削除。\n例: -max 1:23")
    adv_queue_flags.add_argument('-amount', '-counter', '-count', '-c', type=int, default=None,
                                 help="移動する曲の数を指定。\n例: -amount 5")
    adv_queue_flags.add_argument('-startposition', '-startpos', '-start', type=int, default=0,
                                 help="キューの開始位置から曲を削除。\n例: -start 10")
    adv_queue_flags.add_argument('-endposition', '-endpos', '-end', type=int, default=0,
                                 help="キューの指定位置まで曲を削除。\n例: -end 15")
    adv_queue_flags.add_argument('-absentmembers', '-absent', '-abs', action='store_true',
                                 help="チャンネルから退出したメンバーが追加した曲を削除")

    clear_flags = CommandArgparse(parents=[adv_queue_flags])

    @is_dj()
    @has_player()
    @check_voice()
    @pool_command(name="clear", aliases=["limpar", "clearqueue"], description="音楽キューをクリアします。",
                  only_voiced=True,
                  extras={"flags": clear_flags}, cooldown=queue_manipulation_cd, max_concurrency=remove_mc)
    async def clear_legacy(self, ctx: CustomContext, *, flags: str = ""):

        args, unknown = ctx.command.extras['flags'].parse_known_args(flags.split())

        await self.clear.callback(
            self=self, inter=ctx,
            song_name=" ".join(args.songtitle + unknown),
            song_author=" ".join(args.uploader),
            user=await commands.MemberConverter().convert(ctx, " ".join(args.member)) if args.member else None,
            duplicates=args.duplicates,
            playlist=" ".join(args.playlist),
            min_duration=args.minimaltime,
            max_duration=args.maxduration,
            amount=args.amount,
            range_start=args.startposition,
            range_end=args.endposition,
            absent_members=args.absentmembers
        )

    @check_queue_loading()
    @is_dj()
    @has_player()
    @check_voice()
    @q.sub_command(
        name="clear",
        description=f"{desc_prefix}音楽キューをクリアします。",
        extras={"only_voiced": True}, cooldown=queue_manipulation_cd, max_concurrency=remove_mc
    )
    async def clear(
            self,
            inter: disnake.ApplicationCommandInteraction,
            song_name: str = commands.Param(name="nome", description="曲名に含まれる名前を指定。",
                                            default=None),
            song_author: str = commands.Param(name="uploader",
                                              description="曲のアップローダー/アーティスト名に含まれる名前を指定。",
                                              default=None),
            user: disnake.Member = commands.Param(name='membro',
                                                  description="選択したメンバーがリクエストした曲を含める。",
                                                  default=None),
            duplicates: bool = commands.Param(name="duplicados", description="重複した曲を含める",
                                              default=False),
            playlist: str = commands.Param(description="プレイリスト名に含まれる名前を指定。", default=None),
            min_duration: str = commands.Param(name="duração_inicial",
                                               description="指定した再生時間以上の曲を含める（例: 1:23）。",
                                               default=None),
            max_duration: str = commands.Param(name="duração_máxima",
                                               description="指定した最大再生時間の曲を含める（例: 1:45）。",
                                               default=None),
            amount: int = commands.Param(name="quantidade", description="移動する曲の数。",
                                         min_value=0, max_value=99, default=None),
            range_start: int = commands.Param(name="posição_inicial",
                                              description="指定した位置からキューの曲を含める。",
                                              min_value=1.0, max_value=500.0, default=0),
            range_end: int = commands.Param(name="posição_final",
                                            description="指定した位置までキューの曲を含める。",
                                            min_value=1.0, max_value=500.0, default=0),
            absent_members: bool = commands.Param(name="membros_ausentes",
                                                  description="チャンネルから退出したメンバーが追加した曲を含める。",
                                                  default=False)
    ):

        if min_duration and max_duration:
            raise GenericError(
                "**duração_abaixo_de** または **duração_acima_de** のいずれか一つだけを選択してください。")

        try:
            bot = inter.music_bot
        except AttributeError:
            bot = inter.bot

        player: LavalinkPlayer = bot.music.players[inter.guild_id]

        if not player.queue:
            raise GenericError("**キューに曲がありません。**")

        if amount is None:
            amount = 0

        filters = []
        final_filters = set()

        txt = []
        playlist_hyperlink = set()

        tracklist = []

        if song_name:
            song_name = song_name.replace("️", "")
            filters.append('song_name')
        if song_author:
            song_author = song_author.replace("️", "")
            filters.append('song_author')
        if user:
            filters.append('user')
        if playlist:
            playlist = playlist.replace("️", "")
            filters.append('playlist')
        if min_duration:
            filters.append('time_below')
            min_duration = string_to_seconds(min_duration) * 1000
        if max_duration:
            filters.append('time_above')
            max_duration = string_to_seconds(max_duration) * 1000
        if absent_members:
            filters.append('absent_members')
        if duplicates:
            filters.append('duplicates')

        if not filters and not range_start and not range_end:
            player.queue.clear()
            txt = ['音楽キューをクリアしました。', f'♻️ **⠂{inter.author.mention} が音楽キューをクリアしました。**']

        else:

            if range_start > 0 and range_end > 0:

                if range_start >= range_end:
                    raise GenericError("**終了位置は開始位置より大きくなければなりません！**")

                song_list = list(player.queue)[range_start - 1: -(range_end - 1)]
                txt.append(f"**キューの開始位置:** `{range_start}`\n"
                           f"**キューの終了位置:** `{range_end}`")

            elif range_start > 0:
                song_list = list(player.queue)[range_start - 1:]
                txt.append(f"**キューの開始位置:** `{range_start}`")
            elif range_end > 0:
                song_list = list(player.queue)[:-(range_end - 1)]
                txt.append(f"**キューの終了位置:** `{range_end}`")
            else:
                song_list = list(player.queue)

            deleted_tracks = 0

            duplicated_titles = set()

            amount_counter = int(amount) if amount > 0 else 0

            for t in song_list:

                if amount and amount_counter < 1:
                    break

                temp_filter = list(filters)

                if 'duplicates' in temp_filter:
                    if (title:=f"{t.author} - {t.title}".lower()) in duplicated_titles:
                        temp_filter.remove('duplicates')
                        final_filters.add('duplicates')
                    else:
                        duplicated_titles.add(title)

                if 'time_below' in temp_filter and t.duration >= min_duration:
                    temp_filter.remove('time_below')
                    final_filters.add('time_below')

                elif 'time_above' in temp_filter and t.duration <= max_duration:
                    temp_filter.remove('time_above')
                    final_filters.add('time_above')

                if 'song_name' in temp_filter:

                    title = t.title.replace("️", "").lower().split()

                    query_words = song_name.lower().split()

                    word_count = 0

                    for query_word in song_name.lower().split():
                        for title_word in title:
                            if query_word in title_word:
                                title.remove(title_word)
                                word_count += 1
                                break

                    if word_count == len(query_words):
                        temp_filter.remove('song_name')
                        final_filters.add('song_name')

                if 'song_author' in temp_filter and song_author.lower() in t.author.replace("️", "").lower():
                    temp_filter.remove('song_author')
                    final_filters.add('song_author')

                if 'user' in temp_filter and user.id == t.requester:
                    temp_filter.remove('user')
                    final_filters.add('user')

                elif 'absent_members' in temp_filter and t.requester not in player.guild.me.voice.channel.voice_states:
                    temp_filter.remove('absent_members')
                    final_filters.add('absent_members')

                playlist_link = None

                if 'playlist' in temp_filter:
                    if playlist == t.playlist_name.replace("️", "") or (isinstance(inter, CustomContext) and playlist.lower() in t.playlist_name.replace("️", "").lower()):
                        playlist_link = f"[`{fix_characters(t.playlist_name)}`](<{t.playlist_url}>)"
                        temp_filter.remove('playlist')
                        final_filters.add('playlist')

                if not temp_filter:
                    tracklist.append(t)
                    player.queue.remove(t)
                    deleted_tracks += 1
                    if playlist_link:
                        playlist_hyperlink.add(playlist_link)

                    if amount:
                        amount_counter -= 1

            duplicated_titles.clear()

            if not deleted_tracks:
                await inter.send("該当する曲が見つかりませんでした！", ephemeral=True)
                return

            try:
                final_filters.remove("song_name")
                txt.append(f"**名前に含まれる:** `{fix_characters(song_name)}`")
            except:
                pass

            try:
                final_filters.remove("song_author")
                txt.append(f"**アップローダー/アーティスト名に含まれる:** `{fix_characters(song_author)}`")
            except:
                pass

            try:
                final_filters.remove("user")
                txt.append(f"**リクエストしたメンバー:** {user.mention}")
            except:
                pass

            try:
                final_filters.remove("playlist")
                txt.append(f"**プレイリスト:** {' | '.join(playlist_hyperlink)}")
            except:
                pass

            try:
                final_filters.remove("time_below")
                txt.append(f"**最小再生時間:** `{time_format(min_duration)}`")
            except:
                pass

            try:
                final_filters.remove("time_above")
                txt.append(f"**最大再生時間:** `{time_format(max_duration)}`")
            except:
                pass

            try:
                final_filters.remove("duplicates")
                txt.append(f"**重複した曲**")
            except:
                pass

            try:
                final_filters.remove("absent_members")
                txt.append("`チャンネルから退出したメンバーがリクエストした曲。`")
            except:
                pass

            msg_txt = f"### ♻️ ⠂{inter.author.mention} がキューから {deleted_tracks} 曲を削除しました:\n" + "\n".join(f"[`{fix_characters(t.title, 45)}`](<{t.uri}>)" for t in tracklist[:7])

            if (trackcount:=(len(tracklist) - 7)) > 0:
                msg_txt += f"\n`その他 {trackcount} 曲。`"

            msg_txt += f"\n### ✅ ⠂使用したフィルター:\n" + '\n'.join(txt)

            txt = [f"clearコマンドでキューから {deleted_tracks} 曲を削除しました。", msg_txt]

        try:
            kwargs = {"thumb": tracklist[0].thumb}
        except IndexError:
            kwargs = {}

        await self.interaction_message(inter, txt, emoji="♻️", **kwargs)


    move_queue_flags = CommandArgparse(parents=[adv_queue_flags])
    move_queue_flags.add_argument('-position', '-pos',
                           help="移動先の位置を指定（任意）。\n例: -pos 1",
                           type=int, default=None)
    move_queue_flags.add_argument('-casesensitive', '-cs',  action='store_true',
                           help="単語単位ではなく、曲名の完全一致で検索します。")

    @check_queue_loading()
    @is_dj()
    @has_player()
    @check_voice()
    @pool_command(name="move", aliases=["movequeue", "moveadv", "moveadvanced", "moveq", "mq", "mv", "mover"],
                  description="キュー内の曲を移動します。", only_voiced=True,
                  extras={"flags": move_queue_flags}, cooldown=queue_manipulation_cd, max_concurrency=remove_mc)
    async def move_legacy(self, ctx: CustomContext, position: Optional[int] = None, *, flags: str = ""):

        args, unknown = ctx.command.extras['flags'].parse_known_args(flags.split())

        if args.position:
            if position:
                unknown.insert(0, str(position))
            position = args.position

        if position is None:
            position = 1

        await self.do_move(
            inter=ctx,
            position=position,
            song_name=" ".join(unknown + args.songtitle),
            song_author=" ".join(args.uploader),
            user=await commands.MemberConverter().convert(ctx, " ".join(args.member)) if args.member else None,
            duplicates=args.duplicates,
            playlist=" ".join(args.playlist),
            min_duration=args.minimaltime,
            max_duration=args.maxduration,
            amount=args.amount,
            range_start=args.startposition,
            range_end=args.endposition,
            absent_members=args.absentmembers
        )

    @check_queue_loading()
    @is_dj()
    @has_player()
    @check_voice()
    @commands.slash_command(
        name="move",
        description=f"{desc_prefix}キュー内の曲を移動します。",
        extras={"only_voiced": True}, cooldown=queue_manipulation_cd, max_concurrency=remove_mc
    )
    @commands.contexts(guild=True)
    async def move(
            self,
            inter: disnake.ApplicationCommandInteraction,
            song_name: str = commands.Param(name="nome", description="曲名に含まれる名前を指定。",
                                            default=None),
            position: int = commands.Param(name="posição", description="キュー内の移動先の位置（任意）。",
                                           min_value=1, max_value=900, default=1),
            song_author: str = commands.Param(name="uploader",
                                              description="曲のアップローダー/アーティスト名に含まれる名前を指定。",
                                              default=None),
            user: disnake.Member = commands.Param(name='membro',
                                                  description="選択したメンバーがリクエストした曲を含める。",
                                                  default=None),
            duplicates: bool = commands.Param(name="duplicados", description="重複した曲を含める",
                                              default=False),
            playlist: str = commands.Param(description="プレイリスト名に含まれる名前を指定。", default=None),
            min_duration: str = commands.Param(name="duração_inicial",
                                               description="指定した再生時間以上の曲を含める（例: 1:23）。",
                                               default=None),
            max_duration: str = commands.Param(name="duração_máxima",
                                               description="指定した最大再生時間の曲を含める（例: 1:45）。",
                                               default=None),
            amount: int = commands.Param(name="quantidade", description="移動する曲の数。",
                                         min_value=0, max_value=99, default=None),
            range_start: int = commands.Param(name="posição_inicial",
                                              description="指定した位置からキューの曲を含める。",
                                              min_value=1.0, max_value=500.0, default=0),
            range_end: int = commands.Param(name="posição_final",
                                            description="指定した位置までキューの曲を含める。",
                                            min_value=1.0, max_value=500.0, default=0),
            absent_members: bool = commands.Param(name="membros_ausentes",
                                                  description="チャンネルから退出したメンバーが追加した曲を含める。",
                                                  default=False),
    ):

        await self.do_move(
            inter=inter, position=position, song_name=song_name, song_author=song_author, user=user,
            duplicates=duplicates, playlist=playlist, min_duration=min_duration, max_duration=max_duration,
            amount=amount, range_start=range_start, range_end=range_end, absent_members=absent_members
        )

    async def do_move(
            self, inter: Union[disnake.ApplicationCommandInteraction, CustomContext], position: int = 1, song_name: str = None,
            song_author: str = None, user: disnake.Member = None, duplicates: bool = False, playlist: str = None,
            min_duration: str = None, max_duration: str = None, amount: int = None, range_start: int = 0,
            range_end: int = 0, absent_members: bool = False, case_sensitive=False
    ):

        if min_duration and max_duration:
            raise GenericError(
                "**duração_abaixo_de** または **duração_acima_de** のいずれか一つだけを選択してください。")

        try:
            bot = inter.music_bot
        except AttributeError:
            bot = inter.bot

        player: LavalinkPlayer = bot.music.players[inter.guild_id]

        if not player.queue and not player.queue_autoplay:
            raise GenericError("**キューに曲がありません。**")

        filters = []
        final_filters = set()

        txt = []
        playlist_hyperlink = set()

        tracklist = []

        if song_name:
            song_name = song_name.replace("️", "")
            filters.append('song_name')
        if song_author:
            song_author = song_author.replace("️", "")
            filters.append('song_author')
        if user:
            filters.append('user')
        if playlist:
            playlist = playlist.replace("️", "")
            filters.append('playlist')
        if min_duration:
            filters.append('time_below')
            min_duration = string_to_seconds(min_duration) * 1000
        if max_duration:
            filters.append('time_above')
            max_duration = string_to_seconds(max_duration) * 1000
        if absent_members:
            filters.append('absent_members')
        if duplicates:
            filters.append('duplicates')

        if not filters and not range_start and not range_end:
            raise GenericError("**移動するには少なくとも1つのオプションを使用する必要があります**")

        indexes = None

        try:
            has_id = song_name.split(" || ID > ")[1]
        except:
            has_id = isinstance(inter, CustomContext)

        insert_func = player.queue.insert

        if range_start > 0 and range_end > 0:

            if range_start >= range_end:
                raise GenericError("**終了位置は開始位置より大きくなければなりません！**")

            song_list = list(player.queue)[range_start - 1: -(range_end - 1)]
            txt.append(f"**キューの開始位置:** `{range_start}`\n"
                       f"**キューの終了位置:** `{range_end}`")

        elif range_start > 0:
            song_list = list(player.queue)[range_start - 1:]
            txt.append(f"**キューの開始位置:** `{range_start}`")
        elif range_end > 0:
            song_list = list(player.queue)[:-(range_end - 1)]
            txt.append(f"**キューの終了位置:** `{range_end}`")
        elif song_name and has_id and filters == ["song_name"] and amount is None:
            indexes = queue_track_index(inter, bot, song_name, match_count=1, case_sensitive=case_sensitive)
            for index, track in reversed(indexes):
                try:
                    player.queue.remove(track)
                except ValueError:
                    player.queue_autoplay.remove(track)
                    insert_func = player.queue_autoplay.insert
                tracklist.append(track)
            song_list = []

        else:
            song_list = list(player.queue)

        if not tracklist:

            if amount is None:
                amount = 0

            duplicated_titles = set()

            amount_counter = int(amount) if amount > 0 else 0

            for t in song_list:

                if amount and amount_counter < 1:
                    break

                temp_filter = list(filters)

                if 'duplicates' in temp_filter:
                    if (title := f"{t.author} - {t.title}".lower()) in duplicated_titles:
                        temp_filter.remove('duplicates')
                        final_filters.add('duplicates')
                    else:
                        duplicated_titles.add(title)

                if 'time_below' in temp_filter and t.duration >= min_duration:
                    temp_filter.remove('time_below')
                    final_filters.add('time_below')

                elif 'time_above' in temp_filter and t.duration <= max_duration:
                    temp_filter.remove('time_above')
                    final_filters.add('time_above')

                if 'song_name' in temp_filter:

                    title = t.title.replace("️", "").lower().split()

                    query_words = song_name.lower().split()

                    word_count = 0

                    for query_word in song_name.lower().split():
                        for title_word in title:
                            if query_word in title_word:
                                title.remove(title_word)
                                word_count += 1
                                break

                    if word_count == len(query_words):
                        temp_filter.remove('song_name')
                        final_filters.add('song_name')

                if 'song_author' in temp_filter and song_author.lower() in t.author.replace("️", "").lower():
                    temp_filter.remove('song_author')
                    final_filters.add('song_author')

                if 'user' in temp_filter and user.id == t.requester:
                    temp_filter.remove('user')
                    final_filters.add('user')

                elif 'absent_members' in temp_filter and t.requester not in player.guild.me.voice.channel.voice_states:
                    temp_filter.remove('absent_members')
                    final_filters.add('absent_members')

                playlist_link = None

                if 'playlist' in temp_filter:
                    if playlist == t.playlist_name.replace("️", "") or (isinstance(inter, CustomContext) and playlist.lower() in t.playlist_name.replace("️", "").lower()):
                        playlist_link = f"[`{fix_characters(t.playlist_name)}`]({t.playlist_url})"
                        temp_filter.remove('playlist')
                        final_filters.add('playlist')

                if not temp_filter:

                    track = player.queue[player.queue.index(t)]
                    player.queue.remove(t)
                    tracklist.append(track)
                    if playlist_link:
                        playlist_hyperlink.add(playlist_link)

                    if amount:
                        amount_counter -= 1

            duplicated_titles.clear()

        if not tracklist:
            raise GenericError("選択したフィルターに一致する曲が見つかりませんでした！")

        for t in reversed(tracklist):
            insert_func(position-1, t)

        try:
            final_filters.remove("song_name")
            txt.append(f"**名前に含まれる:** `{fix_characters(song_name)}`")
        except:
            pass

        try:
            final_filters.remove("song_author")
            txt.append(f"**アップローダー/アーティスト名に含まれる:** `{fix_characters(song_author)}`")
        except:
            pass

        try:
            final_filters.remove("user")
            txt.append(f"**リクエストしたメンバー:** {user.mention}")
        except:
            pass

        try:
            final_filters.remove("playlist")
            txt.append(f"**プレイリスト:** {' | '.join(playlist_hyperlink)}")
        except:
            pass

        try:
            final_filters.remove("time_below")
            txt.append(f"**最小再生時間:** `{time_format(min_duration)}`")
        except:
            pass

        try:
            final_filters.remove("time_above")
            txt.append(f"**最大再生時間:** `{time_format(max_duration)}`")
        except:
            pass

        try:
            final_filters.remove("duplicates")
            txt.append(f"**重複した曲**")
        except:
            pass

        try:
            final_filters.remove("absent_members")
            txt.append("`チャンネルから退出したメンバーがリクエストした曲。`")
        except:
            pass

        components = [
                disnake.ui.Button(emoji="▶️", label="今すぐ再生", custom_id=PlayerControls.embed_forceplay),
            ]

        if indexes:
            track = tracklist[0]
            txt = [
                f"曲 [`{fix_characters(track.title, limit=25)}`](<{track.uri or track.search_uri}>) をキューの位置 **[{position}]** に移動しました。",
                f"↪️ **⠂{inter.author.mention} が曲を位置 [{position}] に移動しました:**\n"
                f"╰[`{fix_characters(track.title, limit=43)}`](<{track.uri or track.search_uri}>)"
            ]

            await self.interaction_message(inter, txt, emoji="↪️", components=components)

        else:

            moved_tracks = len(tracklist)

            moved_tracks_txt = moved_tracks if moved_tracks == 1 else f"[{position}-{position+moved_tracks-1}]"

            msg_txt = f"### ↪️ ⠂{inter.author.mention} が {moved_tracks} 曲をキューの位置 {moved_tracks_txt} に移動しました:\n" + "\n".join(f"`{position+n}.` [`{fix_characters(t.title, 45)}`](<{t.uri}>)" for n, t in enumerate(tracklist[:7]))

            if (track_extra:=(moved_tracks - 7)) > 0:
                msg_txt += f"\n`その他 {track_extra} 曲。`"

            msg_txt += f"\n### ✅ ⠂使用したフィルター:\n" + '\n'.join(txt)

            txt = [f"{moved_tracks} 曲をキューの位置 **[{position}]** に移動しました。", msg_txt]

            await self.interaction_message(inter, txt, emoji="↪️", force=True, thumb=tracklist[0].thumb, components=components)

    @move.autocomplete("playlist")
    @clear.autocomplete("playlist")
    async def queue_playlist(self, inter: disnake.Interaction, query: str):

        try:
            if not inter.author.voice:
                return
        except:
            pass

        if not self.bot.bot_ready or not self.bot.is_ready():
            return [query]

        try:
            await check_pool_bots(inter, only_voiced=True)
            bot = inter.music_bot
        except:
            traceback.print_exc()
            return

        try:
            player = bot.music.players[inter.guild_id]
        except KeyError:
            return

        return list(set([track.playlist_name for track in player.queue if track.playlist_name and
                         query.lower() in track.playlist_name.lower()]))[:20]

    @rotate.autocomplete("nome")
    @move.autocomplete("nome")
    @skip.autocomplete("nome")
    @skipto.autocomplete("nome")
    @remove.autocomplete("nome")
    async def queue_tracks(self, inter: disnake.ApplicationCommandInteraction, query: str):

        try:
            if not inter.author.voice:
                return
        except AttributeError:
            pass

        if not self.bot.bot_ready or not self.bot.is_ready():
            return [query]

        try:
            if not await check_pool_bots(inter, only_voiced=True):
                return
        except PoolException:
            pass
        except:
            return

        try:
            player: LavalinkPlayer = inter.music_bot.music.players[inter.guild_id]
        except KeyError:
            return

        results = []

        count = 0

        for track in player.queue + player.queue_autoplay:

            if count == 20:
                break

            title = track.title.lower().split()

            query_words = query.lower().split()

            word_count = 0

            for query_word in query.lower().split():
                for title_word in title:
                    if query_word in title_word:
                        title.remove(title_word)
                        word_count += 1
                        break

            if word_count == len(query_words):
                results.append(f"{track.title[:81]} || ID > {track.unique_id}")
                count += 1

        return results or [f"{track.title[:81]} || ID > {track.unique_id}" for n, track in enumerate(player.queue + player.queue_autoplay)
                           if query.lower() in track.title.lower()][:20]

    @move.autocomplete("uploader")
    @clear.autocomplete("uploader")
    async def queue_author(self, inter: disnake.Interaction, query: str):

        if not self.bot.bot_ready or not self.bot.is_ready():
            return [query]

        try:
            await check_pool_bots(inter, only_voiced=True)
            bot = inter.music_bot
        except:
            return

        if not inter.author.voice:
            return

        try:
            player = bot.music.players[inter.guild_id]
        except KeyError:
            return

        if not query:
            return list(set([track.authors_string for track in player.queue]))[:20]
        else:
            return list(set([track.authors_string for track in player.queue if query.lower() in track.authors_string.lower()]))[:20]

    restrict_cd = commands.CooldownMapping.from_cooldown(2, 7, commands.BucketType.member)
    restrict_mc =commands.MaxConcurrency(1, per=commands.BucketType.member, wait=False)

    @is_dj()
    @has_player()
    @check_voice()
    @pool_command(name="restrictmode", aliases=["rstc", "restrict", "restrito", "modorestrito"], only_voiced=True, cooldown=restrict_cd, max_concurrency=restrict_mc,
                  description="DJ/スタッフを必要とする制限モードを有効/無効にします。")
    async def restrict_mode_legacy(self, ctx: CustomContext):

        await self.restrict_mode.callback(self=self, inter=ctx)

    @is_dj()
    @has_player()
    @check_voice()
    @commands.slash_command(
        description=f"{desc_prefix}DJ/スタッフを必要とする制限モードを有効/無効にします。",
        extras={"only_voiced": True}, cooldown=restrict_cd, max_concurrency=restrict_mc)
    @commands.contexts(guild=True)
    async def restrict_mode(self, inter: disnake.ApplicationCommandInteraction):

        try:
            bot = inter.music_bot
        except AttributeError:
            bot = inter.bot

        player: LavalinkPlayer = bot.music.players[inter.guild_id]

        player.restrict_mode = not player.restrict_mode

        msg = ["有効にしました", "🔐"] if player.restrict_mode else ["無効にしました", "🔓"]

        text = [
            f"プレイヤーの制限モード（DJ/スタッフが必要）を{msg[0]}。",
            f"{msg[1]} **⠂{inter.author.mention} がプレイヤーの制限モード（DJ/スタッフが必要）を{msg[0]}。**"
        ]

        await self.interaction_message(inter, text, emoji=msg[1])

    nonstop_cd = commands.CooldownMapping.from_cooldown(2, 15, commands.BucketType.member)
    nonstop_mc =commands.MaxConcurrency(1, per=commands.BucketType.member, wait=False)

    @has_player()
    @check_voice()
    @commands.has_guild_permissions(manage_guild=True)
    @pool_command(name="247", aliases=["nonstop"], only_voiced=True, cooldown=nonstop_cd, max_concurrency=nonstop_mc,
                  description="プレイヤーの24時間365日モードを有効/無効にします（テスト中）。")
    async def nonstop_legacy(self, ctx: CustomContext):
        await self.nonstop.callback(self=self, inter=ctx)

    @has_player()
    @check_voice()
    @commands.slash_command(
        name="247",
        description=f"{desc_prefix}プレイヤーの24時間365日モードを有効/無効にします（テスト中）。",
        default_member_permissions=disnake.Permissions(manage_guild=True),
        extras={"only_voiced": True}, cooldown=nonstop_cd, max_concurrency=nonstop_mc
    )
    @commands.contexts(guild=True)
    async def nonstop(self, inter: disnake.ApplicationCommandInteraction):

        try:
            bot = inter.music_bot
        except AttributeError:
            bot = inter.bot

        player: LavalinkPlayer = bot.music.players[inter.guild_id]

        player.keep_connected = not player.keep_connected

        msg = ["有効にしました", "♾️"] if player.keep_connected else ["無効にしました", "❌"]

        text = [
            f"プレイヤーの24時間365日（連続）モードを{msg[0]}。",
            f"{msg[1]} **⠂{inter.author.mention} がプレイヤーの24時間365日（連続）モードを{msg[0]}。**"
        ]

        if not len(player.queue):
            player.queue.extend(player.played)
            player.played.clear()

        await player.process_save_queue()

        if player.current:
            await self.interaction_message(inter, txt=text, emoji=msg[1])
            return

        await self.interaction_message(inter, text)

        await player.process_next()

    autoplay_cd = commands.CooldownMapping.from_cooldown(2, 15, commands.BucketType.member)
    autoplay_mc = commands.MaxConcurrency(1, per=commands.BucketType.member, wait=False)

    @has_player()
    @check_voice()
    @pool_command(name="autoplay", aliases=["ap", "aplay"], only_voiced=True, cooldown=autoplay_cd, max_concurrency=autoplay_mc,
                  description="キューの曲が終了した時の自動再生を有効/無効にします。")
    async def autoplay_legacy(self, ctx: CustomContext):
        await self.autoplay.callback(self=self, inter=ctx)

    @has_player()
    @check_voice()
    @commands.slash_command(
        name="autoplay",
        description=f"{desc_prefix}キューの曲が終了した時の自動再生を有効/無効にします。",
        extras={"only_voiced": True}, cooldown=autoplay_cd, max_concurrency=autoplay_mc
    )
    @commands.contexts(guild=True)
    async def autoplay(self, inter: disnake.ApplicationCommandInteraction):

        try:
            bot = inter.music_bot
        except AttributeError:
            bot = inter.bot

        player: LavalinkPlayer = bot.music.players[inter.guild_id]

        player.autoplay = not player.autoplay

        msg = ["有効にしました", "🔄"] if player.autoplay else ["無効にしました", "❌"]

        text = [f"自動再生を{msg[0]}。",
                f"{msg[1]} **⠂{inter.author.mention} が自動再生を{msg[0]}。**"]

        if player.current:
            await self.interaction_message(inter, txt=text, emoji=msg[1])
            return

        await self.interaction_message(inter, text)

        await player.process_next()

    @check_voice()
    @has_player()
    @is_dj()
    @commands.cooldown(1, 10, commands.BucketType.guild)
    @commands.slash_command(
        description=f"{desc_prefix}プレイヤーを別の音楽サーバーに移行します。"
    )
    @commands.contexts(guild=True)
    async def change_node(
            self,
            inter: disnake.ApplicationCommandInteraction,
            node: str = commands.Param(name="servidor", description="音楽サーバー")
    ):

        try:
            bot = inter.music_bot
        except AttributeError:
            bot = inter.bot

        if node not in bot.music.nodes:
            raise GenericError(f"音楽サーバー **{node}** が見つかりませんでした。")

        player: LavalinkPlayer = bot.music.players[inter.guild_id]

        if node == player.node.identifier:
            raise GenericError(f"プレイヤーは既に音楽サーバー **{node}** にいます。")

        await inter.response.defer(ephemeral=True)

        await player.change_node(node)

        player.native_yt = True

        embed = disnake.Embed(description=f"**プレイヤーが音楽サーバー:** `{node}` **に移行されました**",
                              color=self.bot.get_color(player.guild.me))

        try:
            if bot.user.id != self.bot.user.id:
                embed.set_footer(text=f"選択されたBot: {bot.user.display_name}", icon_url=bot.user.display_avatar.url)
        except AttributeError:
            pass

        player.set_command_log(
            text=f"{inter.author.mention} がプレイヤーを音楽サーバー **{node}** に移行しました",
            emoji="🌎"
        )

        player.update = True

        await inter.edit_original_message(embed=embed)

    @search.autocomplete("server")
    @play.autocomplete("server")
    @change_node.autocomplete("servidor")
    async def node_suggestions(self, inter: disnake.Interaction, query: str):

        if not self.bot.bot_ready or not self.bot.is_ready():
            return []

        try:
            await check_pool_bots(inter)
            bot = inter.music_bot
        except GenericError:
            return
        except:
            bot = inter.bot

        try:
            node = bot.music.players[inter.guild_id].node
        except KeyError:
            node = None

        if not query:
            return [n.identifier for n in bot.music.nodes.values() if
                    n != node and n.available and n.is_available]

        return [n.identifier for n in bot.music.nodes.values() if n != node
                and query.lower() in n.identifier.lower() and n.available and n.is_available]

    @commands.command(aliases=["puptime"], description="プレイヤーがサーバーでアクティブな時間の情報を表示します。")
    @commands.cooldown(1, 5, commands.BucketType.guild)
    async def playeruptime(self, ctx: CustomContext):

        uptime_info = []
        for bot in self.bot.pool.get_guild_bots(ctx.guild.id):
            try:
                player = bot.music.players[ctx.guild.id]
                uptime_info.append(f"**Bot:** {bot.user.mention}\n"
                            f"**Uptime:** <t:{player.uptime}:R>\n"
                            f"**Canal:** {player.guild.me.voice.channel.mention}")
            except KeyError:
                continue

        if not uptime_info:
            raise GenericError("**サーバーにアクティブなプレイヤーがありません。**")

        await ctx.reply(
            embed=disnake.Embed(
                title="**Player Uptime:**",
                description="\n-----\n".join(uptime_info),
                color=self.bot.get_color(ctx.guild.me)
            ), fail_if_not_exists=False
        )

    fav_import_export_cd = commands.CooldownMapping.from_cooldown(1, 15, commands.BucketType.member)
    fav_cd = commands.CooldownMapping.from_cooldown(3, 15, commands.BucketType.member)

    @commands.command(name="favmanager", aliases=["favs", "favoritos", "fvmgr", "favlist",
                                                  "integrations", "integrationmanager", "itg", "itgmgr", "itglist", "integrationlist",
                                                  "serverplaylist", "spl", "svp", "svpl"],
                      description="お気に入り/連携とサーバーリンクを管理します。", cooldown=fav_cd)
    async def fav_manager_legacy(self, ctx: CustomContext):
        await self.fav_manager.callback(self=self, inter=ctx)

    @commands.max_concurrency(1, commands.BucketType.member, wait=False)
    @commands.slash_command(
        description=f"{desc_prefix}お気に入り/連携とサーバーリンクを管理します。",
        cooldown=fav_cd, extras={"allow_private": True})
    @commands.contexts(guild=True)
    async def fav_manager(self, inter: disnake.ApplicationCommandInteraction):

        bot = self.bot

        mode = ViewMode.fav_manager

        guild_data = None
        interaction = None

        if isinstance(inter, CustomContext):
            prefix = inter.clean_prefix

            if inter.invoked_with in ("serverplaylist", "spl", "svp", "svpl") and (inter.author.guild_permissions.manage_guild or await bot.is_owner(inter.author)):

                interaction, bot = await select_bot_pool(inter, return_new=True)

                if not bot:
                    return

                mode = ViewMode.guild_fav_manager

                await interaction.response.defer(ephemeral=True)

                guild_data = await bot.get_data(inter.guild_id, db_name=DBModel.guilds)

            elif inter.invoked_with in ("integrations", "integrationmanager", "itg", "itgmgr", "itglist", "integrationlist"):
                mode = ViewMode.integrations_manager

        else:
            global_data = await bot.get_global_data(inter.guild_id, db_name=DBModel.guilds)
            prefix = global_data['prefix'] or bot.default_prefix

        if not interaction:
            interaction = inter

        cog = self.bot.get_cog("Music")

        if cog:
            ephemeral = await cog.is_request_channel(inter, ignore_thread=True)
            await inter.response.defer(ephemeral=ephemeral)
        else:
            ephemeral = True

        user_data = await bot.get_global_data(inter.author.id, db_name=DBModel.users)

        view = FavMenuView(bot=bot, ctx=interaction, data=user_data, prefix=prefix, mode=mode, is_owner=await bot.is_owner(inter.author))
        view.guild_data = guild_data

        txt = view.build_txt()

        if not txt:
            raise GenericError("**現在この機能はサポートされていません...**\n\n"
                             "`SpotifyとYTDLのサポートが有効化されていません。`")

        view.message = await inter.send(txt, view=view, ephemeral=ephemeral)

        await view.wait()

    @commands.Cog.listener("on_message_delete")
    async def player_message_delete(self, message: disnake.Message):

        if not message.guild:
            return

        try:

            player: LavalinkPlayer = self.bot.music.players[message.guild.id]

            if message.id != player.message.id:
                return

        except (AttributeError, KeyError):
            return

        thread = self.bot.get_channel(message.id)

        if not thread:
            return

        player.message = None
        await thread.edit(archived=True, locked=True, name=f"アーカイブ済み: {thread.name}")

    @commands.Cog.listener('on_ready')
    async def resume_players_ready(self):

        if not self.bot.bot_ready:
            return

        for guild_id in list(self.bot.music.players):

            try:

                player: LavalinkPlayer = self.bot.music.players[guild_id]

                try:
                    channel_id = player.guild.me.voice.channel.id
                except AttributeError:
                    channel_id = player.channel_id

                vc = self.bot.get_channel(channel_id) or player.last_channel

                try:
                    player.guild.voice_client.cleanup()
                except:
                    pass

                if not vc:
                    print(
                        f"{self.bot.user} - {player.guild.name} [{guild_id}] - ボイスチャンネルがないためプレイヤーが終了しました")
                    try:
                        await player.destroy()
                    except:
                        traceback.print_exc()
                    continue

                await player.connect(vc.id)

                if not player.is_paused and not player.is_playing:
                    await player.process_next()
                print(f"{self.bot.user} - {player.guild.name} [{guild_id}] - プレイヤーがボイスチャンネルに再接続しました")
            except:
                traceback.print_exc()

    async def is_request_channel(self, ctx: Union[disnake.ApplicationCommandInteraction, disnake.MessageInteraction, CustomContext], *,
                                 data: dict = None, ignore_thread=False) -> bool:

        if isinstance(ctx, (CustomContext, disnake.MessageInteraction)):
            return True

        try:
            bot = ctx.music_bot
            channel_ctx = bot.get_channel(ctx.channel_id)
        except AttributeError:
            bot = ctx.bot
            channel_ctx = ctx.channel

        if not self.bot.check_bot_forum_post(channel_ctx):
            return True

        try:
            player: LavalinkPlayer = bot.music.players[ctx.guild_id]

            if not player.static:
                return False

            if isinstance(channel_ctx, disnake.Thread) and player.text_channel == channel_ctx.parent:
                return not ignore_thread

            return player.text_channel == channel_ctx

        except KeyError:

            if not data:
                data = await bot.get_data(ctx.guild_id, db_name=DBModel.guilds)

            try:
                channel = bot.get_channel(int(data["player_controller"]["channel"]))
            except:
                channel = None

            if not channel:
                return False

            if isinstance(channel_ctx, disnake.Thread) and channel == channel_ctx.parent:
                return not ignore_thread

            return channel.id == channel_ctx.id

    async def check_channel(
            self,
            guild_data: dict,
            inter: Union[disnake.ApplicationCommandInteraction, CustomContext],
            channel: Union[disnake.TextChannel, disnake.VoiceChannel, disnake.Thread],
            guild: disnake.Guild,
            bot: BotCore
    ):

        static_player = guild_data['player_controller']

        warn_message = None
        message: Optional[disnake.Message] = None

        try:
            channel_db = bot.get_channel(int(static_player['channel'])) or await bot.fetch_channel(
                int(static_player['channel']))
        except (TypeError, disnake.NotFound):
            channel_db = None
        except disnake.Forbidden:
            channel_db = bot.get_channel(inter.channel_id)
            warn_message = f"チャンネル <#{static_player['channel']}> にアクセスする権限がありません。プレイヤーは従来のモードで使用されます。"
            static_player["channel"] = None

        if not channel_db or channel_db.guild.id != inter.guild_id:
            await self.reset_controller_db(inter.guild_id, guild_data, inter)

        else:

            if channel_db.id != channel.id:

                try:
                    if isinstance(channel_db, disnake.Thread):

                        if not channel_db.parent:
                            await self.reset_controller_db(inter.guild_id, guild_data, inter)
                            channel_db = None

                        else:
                            if channel_db.owner != bot.user.id:

                                if not isinstance(channel_db.parent, disnake.ForumChannel) or not channel_db.parent.permissions_for(channel_db.guild.me).create_forum_threads:
                                    await self.reset_controller_db(inter.guild_id, guild_data, inter)
                                    channel_db = None
                                else:

                                    thread = None

                                    for t in channel_db.parent.threads:

                                        if t.owner_id == bot.user.id:
                                            try:
                                                message = await t.fetch_message(t.id)
                                            except disnake.NotFound:
                                                continue
                                            if not message or message.author.id != bot.user.id:
                                                continue
                                            thread = t
                                            break

                                    if not thread and guild.me.guild_permissions.read_message_history:
                                        async for t in channel_db.parent.archived_threads(limit=100):
                                            if t.owner_id == bot.user.id:
                                                try:
                                                    message = await t.fetch_message(t.id)
                                                except disnake.NotFound:
                                                    continue
                                                if not message or message.author.id != bot.user.id:
                                                    continue
                                                thread = t
                                                break

                                    if not thread:
                                        thread_wmessage = await channel_db.parent.create_thread(
                                            name=f"{bot.user} song-request",
                                            content="音楽リクエスト用の投稿です。",
                                            auto_archive_duration=10080,
                                            slowmode_delay=5,
                                        )
                                        channel_db = thread_wmessage.thread
                                        message = thread_wmessage.message
                                    else:
                                        channel_db = thread

                            thread_kw = {}

                            if channel_db.locked and channel_db.permissions_for(guild.me).manage_threads:
                                thread_kw.update({"locked": False, "archived": False})

                            elif channel_db.archived and channel_db.owner_id == bot.user.id:
                                thread_kw["archived"] = False

                            if thread_kw:
                                await channel_db.edit(**thread_kw)

                            elif isinstance(channel.parent, disnake.ForumChannel):
                                warn_message = f"**{bot.user.mention} にはトピックを管理する権限がないため、" \
                                                f"トピック {channel_db.mention} のアーカイブ解除/ロック解除ができません**"

                except AttributeError:
                    pass

                if channel_db:

                    channel_db_perms = channel_db.permissions_for(guild.me)

                    channel = bot.get_channel(inter.channel.id)

                    if isinstance(channel, disnake.Thread):
                        send_message_perm = getattr(channel_db, "parent", channel_db).permissions_for(channel.guild.me).send_messages_in_threads
                    else:
                        send_message_perm = channel_db.permissions_for(channel.guild.me).send_messages

                    if not send_message_perm:
                        raise GenericError(
                            f"**{bot.user.mention} にはチャンネル <#{static_player['channel']}> でメッセージを送信する権限がありません**\n"
                            "音楽リクエストチャンネルの設定をリセットしたい場合は、/reset または /setup コマンドを"
                            "再度使用してください..."
                        )

                    if not channel_db_perms.embed_links:
                        raise GenericError(
                            f"**{bot.user.mention} にはチャンネル <#{static_player['channel']}> でリンク/埋め込みを添付する権限がありません**\n"
                            "音楽リクエストチャンネルの設定をリセットしたい場合は、/reset または /setup コマンドを"
                            "再度使用してください..."
                        )

        return channel_db, warn_message, message

    async def process_player_interaction(
            self,
            interaction: Union[disnake.MessageInteraction, disnake.ModalInteraction],
            command: Optional[disnake.ApplicationCommandInteraction],
            kwargs: dict
    ):

        if not command:
            raise GenericError("コマンドが見つからないか、実装されていません。")

        try:
            interaction.application_command = command
            await command._max_concurrency.acquire(interaction)
        except AttributeError:
            pass

        await check_cmd(command, interaction)

        await command(interaction, **kwargs)

        try:
            await command._max_concurrency.release(interaction)
        except:
            pass

        try:
            player: LavalinkPlayer = self.bot.music.players[interaction.guild_id]
            player.interaction_cooldown = True
            await asyncio.sleep(1)
            player.interaction_cooldown = False
        except (KeyError, AttributeError):
            pass

    @commands.Cog.listener("on_dropdown")
    async def guild_pin(self, interaction: disnake.MessageInteraction):

        if not self.bot.bot_ready:
            await interaction.send("まだ初期化中です...\nもう少しお待ちください...", ephemeral=True)
            return

        if interaction.data.custom_id != "player_guild_pin":
            return

        if not interaction.data.values:
            await interaction.response.defer()
            return

        if not interaction.user.voice:
            await interaction.send("これを使用するにはボイスチャンネルに参加する必要があります。", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        guild_data = await self.bot.get_data(interaction.guild_id, db_name=DBModel.guilds)

        try:
            query = interaction.data.values[0]
        except KeyError:
            await interaction.send("**選択したアイテムがデータベースに見つかりませんでした...**", ephemeral=True)
            await send_idle_embed(interaction.message, bot=self.bot, guild_data=guild_data, force=True)
            return

        kwargs = {
            "query": f"> pin: {query}",
            "position": 0,
            "options": False,
            "manual_selection": True,
            "server": None,
            "force_play": "no"
        }

        try:
            await self.play.callback(self=self, inter=interaction, **kwargs)
        except Exception as e:
            self.bot.dispatch('interaction_player_error', interaction, e)

    @commands.Cog.listener("on_dropdown")
    async def player_dropdown_event(self, interaction: disnake.MessageInteraction):

        if interaction.data.custom_id == "musicplayer_queue_dropdown":
            await self.process_player_interaction(
                interaction=interaction, command=self.bot.get_slash_command("skipto"),
                kwargs={"query": interaction.values[0][3:], "case_sensitive": True}
            )
            return

        if not interaction.data.custom_id.startswith("musicplayer_dropdown_"):
            return

        if not interaction.values:
            await interaction.response.defer()
            return

        await self.player_controller(interaction, interaction.values[0])

    @commands.Cog.listener("on_button_click")
    async def player_button_event(self, interaction: disnake.MessageInteraction):

        if not interaction.data.custom_id.startswith("musicplayer_"):
            return

        await self.player_controller(interaction, interaction.data.custom_id)

    async def check_stage_title(self, inter, bot: BotCore, player: LavalinkPlayer):

        time_limit = 30 if isinstance(player.guild.me.voice.channel, disnake.VoiceChannel) else 120

        if player.stage_title_event and (time_:=int((disnake.utils.utcnow() - player.start_time).total_seconds())) < time_limit and not (await bot.is_owner(inter.author)):
            raise GenericError(
                f"**ステージの自動アナウンスがアクティブな状態でこの機能を使用するには {time_format((time_limit - time_) * 1000, use_names=True)} 待つ必要があります...**"
            )

    async def player_controller(self, interaction: disnake.MessageInteraction, control: str, **kwargs):

        if not self.bot.bot_ready or not self.bot.is_ready():
            await interaction.send("まだ初期化中です...", ephemeral=True)
            return

        if not interaction.guild_id:
            await interaction.response.edit_message(components=None)
            return

        cmd_kwargs = {}

        cmd: Optional[disnake.ApplicationCommandInteraction] = None

        if control in (
                PlayerControls.embed_forceplay,
                PlayerControls.embed_enqueue_track,
                PlayerControls.embed_enqueue_playlist,
        ):

            try:
                try:
                    if not (url:=interaction.message.embeds[0].author.url):
                        if not (matches:=URL_REG.findall(interaction.message.embeds[0].description)):
                            return
                        url = matches[0].split(">")[0]
                except:
                    return

                try:
                    await self.player_interaction_concurrency.acquire(interaction)
                except:
                    raise GenericError("現在曲を処理中です...")

                bot: Optional[BotCore] = None
                player: Optional[LavalinkPlayer] = None
                channel: Union[disnake.TextChannel, disnake.VoiceChannel, disnake.StageChannel, disnake.Thread] = None
                author: Optional[disnake.Member] = None

                for b in sorted(self.bot.pool.get_guild_bots(interaction.guild_id), key=lambda b: b.identifier, reverse=True):

                    try:
                        p = b.music.players[interaction.guild_id]
                    except KeyError:
                        if c := b.get_channel(interaction.channel_id):
                            bot = b
                            channel = c
                            author = c.guild.get_member(interaction.author.id)
                        continue

                    if p.guild.me.voice and interaction.author.id in p.guild.me.voice.channel.voice_states:

                        if p.locked:
                            raise GenericError(
                                "**曲の処理中にこのアクションを実行することはできません "
                                "（数秒待ってから再試行してください）。**")

                        player = p
                        bot = b
                        channel = player.text_channel
                        author = p.guild.get_member(interaction.author.id)
                        break

                if not channel:
                    raise GenericError("現在利用可能なBotがありません。")

                if not author.voice:
                    raise GenericError("このボタンを使用するにはボイスチャンネルに参加する必要があります....")

                try:
                    node = player.node
                except:
                    node: Optional[wavelink.Node] = None

                try:
                    interaction.author = author
                except AttributeError:
                    pass

                await check_player_perm(inter=interaction, bot=bot, channel=interaction.channel)

                vc_id: int = author.voice.channel.id

                can_connect(channel=author.voice.channel, guild=channel.guild)

                await interaction.response.defer()

                if control == PlayerControls.embed_enqueue_playlist:

                    if (retry_after := self.bot.pool.enqueue_playlist_embed_cooldown.get_bucket(interaction).update_rate_limit()):
                        raise GenericError(
                            f"**現在のプレイヤーにプレイリストを追加するには {(rta:=int(retry_after))} 秒待つ必要があります。**")

                    if not player:
                        player = await self.create_player(inter=interaction, bot=bot, guild=channel.guild,
                                                          channel=channel, node=node)

                    await self.check_player_queue(interaction.author, bot, interaction.guild_id)
                    result, node = await self.get_tracks(url, interaction, author, source=False, node=player.node, bot=bot)
                    result = await self.check_player_queue(interaction.author, bot, interaction.guild_id, tracks=result)
                    player.queue.extend(result.tracks)
                    await interaction.send(f"{interaction.author.mention}、プレイリスト [`{result.name}`](<{url}>) が正常に追加されました！{player.controller_link}", ephemeral=True)

                    if not player.is_connected:
                        await player.connect(vc_id)

                    try:
                        vc = interaction.author.voice.channel
                    except AttributeError:
                        vc = player.bot.get_channel(vc_id)

                    if isinstance(vc, disnake.StageChannel):

                        retries = 5

                        while retries > 0:

                            await asyncio.sleep(1)

                            if not player.guild.me.voice:
                                retries -= 1
                                continue

                            break

                        if player.guild.me not in vc.speakers:
                            stage_perms = vc.permissions_for(player.guild.me)
                            if stage_perms.manage_permissions:
                                await asyncio.sleep(1.5)
                                await player.guild.me.edit(suppress=False)

                    if not player.current:
                        await player.process_next()

                else:

                    track = []
                    seek_status = False

                    if player:

                        if control == PlayerControls.embed_forceplay and player.current and (player.current.uri.startswith(url) or url.startswith(player.current.uri)):
                            await self.check_stage_title(inter=interaction, bot=bot, player=player)
                            await player.seek(0)
                            player.set_command_log("曲の先頭に戻りました。", emoji="⏪")
                            await asyncio.sleep(3)
                            await player.update_stage_topic()
                            await asyncio.sleep(7)
                            seek_status = True

                        else:

                            for t in list(player.queue):
                                if t.uri.startswith(url) or url.startswith(t.uri):
                                    track = [t]
                                    player.queue.remove(t)
                                    break

                            if not track:
                                for t in list(player.played):
                                    if t.uri.startswith(url) or url.startswith(t.uri):
                                        track = [t]
                                        player.played.remove(t)
                                        break

                                if not track:

                                    for t in list(player.failed_tracks):
                                        if t.uri.startswith(url) or url.startswith(t.uri):
                                            track = [t]
                                            player.failed_tracks.remove(t)
                                            break

                    if not seek_status:

                        if not track:

                            if (retry_after := self.bot.pool.enqueue_track_embed_cooldown.get_bucket(interaction).update_rate_limit()):
                                raise GenericError(
                                    f"**新しい曲をキューに追加するには {(rta:=int(retry_after))} 秒待つ必要があります。**")

                            if control == PlayerControls.embed_enqueue_track:
                                await self.check_player_queue(interaction.author, bot, interaction.guild_id)

                            result, node = await self.get_tracks(url, interaction, author, source=False, node=node, bot=bot)

                            track = result

                        if control == PlayerControls.embed_enqueue_track:

                            if not player:
                                player = await self.create_player(inter=interaction, bot=bot, guild=channel.guild,
                                                                  channel=channel, node=node)
                            await self.check_player_queue(interaction.author, bot, interaction.guild_id)
                            player.update = True
                            if isinstance(track, list):
                                t = track[0]
                                player.queue.append(t)
                                await interaction.send(
                                    f"{author.mention}、曲 [`{t.title}`](<{t.uri}>) がキューに追加されました。{player.controller_link}",
                                    ephemeral=True)
                            else:
                                player.queue.extend(track.tracks)
                                await interaction.send(
                                    f"{author.mention}、プレイリスト [`{track.name}`](<{track.url}>) がキューに追加されました。{player.controller_link}",
                                    ephemeral=True)
                            if not player.is_connected:
                                await player.connect(vc_id)
                            if not player.current:
                                await player.process_next()

                        else:
                            if not player:
                                player = await self.create_player(inter=interaction, bot=bot, guild=channel.guild,
                                                                  channel=channel, node=node)
                            else:
                                await self.check_stage_title(inter=interaction, bot=bot, player=player)

                            if isinstance(track, list):
                                player.queue.insert(0, track[0])
                            else:
                                index = len(player.queue)
                                player.queue.extend(track.tracks)
                                if index:
                                    player.queue.rotate(index * -1)
                            if not player.is_connected:
                                await player.connect(vc_id)
                            await self.process_music(inter=interaction, player=player, force_play="yes")

            except Exception as e:
                self.bot.dispatch('interaction_player_error', interaction, e)
                if not isinstance(e, GenericError):
                    await asyncio.sleep(5)
            try:
                await self.player_interaction_concurrency.release(interaction)
            except:
                pass
            return

        if control == PlayerControls.embed_add_fav:

            try:
                embed = interaction.message.embeds[0]
            except IndexError:
                await interaction.send("メッセージの埋め込みが削除されました...", ephemeral=True)
                return

            if (retry_after := self.bot.pool.add_fav_embed_cooldown.get_bucket(interaction).update_rate_limit()):
                await interaction.send(
                    f"**新しいお気に入りを追加するには {(rta:=int(retry_after))} 秒待つ必要があります。**",
                    ephemeral=True)
                return

            await interaction.response.defer()

            user_data = await self.bot.get_global_data(interaction.author.id, db_name=DBModel.users)

            if self.bot.config["MAX_USER_FAVS"] > 0 and not (await self.bot.is_owner(interaction.author)):

                if (current_favs_size := len(user_data["fav_links"])) > self.bot.config["MAX_USER_FAVS"]:
                    await interaction.edit_original_message(f"お気に入りファイルのアイテム数が "
                                                            f"許可された最大数（{self.bot.config['MAX_USER_FAVS']}）を超えています。")
                    return

                if (current_favs_size + (user_favs := len(user_data["fav_links"]))) > self.bot.config["MAX_USER_FAVS"]:
                    await interaction.edit_original_message(
                        "ファイルのすべてのお気に入りを追加するのに十分なスペースがありません...\n"
                        f"現在の制限: {self.bot.config['MAX_USER_FAVS']}\n"
                        f"保存されているお気に入りの数: {user_favs}\n"
                        f"必要な空き: {(current_favs_size + user_favs) - self.bot.config['MAX_USER_FAVS']}")
                    return

            fav_name = embed.author.name[1:]

            user_data["fav_links"][fav_name] = embed.author.url

            await self.bot.update_global_data(interaction.author.id, user_data, db_name=DBModel.users)

            global_data = await self.bot.get_global_data(interaction.guild_id, db_name=DBModel.guilds)

            try:
                cmd = f"</play:" + str(self.bot.get_global_command_named("play",
                                                                                             cmd_type=disnake.ApplicationCommandType.chat_input).id) + ">"
            except AttributeError:
                cmd = "/play"

            try:
                interaction.message.embeds[0].fields[0].value = f"{interaction.author.mention} " + \
                                                                interaction.message.embeds[0].fields[0].value.replace(
                                                                    interaction.author.mention, "")
            except IndexError:
                interaction.message.embeds[0].add_field(name="**リンクをお気に入りに追加したメンバー:**",
                                                        value=interaction.author.mention)

            await interaction.send(embed=disnake.Embed(
                description=f"[`{fav_name}`](<{embed.author.url}>) **があなたのお気に入りに追加されました！**\n\n"
                            "**使用方法**\n"
                            f"* コマンド {cmd} を使用（検索の自動補完でお気に入りを選択）\n"
                            "* プレイヤーのお気に入り/連携再生ボタン/セレクトをクリック。\n"
                            f"* コマンド {global_data['prefix'] or self.bot.default_prefix}{self.play_legacy.name} を曲/動画の名前やリンクなしで使用。\n"


            ).set_footer(text=f"すべてのお気に入りを表示するにはコマンド {global_data['prefix'] or self.bot.default_prefix}{self.fav_manager_legacy.name} を使用してください"), ephemeral=True)

            if not interaction.message.flags.ephemeral:
                if not interaction.guild:
                    await (await interaction.original_response()).edit(embed=interaction.message.embeds[0])
                else:
                    await interaction.message.edit(embed=interaction.message.embeds[0])
            return

        if not interaction.guild:
            await interaction.response.edit_message(components=None)
            return

        try:

            if control == "musicplayer_request_channel":
                cmd = self.bot.get_slash_command("setup")
                cmd_kwargs = {"target": interaction.channel}
                await self.process_player_interaction(interaction, cmd, cmd_kwargs)
                return

            if control == PlayerControls.fav_manager:

                if str(interaction.user.id) not in interaction.message.content:
                    await interaction.send("ここで操作することはできません！", ephemeral=True)
                    return

                cmd = self.bot.get_slash_command("fav_manager")
                await self.process_player_interaction(interaction, cmd, cmd_kwargs)
                return

            if control in (PlayerControls.add_song, PlayerControls.enqueue_fav):

                if not interaction.user.voice:
                    raise GenericError("**このボタンを使用するにはボイスチャンネルに参加する必要があります。**")

                user_data = await self.bot.get_global_data(id_=interaction.user.id, db_name=DBModel.users)

                has_fav = bool(user_data["fav_links"])

                modal_components = [
                    disnake.ui.TextInput(
                        label="曲名またはURL",
                        placeholder="名前またはyoutube/spotify/soundcloudのリンク...",
                        custom_id="song_input",
                        max_length=150,
                        required=not has_fav
                    ),
                    disnake.ui.TextInput(
                        label="キュー内の位置（番号）",
                        placeholder="任意: キュー内の位置を指定",
                        custom_id="song_position",
                        max_length=3,
                        required=False
                    )
                ]

                await interaction.response.send_modal(
                    title="曲をリクエストする",
                    custom_id="modal_add_song" + (f"_{interaction.message.id}" if interaction.message.thread else ""),
                    components=modal_components
                )

                return

            if control == PlayerControls.lastfm_scrobble:
                await interaction.response.defer(ephemeral=True, with_message=True)
                user_data = await self.bot.get_global_data(interaction.author.id, db_name=DBModel.users)

                if not user_data["lastfm"]["sessionkey"]:
                    try:
                        cmd = f"</lastfm:" + str(self.bot.get_global_command_named("lastfm",
                                                                                 cmd_type=disnake.ApplicationCommandType.chat_input).id) + ">"
                    except AttributeError:
                        cmd = "/lastfm"

                    await interaction.edit_original_message(
                        content=f"私のデータにlast.fmアカウントがリンクされていません。"
                                f"コマンド {cmd} を使用してlast.fmアカウントをリンクできます。"
                    )
                    return

                user_data["lastfm"]["scrobble"] = not user_data["lastfm"]["scrobble"]
                self.bot.pool.lastfm_sessions[interaction.author.id] = user_data["lastfm"]
                await self.bot.update_global_data(interaction.author.id, user_data, db_name=DBModel.users)
                await interaction.edit_original_message(
                    embed=disnake.Embed(
                        description=f'**アカウント [{user_data["lastfm"]["username"]}](<https://www.last.fm/user/{user_data["lastfm"]["username"]}>) でスクロブル/曲の記録が{"有効" if user_data["lastfm"]["scrobble"] else "無効"}になりました。**',
                        color=self.bot.get_color()
                    )
                )
                return

            try:
                player: LavalinkPlayer = self.bot.music.players[interaction.guild_id]
            except KeyError:
                await interaction.send("サーバーにアクティブなプレイヤーがありません...", ephemeral=True)
                await send_idle_embed(interaction.message, bot=self.bot)
                return

            if interaction.message != player.message:
                if control != PlayerControls.queue:
                    return

            if player.interaction_cooldown:
                raise GenericError("プレイヤーはクールダウン中です。しばらくしてから再試行してください。")

            try:
                vc = player.guild.me.voice.channel
            except AttributeError:
                await player.destroy(force=True)
                return

            if control == PlayerControls.help_button:
                embed = disnake.Embed(
                    description="📘 **ボタンについての情報** 📘\n\n"
                                "⏯️ `= 曲を一時停止/再開する。`\n"
                                "⏮️ `= 前に再生していた曲に戻る。`\n"
                                "⏭️ `= 次の曲にスキップする。`\n"
                                "🔀 `= キューの曲をシャッフルする。`\n"
                                "🎶 `= 曲/プレイリスト/お気に入りを追加する。`\n"
                                "⏹️ `= プレイヤーを停止してチャンネルから切断する。`\n"
                                "📑 `= 音楽キューを表示する。`\n"
                                "🛠️ `= プレイヤーの設定を変更する:`\n"
                                "`音量 / ナイトコアエフェクト / リピート / 制限モード。`\n",
                    color=self.bot.get_color(interaction.guild.me)
                )

                await interaction.response.send_message(embed=embed, ephemeral=True)
                return

            if not interaction.author.voice or interaction.author.voice.channel != vc:
                raise GenericError(f"プレイヤーのボタンを使用するにはチャンネル <#{vc.id}> にいる必要があります。")

            if control == PlayerControls.miniqueue:
                await is_dj().predicate(interaction)
                player.mini_queue_enabled = not player.mini_queue_enabled
                player.set_command_log(
                    emoji="📑",
                    text=f"{interaction.author.mention} がプレイヤーのミニキューを{'有効' if player.mini_queue_enabled else '無効'}にしました。"
                )
                await player.invoke_np(interaction=interaction)
                return

            if control != PlayerControls.queue:
                try:
                    await self.player_interaction_concurrency.acquire(interaction)
                except commands.MaxConcurrencyReached:
                    raise GenericError(
                        "**進行中のインタラクションがあります！**\n`非表示のメッセージの場合は、「無視」をクリックしないでください。`")

            if control == PlayerControls.add_favorite:

                if not player.current:
                    await interaction.send("**現在再生中の曲がありません...**", ephemeral=True)
                    return

                choices = {}
                msg = ""

                if player.current.uri:
                    choices["曲"] = {
                        "name": player.current.title,
                        "url": player.current.uri,
                        "emoji": "🎵"
                    }
                    msg += f"**曲:** [`{player.current.title}`]({player.current.uri})\n"

                if player.current.album_url:
                    choices["アルバム"] = {
                        "name": player.current.album_name,
                        "url": player.current.album_url,
                        "emoji": "💽"
                    }
                    msg += f"**アルバム:** [`{player.current.album_name}`]({player.current.album_url})\n"

                if player.current.playlist_url:
                    choices["プレイリスト"] = {
                        "name": player.current.playlist_name,
                        "url": player.current.playlist_url,
                        "emoji": "<:music_queue:703761160679194734>"
                    }
                    msg += f"**プレイリスト:** [`{player.current.playlist_name}`]({player.current.playlist_url})\n"

                if not choices:
                    try:
                        await self.player_interaction_concurrency.release(interaction)
                    except:
                        pass
                    await interaction.send(
                        embed=disnake.Embed(
                            color=self.bot.get_color(interaction.guild.me),
                            description="### 現在の曲にお気に入りに追加できるアイテムがありません。"
                        ), ephemeral=True
                    )
                    return

                if len(choices) == 1:
                    select_type, info = list(choices.items())[0]

                else:
                    view = SelectInteraction(
                        user=interaction.author, timeout=30,
                        opts=[disnake.SelectOption(label=k, description=v["name"][:50], emoji=v["emoji"]) for k,v in choices.items()]
                    )

                    await interaction.send(
                        embed=disnake.Embed(
                            color=self.bot.get_color(interaction.guild.me),
                            description=f"### 現在の曲からお気に入りに追加するアイテムを選択してください:"
                                        f"\n\n{msg}"
                        ), view=view, ephemeral=True
                    )

                    await view.wait()

                    select_interaction = view.inter

                    if not select_interaction or view.selected is False:
                        try:
                            await self.player_interaction_concurrency.release(interaction)
                        except:
                            pass
                        await interaction.edit_original_message(
                            embed=disnake.Embed(
                                color=self.bot.get_color(interaction.guild.me),
                                description="### 操作がキャンセルされました！"
                            ), view=None
                        )
                        return

                    interaction = select_interaction

                    select_type = view.selected
                    info = choices[select_type]

                await interaction.response.defer()

                user_data = await self.bot.get_global_data(interaction.author.id, db_name=DBModel.users)

                if self.bot.config["MAX_USER_FAVS"] > 0 and not (await self.bot.is_owner(interaction.author)):

                    if len(user_data["fav_links"]) >= self.bot.config["MAX_USER_FAVS"]:
                        await interaction.edit_original_message(
                            embed=disnake.Embed(
                                color=self.bot.get_color(interaction.guild.me),
                                description="ファイルのすべてのお気に入りを追加するのに十分なスペースがありません...\n"
                                            f"現在の制限: {self.bot.config['MAX_USER_FAVS']}"
                            ), view=None)
                        return

                user_data["fav_links"][fix_characters(info["name"], self.bot.config["USER_FAV_MAX_URL_LENGTH"])] = info["url"]

                await self.bot.update_global_data(interaction.author.id, user_data, db_name=DBModel.users)

                self.bot.dispatch("fav_add", interaction.user, user_data, f"[`{info['name']}`]({info['url']})")

                global_data = await self.bot.get_global_data(interaction.author.id, db_name=DBModel.guilds)

                try:
                    slashcmd = f"</play:" + str(self.bot.get_global_command_named("play", cmd_type=disnake.ApplicationCommandType.chat_input).id) + ">"
                except AttributeError:
                    slashcmd = "/play"

                await interaction.edit_original_response(
                    embed=disnake.Embed(
                        color=self.bot.get_color(interaction.guild.me),
                        description="### アイテムがお気に入りに正常に追加/編集されました:\n\n"
                                    f"**{select_type}:** [`{info['name']}`]({info['url']})\n\n"
                                    f"### 使用方法\n"
                                    f"* コマンド {slashcmd} を使用（検索の自動補完で選択）\n"
                                    f"* プレイヤーのお気に入り/連携再生ボタン/セレクトをクリック。\n"
                                    f"* コマンド {global_data['prefix'] or self.bot.default_prefix}{self.play_legacy.name} を曲/動画の名前やリンクなしで使用。"
                    ), view=None
                )

                try:
                    await self.player_interaction_concurrency.release(interaction)
                except:
                    pass

                return

            if control == PlayerControls.lyrics:
                if not player.current:
                    try:
                        await self.player_interaction_concurrency.release(interaction)
                    except:
                        pass
                    await interaction.send("**現在何も再生していません...**", ephemeral=True)
                    return

                if not player.current.ytid:
                    try:
                        await self.player_interaction_concurrency.release(interaction)
                    except:
                        pass
                    await interaction.send("現在はYouTubeの曲のみサポートされています。", ephemeral=True)
                    return

                not_found_msg = "現在の曲に利用可能な歌詞がありません..."

                await interaction.response.defer(ephemeral=True, with_message=True)

                if player.current.info["extra"].get("lyrics") is None:
                    lyrics_data = await player.node.fetch_ytm_lyrics(player.current.ytid)
                    player.current.info["extra"]["lyrics"] = {} if lyrics_data.get("track") is None else lyrics_data

                elif not player.current.info["extra"]["lyrics"]:
                    try:
                        await self.player_interaction_concurrency.release(interaction)
                    except:
                        pass
                    await interaction.edit_original_message(f"**{not_found_msg}**")
                    return

                if not player.current.info["extra"]["lyrics"]:
                    try:
                        await self.player_interaction_concurrency.release(interaction)
                    except:
                        pass
                    await interaction.edit_original_message(f"**{not_found_msg}**")
                    return

                player.current.info["extra"]["lyrics"]["track"]["albumArt"] = player.current.info["extra"]["lyrics"]["track"]["albumArt"][:-1]

                try:
                    lyrics_string = "\n".join([d['line'] for d in  player.current.info["extra"]["lyrics"]['lines']])
                except KeyError:
                    lyrics_string = player.current.info["extra"]["lyrics"]["text"]

                try:
                    await self.player_interaction_concurrency.release(interaction)
                except:
                    pass

                await interaction.edit_original_message(
                    embed=disnake.Embed(
                        description=f"### 曲の歌詞: [{player.current.title}](<{player.current.uri}>)\n{lyrics_string}",
                        color=self.bot.get_color(player.guild.me)
                    )
                )
                return

            if control == PlayerControls.volume:
                cmd_kwargs = {"value": None}

            elif control == PlayerControls.queue:
                cmd = self.bot.get_slash_command("queue").children.get("display")

            elif control == PlayerControls.shuffle:
                cmd = self.bot.get_slash_command("queue").children.get("shuffle")

            elif control == PlayerControls.seek_to_start:
                cmd = self.bot.get_slash_command("seek")
                cmd_kwargs = {"position": "0"}

            elif control == PlayerControls.pause_resume:
                control = PlayerControls.pause if not player.paused else PlayerControls.resume

            elif control == PlayerControls.loop_mode:

                if player.loop == "current":
                    cmd_kwargs['mode'] = 'queue'
                elif player.loop == "queue":
                    cmd_kwargs['mode'] = 'off'
                else:
                    cmd_kwargs['mode'] = 'current'

            elif control == PlayerControls.skip:
                cmd_kwargs = {"query": None, "play_only": "no"}

            if not cmd:
                cmd = self.bot.get_slash_command(control[12:])

            await self.process_player_interaction(
                interaction=interaction,
                command=cmd,
                kwargs=cmd_kwargs
            )

            try:
                await self.player_interaction_concurrency.release(interaction)
            except:
                pass

        except Exception as e:
            try:
                await self.player_interaction_concurrency.release(interaction)
            except:
                pass
            self.bot.dispatch('interaction_player_error', interaction, e)

    @commands.Cog.listener("on_modal_submit")
    async def song_request_modal(self, inter: disnake.ModalInteraction):

        if inter.custom_id.startswith("modal_add_song"):

            try:

                query = inter.values["song_input"]
                position = inter.values["song_position"]

                try:
                    selected_fav = inter.values["fav_links"][0]
                except (KeyError, IndexError):
                    selected_fav = None

                try:
                    selected_integration = inter.values["integration_links"][0]
                except (KeyError, IndexError):
                    selected_integration = None

                multichoice_opts = []

                if query:
                    multichoice_opts.append(
                        disnake.SelectOption(
                            label="Nome/Link:",
                            emoji="🔍",
                            description=fix_characters(query, limit=45),
                            value="music_query"
                        )
                    )

                if selected_fav:
                    multichoice_opts.append(
                        disnake.SelectOption(
                            label="Favorito:",
                            emoji="⭐",
                            description=fix_characters(selected_fav[6:], 45),
                            value="music_fav"
                        )
                    )

                if selected_integration:
                    multichoice_opts.append(
                        disnake.SelectOption(
                            label="Integração:",
                            emoji="💠",
                            description=fix_characters(selected_integration[13:], 45),
                            value="music_integration"
                        )
                    )

                if not multichoice_opts:
                    raise GenericError("少なくとも1つの情報を含める必要があります")
                
                if len(multichoice_opts) > 1:

                    view = SelectInteraction(
                        user=inter.author,
                        opts=multichoice_opts, timeout=30)

                    embed = disnake.Embed(
                        description="**リクエストに2つのアイテムを使用しました...**\n"
                                    f'続行するオプションを選択してください（制限時間: <t:{int((disnake.utils.utcnow() + datetime.timedelta(seconds=30)).timestamp())}:R>）。',
                        color=self.bot.get_color(inter.guild.me)
                    )

                    await inter.send(inter.author.mention, embed=embed, view=view, ephemeral=True)

                    await view.wait()

                    if not view.inter:
                        await inter.edit_original_message(
                            content=f"{inter.author.mention}, タイムアウトしました！",
                            embed=None, view=None
                        )
                        return

                    update_inter(inter, view.inter)

                    inter = view.inter

                    selected_opt = view.selected

                    await inter.response.defer(ephemeral=True)
                    
                else:
                    selected_opt = multichoice_opts[0].value
                    
                match selected_opt:
                    case "music_fav":
                        query = selected_fav
                    case "music_integration":
                        query = selected_integration

                kwargs = {
                    "query": query,
                    "position": int(position) if position else 0,
                    "options": False,
                    "manual_selection": True,
                    "server": None,
                    "force_play": "no",
                }

                await self.process_player_interaction(
                    interaction=inter,
                    command=self.bot.get_slash_command("play"),
                    kwargs=kwargs,
                )
            except Exception as e:
                self.bot.dispatch('interaction_player_error', inter, e)

    async def delete_message(self, message: disnake.Message, delay: int = None, ignore=False):

        if ignore:
            return

        try:
            is_forum = isinstance(message.channel.parent, disnake.ForumChannel)
        except AttributeError:
            is_forum = False

        if message.is_system() and is_forum:
            return

        if message.channel.permissions_for(message.guild.me).manage_messages or message.author.id == self.bot.user.id:

            try:
                await message.delete(delay=delay)
            except:
                traceback.print_exc()

    @commands.Cog.listener("on_song_request")
    async def song_requests(self, ctx: Optional[CustomContext], message: disnake.Message):

        if ctx.command or message.mentions:
            return

        if message.author.bot and not isinstance(message.channel, disnake.StageChannel):
            return

        try:
            data = await self.bot.get_data(message.guild.id, db_name=DBModel.guilds)
        except AttributeError:
            return

        player: Optional[LavalinkPlayer] = self.bot.music.players.get(message.guild.id)

        if player and isinstance(message.channel, disnake.Thread) and not player.static:

            try:
                if player.text_channel.id != message.id:
                    return
            except AttributeError:
                return

            if not player.controller_mode:
                return

            text_channel = message.channel

        else:

            static_player = data['player_controller']

            channel_id = static_player['channel']

            if not channel_id:
                return

            if isinstance(message.channel, disnake.Thread):
                if isinstance(message.channel.parent, disnake.TextChannel):
                    if str(message.channel.parent.id) != channel_id:
                        return
                elif str(message.channel.id) != channel_id:
                    return
            elif str(message.channel.id) != channel_id:
                return

            try:
                text_channel = self.bot.get_channel(int(channel_id)) or await self.bot.fetch_channel(int(channel_id))
            except disnake.NotFound:
                text_channel = None

            if not text_channel:
                await self.reset_controller_db(message.guild.id, data)
                return

            if isinstance(text_channel, disnake.Thread):
                send_message_perm = text_channel.parent.permissions_for(message.guild.me).send_messages_in_threads
            else:
                send_message_perm = text_channel.permissions_for(message.guild.me).send_messages

            if not send_message_perm:
                return

            if not self.bot.intents.message_content:

                if self.song_request_cooldown.get_bucket(message).update_rate_limit():
                    return

                await message.channel.send(
                    message.author.mention,
                    embed=disnake.Embed(
                        description="申し訳ありませんが、メッセージの内容を確認できません...\n"
                                    "**/play** を使用して曲を追加するか、下のボタンをクリックしてください:",
                        color=self.bot.get_color(message.guild.me)
                    ),
                    components=song_request_buttons, delete_after=20
                )
                return

        if message.content.startswith("/") or message.is_system():
            await self.delete_message(message)
            return

        try:
            if isinstance(message.channel, disnake.Thread):

                if isinstance(message.channel.parent, disnake.ForumChannel):

                    if data['player_controller']["channel"] != str(message.channel.id):
                        return
                    await self.delete_message(message)

        except AttributeError:
            pass

        msg = None
        error = None
        has_exception = None

        try:
            if message.author.bot:
                await self.delete_message(message)
                return

            if not message.content:

                if message.type == disnake.MessageType.thread_starter_message:
                    return

                if message.is_system():
                    await self.delete_message(message)
                    return

                try:
                    attachment = message.attachments[0]
                except IndexError:
                    await message.channel.send(f"{message.author.mention} 曲のリンク/名前を送信する必要があります。", delete_after=8)
                    return

                else:

                    if attachment.size > 18000000:
                        await message.channel.send(f"{message.author.mention} 送信したファイルは "
                                                   f"18mb未満である必要があります。", delete_after=8)
                        return

                    if attachment.content_type not in self.audio_formats:
                        await message.channel.send(f"{message.author.mention} 送信したファイルは "
                                                   f"18mb未満である必要があります。", delete_after=8)
                        return

                    message.content = attachment.url

            try:
                await self.song_request_concurrency.acquire(message)
            except:

                await message.channel.send(
                    f"{message.author.mention} 前の曲リクエストが読み込まれるまでお待ちください...",
                )

                await self.delete_message(message)
                return

            message.content = message.content.strip("<>")

            urls = URL_REG.findall(message.content)

            if not urls:
                source = None

            else:
                source = False
                message.content = urls[0]

                if "&list=" in message.content:

                    view = ButtonInteraction(
                        user=message.author, timeout=45,
                        buttons=[
                            disnake.ui.Button(label="曲のみを読み込む", emoji="🎵", custom_id="music"),
                            disnake.ui.Button(label="プレイリストを読み込む", emoji="🎶", custom_id="playlist"),
                        ]
                    )

                    embed = disnake.Embed(
                        description='**リンクにはプレイリスト付きの動画が含まれています。**\n'
                                    f'続行するには <t:{int((disnake.utils.utcnow() + datetime.timedelta(seconds=45)).timestamp())}:R> までにオプションを選択してください。\n'
                                    f'-# 注意: プライベートプレイリストのリンクの場合、現在のリンクの動画のみが読み込まれます。',
                        color=self.bot.get_color(message.guild.me)
                    )

                    msg = await message.channel.send(message.author.mention, embed=embed, view=view)

                    await view.wait()

                    try:
                        await view.inter.response.defer()
                    except:
                        pass

                    if view.selected == "music":
                        message.content = YOUTUBE_VIDEO_REG.search(message.content).group()

            await self.parse_song_request(message, text_channel, data, response=msg, source=source)

        except GenericError as e:
            error = f"{message.author.mention}. {e}"

        except Exception as e:

            if isinstance(e, PoolException):
                return

            try:
                error_msg, full_error_msg, kill_process, components, mention_author = parse_error(ctx, e)
            except:
                has_exception = e
            else:
                if not error_msg:
                    has_exception = e
                    error = f"{message.author.mention} **検索結果の取得中にエラーが発生しました:** ```py\n{error_msg}```"
                else:
                    error = f"{message.author.mention}. {error_msg}"

        if error:

            await self.delete_message(message)

            try:
                if msg:
                    await msg.edit(content=error, embed=None, view=None)
                else:
                    await message.channel.send(error, delete_after=9)
            except:
                traceback.print_exc()

        await self.song_request_concurrency.release(message)

        if has_exception and self.bot.config["AUTO_ERROR_REPORT_WEBHOOK"]:

            cog = self.bot.get_cog("ErrorHandler")

            if not cog:
                return

            max_concurrency = cog.webhook_max_concurrency

            await max_concurrency.acquire(message)

            try:
                try:
                    error_msg, full_error_msg, kill_process, components, mention_author = parse_error(message, has_exception)
                except:
                    full_error_msg = has_exception

                embed = disnake.Embed(
                    title="サーバーでエラーが発生しました（song-request）:",
                    timestamp=disnake.utils.utcnow(),
                    description=f"```py\n{repr(has_exception)[:2030].replace(self.bot.http.token, 'mytoken')}```"
                )

                embed.set_footer(
                    text=f"{message.author} [{message.author.id}]",
                    icon_url=message.author.display_avatar.with_static_format("png").url
                )

                embed.add_field(
                    name="サーバー:", inline=False,
                    value=f"```\n{disnake.utils.escape_markdown(ctx.guild.name)}\nID: {ctx.guild.id}```"
                )

                embed.add_field(
                    name="曲リクエストの内容:", inline=False,
                    value=f"```\n{message.content}```"
                )

                embed.add_field(
                    name="テキストチャンネル:", inline=False,
                    value=f"```\n{disnake.utils.escape_markdown(ctx.channel.name)}\nID: {ctx.channel.id}```"
                )

                if vc := ctx.author.voice:
                    embed.add_field(
                        name="ボイスチャンネル（ユーザー）:", inline=False,
                        value=f"```\n{disnake.utils.escape_markdown(vc.channel.name)}" +
                              (f" ({len(vc.channel.voice_states)}/{vc.channel.user_limit})"
                               if vc.channel.user_limit else "") + f"\nID: {vc.channel.id}```"
                    )

                if vcbot := ctx.guild.me.voice:
                    if vcbot.channel != vc.channel:
                        embed.add_field(
                            name="ボイスチャンネル（Bot）:", inline=False,
                            value=f"{vc.channel.name}" +
                                  (f" ({len(vc.channel.voice_states)}/{vc.channel.user_limit})"
                                   if vc.channel.user_limit else "") + f"\nID: {vc.channel.id}```"
                        )

                if ctx.guild.icon:
                    embed.set_thumbnail(url=ctx.guild.icon.with_static_format("png").url)

                await cog.send_webhook(
                    embed=embed,
                    file=string_to_file(full_error_msg, "error_traceback_songrequest.txt")
                )

            except:
                traceback.print_exc()

            await asyncio.sleep(20)

            try:
                await max_concurrency.release(message)
            except:
                pass


    async def process_music(
            self, inter: Union[disnake.Message, disnake.MessageInteraction, disnake.ApplicationCommandInteraction, CustomContext, disnake.ModalInteraction],
            player: LavalinkPlayer, force_play: str = "no", ephemeral=True, log_text = "", emoji="",
            warn_message: str = "", user_data: dict = None, reg_query: dict = None
    ):

        if not player.current:
            if warn_message:
                player.set_command_log(emoji="⚠️", text=warn_message)
            await player.process_next()
        elif force_play == "yes":
            player.set_command_log(
                emoji="▶️",
                text=f"{inter.author.mention} が現在の曲を今すぐ再生するように追加しました。"
            )
            await player.track_end()
            await player.process_next()
        #elif player.current.autoplay:
        #    player.set_command_log(text=log_text, emoji=emoji)
        #    await player.track_end()
        #    await player.process_next()
        else:
            if ephemeral:
                player.set_command_log(text=log_text, emoji=emoji)
            player.update = True

        if reg_query is not None:

            if not user_data:
                user_data = await self.bot.get_global_data(inter.author.id, db_name=DBModel.users)

            try:
                user_data["last_tracks"].remove(reg_query)
            except:
                pass

            if len(user_data["last_tracks"]) > 6:
                user_data["last_tracks"].pop(0)

            user_data["last_tracks"].append(reg_query)

            await self.bot.update_global_data(inter.author.id, user_data, db_name=DBModel.users)

    async def create_player(
            self,
            inter: Union[disnake.Message, disnake.MessageInteraction, disnake.ApplicationCommandInteraction, CustomContext, disnake.ModalInteraction],
            bot: BotCore, guild: disnake.Guild, channel: Union[disnake.TextChannel, disnake.VoiceChannel, disnake.Thread],
            guild_data: dict = None, message_inter = None,
            node: wavelink.Node = None, modal_message_id: int = None
    ):

        try:
            return bot.music.players[guild.id]
        except KeyError:
            pass

        if not guild_data:
            guild_data = await bot.get_data(inter.guild_id, db_name=DBModel.guilds)

        skin = guild_data["player_controller"]["skin"]
        static_skin = guild_data["player_controller"]["static_skin"]
        static_player = guild_data["player_controller"]

        if not node:
            node = await self.get_best_node(bot)

        global_data = await bot.get_global_data(guild.id, db_name=DBModel.guilds)

        try:
            vc = inter.author.voice.channel
        except AttributeError:
            vc = None

        if global_data["global_skin"]:
            skin = global_data["player_skin"] or skin
            static_skin = global_data["player_skin_static"] or guild_data["player_controller"]["static_skin"]

        try:
            invite = global_data["listen_along_invites"][str(vc.id)]
        except (AttributeError, KeyError):
            invite = ""

        if invite:
            try:
                invite = (await bot.fetch_invite(invite)).url
            except disnake.NotFound:
                invite = None
            except Exception:
                traceback.print_exc()
            else:
                try:
                    if invite.channel.id != vc.id:
                        invite = None
                except AttributeError:
                    pass

        if invite is None:
            try:
                del global_data["listen_along_invites"][str(vc.id)]
            except KeyError:
                pass
            else:
                print(
                    f'{"-" * 15}\n'
                    f'招待を削除: {invite} \n' +
                    (f"サーバー: {vc.guild.name} [{vc.guild.id}]\n"
                     f"チャンネル: {vc.name} [{vc.id}]\n" if vc else "") +
                    f'{"-" * 15}'
                )
                await self.bot.update_global_data(inter.guild_id, global_data, db_name=DBModel.guilds)

        for n, s in global_data["custom_skins"].items():
            if isinstance(s, str):
                global_data["custom_skins"][n] = pickle.loads(b64decode(s))

        for n, s in global_data["custom_skins_static"].items():
            if isinstance(s, str):
                global_data["custom_skins_static"][n] = pickle.loads(b64decode(s))

        try:
            guild_id =inter.guild.id
        except AttributeError:
            guild_id = inter.guild_id

        static_channel = None

        if static_player['channel']:

            try:
                static_channel = bot.get_channel(int(static_player['channel'])) or await bot.fetch_channel(
                    int(static_player['channel']))
            except disnake.Forbidden:
                pass
            except disnake.NotFound:
                await self.reset_controller_db(inter.guild_id, guild_data, inter)

            allowed_channel = None

            for ch in (static_channel, channel):

                if not ch:
                    continue

                if isinstance(ch, disnake.Thread):

                    if not ch.parent:
                        await self.reset_controller_db(inter.guild_id, guild_data, inter)
                        continue

                    channel_check = ch.parent

                else:
                    channel_check = ch

                bot_perms = channel_check.permissions_for(guild.me)

                if bot_perms.read_message_history:
                    allowed_channel = ch
                    break

                elif bot_perms.manage_permissions:
                    overwrites = {
                        guild.me: disnake.PermissionOverwrite(
                            embed_links=True,
                            send_messages=True,
                            send_messages_in_threads=True,
                            read_messages=True,
                            create_public_threads=True,
                            read_message_history=True,
                            manage_messages=True,
                            manage_channels=True,
                            attach_files=True,
                        )
                    }

                    await channel_check.edit(overwrites=overwrites)
                    allowed_channel = ch
                    break

            channel = allowed_channel

        player: LavalinkPlayer = bot.music.get_player(
            guild_id=guild_id,
            cls=LavalinkPlayer,
            player_creator=inter.author.id,
            guild=guild,
            channel=channel,
            last_message_id=guild_data['player_controller']['message_id'],
            node_id=node.identifier,
            static=bool(static_channel),
            skin=bot.pool.check_skin(skin),
            skin_static=bot.pool.check_static_skin(static_skin),
            custom_skin_data=global_data["custom_skins"],
            custom_skin_static_data=global_data["custom_skins_static"],
            extra_hints=self.extra_hints,
            restrict_mode=guild_data['enable_restrict_mode'],
            listen_along_invite=invite,
            autoplay=guild_data["autoplay"],
            prefix=global_data["prefix"] or bot.default_prefix,
            stage_title_template=global_data['voice_channel_status'],
        )

        if (vol:=int(guild_data['default_player_volume'])) != 100:
            await player.set_volume(vol)

        if not player.message and player.text_channel:
            try:
                player.message = await player.text_channel.fetch_message(int(static_player['message_id']))
            except TypeError:
                player.message = None
            except Exception:
                traceback.print_exc()
                if hasattr(player.text_channel, 'parent') and isinstance(player.text_channel.parent,
                                                                         disnake.ForumChannel) and str(
                        player.text_channel.id) == static_player['message_id']:
                    pass
                elif player.static:
                    player.text_channel = None

        if not player.static and player.text_channel:

            if message_inter and inter.bot != bot:
                player.message = message_inter
            elif modal_message_id:
                try:
                    player.message = await player.text_channel.fetch_message(modal_message_id)
                except:
                    pass

            if not player.has_thread:
                player.message = None
            else:
                await self.thread_song_request(message_inter.thread, reopen=True, bot=bot)

        return player


    async def parse_song_request(self, message: disnake.Message, text_channel, data, *, response=None, attachment: disnake.Attachment=None, source=None):

        if not message.author.voice:
            raise GenericError("曲をリクエストするにはボイスチャンネルに参加する必要があります。")

        can_connect(
            channel=message.author.voice.channel,
            guild=message.guild,
            check_other_bots_in_vc=data["check_other_bots_in_vc"],
            bot=self.bot,
        )

        try:
            if message.guild.me.voice.channel != message.author.voice.channel:
                raise GenericError(
                    f"曲をリクエストするにはチャンネル <#{message.guild.me.voice.channel.id}> に参加する必要があります。")
        except AttributeError:
            pass

        try:
            message_id = int(data['player_controller']['message_id'])
        except TypeError:
            message_id = None

        try:
            player = self.bot.music.players[message.guild.id]
            await check_player_perm(message, self.bot, message.channel, guild_data=data)
            destroy_message = True
        except KeyError:
            destroy_message = False
            player = await self.create_player(inter=message, bot=self.bot, guild=message.guild, channel=text_channel,
                                              guild_data=data)

        tracks, node = await self.get_tracks(message.content, message, message.author, source=source)
        tracks = await self.check_player_queue(message.author, self.bot, message.guild.id, tracks)

        if not player.message:
            try:
                cached_message = await text_channel.fetch_message(message_id)
            except:
                cached_message = await send_idle_embed(message, bot=self.bot, guild_data=data)
                data['player_controller']['message_id'] = str(cached_message.id)
                await self.bot.update_data(message.guild.id, data, db_name=DBModel.guilds)

            player.message = cached_message

        embed = disnake.Embed(color=self.bot.get_color(message.guild.me))

        try:
            components = [disnake.ui.Button(emoji="🎛️", label="Player-controller", url=player.message.jump_url)]
        except AttributeError:
            components = []

        if not isinstance(tracks, list):

            player.queue.extend(tracks.tracks)

            if isinstance(message.channel, disnake.Thread) and not isinstance(message.channel.parent, disnake.ForumChannel):
                tcount = len(tracks.tracks)
                embed.description = f"✋ **⠂ リクエスト者:** {message.author.mention}\n" \
                                    f"🎼 **⠂ 曲:** `[{tcount}]`"
                embed.set_thumbnail(url=tracks.tracks[0].thumb)
                embed.set_author(name="⠂" + fix_characters(tracks.tracks[0].playlist_name, 35), url=message.content,
                                 icon_url=music_source_image(tracks.tracks[0].info["sourceName"]))

                try:
                    embed.description += f"\n🔊 **⠂ ボイスチャンネル:** {message.author.voice.channel.mention}"
                except AttributeError:
                    pass

                try:
                    self.bot.pool.enqueue_playlist_embed_cooldown.get_bucket(message).update_rate_limit()
                except:
                    pass

                components.extend(
                    [
                        disnake.ui.Button(emoji="💗", label="お気に入り", custom_id=PlayerControls.embed_add_fav),
                        disnake.ui.Button(emoji="<:add_music:588172015760965654>", label="キューに追加",custom_id=PlayerControls.embed_enqueue_playlist)
                    ]
                )

                if response:
                    await response.edit(content=None, embed=embed, components=components)
                else:
                    await message.reply(embed=embed, fail_if_not_exists=False, mention_author=False)

            else:
                player.set_command_log(
                    text=f"{message.author.mention} がプレイリスト [`{fix_characters(tracks.data['playlistInfo']['name'], 20)}`]"
                         f"(<{tracks.tracks[0].playlist_url}>) `({len(tracks.tracks)})` を追加しました。",
                    emoji="🎶"
                )
            if destroy_message:
                await self.delete_message(message)

        else:
            track = tracks[0]

            if track.info.get("sourceName") == "http":

                if track.title == "Unknown title":
                    if attachment:
                        track.info["title"] = attachment.filename
                    else:
                        track.info["title"] = track.uri.split("/")[-1]
                    track.title = track.info["title"]

                track.info["uri"] = ""

            player.queue.append(track)

            if isinstance(message.channel, disnake.Thread) and not isinstance(message.channel.parent, disnake.ForumChannel):
                embed.description = f"💠 **⠂ アップローダー:** `{track.author}`\n" \
                                    f"✋ **⠂ リクエスト者:** {message.author.mention}\n" \
                                    f"⏰ **⠂ 再生時間:** `{time_format(track.duration) if not track.is_stream else '🔴 ライブ配信'}`"

                try:
                    embed.description += f"\n🔊 **⠂ ボイスチャンネル:** {message.author.voice.channel.mention}"
                except AttributeError:
                    pass

                try:
                    self.bot.pool.enqueue_track_embed_cooldown.get_bucket(message).update_rate_limit()
                except:
                    pass

                components.extend(
                    [
                        disnake.ui.Button(emoji="💗", label="お気に入り", custom_id=PlayerControls.embed_add_fav),
                        disnake.ui.Button(emoji="<:play:914841137938829402>", label="再生" + ("（今すぐ）" if (player.current and player.current.autoplay) else ""), custom_id=PlayerControls.embed_forceplay),
                        disnake.ui.Button(emoji="<:add_music:588172015760965654>", label="キューに追加",
                                          custom_id=PlayerControls.embed_enqueue_track)
                    ]
                )

                embed.set_thumbnail(url=track.thumb)
                embed.set_author(name=fix_characters(track.title, 35), url=track.uri or track.search_uri, icon_url=music_source_image(track.info["sourceName"]))
                if response:
                    await response.edit(content=None, embed=embed, components=components)
                else:
                    await message.reply(embed=embed, fail_if_not_exists=False, mention_author=False, components=components)

            else:
                duration = time_format(tracks[0].duration) if not tracks[0].is_stream else '🔴 ライブ配信'
                player.set_command_log(
                    text=f"{message.author.mention} が [`{fix_characters(tracks[0].title, 20)}`](<{tracks[0].uri or tracks[0].search_uri}>) `({duration})` を追加しました。",
                    emoji="🎵"
                )
                if destroy_message:
                    await self.delete_message(message)

        if not player.is_connected:
            await self.do_connect(
                message,
                channel=message.author.voice.channel,
                check_other_bots_in_vc=data["check_other_bots_in_vc"]
            )

        if not player.current:
            await player.process_next()
        else:
            await player.update_message()

        await asyncio.sleep(1)

    async def cog_check(self, ctx: CustomContext) -> bool:

        return await check_requester_channel(ctx)

    def cog_unload(self):
        try:
            self.error_report_task.cancel()
        except:
            pass

    async def interaction_message(self, inter: Union[disnake.Interaction, CustomContext], txt, emoji: str = "✅",
                                  rpc_update: bool = False, data: dict = None, store_embed: bool = False, force=False,
                                  defered=False, thumb=None, components=None):

        try:
            txt, txt_ephemeral = txt
        except:
            txt_ephemeral = False

        try:
            bot = inter.music_bot
            guild = inter.music_guild
        except AttributeError:
            bot = inter.bot
            guild = inter.guild

        player: LavalinkPlayer = bot.music.players[inter.guild_id]

        component_interaction = isinstance(inter, disnake.MessageInteraction)

        ephemeral = await self.is_request_channel(inter, data=data)

        if ephemeral:
            player.set_command_log(text=f"{inter.author.mention} {txt}", emoji=emoji)
            player.update = True

        await player.update_message(interaction=inter if (bot.user.id == self.bot.user.id and component_interaction) \
            else False, rpc_update=rpc_update, force=force)

        if isinstance(inter, CustomContext):
            embed = disnake.Embed(color=self.bot.get_color(guild.me),
                                  description=f"{txt_ephemeral or txt}{player.controller_link}")

            if thumb:
                embed.set_thumbnail(url=thumb)

            try:
                if bot.user.id != self.bot.user.id:
                    embed.set_footer(text=f"選択されたBot: {bot.user.display_name}", icon_url=bot.user.display_avatar.url)
            except AttributeError:
                pass

            if store_embed and not player.controller_mode and len(player.queue) > 0:
                player.temp_embed = embed

            else:
                kwargs = {"components": components} if components else {}
                try:
                    await inter.store_message.edit(embed=embed, view=None, content=None, **kwargs)
                except AttributeError:
                    await inter.send(embed=embed, **kwargs)

        elif not component_interaction:
            
            kwargs = {"components": components} if components else {}

            embed = disnake.Embed(
                color=self.bot.get_color(guild.me),
                description=(txt_ephemeral or f"{inter.author.mention} **{txt}**") + player.controller_link
            )

            if thumb:
                embed.set_thumbnail(url=thumb)

            try:
                if bot.user.id != self.bot.user.id:
                    embed.set_footer(text=f"選択されたBot: {bot.user.display_name}", icon_url=bot.user.display_avatar.url)
            except AttributeError:
                pass

            if not inter.response.is_done():
                await inter.send(embed=embed, ephemeral=ephemeral, **kwargs)

            elif defered:
                await inter.edit_original_response(embed=embed, **kwargs)

    @commands.Cog.listener("on_wavelink_node_connection_closed")
    async def node_connection_closed(self, node: wavelink.Node):

        try:
            self.bot.wavelink_node_reconnect_tasks[node.identifier].cancel()
        except:
            pass

        self.bot.wavelink_node_reconnect_tasks[node.identifier] = self.bot.loop.create_task(self.node_reconnect(node))

    async def node_reconnect(self, node: wavelink.Node):

        retries = 0
        backoff = 7

        if ((dt_now:=datetime.datetime.now()) - node._retry_dt).total_seconds() < 7:
            node._retry_count += 1
        if node._retry_count >= 4:
            print(f"❌ - {self.bot.user} - [{node.identifier} / v{node.version}] Reconexão cancelada.")
            node._retry_count = 0
            return
        else:
            node._retry_dt = dt_now

        print(f"⚠️ - {self.bot.user} - [{node.identifier} / v{node.version}] Conexão perdida - reconectando em {int(backoff)} segundos.")

        while True:

            if node.is_available:
                return

            for player in list(node.players.values()):

                try:
                    player._new_node_task.cancel()
                except:
                    pass

                player._new_node_task = player.bot.loop.create_task(player._wait_for_new_node())

            if self.bot.config["LAVALINK_RECONNECT_RETRIES"] and retries == self.bot.config["LAVALINK_RECONNECT_RETRIES"]:
                print(f"❌ - {self.bot.user} - [{node.identifier}] Todas as tentativas de reconectar falharam...")
                return

            await self.bot.wait_until_ready()

            try:
                async with self.bot.session.get(f"{node.rest_uri}/v4/info", timeout=45, headers=node.headers) as r:
                    if r.status == 200:
                        node.version = 4
                        node.update_info(await r.json())
                    elif r.status != 404:
                        raise Exception(f"{self.bot.user} - [{r.status}]: {await r.text()}"[:300])
                    else:
                        node.version = 3
                        node.info["sourceManagers"] = ["youtube", "soundcloud", "http"]

                await node._websocket._connect()
                return
            except Exception as e:
                error = repr(e)

            backoff *= 1.5
            if node.identifier != "LOCAL":
                print(
                    f'⚠️ - {self.bot.user} - サーバー [{node.identifier}] への再接続に失敗しました。{int(backoff)} 秒後に再試行します。'
                    f' エラー: {error}'[:300])
            await asyncio.sleep(backoff)
            retries += 1

    def remove_provider(self, lst, queries: list):
        for q in queries:
            try:
                lst.remove(q)
            except:
                continue

    def add_provider(self, lst, queries: list):
        for q in queries:
            if q in lst:
                lst.remove(q)
            lst.append(q)

    @commands.Cog.listener("on_wavelink_node_ready")
    async def node_ready(self, node: wavelink.Node):
        print(f'🌋 - {self.bot.user} - 音楽サーバー: [{node.identifier} / v{node.version}] が使用可能になりました！')
        retries = 25
        while retries > 0:

            if not node._websocket.is_connected:
                return

            if not node.stats:
                await asyncio.sleep(5)
                retries -= 1
                continue

            if "deezer" not in node.info["sourceManagers"]:
                self.remove_provider(node.search_providers, ["dzsearch"])
                self.remove_provider(node.partial_providers, ["dzisrc:{isrc}", "dzsearch:{author} - {title}"])
                try:
                    node.native_sources.remove("deezer")
                except:
                    pass
            elif "dzsearch" not in node.search_providers:
                node.native_sources.add("deezer")
                self.add_provider(node.search_providers, ["dzsearch"])
                self.add_provider(node.partial_providers, ["dzisrc:{isrc}", "dzsearch:{author} - {title}"])
            else:
                node.native_sources.add("deezer")

            if "tidal" not in node.info["sourceManagers"] or node.only_use_native_search_providers is True:
                self.remove_provider(node.search_providers, ["tdsearch"])
                self.remove_provider(node.partial_providers, ["tdsearch:{author} - {title}"])
            elif "tdsearch" not in node.search_providers and node.only_use_native_search_providers is False:
                self.add_provider(node.search_providers, ["tdsearch"])
                self.add_provider(node.partial_providers, ["tdsearch:{author} - {title}"])

            if "applemusic" not in node.info["sourceManagers"] or node.only_use_native_search_providers is True:
                self.remove_provider(node.search_providers, ["amsearch"])
                self.remove_provider(node.partial_providers, ["amsearch:{author} - {title}"])
            elif "amsearch" not in node.search_providers and node.only_use_native_search_providers is False:
                self.add_provider(node.search_providers, ["amsearch"])
                self.add_provider(node.partial_providers, ["amsearch:{author} - {title}"])

            if "bandcamp" not in node.info["sourceManagers"]:
                self.remove_provider(node.search_providers, ["bcsearch"])
                self.remove_provider(node.partial_providers, ["bcsearch:{author} - {title}"])
            elif "bcsearch" not in node.search_providers:
                self.add_provider(node.search_providers, ["bcsearch"])
                self.add_provider(node.partial_providers, ["bcsearch:{author} - {title}"])

            if "spotify" not in node.info["sourceManagers"] or node.only_use_native_search_providers is True:
                self.remove_provider(node.search_providers, ["spsearch"])
                self.remove_provider(node.partial_providers, ["spsearch:{author} - {title}"])
            elif "spsearch" not in node.search_providers and node.only_use_native_search_providers is False:
                self.add_provider(node.search_providers, ["spsearch"])
                self.add_provider(node.partial_providers, ["spsearch:{author} - {title}"])

            if "youtube" not in node.info["sourceManagers"] and "ytsearch" not in node.original_providers:
                self.remove_provider(node.search_providers, ["ytsearch"])
                self.remove_provider(node.partial_providers, ["ytsearch:\"{isrc}\"", "ytsearch:\"{title} - {author}\""])
            elif "ytsearch" not in node.search_providers:
                if "ytsearch" in node.original_providers:
                    self.add_provider(node.search_providers, ["ytsearch"])
                    self.add_provider(node.partial_providers, ["ytsearch:\"{isrc}\"", "ytsearch:\"{title} - {author}\""])

            if "youtube" not in node.info["sourceManagers"] and "ytmsearch" not in node.original_providers:
                self.remove_provider(node.search_providers, ["ytmsearch"])
                self.remove_provider(node.partial_providers, ["ytmsearch:\"{isrc}\"", "ytmsearch:\"{title} - {author}\""])
            elif "ytmsearch" not in node.search_providers:
                if "ytmsearch" in node.original_providers:
                    self.add_provider(node.search_providers, ["ytmsearch"])
                    self.add_provider(node.partial_providers, ["ytmsearch:\"{isrc}\"", "ytmsearch:\"{title} - {author}\""])

            if "soundcloud" not in node.info["sourceManagers"]:
                self.remove_provider(node.search_providers, ["scsearch"])
                self.remove_provider(node.partial_providers, ["scsearch:{author} - {title}"])
            elif "scsearch" not in node.search_providers:
                self.add_provider(node.search_providers, ["scsearch"])
                self.add_provider(node.partial_providers, ["scsearch:{author} - {title}"])

            if "jiosaavn" not in node.info["sourceManagers"]:
                self.remove_provider(node.search_providers, ["jssearch"])
                # self.remove_provider(node.partial_providers, ["jssearch:{title} - {author}"])
            elif "jssearch" not in node.search_providers:
                self.add_provider(node.search_providers, ["jssearch"])
                # self.add_provider(node.partial_providers, ["jssearch:{title} {author}"])

            if node.stats.uptime < 600000:
                node.open()
            return

    async def connect_node(self, data: dict):

        if data["identifier"] in self.bot.music.nodes:
            node = self.bot.music.nodes[data['identifier']]
            try:
                if not node._websocket.is_connected:
                    await node.connect()
            except AttributeError:
                pass
            return

        data = deepcopy(data)

        data['rest_uri'] = ("https" if data.get('secure') else "http") + f"://{data['host']}:{data['port']}"
        #data['user_agent'] = self.bot.pool.current_useragent
        search = data.pop("search", True)
        node_website = data.pop('website', '')
        region = data.pop('region', 'us_central')
        heartbeat = int(data.pop('heartbeat', 30))
        search_providers = data.pop("search_providers", None) or ["ytsearch", "scsearch"]
        info = data.pop("info", {})

        try:
            max_retries = int(data.pop('retries'))
        except (TypeError, KeyError):
            max_retries = 1

        node = await self.bot.music.initiate_node(auto_reconnect=False, region=region, heartbeat=heartbeat, max_retries=max_retries, **data)
        node.info = info
        node.search = search
        node.website = node_website
        node.search_providers = search_providers
        node.original_providers = set(node.search_providers)
        node.partial_providers = []
        node.native_sources = deepcopy(native_sources)
        node.prefer_youtube_native_playback = data.pop("prefer_youtube_native_playback", True)
        node.only_use_native_search_providers = data.pop("only_use_native_search_providers", True)

        for p in node.search_providers:
            if p == "dzsearch":
                node.partial_providers.append("dzisrc:{isrc}")
                node.partial_providers.append("dzsearch:{title} - {author}")
            elif p == "tdsearch":
                node.partial_providers.append("tdsearch:{title} - {author}")
            elif p == "amsearch":
                node.partial_providers.append("amsearch:{title} - {author}")
            elif p == "spsearch":
                node.partial_providers.append("spsearch:{title} - {author}")
            elif p == "bcsearch":
                node.partial_providers.append("bcsearch:{title} - {author}")
            elif p == "ytsearch":
                node.partial_providers.append("ytsearch:\"{isrc}\"")
                node.partial_providers.append("ytsearch:\"{title} - {author}\"")
            elif p == "ytmsearch":
                node.partial_providers.append("ytmsearch:\"{isrc}\"")
                node.partial_providers.append("ytmsearch:\"{title} - {author}\"")
            elif p == "scsearch":
                node.partial_providers.append("scsearch:{title} - {author}")

        await node.connect(info=info)

    async def get_partial_tracks(self, query: str, ctx: Union[disnake.ApplicationCommandInteraction, CustomContext, disnake.MessageInteraction, disnake.Message],
            user: disnake.Member, node: wavelink.Node = None, bot: BotCore = None):

        if not bot:
            bot = self.bot

        tracks = []

        exceptions = set()

        if (bot.pool.config["FORCE_USE_DEEZER_CLIENT"] or [n for n in bot.music.nodes.values() if
                                                           "deezer" not in n.info.get("sourceManagers", [])]):
            try:
                tracks = await self.bot.pool.deezer.get_tracks(url=query, requester=user.id, search=True, check_title=80)
            except Exception as e:
                self.bot.dispatch("custom_error", ctx=ctx, error=e)
                exceptions.add(repr(e))

        if not tracks and bot.spotify and not [n for n in bot.music.nodes.values() if "spotify" in n.info.get("sourceManagers", [])]:
            try:
                tracks = await self.bot.pool.spotify.get_tracks(self.bot, user.id, query, search=True, check_title=80)
            except Exception as e:
                self.bot.dispatch("custom_error", ctx=ctx, error=e)
                exceptions.add(repr(e))

        return tracks, node, exceptions

    async def get_lavalink_tracks(self, query: str, ctx: Union[disnake.ApplicationCommandInteraction, CustomContext, disnake.MessageInteraction, disnake.Message],
            user: disnake.Member, node: wavelink.Node = None, source=None, bot: BotCore = None):

        if not bot:
            bot = self.bot

        if not node:
            nodes = sorted([n for n in bot.music.nodes.values() if n.is_available and n.available],
                           key=lambda n: len(n.players))
        else:
            nodes = sorted([n for n in bot.music.nodes.values() if n != node and n.is_available and n.available],
                           key=lambda n: len(n.players))
            nodes.insert(0, node)

        if not nodes:
            raise GenericError("**利用可能な音楽サーバーがありません！**")

        exceptions = set()

        tracks = []

        for n in nodes:

            node_retry = False

            if source is False:
                providers = n.search_providers[:1]
                if query.startswith("https://www.youtube.com/live/"):
                    query = query.split("?")[0].replace("/live/", "/watch?v=")

                elif query.startswith("https://listen.tidal.com/album/") and "/track/" in query:
                    query = f"http://www.tidal.com/track/{query.split('/track/')[-1]}"

                elif query.startswith(("https://youtu.be/", "https://www.youtube.com/")):

                    for p in ("&ab_channel=", "&start_radio="):
                        if p in query:
                            try:
                                query = f'https://www.youtube.com/watch?v={re.search(r"v=([a-zA-Z0-9_-]+)", query).group(1)}'
                            except:
                                pass
                            break
            elif source:
                providers = [s for s in n.search_providers if s != source]
                providers.insert(0, source)
            else:
                source = True
                providers = n.search_providers

            for search_provider in providers:

                tracks = None

                search_query = query

                if source:
                    if search_provider not in n.search_providers:
                        try:
                            if search_provider.startswith("dzsearch"):
                                tracks = await self.bot.pool.deezer.get_tracks(url=query, requester=user.id, search=True,
                                                                               check_title=50)
                            elif search_provider.startswith("spsearch"):
                                tracks = await self.bot.pool.spotify.get_tracks(self.bot, user.id, query, search=True,
                                                                                check_title=50)
                            else:
                                continue

                            if tracks:
                                return tracks, node, exceptions
                            else:
                                continue

                        except Exception as e:
                            self.bot.dispatch("custom_error", ctx=ctx, error=e)
                            exceptions.add(repr(e))
                            continue
                    else:
                        search_query = f"{search_provider}:{query}"

                try:
                    tracks = await n.get_tracks(
                        search_query, track_cls=LavalinkTrack, playlist_cls=LavalinkPlaylist, requester=user.id,
                        #check_title=80
                    )
                except Exception as e:
                    #traceback.print_exc()
                    exceptions.add(repr(e))

                    if not isinstance(e, wavelink.TrackNotFound):
                        print(f"検索の処理に失敗しました...\n{query}\n{traceback.format_exc()}")
                        node_retry = True
                    elif not isinstance(e, GenericError):
                        self.bot.dispatch("custom_error", ctx=ctx, error=e)

                if tracks or not source:
                    break

            if not node_retry:
                node = n
                break

        return tracks, node, exceptions

    async def get_tracks(
            self, query: str, ctx: Union[disnake.ApplicationCommandInteraction, CustomContext, disnake.MessageInteraction, disnake.Message],
            user: disnake.Member, node: wavelink.Node = None, source=None, bot: BotCore = None, mix=False):

        exceptions = set()

        if mix:
            if not self.bot.pool.last_fm:
                raise GenericError("**私の構造でLast.fmが設定されていないため、現在ミックス/おすすめ機能はサポートされていません。**")

            query = query.title()

            try:
                artist, track = query.split(" - ", 1)
            except:
                try:
                    artist, track = query.split(' ', 1)
                except:
                    raise GenericError("次の形式で検索を入力してください: アーティスト名 - 曲名")

            current = None

            try:
                info = await self.bot.pool.last_fm.get_similar_tracks(track=track, artist=artist)
            except Exception as e:
                exceptions.add(e)
                info = []

            if not info:
                try:
                    info = await self.bot.pool.last_fm.get_artist_toptracks(artist)
                except Exception as e:
                    exceptions.add(e)

                if not info:
                    txt = f"**検索 {artist} - {track} のミックス結果が見つかりませんでした**"
                    if exceptions:
                        txt += f"\n\nエラー: ```py\n" + "\n".join(repr(e) for e in exceptions) + "```"
                    raise GenericError(txt)

                track_url = f"https://www.last.fm/music/{quote(artist)}"
                playlist_name = f"TopTracks: {artist}"

            else:
                track_url = f"https://www.last.fm/music/{quote(artist)}/_/{quote(track)}"
                playlist_name = f"Mix: {artist} - {track}"
                current = PartialTrack(
                    uri=track_url,
                    title=track,
                    author=artist,
                    requester=user.id,
                    source_name="last.fm",
                )

            playlist = PartialPlaylist(
                url=track_url,
                data={"playlistInfo": {"name": playlist_name}}
            )

            playlist.tracks = [PartialTrack(
                uri=i["url"],
                title=i["name"],
                author=i["artist"]["name"],
                requester=user.id,
                source_name="last.fm",
            ) for i in info]

            if current:
                playlist.tracks.insert(0, current)

            return playlist, node

        if bool(sc_recommended.search(query)):
            try:
                info = await bot.loop.run_in_executor(None, lambda: self.bot.pool.ytdl.extract_info(query, download=False))
            except AttributeError:
                raise GenericError("**yt-dlpの使用は無効になっています...**")

            playlist = PartialPlaylist(url=info["webpage_url"], data={"playlistInfo": {"name": info["title"]}})

            playlist.tracks = [PartialTrack(
                uri=i["url"],
                title=i["title"],
                requester=user.id,
                source_name="soundcloud",
                identifier=i["id"],
                playlist=playlist,
            ) for i in info['entries']]

            return playlist, node

        tracks, node, exceptions = await self.get_lavalink_tracks(query=query, user=user, ctx=ctx, node=node, bot=bot, source=source)

        if not tracks:

            tracks, node, exceptions = await self.get_partial_tracks(query=query, ctx=ctx, user=user, node=node, bot=bot)

            if not tracks:

                txt = "\n".join(exceptions)

                if txt:
                    if "This track is not readable. Available countries:" in txt:
                        txt = "指定された曲は現在の地域では利用できません..."
                    raise GenericError(f"**検索の処理中にエラーが発生しました:** \n{txt}", error=txt)
                raise GenericError("**検索結果が見つかりませんでした。**")

        return tracks, node

    @commands.Cog.listener("on_thread_create")
    async def thread_song_request(self, thread: disnake.Thread, reopen: bool = False, bot: BotCore = None):

        if not bot:
            bot=self.bot

        try:
            player: LavalinkPlayer = bot.music.players[thread.guild.id]
        except KeyError:
            return

        if player.static or player.message.id != thread.id:
            return

        if not thread.parent.permissions_for(thread.guild.me).send_messages_in_threads:
            await player.text_channel.send(
                embed=disnake.Embed(
                    color=self.bot.get_color(thread.guild.me),
                    description="**song-requestシステムを有効にするために、現在のチャンネルのスレッドにメッセージを送信する権限がありません...**\n\n"
                                f"スレッド {thread.mention} で送信されたメッセージは無視されます。"
                ), delete_after=30
            )
            return

        embed = disnake.Embed(color=bot.get_color(thread.guild.me))

        if not bot.intents.message_content:
            embed.description = "**警告！開発者によってmessage_contentインテントが有効化されていません...\n" \
                                "ここで曲をリクエストする機能は期待通りに動作しない可能性があります...**"

        elif not player.controller_mode:
            embed.description = "**現在のスキン/外観は、スレッド/会話を通じたsong-requestシステムと互換性がありません\n\n" \
                               "注意:** `このシステムにはボタンを使用するスキンが必要です。`"

        else:
            if reopen:
                embed.description = "**このスレッドでの曲リクエストセッションが再開されました。**"
            else:
                embed.description = "**このスレッドは一時的に曲のリクエストに使用されます。**\n\n" \
                                    "**以下のサポートされているプラットフォームの曲/動画の名前またはリンクを送信して曲をリクエストしてください:**\n" \
                                    "[`Youtube`](<https://www.youtube.com/>), [`Soundcloud`](<https://soundcloud.com/>), " \
                                    "[`Spotify`](<https://open.spotify.com/>), [`Twitch`](<https://www.twitch.tv/>)"

        await thread.send(embed=embed)

    @commands.Cog.listener("on_voice_state_update")
    async def player_vc_disconnect(
            self,
            member: disnake.Member,
            before: disnake.VoiceState,
            after: disnake.VoiceState
    ):
        try:
            player: LavalinkPlayer = self.bot.music.players[member.guild.id]
        except KeyError:
            return

        if before.channel and not after.channel:
            if player.last_channel != before.channel:
                return

        elif after.channel and not before.channel:
            if player.last_channel != after.channel:
                return

        if member.bot:
            # 他のBotを無視
            if player.bot.user.id == member.id and not after.channel:

                await asyncio.sleep(3)

                if player.is_closing:
                    return

                try:
                    player.reconnect_voice_channel_task.cancel()
                except:
                    pass
                player.reconnect_voice_channel_task = player.bot.loop.create_task(player.reconnect_voice_channel())

            return

        if before.channel == after.channel:
            try:
                vc = player.last_channel
                if vc != after.channel:
                    return
            except AttributeError:
                pass
            else:
                if after.channel == vc:
                    try:
                        player.members_timeout_task.cancel()
                    except:
                        pass
                    try:
                        check = (m for m in vc.members if not m.bot and not (m.voice.deaf or m.voice.self_deaf))
                    except:
                        check = None
                    player.start_members_timeout(check=bool(check))
            return

        try:
            player.members_timeout_task.cancel()
            player.members_timeout_task = None
        except AttributeError:
            pass

        if member.id == player.bot.user.id:

            """for b in self.bot.pool.get_guild_bots(member.guild.id):
                if b == player.bot:
                    if after.channel:
                        player._last_channel = after.channel
                    continue
                try:
                    try:
                        after.channel.voice_states[b.user.id]
                    except KeyError:
                        continue
                    if before.channel.permissions_for(member.guild.me).connect:
                        await asyncio.sleep(1)
                        await player.guild.voice_client.move_to(before.channel)
                    else:
                        player.set_command_log(text="O player foi finalizado porque me moveram ao canal "
                                                    f"{after.channel.mention} no qual o bot {b.user.mention} "
                                                    "também estava conectado gerando incompatibilidade com "
                                                    "meu sistema de multi-voice.", emoji="⚠️")
                        await player.destroy()
                    return
                except AttributeError:
                    pass
                except Exception:
                    traceback.print_exc()"""

            try:
                vc = member.guild.me.voice.channel
            except AttributeError:
                pass
            else:
                # BotをチャンネルからChannel移動した時にvoice_clientのチャンネルが設定されないことの一時的な修正
                player.guild.voice_client.channel = vc
                player._last_channel = vc
                player.update = True

        try:
            check = [m for m in player.guild.me.voice.channel.members if not m.bot and not (m.voice.deaf or m.voice.self_deaf)]
        except:
            check = None

        if player.stage_title_event and member.bot and not player.is_closing:

            try:
                if isinstance(before.channel, disnake.StageChannel):

                    if before.channel.instance and member not in before.channel.members:
                        try:
                            await before.channel.instance.edit(topic="自動更新は無効です")
                        except:
                            traceback.print_exc()
                        player.stage_title_event = False

                else:
                    if isinstance(before.channel, disnake.VoiceChannel) and member not in before.channel.members:
                        player.stage_title_event = False
                        if player.last_stage_title:
                            self.bot.loop.create_task(player.bot.edit_voice_channel_status(status=None, channel_id=before.channel.id))
            except Exception:
                traceback.print_exc()

        if member.bot and isinstance(after.channel, disnake.StageChannel) and after.channel.permissions_for(member).mute_members:
            await asyncio.sleep(1.5)
            if member not in after.channel.speakers:
                try:
                    await member.guild.me.edit(suppress=False)
                except Exception:
                    traceback.print_exc()

        if check:
            try:
                player.auto_skip_track_task.cancel()
            except AttributeError:
                pass
            player.auto_skip_track_task = None

        player.start_members_timeout(check=bool(check))

        if not member.guild.me.voice:
            await asyncio.sleep(1)
            if not player.is_closing and not player._new_node_task:
                try:
                    await player.destroy(force=True)
                except Exception:
                    traceback.print_exc()

        # rich presence stuff

        if player.auto_pause:
            return

        if player.is_closing or (member.bot and not before.channel):
            return

        channels = set()

        try:
            channels.add(before.channel.id)
        except:
            pass

        try:
            channels.add(after.channel.id)
        except:
            pass

        try:
            try:
                vc = player.guild.me.voice.channel
            except AttributeError:
                vc = player.last_channel

            if vc.id not in channels:
                return
        except AttributeError:
            pass

        if not after or before.channel != after.channel:

            try:
                vc = player.guild.me.voice.channel
            except AttributeError:
                vc = before.channel

            if vc:

                try:
                    await player.process_rpc(vc, users=[member.id], close=not player.guild.me.voice or after.channel != player.guild.me.voice.channel, wait=True)
                except AttributeError:
                    traceback.print_exc()
                    pass

                await player.process_rpc(vc, users=[m for m in vc.voice_states if (m != member.id)])

    async def check_available_bot(self, inter, guild: disnake.Guild, bot: BotCore = None, message: disnake.Message = None):

        free_bots = []
        voice_channels = []
        bot_count = 0

        if bot:
            try:
                player = bot.music.players[guild.id]
            except KeyError:
                pass
            else:
                if player.guild.me.voice and inter.author.id in player.guild.me.voice.channel.voice_states:
                    return [bot]

        for b in self.bot.pool.get_guild_bots(guild.id):

            if not b.bot_ready:
                continue

            g = b.get_guild(guild.id)

            if not g:
                bot_count += 1
                continue

            author = g.get_member(inter.author.id)

            if not author:
                continue

            inter.author = author

            if b.user in inter.author.voice.channel.members:
                free_bots.append(b)
                break

            p: LavalinkPlayer = b.music.players.get(guild.id)

            if p:

                try:
                    vc = g.me.voice.channel
                except AttributeError:
                    vc = p.last_channel

                if not vc:
                    continue

                if inter.author.id in vc.members:
                    free_bots.append(b)
                    break
                else:
                    voice_channels.append(vc.mention)
                    continue

            free_bots.append(b)

        if not free_bots:

            if bot_count:
                txt = "**現在すべてのBotが使用中です...**"
                if voice_channels:
                    txt += "\n\n**アクティブなセッションがある以下のチャンネルのいずれかに接続できます:**\n" + ", ".join(
                        voice_channels)
                    if inter.author.guild_permissions.manage_guild:
                        txt += "\n\n**または、下のボタンをクリックして現在のサーバーに音楽Botを追加することもできます:**"
                    else:
                        txt += "\n\n**または、サーバーの管理者/マネージャーに下のボタンをクリックして " \
                               "現在のサーバーに音楽Botを追加するように依頼してください。**"
            else:
                txt = "**サーバーに互換性のある音楽Botがありません...**" \
                      "\n\n下のボタンをクリックして、少なくとも1つの互換性のあるBotを追加する必要があります:"

            kwargs = {}

            try:
                func = inter.edit_original_message
            except:
                try:
                    func = inter.store_message.edit
                except:
                    try:
                        func = message.edit
                    except:
                        func = inter.send
                        kwargs["ephemeral"] = True

            await func(txt, components=[disnake.ui.Button(custom_id="bot_invite", label="Botを追加")], **kwargs)
            return []

        return free_bots

    async def reset_controller_db(self, guild_id: int, data: dict, inter: disnake.ApplicationCommandInteraction = None):

        data['player_controller']['channel'] = None
        data['player_controller']['message_id'] = None

        if inter:
            try:
                bot = inter.music_bot
            except AttributeError:
                bot = inter.bot
        else:
            bot = self.bot

        try:
            await bot.update_data(guild_id, data, db_name=DBModel.guilds)
        except Exception:
            traceback.print_exc()

        try:
            player: LavalinkPlayer = bot.music.players[guild_id]
        except KeyError:
            return

        player.static = False

        if inter:
            try:
                if isinstance(inter.channel.parent, disnake.TextChannel):
                    player.text_channel = inter.channel.parent
                else:
                    player.text_channel = inter.channel
            except AttributeError:
                player.text_channel = inter.channel

    async def get_best_node(self, bot: BotCore = None):

        if not bot:
            bot = self.bot

        try:
            return sorted(
                [n for n in bot.music.nodes.values() if n.stats and n.is_available and n.available],
                key=lambda n: n.stats.players
            )[0]

        except IndexError:
            try:
                node = bot.music.nodes['LOCAL']
            except KeyError:
                pass
            else:
                if not node._websocket.is_connected:
                    await node.connect()
                return node

            raise GenericError("**利用可能な音楽サーバーがありません。**")

    async def error_report_loop(self):

        while True:

            data = await self.error_report_queue.get()

            async with aiohttp.ClientSession() as session:
                webhook = disnake.Webhook.from_url(self.bot.config["AUTO_ERROR_REPORT_WEBHOOK"], session=session)
                await webhook.send(username=self.bot.user.display_name, avatar_url=self.bot.user.display_avatar.url, **data)

            await asyncio.sleep(15)


def setup(bot: BotCore):

    if not getattr(bot.pool, 'ytdl', None):

        bot.pool.ytdl = CustomYTDL(
            {
                'format': 'webm[abr>0]/bestaudio/best',
                'extract_flat': True,
                'quiet': True,
                'no_warnings': True,
                'lazy_playlist': True,
                'playlist_items': '1-700',
                'simulate': True,
                'download': False,
                'cachedir': False,
                'allowed_extractors': [
                    r'.*youtube.*',
                    r'.*soundcloud.*',
                ],
                'extractor_args': {
                    'youtube': {
                        #'player_client': [
                        #    'web',
                        #    'android',
                        #    'android_creator',
                        #    'web_creator',
                        #],
                        'max_comments': [0],
                    },
                    'youtubetab': {
                        "skip": ["webpage", "authcheck"]
                    }
                }
            }
        )

    bot.add_cog(Music(bot))
