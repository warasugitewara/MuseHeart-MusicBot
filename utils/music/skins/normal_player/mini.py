# -*- coding: utf-8 -*-
import itertools
from os.path import basename

import disnake

from utils.music.converters import time_format, fix_characters, get_button_style, music_source_image
from utils.music.models import LavalinkPlayer
from utils.others import PlayerControls


class MiniSkin:

    __slots__ = ("name", "preview")

    def __init__(self):
        self.name = basename(__file__)[:-3]
        self.preview = "https://i.ibb.co/ZBTbdvT/mini.png"

    def setup_features(self, player: LavalinkPlayer):
        player.mini_queue_feature = True
        player.controller_mode = True
        player.auto_update = 0
        player.hint_rate = player.bot.config["HINT_RATE"]
        player.static = False

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

        duration = "🔴 Livestream" if player.current.is_stream else \
            time_format(player.current.duration)

        embed.add_field(name="⏰ **⠂再生時間:**", value=f"```ansi\n[34;1m{duration}[0m\n```")
        embed.add_field(name="💠 **⠂アップローダー/アーティスト:**",
                        value=f"```ansi\n[34;1m{fix_characters(player.current.author, 18)}[0m\n```")

        if player.command_log:
            embed.add_field(name=f"{player.command_log_emoji} **⠂最後の操作:**",
                            value=f"{player.command_log}", inline=False)

        if queue_size:

            embed.description += f" `({queue_size})`"

            if player.mini_queue_enabled:
                embed_queue = disnake.Embed(
                    color=embed_color,
                    description="\n".join(
                        f"`{(n + 1):02}) [{time_format(t.duration) if not t.is_stream else '🔴 Livestream'}]` [`{fix_characters(t.title, 38)}`]({t.uri})"
                        for n, t in (enumerate(itertools.islice(player.queue, 5)))
                    )
                )
                embed_queue.set_image(url="https://cdn.discordapp.com/attachments/554468640942981147/1082887587770937455/rainbow_bar2.gif")

        embed.set_thumbnail(url=player.current.thumb)
        embed.set_image(url="https://cdn.discordapp.com/attachments/554468640942981147/1082887587770937455/rainbow_bar2.gif")

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
                        label=f"Volume: {player.volume}%", emoji="🔊",
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
                        description="キューが空になった時に自動で曲を追加します。"
                    ),
                    disnake.SelectOption(
                        label="Last.fm scrobble", emoji="<:Lastfm:1278883704097341541>",
                        value=PlayerControls.lastfm_scrobble,
                        description="Last.fmアカウントへの記録を切り替えます。"
                    ),
                    disnake.SelectOption(
                        label= ("無効にする" if player.restrict_mode else "有効にする") + " 制限モード", emoji="🔐",
                        value=PlayerControls.restrict_mode,
                        description="DJ/スタッフのみが制限コマンドを使用できます。"
                    ),
                ]
            ),
        ]

        if player.current.ytid and player.node.lyric_support:
            data["components"][5].options.append(
                disnake.SelectOption(
                    label="歌詞を表示", emoji="📃",
                    value=PlayerControls.lyrics,
                    description="現在の曲の歌詞を取得します。"
                )
            )


        if player.mini_queue_feature:
            data["components"][5].options.append(
                disnake.SelectOption(
                    label="ミニキュー", emoji="<:music_queue:703761160679194734>",
                    value=PlayerControls.miniqueue,
                    description="プレイヤーのミニキューを切り替えます。"
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

        if not player.has_thread:
            data["components"][5].options.append(
                disnake.SelectOption(
                    label="リクエストスレッド", emoji="💬",
                    value=PlayerControls.song_request_thread,
                    description="曲名/リンクでリクエストできる一時スレッドを作成します。"
                )
            )

        return data

def load():
    return MiniSkin()
