# -*- coding: utf-8 -*-
import os
import traceback
from typing import Union, Optional

import disnake
from disnake.ext import commands
from disnake.utils import escape_mentions
from pymongo.errors import ServerSelectionTimeoutError

from utils.music.converters import time_format, perms_translations
from wavelink import WavelinkException, TrackNotFound, MissingSessionID


class PoolException(commands.CheckFailure):
    pass

class ArgumentParsingError(commands.CommandError):
    def __init__(self, message):
        super().__init__(escape_mentions(message))

class GenericError(commands.CheckFailure):

    def __init__(self, text: str, *, self_delete: int = None, delete_original: Optional[int] = None, components: list = None, error: str = None):
        self.text = text
        self.self_delete = self_delete
        self.delete_original = delete_original
        self.components = components
        self.error = error

    def __repr__(self):
        return disnake.utils.escape_markdown(self.text)

    def __str__(self):
        return disnake.utils.escape_markdown(self.text)


class EmptyFavIntegration(commands.CheckFailure):
    pass

class MissingSpotifyClient(commands.CheckFailure):
    pass


class NoPlayer(commands.CheckFailure):
    pass


class NoVoice(commands.CheckFailure):
    pass


class MissingVoicePerms(commands.CheckFailure):

    def __init__(self, voice_channel: Union[disnake.VoiceChannel, disnake.StageChannel]):
        self.voice_channel = voice_channel


class DiffVoiceChannel(commands.CheckFailure):
    pass


class NoSource(commands.CheckFailure):
    pass


class NotDJorStaff(commands.CheckFailure):
    pass


class NotRequester(commands.CheckFailure):
    pass


class YoutubeSourceDisabled(commands.CheckFailure):
    pass


def parse_error(
        ctx: Union[disnake.ApplicationCommandInteraction, commands.Context, disnake.MessageInteraction],
        error: Exception, **kwargs
):

    error_txt = None

    kill_process = False

    mention_author = False

    components = []

    send_error = False

    error = getattr(error, 'original', error)

    if isinstance(error, NotDJorStaff):
        error_txt = "**このコマンドを使用するには、DJリストに登録されているか、**メンバーを移動** " \
                    "の権限が必要です。**"

    elif isinstance(error, MissingVoicePerms):
        error_txt = f"**チャンネルに接続/発言する権限がありません:** {error.voice_channel.mention}"

    elif isinstance(error, commands.NotOwner):
        error_txt = "**このコマンドは開発者のみが使用できます。**"

    elif isinstance(error, commands.BotMissingPermissions):
        error_txt = "このコマンドを実行するために必要な権限がありません: ```\n{}```" \
            .format(", ".join(perms_translations.get(perm, perm) for perm in error.missing_permissions))

    elif isinstance(error, commands.MissingPermissions):
        error_txt = "このコマンドを実行するために必要な権限がありません: ```\n{}```" \
            .format(", ".join(perms_translations.get(perm, perm) for perm in error.missing_permissions))

    elif isinstance(error, GenericError):
        error_txt = error.text
        components = error.components
        if error.text:
            send_error = True

    elif isinstance(error, NotRequester):
        error_txt = "**曲をスキップするには、現在の曲をリクエストしたか、DJリストに登録されているか、" \
                    "**チャンネルを管理**の権限が必要です。**"

    elif isinstance(error, DiffVoiceChannel):
        error_txt = "**このコマンドを使用するには、私が接続しているボイスチャンネルに参加する必要があります。**"

    elif isinstance(error, NoSource):
        error_txt = "**現在プレイヤーに曲がありません。**"

    elif isinstance(error, NoVoice):
        error_txt = "**このコマンドを使用するには、ボイスチャンネルに参加する必要があります。**"

    elif isinstance(error, NoPlayer):
        try:
            error_txt = f"**チャンネル {ctx.author.voice.channel.mention} にアクティブなプレイヤーがありません。**"
        except AttributeError:
            error_txt = "**サーバーで初期化されたプレイヤーがありません。**"

    elif isinstance(error, (commands.UserInputError, commands.MissingRequiredArgument)) and ctx.command.usage:

        error_txt = "### コマンドの使用方法が正しくありません。\n"

        if ctx.command.usage:

            prefix = ctx.prefix if str(ctx.me.id) not in ctx.prefix else f"@{ctx.me.display_name} "

            error_txt += f'📘 **⠂使用方法:** ```\n{ctx.command.usage.replace("{prefix}", prefix).replace("{cmd}", ctx.command.name).replace("{parent}", ctx.command.full_parent_name)}```\n' \
                        f"⚠️ **⠂引数の使用に関する注意事項:** ```\n" \
                        f"[] = 必須 | <> = 任意```\n"

    elif isinstance(error, MissingSpotifyClient):
        error_txt = "**現在、Spotifyのリンクには対応していません。**"

    elif isinstance(error, commands.NoPrivateMessage):
        error_txt = "このコマンドはダイレクトメッセージでは実行できません。"

    elif isinstance(error, MissingSessionID):
        error_txt = f"**音楽サーバー {error.node.identifier} が切断されています。数秒お待ちいただき、再度お試しください。**"

    elif isinstance(error, commands.CommandOnCooldown):
        remaing = int(error.retry_after)
        if remaing < 1:
            remaing = 1
        error_txt = "**このコマンドを使用するには {} お待ちください。**".format(time_format(int(remaing) * 1000, use_names=True))

    elif isinstance(error, EmptyFavIntegration):

        if isinstance(ctx, disnake.MessageInteraction):
            error_txt = "**お気に入り/連携がありません**\n\n" \
                        "`次回このボタンを使用するために、お気に入りまたは連携を追加できます。" \
                        "下のボタンをクリックして追加してください。`"
        else:
            error_txt = "**曲や動画の名前またはリンクを含めずにコマンドを使用しましたが、" \
                        "このコマンドを直接使用するためのお気に入りや連携がありません...**\n\n" \
                        "`名前やリンクを含めずにこのコマンドを使用するために、お気に入りまたは連携を追加できます。" \
                        "下のボタンをクリックして追加してください。`"

        mention_author = True

        components = [
            disnake.ui.Button(label="お気に入りと連携の管理を開く",
                              custom_id="musicplayer_fav_manager", emoji="⭐"),
        ]

    elif isinstance(error, commands.MaxConcurrencyReached):
        txt = f"{error.number}回 " if error.number > 1 else ''
        txt = {
            commands.BucketType.member: f"このサーバーで{txt}このコマンドを既に使用しています",
            commands.BucketType.guild: f"このコマンドはサーバーで{txt}既に使用されています",
            commands.BucketType.user: f"このコマンドを{txt}既に使用しています",
            commands.BucketType.channel: f"このコマンドは現在のチャンネルで{txt}既に使用されています",
            commands.BucketType.category: f"このコマンドは現在のチャンネルカテゴリで{txt}既に使用されています",
            commands.BucketType.role: f"このコマンドは許可されたロールを持つメンバーによって{txt}既に使用されています",
            commands.BucketType.default: f"このコマンドは誰かによって{txt}既に使用されています"
        }

        error_txt = f"{ctx.author.mention} **{txt[error.per]}が、まだ使用が完了していません！**"

    elif isinstance(error, TrackNotFound):
        error_txt = "**検索結果が見つかりませんでした...**"

    elif isinstance(error, YoutubeSourceDisabled):
        error_txt = "YouTubeのリンク/検索サポートは、YouTubeリンクのネイティブ動作を妨げるYouTube自体の強化された措置により無効になっています。" \
                     "これに関するYouTubeの投稿を確認したい場合は、[こちらをクリック](<https://support.google.com/youtube/thread/269521462/enforcement-on-third-party-apps?hl=en>)してください。"

    if isinstance(error, ServerSelectionTimeoutError) and os.environ.get("REPL_SLUG"):
        error_txt = "repl.itでDNSエラーが検出され、mongo/atlasデータベースに接続できません。" \
                    "再起動しますので、まもなく再度ご利用いただけるようになります..."
        kill_process = True

    elif isinstance(error, WavelinkException):
        if "Unknown file format" in (wave_error := str(error)):
            error_txt = "**指定されたリンクには対応していません...**"
        elif "No supported audio format" in wave_error:
            error_txt = "**指定されたリンクには対応していません。**"
        elif "This video is not available" in wave_error:
            error_txt = "**この動画は利用できないか、非公開です...**"
        elif "This playlist type is unviewable" in wave_error:
            error_txt = "**プレイリストのリンクに対応していないパラメータ/IDが含まれています...**"
        elif "The playlist does not exist" in wave_error:
            error_txt = "**プレイリストが存在しません（または非公開です）。**"
        elif "not made this video available in your country" in wave_error.lower() or \
                "who has blocked it in your country on copyright grounds" in wave_error.lower():
            error_txt = "**このリンクのコンテンツは、私が稼働している地域では利用できません...**"

    full_error_txt = ""

    if not error_txt:
        full_error_txt = "".join(traceback.format_exception(type(error), error, error.__traceback__))
        if not kwargs.get("no_log"):
            print(full_error_txt)
    elif send_error:
        full_error_txt = "".join(traceback.format_exception(type(error), error, error.__traceback__))

    return error_txt, full_error_txt, kill_process, components, mention_author
