# -*- coding: utf-8 -*-
import datetime
import itertools
from os.path import basename

import disnake

from utils.music.converters import fix_characters, time_format, get_button_style, music_source_image
from utils.music.models import LavalinkPlayer
from utils.others import ProgressBar, PlayerControls


class DefaultProgressbarStaticSkin:

    __slots__ = ("name", "preview")

    def __init__(self):
        self.name = basename(__file__)[:-3] + "_static"
        self.preview = "https://i.ibb.co/WtyW264/progressbar-static-skin.png"

    def setup_features(self, player: LavalinkPlayer):
        player.mini_queue_feature = False
        player.controller_mode = True
        player.auto_update = 15
        player.hint_rate = player.bot.config["HINT_RATE"]
        player.static = True

    def load(self, player: LavalinkPlayer) -> dict:

        data = {
            "content": None,
            "embeds": []
        }

        embed = disnake.Embed(color=player.bot.get_color(player.guild.me))
        embed_queue = None

        if not player.paused:
            embed.set_author(
                name="再生中:",
                icon_url=music_source_image(player.current.info["sourceName"])
            )
        else:
            embed.set_author(
                name="一時停止中:",
                icon_url="https://cdn.discordapp.com/attachments/480195401543188483/896013933197013002/pause.png"
            )

        if player.current_hint:
            embed.set_footer(text=f"💡 ヒント: {player.current_hint}")
        else:
            embed.set_footer(
                text=str(player),
                icon_url="https://i.ibb.co/QXtk5VB/neon-circle.gif"
            )

        if player.current.is_stream:
            duration = "```ansi\n🔴 [31;1m ライブ配信[0m```"
        else:

            progress = ProgressBar(
                player.position,
                player.current.duration,
                bar_count=17
            )

            duration = f"```ansi\n[34;1m[{time_format(player.position)}] {('='*progress.start)}[0m🔴️[36;1m{'-'*progress.end} " \
                       f"[{time_format(player.current.duration)}][0m```\n"

        vc_txt = ""
        queue_img = ""

        txt = f"-# [`{player.current.single_title}`]({player.current.uri or player.current.search_uri})\n\n" \
              f"> -# 💠 **⠂アーティスト:** {player.current.authors_md}"

        if not player.current.autoplay:
            txt += f"\n> -# ✋ **⠂Pedido アーティスト:** <@{player.current.requester}>"
        else:
            try:
                mode = f" [`おすすめ`]({player.current.info['extra']['related']['uri']})"
            except:
                mode = "`おすすめ`"
            txt += f"\n> -# 👍 **⠂追加方法:** {mode}"

        try:
            vc_txt = f"\n> -# *️⃣ **⠂ボイスチャンネル:** {player.guild.me.voice.channel.mention}"
        except AttributeError:
            pass

        if player.current.track_loops:
            txt += f"\n> -# 🔂 **⠂残りリピート回数:** `{player.current.track_loops}`"

        if player.loop:
            if player.loop == 'current':
                e = '🔂'
                m = '現在の曲'
            else:
                e = '🔁'
                m = 'キュー'
            txt += f"\n> -# {e} **⠂リピートモード:** `{m}`"

        if player.current.album_name:
            txt += f"\n> -# 💽 **⠂アルバム:** [`{fix_characters(player.current.album_name, limit=20)}`]({player.current.album_url})"

        if player.current.playlist_name:
            txt += f"\n> -# 📑 **⠂プレイリスト:** [`{fix_characters(player.current.playlist_name, limit=20)}`]({player.current.playlist_url})"

        if player.keep_connected:
            txt += "\n> -# ♾️ **⠂24/7モード:** `有効`"

        txt += f"{vc_txt}\n"

        if player.command_log:
            txt += f"> -# {player.command_log_emoji} **⠂最後の操作:** {player.command_log}\n"

        txt += duration

        if qlenght:=len(player.queue):

            queue_txt = ""

            has_stream = False

            current_time = disnake.utils.utcnow() - datetime.timedelta(milliseconds=player.position + player.current.duration)

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

                    queue_txt += f"`┌ {n + 1})` [`{fix_characters(t.title, limit=34)}`]({t.uri})\n" \
                                 f"`└ ⏲️` {duration}" + (f" - `リピート: {t.track_loops}`" if t.track_loops else "") + \
                                 f" **|** `✋` <@{t.requester}>\n"

            embed_queue = disnake.Embed(title=f"再生キュー: {qlenght}",
                                        color=player.bot.get_color(player.guild.me),
                                        description=f"\n{queue_txt}")

            if not has_stream and not player.loop and not player.keep_connected and not player.paused and not player.current.is_stream:
                embed_queue.description += f"\n`[ ⌛ 再生終了` <t:{int((current_time + datetime.timedelta(milliseconds=queue_duration + player.current.duration)).timestamp())}:R> `⌛ ]`"

            embed_queue.set_image(url=queue_img)

        elif len(player.queue_autoplay):

            queue_txt = ""

            has_stream = False

            current_time = disnake.utils.utcnow() - datetime.timedelta(milliseconds=player.position + player.current.duration)

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

                    queue_txt += f"-# `┌ {n+1})` [`{fix_characters(t.title, limit=34)}`]({t.uri})\n" \
                           f"-# `└ ⏲️ {duration}`" + (f" - `リピート: {t.track_loops}`" if t.track_loops else "") + \
                           f" **|** `👍⠂おすすめ`\n"

                else:
                    duration = f"<t:{int((current_time + datetime.timedelta(milliseconds=queue_duration)).timestamp())}:R>"

                    queue_txt += f"-# `┌ {n+1})` [`{fix_characters(t.title, limit=34)}`]({t.uri})\n" \
                           f"-# `└ ⏲️` {duration}" + (f" - `リピート: {t.track_loops}`" if t.track_loops else "") + \
                           f" **|** `👍⠂おすすめ`\n"

            embed_queue = disnake.Embed(title="次のおすすめ曲:", color=player.bot.get_color(player.guild.me),
                                        description=f"\n{queue_txt}")

            embed_queue.set_image(url=queue_img)

        embed.description = txt
        embed.set_image(url=player.current.thumb)

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
    return DefaultProgressbarStaticSkin()






