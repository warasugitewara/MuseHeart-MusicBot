# -*- coding: utf-8 -*-
import datetime
import itertools
from os.path import basename

import disnake

from utils.music.converters import time_format, fix_characters, get_button_style, music_source_image
from utils.music.models import LavalinkPlayer
from utils.others import PlayerControls


class MiniStaticSkin:

    __slots__ = ("name", "preview")

    def __init__(self):
        self.name = basename(__file__)[:-3] + "_static"
        self.preview = "https://i.ibb.co/F3NTnPc/mini-static-skin.png"

    def setup_features(self, player: LavalinkPlayer):
        player.mini_queue_feature = False
        player.controller_mode = True
        player.auto_update = 0
        player.hint_rate = player.bot.config["HINT_RATE"]
        player.static = True

    def load(self, player: LavalinkPlayer) -> dict:

        data = {
            "content": None,
            "embeds": [],
        }

        embed_color = player.bot.get_color(player.guild.me)

        embed = disnake.Embed(
            color=embed_color,
            description=f"-# [`{player.current.single_title}`]({player.current.uri or player.current.search_uri})"
        )
        embed_queue = None
        queue_size = len(player.queue)

        if not player.paused:
            embed.set_author(
                name="再生中:",
                icon_url=music_source_image(player.current.info["sourceName"]),
            )

        else:
            embed.set_author(
                name="一時停止中:",
                icon_url="https://cdn.discordapp.com/attachments/480195401543188483/896013933197013002/pause.png"
            )

        if player.current.track_loops:
            embed.description += f" `[🔂 {player.current.track_loops}]`"

        elif player.loop:
            if player.loop == 'current':
                embed.description += ' `[🔂 現在の曲]`'
            else:
                embed.description += ' `[🔁 キュー]`'

        if not player.current.autoplay:
            embed.description += f" `[`<@{player.current.requester}>`]`"
        else:
            try:
                embed.description += f" [`[おすすめ]`]({player.current.info['extra']['related']['uri']})"
            except:
                embed.description += "` [おすすめ]`"

        duration = "🔴 ライブ配信" if player.current.is_stream else \
            time_format(player.current.duration)

        embed.add_field(name="⏰ **⠂再生時間:**", value=f"```ansi\n[34;1m{duration}[0m\n```")
        embed.add_field(name="💠 **⠂アップローダー/アーティスト:**",
                        value=f"```ansi\n[34;1m{fix_characters(player.current.author, 18)}[0m\n```")

        if player.command_log:
            embed.add_field(name=f"{player.command_log_emoji} **⠂最後の操作:**",
                            value=f"{player.command_log}", inline=False)

        embed.set_image(url=player.current.thumb or "https://media.discordapp.net/attachments/480195401543188483/987830071815471114/musicequalizer.gif")

        if queue_size:

            queue_txt = ""

            has_stream = False

            current_time = disnake.utils.utcnow() - datetime.timedelta(milliseconds=player.position) + datetime.timedelta(milliseconds=player.current.duration)

            queue_duration = 0

            for n, t in enumerate(player.queue):

                if t.is_stream:
                    has_stream = True

                elif n != 0:
                    queue_duration += t.duration

                if n > 7:
                    if has_stream:
                        break
                    continue

                if has_stream:
                    duration = time_format(t.duration) if not t.is_stream else '🔴 ライブ'

                    queue_txt += f"`┌ {n + 1})` [`{fix_characters(t.title, limit=34)}`]({t.uri})\n" \
                                 f"`└ ⏲️ {duration}`" + (f" - `リピート: {t.track_loops}`" if t.track_loops else "") + \
                                 f" **|** `✋` <@{t.requester}>\n"

                else:
                    duration = f"<t:{int((current_time + datetime.timedelta(milliseconds=queue_duration)).timestamp())}:R>"

                    queue_txt += f"-# `┌ {n + 1})` [`{fix_characters(t.title, limit=34)}`]({t.uri})\n" \
                                 f"-# `└ ⏲️` {duration}" + (f" - `リピート: {t.track_loops}`" if t.track_loops else "") + \
                                 f" **|** `✋` <@{t.requester}>\n"

            embed_queue = disnake.Embed(title=f"再生キュー: {queue_size}",
                                        color=player.bot.get_color(player.guild.me),
                                        description=f"\n{queue_txt}")

            if not has_stream and not player.loop and not player.keep_connected and not player.paused and not player.current.is_stream:
                embed_queue.description += f"\n-# `[ ⌛ 再生終了` <t:{int((current_time + datetime.timedelta(milliseconds=queue_duration + player.current.duration)).timestamp())}:R> `⌛ ]`"

        elif player.queue_autoplay:

            queue_txt = ""

            has_stream = False

            current_time = disnake.utils.utcnow() - datetime.timedelta(milliseconds=player.position) + datetime.timedelta(milliseconds=player.current.duration)

            queue_duration = 0

            for n, t in enumerate(player.queue_autoplay):

                if t.is_stream:
                    has_stream = True

                elif n != 0:
                    queue_duration += t.duration

                if n > 7:
                    if has_stream:
                        break
                    continue

                if has_stream:
                    duration = time_format(t.duration) if not t.is_stream else '🔴 ライブ'

                    queue_txt += f"`┌ {n + 1})` [`{fix_characters(t.title, limit=34)}`]({t.uri})\n" \
                                 f"`└ ⏲️ {duration}`" + (f" - `リピート: {t.track_loops}`" if t.track_loops else "") + \
                                 f" **|** `👍⠂おすすめ`\n"

                else:
                    duration = f"<t:{int((current_time + datetime.timedelta(milliseconds=queue_duration)).timestamp())}:R>"

                    queue_txt += f"-# `┌ {n + 1})` [`{fix_characters(t.title, limit=34)}`]({t.uri})\n" \
                                 f"-# `└ ⏲️` {duration}" + (f" - `リピート: {t.track_loops}`" if t.track_loops else "") + \
                                 f" **|** `👍⠂おすすめ`\n"

            embed_queue = disnake.Embed(title="次のおすすめ曲:",
                                        color=player.bot.get_color(player.guild.me),
                                        description=f"\n{queue_txt}")

        if player.current_hint:
            embed.set_footer(text=f"💡 ヒント: {player.current_hint}")

        data["embeds"] = [embed_queue, embed] if embed_queue else [embed]

        data["components"] = [
            disnake.ui.Button(emoji="⏯️", custom_id=PlayerControls.pause_resume, style=get_button_style(player.paused)),
            disnake.ui.Button(emoji="⏮️", custom_id=PlayerControls.back),
            disnake.ui.Button(emoji="⏹️", custom_id=PlayerControls.stop),
            disnake.ui.Button(emoji="⏭️", custom_id=PlayerControls.skip),
            disnake.ui.Button(emoji="<:music_queue:703761160679194734>", custom_id=PlayerControls.queue, disabled=not (player.queue or player.queue_autoplay)),
            disnake.ui.Select(
                placeholder="その他のオプション:",
                custom_id="musicplayer_dropdown_inter",
                min_values=0, max_values=1, required = False,
                options=[
                    disnake.SelectOption(
                        label="曲を追加", emoji="<:add_music:588172015760965654>",
                        value=PlayerControls.add_song,
                        description="曲/プレイリストをキューに追加します。"
                    ),
                    disnake.SelectOption(
                        label="お気に入りに追加", emoji="💗",
                        value=PlayerControls.add_favorite,
                        description="現在の曲をお気に入りに追加します。"
                    ),
                    disnake.SelectOption(
                        label="最初から再生", emoji="⏪",
                        value=PlayerControls.seek_to_start,
                        description="現在の曲を最初から再生します。"
                    ),
                    disnake.SelectOption(
                        label=f"音量: {player.volume}%", emoji="🔊",
                        value=PlayerControls.volume,
                        description="音量を調整します。"
                    ),
                    disnake.SelectOption(
                        label="シャッフル", emoji="🔀",
                        value=PlayerControls.shuffle,
                        description="キュー内の曲をシャッフルします。"
                    ),
                    disnake.SelectOption(
                        label="再追加", emoji="🎶",
                        value=PlayerControls.readd,
                        description="再生済みの曲をキューに戻します。"
                    ),
                    disnake.SelectOption(
                        label="リピート", emoji="🔁",
                        value=PlayerControls.loop_mode,
                        description="曲/キューのリピートを切り替えます。"
                    ),
                    disnake.SelectOption(
                        label=("無効にする" if player.nightcore else "有効にする") + " nightcoreエフェクト", emoji="🇳",
                        value=PlayerControls.nightcore,
                        description="曲の速度と音程を上げるエフェクトです。"
                    ),
                    disnake.SelectOption(
                        label=("無効にする" if player.autoplay else "有効にする") + " 自動再生", emoji="🔄",
                        value=PlayerControls.autoplay,
                        description="キューが空になったら自動で曲を追加します。"
                    ),
                    disnake.SelectOption(
                        label="Last.fm scrobble", emoji="<:Lastfm:1278883704097341541>",
                        value=PlayerControls.lastfm_scrobble,
                        description="Last.fmアカウントへのscrobble/記録を切り替えます。"
                    ),
                    disnake.SelectOption(
                        label=("無効にする" if player.restrict_mode else "有効にする") + " 制限モード", emoji="🔐",
                        value=PlayerControls.restrict_mode,
                        description="DJ/スタッフのみが制限コマンドを使用できます。"
                    ),
                ]
            ),
        ]

        if (queue:=player.queue or player.queue_autoplay):
            data["components"].append(
                disnake.ui.Select(
                    placeholder="次の曲:",
                    custom_id="musicplayer_queue_dropdown",
                    min_values=0, max_values=1, required = False,
                    options=[
                        disnake.SelectOption(
                            label=fix_characters(f"{n+1}. {t.single_title}", 47),
                            description=fix_characters(f"[{time_format(t.duration) if not t.is_stream else '🔴 Live'}]. {t.authors_string}", 47),
                            value=f"{n:02d}.{t.title[:96]}"
                        ) for n, t in enumerate(itertools.islice(queue, 25))
                    ]
                )
            )

        if player.current.ytid and player.node.lyric_support:
            data["components"][5].options.append(
                disnake.SelectOption(
                    label= "歌詞を表示", emoji="📃",
                    value=PlayerControls.lyrics,
                    description="Obter letra da 現在の曲."
                )
            )


        if isinstance(player.last_channel, disnake.VoiceChannel):
            data["components"][5].options.append(
                disnake.SelectOption(
                    label="自動ステータス", emoji="📢",
                    value=PlayerControls.set_voice_status,
                    description="ボイスチャンネルの自動ステータスを設定します。"
                )
            )

        return data

def load():
    return MiniStaticSkin()






