# -*- coding: utf-8 -*-
import datetime
import itertools
from os.path import basename

import disnake

from utils.music.converters import time_format, fix_characters, get_button_style
from utils.music.models import LavalinkPlayer
from utils.others import PlayerControls


class EmbedLinkStaticSkin:
    __slots__ = ("name", "preview")

    def __init__(self):
        self.name = basename(__file__)[:-3] + "_static"
        self.preview = "https://media.discordapp.net/attachments/554468640942981147/1101328287466274816/image.png"

    def setup_features(self, player: LavalinkPlayer):
        player.mini_queue_feature = False
        player.controller_mode = True
        player.auto_update = 0
        player.hint_rate = player.bot.config["HINT_RATE"]
        player.static = True

    def load(self, player: LavalinkPlayer) -> dict:

        txt = ""

        if player.current_hint:
            txt += f"\n> -# `💡 ヒント: {player.current_hint}`\n"

        if player.current.is_stream:
            duration_txt = f"\n> -# 🔴 **⠂再生時間:** `ライブ配信`"
        else:
            duration_txt = f"\n> -# ⏰ **⠂再生時間:** `{time_format(player.current.duration)}`"

        title = fix_characters(player.current.title) if not player.current.uri else f"[{fix_characters(player.current.title)}]({player.current.uri})"

        if player.paused:
            txt += f"\n> ### ⏸️ ⠂一時停止中: {title}\n{duration_txt}"

        else:
            txt += f"\n> ### ▶️ ⠂再生中: {title}\n{duration_txt}"
            if not player.current.is_stream and not player.paused:
                txt += f" `[`<t:{int((disnake.utils.utcnow() + datetime.timedelta(milliseconds=player.current.duration - player.position)).timestamp())}:R>`]`"

        vc_txt = ""

        if not player.current.autoplay:
            txt += f"\n> -# ✋ **⠂リクエスト:** <@{player.current.requester}>\n"
        else:
            try:
                mode = f" [`おすすめの曲`](<{player.current.info['extra']['related']['uri']}>)"
            except:
                mode = "`おすすめの曲`"
            txt += f"\n> -# 👍 **⠂追加方法:** {mode}\n"

        try:
            vc_txt += f"> -# *️⃣ **⠂ボイスチャンネル:** {player.guild.me.voice.channel.mention}\n"
        except AttributeError:
            pass

        if player.current.playlist_name:
            txt += f"> -# 📑 **⠂プレイリスト:** [`{fix_characters(player.current.playlist_name) or '表示'}`](<{player.current.playlist_url}>)\n"

        if player.current.track_loops:
            txt += f"> -# 🔂 **⠂残りリピート回数:** `{player.current.track_loops}`\n"

        elif player.loop:
            if player.loop == 'current':
                txt += '> -# 🔂 **⠂リピート:** `現在の曲`\n'
            else:
                txt += '> -# 🔁 **⠂リピート:** `キュー`\n'

        txt += vc_txt

        if player.command_log:

            txt += f"> -# {player.command_log_emoji} **⠂最後の操作:** {player.command_log}\n"

        if qsize := len(player.queue):

            qtext = "> -# **再生キュー"

            if qsize  > 4:
                qtext += f" [{qsize}]:"

            qtext += "**\n" + "\n".join(
                                  f"> -# `{(n + 1)} [{time_format(t.duration) if not t.is_stream else '🔴 配信'}]` [`{fix_characters(t.title, 30)}`](<{t.uri}>)"
                                  for n, t in enumerate(
                                      itertools.islice(player.queue, 4)))

            txt = f"{qtext}\n{txt}"

        elif len(player.queue_autoplay):

            txt = "**次のおすすめ曲:**\n" + \
                              "\n".join(
                                  f"-# `{(n + 1)} [{time_format(t.duration) if not t.is_stream else '🔴 配信'}]` [`{fix_characters(t.title, 30)}`](<{t.uri}>)"
                                  for n, t in enumerate(
                                      itertools.islice(player.queue_autoplay, 4))) + f"\n{txt}"

        data = {
            "content": txt,
            "embeds": [],
            "components": [
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
        }

        if (queue:=player.queue or player.queue_autoplay):
            data["components"].append(
                disnake.ui.Select(
                    placeholder="次の曲:",
                    custom_id="musicplayer_queue_dropdown",
                    min_values=0, max_values=1,
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
    return EmbedLinkStaticSkin()






