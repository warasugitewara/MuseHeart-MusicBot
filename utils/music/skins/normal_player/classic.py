# -*- coding: utf-8 -*-
import itertools
from os.path import basename

import disnake

from utils.music.converters import fix_characters, time_format, get_button_style, music_source_image
from utils.music.models import LavalinkPlayer
from utils.others import PlayerControls


class ClassicSkin:

    __slots__ = ("name", "preview")

    def __init__(self):
        self.name = basename(__file__)[:-3]
        self.preview = "https://i.ibb.co/893S3dJ/image.png"

    def setup_features(self, player: LavalinkPlayer):
        player.mini_queue_feature = True
        player.controller_mode = True
        player.auto_update = 0
        player.hint_rate = player.bot.config["HINT_RATE"]
        player.static = False

    def load(self, player: LavalinkPlayer) -> dict:

        data = {
            "content": None,
            "embeds": []
        }

        color = player.bot.get_color(player.guild.me)

        embed = disnake.Embed(color=color, description="")

        queue_txt = ""

        bar = "https://cdn.discordapp.com/attachments/554468640942981147/1127294696025227367/rainbow_bar3.gif"

        embed_top = disnake.Embed(
            color=color,
            description=f"### [{player.current.title}]({player.current.uri or player.current.search_uri})"
        )
        embed.set_image(url=bar)

        embed_top.set_image(url=bar)

        embed_top.set_thumbnail(url=player.current.thumb)

        if not player.paused:
            (embed_top or embed).set_author(
                name="再生中:",
                icon_url=music_source_image(player.current.info["sourceName"])
            )
        else:
            (embed_top or embed).set_author(
                name="一時停止中:",
                icon_url="https://cdn.discordapp.com/attachments/480195401543188483/896013933197013002/pause.png"
            )

        if player.current.is_stream:
            duration = "🔴 **⠂ `Livestream`"
        else:
            duration = f"⏰ **⠂** `{time_format(player.current.duration)}`"

        txt = f"{duration}\n" \
              f"👤 **⠂** `{player.current.author}`\n"

        if not player.current.autoplay:
            txt += f"🎧 **⠂** <@{player.current.requester}>\n"
        else:
            try:
                mode = f" [`おすすめ`]({player.current.info['extra']['related']['uri']})"
            except:
                mode = "`おすすめ`"
            txt += f"> 👍 **⠂** {mode}\n"

        if player.current.playlist_name:
            txt += f"📑 **⠂** [`{fix_characters(player.current.playlist_name, limit=19)}`]({player.current.playlist_url})\n"

        if qsize := len(player.queue):

            if not player.mini_queue_enabled:
                txt += f"🎶 **⠂** `キューに{qsize}曲あります`\n"
            else:
                queue_txt += "```ansi\n[0;33m次の曲:[0m```" + "\n".join(
                    f"`{(n + 1):02}) [{time_format(t.duration) if t.duration else '🔴 Livestream'}]` "
                    f"[`{fix_characters(t.title, 29)}`]({t.uri})" for n, t in
                    enumerate(itertools.islice(player.queue, 3))
                )

                if qsize > 3:
                    queue_txt += f"\n`╚══════ 他に{(t:=qsize - 3)}曲 ══════╝`"

        elif len(player.queue_autoplay):
            queue_txt += "```ansi\n[0;33m次の曲:[0m```" + "\n".join(
                f"`👍⠂{(n + 1):02}) [{time_format(t.duration) if t.duration else '🔴 Livestream'}]` "
                f"[`{fix_characters(t.title, 29)}`]({t.uri})" for n, t in
                enumerate(itertools.islice(player.queue_autoplay, 3))
            )

        if player.command_log:
            txt += f"{player.command_log_emoji} **⠂最後の操作:** {player.command_log}\n"

        embed.description += txt + queue_txt

        if player.current_hint:
            embed.set_footer(text=f"💡 ヒント: {player.current_hint}")
        else:
            embed.set_footer(
                text=str(player),
                icon_url="https://i.ibb.co/QXtk5VB/neon-circle.gif"
            )

        data["embeds"] = [embed_top, embed] if embed_top else [embed]

        data["components"] = [
            disnake.ui.Button(emoji="⏯️", custom_id=PlayerControls.pause_resume, style=get_button_style(player.paused)),
            disnake.ui.Button(emoji="⏮️", custom_id=PlayerControls.back),
            disnake.ui.Button(emoji="⏹️", custom_id=PlayerControls.stop),
            disnake.ui.Button(emoji="⏭️", custom_id=PlayerControls.skip),
            disnake.ui.Button(emoji="<:music_queue:703761160679194734>", custom_id=PlayerControls.queue, disabled=not (player.queue or player.queue_autoplay)),
            disnake.ui.Select(
                placeholder="その他のオプション:",
                custom_id="musicplayer_dropdown_inter",
                min_values=0, max_values=1,
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
                    label= "歌詞を表示", emoji="📃",
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
    return ClassicSkin()
