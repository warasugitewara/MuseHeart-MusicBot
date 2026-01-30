# -*- coding: utf-8 -*-
import asyncio
import json
import os
import re
import shutil
import sys
import traceback
from typing import Union, Optional
from zipfile import ZipFile

import disnake
import dotenv
import humanize
from aiohttp import ClientSession
from disnake.ext import commands
from disnake.http import Route

import wavelink
from config_loader import DEFAULT_CONFIG, load_config
from utils.client import BotCore
from utils.db import DBModel
from utils.music.checks import check_voice, check_requester_channel, can_connect
from utils.music.converters import URL_REG
from utils.music.errors import GenericError, NoVoice
from utils.music.interactions import SelectBotVoice
from utils.music.models import LavalinkPlayer
from utils.others import CustomContext, string_to_file, token_regex, CommandArgparse, \
    select_bot_pool
from utils.owner_panel import panel_command, PanelView


def format_git_log(data_list: list):

    data = []

    for d in data_list:
        if not d:
            continue
        t = d.split("*****")
        data.append({"commit": t[0], "abbreviated_commit": t[1], "subject": t[2], "timestamp": t[3]})

    return data


async def run_command(cmd: str):

    p = await asyncio.create_subprocess_shell(
        cmd, stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=dict(os.environ)
    )
    stdout, stderr = await p.communicate()
    r = ShellResult(p.returncode, stdout, stderr)
    if r.status != 0:
        raise Exception(f"{r.stderr or r.stdout}\n\nStatus Code: {r.status}")
    return str(r.stdout)


class ShellResult:

    def __init__(self, status: int, stdout: Optional[bytes], stderr: Optional[bytes]):
        self.status = status
        self.stdout = stdout.decode(encoding="utf-8", errors="replace") if stdout is not None else None
        self.stderr = stderr.decode(encoding="utf-8", errors="replace") if stderr is not None else None


class Owner(commands.Cog):

    os_quote = "\"" if os.name == "nt" else "'"
    git_format = f"--pretty=format:{os_quote}%H*****%h*****%s*****%ct{os_quote}"

    additional_files = [
        "./lavalink.ini",
        "./application.yml",
        "./squarecloud.config",
        "./squarecloud.app",
        "./discloud.config",
    ]

    extra_dirs = [
        "local_database",
        ".player_sessions"
    ]

    def __init__(self, bot: BotCore):
        self.bot = bot
        self.git_init_cmds = [
            "git init",
            f'git remote add origin {self.bot.config["SOURCE_REPO"]}',
            'git fetch origin',
            'git --work-tree=. checkout -b main -f --track origin/main'
        ]
        self.owner_view: Optional[PanelView] = None
        self.extra_hints = bot.config["EXTRA_HINTS"].split("||")

    def format_log(self, data: list):
        return "\n".join(f"[`{c['abbreviated_commit']}`]({self.bot.pool.remote_git_url}/commit/{c['commit']}) `- "
                         f"{(c['subject'][:40].replace('`', '') + '...') if len(c['subject']) > 39 else c['subject']}` "
                         f"(<t:{c['timestamp']}:R>)" for c in data)

    @commands.cooldown(1, 10, commands.BucketType.user)
    @commands.is_owner()
    @commands.command(
        hidden=True, aliases=["gls", "lavalink", "lllist", "lavalinkservers"],
        description="音楽システムで使用するLavalinkサーバーリストのファイルをダウンロードします。"
    )
    async def getlavaservers(self, ctx: CustomContext):

        await ctx.defer()

        await self.download_lavalink_serverlist()

        await ctx.send(
            embed=disnake.Embed(
                description="**lavalink.iniファイルが正常にダウンロードされました！\n"
                            "このファイルのサーバーを使用するには、再起動が必要です。**"
            )
        )

    updatelavalink_flags = CommandArgparse()
    updatelavalink_flags.add_argument('-yml', '--yml', action='store_true',
                                      help="application.ymlファイルをダウンロードします。")
    updatelavalink_flags.add_argument("-resetids", "-reset", "--resetids", "--reset",
                                      help="楽曲IDの情報をリセットします（lavaplayer/lavalinkの特定の変更による問題を"
                                           "回避するのに便利です）。", action="store_true")

    @commands.is_owner()
    @commands.max_concurrency(1, commands.BucketType.default)
    @commands.command(hidden=True, aliases=["restartll", "rtll", "rll"])
    async def restartlavalink(self, ctx: CustomContext):

        if not self.bot.pool.lavalink_instance:
            raise GenericError("**ローカルサーバーは使用されていません！**")

        await self.bot.pool.start_lavalink()

        await ctx.send(
            embed=disnake.Embed(
                description="**ローカルLavalinkサーバーを再起動しています。**",
                color=self.bot.get_color(ctx.guild.me)
            )
        )

    @commands.is_owner()
    @commands.max_concurrency(1, commands.BucketType.default)
    @commands.command(hidden=True, aliases=["ull", "updatell", "llupdate", "llu"], extras={"flags": updatelavalink_flags})
    async def updatelavalink(self, ctx: CustomContext, flags: str = ""):

        if not self.bot.pool.lavalink_instance:
            raise GenericError("**ローカルサーバーは使用されていません！**")

        args, unknown = ctx.command.extras['flags'].parse_known_args(flags.split())

        try:
            self.bot.pool.lavalink_instance.kill()
        except:
            pass

        async with ctx.typing():

            await asyncio.sleep(1.5)

            if os.path.isfile("./Lavalink.jar"):
                os.remove("./Lavalink.jar")

            if args.yml and os.path.isfile("./application.yml"):
                os.remove("./application.yml")
                txt = "Lavalink.jarとapplication.ymlファイルが更新されます"
            else:
                txt = "Lavalink.jarファイルが更新されます"

            await self.bot.pool.start_lavalink()

        if args.resetids:
            for b in self.bot.pool.bots:
                try:
                    node = b.music.nodes["LOCAL"]
                except KeyError:
                    continue
                for p in node.players.values():
                    for t in p.queue:
                        t.id = None
                        t.info["id"] = None
                for p in node.players.values():
                    for t in p.played:
                        t.id = None
                        t.info["id"] = None
                for p in node.players.values():
                    for t in p.queue_autoplay:
                        t.id = None
                        t.info["id"] = None

        await ctx.send(
            embed=disnake.Embed(
                description=f"**{txt}、ローカルLavalinkサーバーが再起動されます。**",
                color=self.bot.get_color(ctx.guild.me)
            )
        )

    @commands.is_owner()
    @panel_command(aliases=["rcfg"], description="ボットの設定を再読み込みします。", emoji="⚙",
                   alt_name="ボットの設定を再読み込みします。")
    async def reloadconfig(self, ctx: Union[CustomContext, disnake.MessageInteraction]):

        self.bot.pool.config = self.bot.pool.load_cfg()

        txt = "**ボットの設定が正常に再読み込みされました！**"

        if isinstance(ctx, CustomContext):
            embed = disnake.Embed(colour=self.bot.get_color(ctx.me), description=txt)
            await ctx.send(embed=embed, view=self.owner_view)
        else:
            return txt

    @commands.is_owner()
    @panel_command(aliases=["rds", "recarregarskins"], description="スキンを再読み込みします。", emoji="🎨")
    async def reloadskins(self, ctx: Union[CustomContext, disnake.MessageInteraction]):

        for m in list(sys.modules):
            if not m.startswith("utils.music.skins."):
                continue
            try:
                del sys.modules[m]
            except:
                continue

        self.bot.pool.load_skins()

        txt = "**スキンが正常に再読み込みされました！**"

        if isinstance(ctx, CustomContext):
            embed = disnake.Embed(colour=self.bot.get_color(ctx.me), description=txt)
            await ctx.send(embed=embed, view=self.owner_view)
        else:
            return txt

    @commands.is_owner()
    @panel_command(aliases=["rd", "recarregar"], description="モジュールを再読み込みします。", emoji="🔄",
                   alt_name="モジュールを読み込み/再読み込みします。")
    async def reload(self, ctx: Union[CustomContext, disnake.MessageInteraction], *modules):

        modules = [f"{m.lower()}.py" for m in modules]

        modules_list = set()

        for bot in (allbots:=set(self.bot.pool.get_all_bots())):
            for m in list(bot.extensions):
                for m_name in modules:
                    if m_name[:-3].lower() == m.split(".")[-1]:
                        modules_list.add(m)

        # unload_sys_list = set()

        #for m in modules_list:
        #    for cog_name, cog in self.bot.cogs.items():
        #        if cog.__module__ == m and (cog_modules:=getattr(cog, "modules", None)):
        #            unload_sys_list.update(cog_modules)

        for bot in allbots:
            for m in modules_list:
                bot.unload_extension(m)

        #for m in unload_sys_list:
        #    try:
        #        del sys.modules[m]
        #    except KeyError:
        #        pass

        data = {}

        if isinstance(ctx, CustomContext):
            await ctx.trigger_typing()

        for bot in allbots:
            data = bot.load_modules(modules)
            bot.sync_command_cooldowns(force=True)
            if not data["failed"]:
                await bot.sync_app_commands(force=True)

        txt = ""

        if loaded := data["loaded"] + data["reloaded"]:
            txt += f'**読み込み/再読み込みされたモジュール:** ```ansi\n[0;34m{" [0;37m| [0;34m".join(loaded)}```\n'

        if data["failed"]:
            txt += f'**失敗したモジュール:** ```ansi\n[0;31m{" [0;37m| [0;31m".join(data["failed"])}```\n'

        if not txt:
            raise GenericError("**モジュールが見つかりませんでした...**")

        self.bot.pool.config = load_config()

        if isinstance(ctx, CustomContext):
            await ctx.send(embed=disnake.Embed(colour=self.bot.get_color(ctx.me), description=txt))
        else:
            return txt

    update_flags = CommandArgparse()
    update_flags.add_argument("-force", "--force", action="store_true",
                              help="ローカルリポジトリの状態を無視して強制的にアップデートします。")
    update_flags.add_argument("-pip", "--pip", action="store_true",
                              help="アップデート後に依存関係をインストール/更新します。")

    @commands.is_owner()
    @commands.max_concurrency(1, commands.BucketType.default)
    @panel_command(aliases=["up", "atualizar"], description="gitを使用してコードを更新します。",
                   emoji="<:git:944873798166020116>", alt_name="ボットを更新", extras={"flags": update_flags})
    async def update(self, ctx: Union[CustomContext, disnake.MessageInteraction], *,
                     opts: str = ""):  # TODO: Rever se há alguma forma de usar commands.Flag sem um argumento obrigatório, ex: --pip.

        out_git = ""

        git_log = []

        if shutil.which("poetry"):
            file = "./pyproject.toml"
            use_poetry = True
        else:
            file = "./requirements.txt"
            use_poetry = False

        requirements_old = ""
        try:
            with open(file) as f:
                requirements_old = f.read()
        except:
            pass

        args, unknown = self.bot.get_command("update").extras['flags'].parse_known_args(opts.split())

        try:
            await ctx.response.defer()
        except:
            pass

        if git_clean:=args.force or not os.path.exists(os.environ["GIT_DIR"]):
            out_git += await self.cleanup_git(force=args.force)

        try:
            await run_command("git --work-tree=. reset --hard")
        except:
            pass

        if not git_clean:

            try:
                pull_log = await run_command("git --work-tree=. pull --allow-unrelated-histories -X theirs")
                if "Already up to date" in pull_log:
                    raise GenericError("**すでに最新のアップデートがインストールされています...**")
                out_git += pull_log

            except GenericError as e:
                raise e

            except Exception as e:

                if "Already up to date" in str(e):
                    raise GenericError("すでに最新のアップデートがインストールされています...")

                elif not "Fast-forward" in str(e):
                    traceback.print_exc()
                    try:
                        await run_command("git --work-tree=. reset --hard origin/main")
                    except:
                        traceback.print_exc()
                        out_git += await self.cleanup_git(force=True)

                elif "Need to specify how to reconcile divergent branches" in str(e):
                    out_git += await run_command("git --work-tree=. rebase --no-ff")

        commit = ""

        for l in out_git.split("\n"):
            if l.startswith("Updating"):
                commit = l.replace("Updating ", "").replace("..", "...")
                break

        data = (await run_command(f"git --work-tree=. log {commit} {self.git_format}")).split("\n")

        git_log += format_git_log(data)

        self.bot.pool.commit = commit.split("...")[-1]

        text = "`変更後に再起動が必要です。`"

        txt = f"`✅` **[アップデートが正常に完了しました！]({self.bot.pool.remote_git_url}/commits/main)**"

        if git_log:
            txt += f"\n\n{self.format_log(git_log[:10])}"

        if git_log_txt := out_git[:1000].split('Fast-forward')[-1]:
            if git_clean:
                git_log_txt = "\n".join(l for l in git_log_txt.split("\n") if not l.startswith("hint: "))
            txt += f"\n\n`📄` **Log:** ```py\n{git_log_txt}```\n{text}"
        else:
            txt += f"\n\n{text}"

        if isinstance(ctx, CustomContext):
            embed = disnake.Embed(
                description=txt,
                color=self.bot.get_color(ctx.guild.me)
            )
            await ctx.send(embed=embed, view=self.owner_view)

            self.bot.loop.create_task(self.update_deps(ctx, requirements_old, args, use_poetry=use_poetry))

        else:
            self.bot.loop.create_task(self.update_deps(ctx, requirements_old, args, use_poetry=use_poetry))
            return txt

    async def update_deps(self, ctx, original_reqs, args, use_poetry=False):

        if use_poetry:
            cmd = "poetry install"
            file = "./pyproject.toml"
        else:
            cmd = "pip3 install -U -r requirements.txt --no-cache-dir"
            file = "./requirements.txt"

        if args.pip:

            embed = disnake.Embed(
                description="**依存関係をインストールしています。\nお待ちください...**",
                color=self.bot.get_color(ctx.guild.me)
            )

            msg = await ctx.channel.send(embed=embed)

            await run_command(cmd)

            embed.description = "**依存関係が正常にインストールされました！**"

            await msg.edit(embed=embed)

        else:

            with open(file) as f:
                requirements_new = f.read()

            if original_reqs != requirements_new:

                txt = ""

                if venv:=os.getenv("VIRTUAL_ENV"):
                    if os.name == "nt":
                        txt += "call " + venv.split('\\')[-1] + " && "
                    else:
                        txt += ". ./" + venv.split('/')[-1] + " && "

                try:
                    prefix = ctx.prefix if (not str(ctx.guild.me.id) in ctx.prefix) else f"@{ctx.guild.me.name}"
                except AttributeError:
                    prefix = self.bot.default_prefix if self.bot.intents.message_content else f"@{ctx.guild.me.name}"

                await ctx.send(
                    embed=disnake.Embed(
                        description="**ターミナル/シェルで以下のコマンドを使用して依存関係を更新する必要があります:**\n"
                                    f"```sh\n{txt}{cmd}```\nまたは次のコマンドを使用してください: "
                                    f"```ansi\n[34;1m{prefix}update --force --pip[0m``` \n"
                                    f"**注意:** ホスティング環境によっては（150MBの空きRAMと0.5vCPUがない場合）、"
                                    f"上記のオプションや下の依存関係インストールボタンを使用する代わりに、"
                                    f"requirements.txtファイルを送信する必要があります...",
                        color=self.bot.get_color(ctx.guild.me)
                    ),
                    components=[
                        disnake.ui.Button(label="Download requirements.txt", custom_id="updatecmd_requirements"),
                        disnake.ui.Button(label="依存関係を更新",
                                          custom_id="updatecmd_installdeps_" + ("poetry" if use_poetry else "pip")),
                        disnake.ui.Button(label="依存関係を更新（強制）",
                                          custom_id="updatecmd_installdeps_force_" + ("poetry" if use_poetry else "pip")),
                    ]
                )

    @commands.Cog.listener("on_button_click")
    async def update_buttons(self, inter: disnake.MessageInteraction):

        if not inter.data.custom_id.startswith("updatecmd_"):
            return

        if inter.data.custom_id.startswith("updatecmd_requirements"):

            try:
                os.remove('./update_reqs.zip')
            except FileNotFoundError:
                pass

            with ZipFile('update_reqs.zip', 'w') as zipObj:
                zipObj.write("requirements.txt")

            await inter.send(
                embed=disnake.Embed(
                    description="**添付ファイルをダウンロードし、コミットなどでホスティングにアップロードしてください。**",
                    color=self.bot.get_color(inter.guild.me)
                ),
                file=disnake.File("update_reqs.zip")
            )

            os.remove("update_reqs.zip")
            return

        # install installdeps

        if inter.data.custom_id.startswith("updatecmd_installdeps_force_"):
            await self.cleanup_git(force=True)

        await inter.message.delete()

        args, unknown = self.bot.get_command("update").extras['flags'].parse_known_args(["-pip"])

        await self.update_deps(inter, "", args, use_poetry=inter.data.custom_id.endswith("_poetry"))

    async def cleanup_git(self, force=False):

        if force:
            try:
                shutil.rmtree(os.environ["GIT_DIR"])
            except FileNotFoundError:
                pass

        out_git = ""

        for c in self.git_init_cmds:
            try:
                out_git += (await run_command(c)) + "\n"
            except Exception as e:
                out_git += f"{e}\n"

        self.bot.pool.commit = (await run_command("git rev-parse HEAD")).strip("\n")
        self.bot.pool.remote_git_url = self.bot.config["SOURCE_REPO"][:-4]

        return out_git

    @commands.max_concurrency(1, commands.BucketType.guild)
    @commands.cooldown(1, 10, commands.BucketType.user)
    @panel_command(aliases=["latest", "lastupdate"], description="最新のアップデートを表示します。", emoji="📈",
                   alt_name="最新のアップデート", hidden=False)
    async def updatelog(self, ctx: Union[CustomContext, disnake.MessageInteraction], amount: int = 10):

        if not os.path.isdir(os.environ["GIT_DIR"]):
            raise GenericError("ボットのディレクトリにリポジトリが初期化されていません...\n注意: updateコマンドを使用してください。")

        if not self.bot.pool.remote_git_url:
            self.bot.pool.remote_git_url = self.bot.config["SOURCE_REPO"][:-4]

        git_log = []

        data = (await run_command(f"git log -{amount or 10} {self.git_format}")).split("\n")

        git_log += format_git_log(data)

        txt = f"🔰 ** | [最近のアップデート:]({self.bot.pool.remote_git_url}/commits/main)**\n\n" + self.format_log(
            git_log)

        if isinstance(ctx, CustomContext):

            embed = disnake.Embed(
                description=txt,
                color=self.bot.get_color(ctx.guild.me)
            )

            await ctx.send(embed=embed)

        else:
            return txt

    @commands.is_owner()
    @commands.command(hidden=True, aliases=["menu"])
    async def panel(self, ctx: CustomContext):

        embed =disnake.Embed(
            title="コントロールパネル",
            color=self.bot.get_color(ctx.guild.me)
        )
        embed.set_footer(text="実行したいタスクをクリックしてください。")
        await ctx.send(embed=embed, view=PanelView(self.bot))

    @commands.has_guild_permissions(manage_guild=True)
    @commands.cooldown(1, 10, commands.BucketType.guild)
    @commands.command(
        aliases=["mudarprefixo", "prefix", "changeprefix"],
        description="サーバーのプレフィックスを変更します",
        usage="{prefix}{cmd} [プレフィックス]\n例: {prefix}{cmd} >>"
    )
    async def setprefix(self, ctx: CustomContext, prefix: str):

        prefix = prefix.strip()

        if not prefix or len(prefix) > 5:
            raise GenericError("**プレフィックスにはスペースを含めることができず、5文字を超えることはできません。**")

        guild_data = await self.bot.get_global_data(ctx.guild.id, db_name=DBModel.guilds)

        self.bot.pool.guild_prefix_cache[ctx.guild.id] = prefix
        guild_data["prefix"] = prefix
        await self.bot.update_global_data(ctx.guild.id, guild_data, db_name=DBModel.guilds)

        prefix = disnake.utils.escape_markdown(prefix)

        embed = disnake.Embed(
            description=f"**このサーバーでの私のプレフィックスは:** `{prefix}`\n"
                        f"**デフォルトのプレフィックスに戻したい場合は、次のコマンドを使用してください:** `{prefix}{self.resetprefix.name}`",
            color=self.bot.get_color(ctx.guild.me)
        )

        await ctx.send(embed=embed)

    @commands.has_guild_permissions(manage_guild=True)
    @commands.cooldown(1, 10, commands.BucketType.guild)
    @commands.command(
        description="サーバーのプレフィックスをリセットします（ボットのデフォルトプレフィックスを使用）"
    )
    async def resetprefix(self, ctx: CustomContext):

        guild_data = await self.bot.get_global_data(ctx.guild.id, db_name=DBModel.guilds)

        if not guild_data["prefix"]:
            raise GenericError("**このサーバーにはプレフィックスが設定されていません。**")

        guild_data["prefix"] = ""
        self.bot.pool.guild_prefix_cache[ctx.guild.id] = ""

        await self.bot.update_global_data(ctx.guild.id, guild_data, db_name=DBModel.guilds)

        embed = disnake.Embed(
            description=f"**サーバーのプレフィックスが正常にリセットされました。\n"
                        f"デフォルトのプレフィックスは:** `{disnake.utils.escape_markdown(self.bot.default_prefix)}`",
            color=self.bot.get_color(ctx.guild.me)
        )

        await ctx.send(embed=embed)

    @commands.cooldown(1, 10, commands.BucketType.guild)
    @commands.command(
        aliases=["uprefix", "spu", "setmyprefix", "spm", "setcustomprefix", "scp", "customprefix", "myprefix"],
        description="ユーザープレフィックスを変更します（サーバーで設定されたプレフィックスに関係なく、"
                    "あなたに応答するプレフィックス）。",
        usage="{prefix}{cmd} [プレフィックス]\n例: {prefix}{cmd} >>"
    )
    async def setuserprefix(self, ctx: CustomContext, prefix: str):

        prefix = prefix.strip()

        if not prefix or len(prefix) > 5:
            raise GenericError("**プレフィックスにはスペースを含めることができず、5文字を超えることはできません。**")

        user_data = await self.bot.get_global_data(ctx.author.id, db_name=DBModel.users)

        user_data["custom_prefix"] = prefix
        self.bot.pool.user_prefix_cache[ctx.author.id] = prefix
        await self.bot.update_global_data(ctx.author.id, user_data, db_name=DBModel.users)

        prefix = disnake.utils.escape_markdown(prefix)

        embed = disnake.Embed(
            description=f"**あなたのユーザープレフィックスは:** `{prefix}`\n"
                        f"**ユーザープレフィックスを削除したい場合は、次のコマンドを使用してください:** `{prefix}{self.resetuserprefix.name}`",
            color=self.bot.get_color(ctx.guild.me)
        )

        await ctx.send(embed=embed)

    @commands.cooldown(1, 10, commands.BucketType.guild)
    @commands.command(description="ユーザープレフィックスを削除します")
    async def resetuserprefix(self, ctx: CustomContext):

        user_data = await self.bot.get_global_data(ctx.author.id, db_name=DBModel.users)

        if not user_data["custom_prefix"]:
            raise GenericError("**プレフィックスが設定されていません。**")

        user_data["custom_prefix"] = ""
        self.bot.pool.user_prefix_cache[ctx.author.id] = ""
        await self.bot.update_global_data(ctx.author.id, user_data, db_name=DBModel.users)

        embed = disnake.Embed(
            description=f"**ユーザープレフィックスが正常に削除されました。**",
            color=self.bot.get_color(ctx.guild.me)
        )

        await ctx.send(embed=embed)

    @commands.is_owner()
    @commands.command(
        aliases=["guildprefix", "sgp", "gp"], hidden=True,
        description="指定されたIDのサーバーにプレフィックスを手動で設定します（ボットリストに便利）",
        usage="{prefix}{cmd} [サーバーID] <プレフィックス>\n例: {prefix}{cmd} 1155223334455667788 >>\n注意: プレフィックスを指定せずにコマンドを使用すると削除されます。"
    )
    async def setguildprefix(self, ctx: CustomContext, server_id: int, prefix: str = None):

        if not 17 < len(str(server_id)) < 24:
            raise GenericError("**サーバーIDの文字数は18〜23文字である必要があります。**")

        guild_data = await self.bot.get_global_data(server_id, db_name=DBModel.guilds)

        embed = disnake.Embed(color=self.bot.get_color(ctx.guild.me))

        prefix = prefix.strip()

        if not prefix:
            guild_data["prefix"] = ""
            await ctx.bot.update_global_data(server_id, guild_data, db_name=DBModel.guilds)
            embed.description = "**指定されたIDのサーバーのプレフィックスが正常にリセットされました。**"

        else:
            guild_data["prefix"] = prefix
            await self.bot.update_global_data(server_id, guild_data, db_name=DBModel.guilds)
            embed.description = f"**指定されたIDのサーバーのプレフィックスは:** {disnake.utils.escape_markdown(prefix)}"

        self.bot.pool.guild_prefix_cache[ctx.guild.id] = prefix

        await ctx.send(embed=embed)

    @commands.is_owner()
    @panel_command(aliases=["expsource", "export", "exs"],
                   description="ソースをzipファイルにエクスポートします。", emoji="💾",
                   alt_name="ソース/ソースコードをエクスポート")
    async def exportsource(self, ctx:Union[CustomContext, disnake.MessageInteraction], *, flags: str = ""):

        if not os.path.isdir(os.environ['GIT_DIR']):
            await self.cleanup_git(force=True)

        try:
            env_file = dotenv.dotenv_values("./.env")
        except:
            env_file = {}

        try:
            with open("config.json") as f:
                config_json = json.load(f)
        except FileNotFoundError:
            config_json = {}

        SECRETS = dict(DEFAULT_CONFIG)
        SECRETS.update({"TOKEN": ""})

        for env, value in os.environ.items():
            if (e:=env.lower()).startswith(("token_bot_", "test_guilds_", "lavalink_node_")) or e == "token":
                SECRETS[env] = os.environ[env]
                continue

            if not isinstance(value, str):
                continue

            tokens = []

            for string in value.split():
                if re.findall(token_regex, value) and len(string) < 91:
                    tokens.append(string)

            if tokens:
                SECRETS[env] = value

        for i in SECRETS:
            try:
                SECRETS[i] = os.environ[i]
            except KeyError:
                continue

        SECRETS.update(config_json)
        SECRETS.update(env_file)

        if any(f in flags.lower() for f in ("-autodll", "--autodll")):
            SECRETS["AUTO_DOWNLOAD_LAVALINK_SERVERLIST"] = True

        if any(f in flags.lower() for f in ("--externalservers", "-externalservers", "--llservers", "-llservers", "--lls", "-lls")):
            await self.download_lavalink_serverlist()

        if not os.path.isfile("./.env-temp"):
            shutil.copyfile("./.example.env", "./.env-temp")

        for i in SECRETS:
            if not isinstance(SECRETS[i], str):
                SECRETS[i] = str(SECRETS[i]).lower()
            dotenv.set_key("./.env-temp", i, SECRETS[i])

        filelist = await run_command("git ls-files --others --exclude-standard --cached")

        for folder, subfolder, files in os.walk("./modules"):
            for file in files:
                if file.endswith(".py") and (filename:=os.path.join(file)) not in filelist.split("\n"):
                    filelist += f"\n{filename}"

        for extra_dir in self.extra_dirs:
            for dir_path, dir_names, filenames in os.walk(extra_dir):
                filelist += "\n" + "\n".join(os.path.join(dir_path, file) for file in filenames)

        for file in self.additional_files:
            if os.path.isfile(file):
                filelist += "\n" + file

        await self.bot.loop.run_in_executor(None, self.zip_dir, filelist.split("\n"))

        os.remove("./.env-temp")

        if (filesize:=(os.path.getsize("source.zip")) / 1024) > 25600:
            try:
                os.remove("./source.zip")
            except:
                pass
            raise GenericError(f"**ファイルサイズが25MBの制限を超えました（現在のサイズ: {humanize.naturalsize(filesize)}）**")

        try:
            embed = disnake.Embed(
                description="**source.zipファイルや.envファイルを誰にも送らないでください。また、.envファイルの内容の"
                            "スクリーンショットを投稿する際は十分注意し、github、repl.it、glitch.comなどの"
                            "公開場所にこのファイルを追加しないでください。**",
                color=self.bot.get_color(ctx.guild.me))
            embed.set_footer(text="セキュリティ上の理由から、このメッセージは2分後に削除されます。")

            msg = await ctx.author.send(
                embed=embed,
                file=disnake.File("./source.zip", filename=f"{self.bot.user}_source.zip"),
                delete_after=120
            )

            os.remove("./source.zip")

        except disnake.Forbidden:
            os.remove("./source.zip")
            raise GenericError("DMが無効になっています！")

        if isinstance(ctx, CustomContext):
            await ctx.send(
                embed=disnake.Embed(
                    description=f"**[source.zip]({msg.jump_url})ファイルがDMに送信されました。**",
                    color=self.bot.get_color(ctx.guild.me)
                )
            )
        else:
            return f"[source.zip]({msg.jump_url})ファイルがDMに正常に送信されました。"

    def zip_dir(self, filelist: list):

        try:
            os.remove("./source.zip")
        except:
            pass

        with ZipFile("./source.zip", 'a') as zipf:

            for f in filelist:
                if not f:
                    continue
                try:
                    if f == ".env-temp":
                        zipf.write('./.env-temp', './.env')
                    else:
                        zipf.write(f"./{f}")
                except FileNotFoundError:
                    continue

    @commands.is_owner()
    @commands.command(hidden=True)
    async def cleardm(self, ctx: CustomContext, amount: int = 20):

        counter = 0

        async with ctx.typing():

            async for msg in ctx.author.history(limit=int(amount)):
                if msg.author.id == self.bot.user.id:
                    await msg.delete()
                    await asyncio.sleep(0.5)
                    counter += 1

        if not counter:
            raise GenericError(f"**{amount}件のメッセージを確認しましたが、削除されたメッセージはありませんでした...**")

        if counter == 1:
            txt = "**1件のメッセージがDMから削除されました。**"
        else:
            txt = f"**{counter}件のメッセージがDMから削除されました。**"

        await ctx.send(embed=disnake.Embed(description=txt, colour=self.bot.get_color(ctx.guild.me)))

    @commands.Cog.listener("on_button_click")
    async def close_shell_result(self, inter: disnake.MessageInteraction):

        if inter.data.custom_id != "close_shell_result":
            return

        if not await self.bot.is_owner(inter.author):
            return await inter.send("**このボタンは私のオーナーのみが使用できます！**", ephemeral=True)

        await inter.response.edit_message(
            content="```ini\n🔒 - [シェルが閉じられました！] - 🔒```",
            attachments=None,
            view=None,
            embed=None
        )

    @commands.is_owner()
    @commands.command(aliases=["sh"], hidden=True)
    async def shell(self, ctx: CustomContext, *, command: str):

        if command.startswith('```') and command.endswith('```'):
            if command[4] != "\n":
                command = f"```\n{command[3:]}"
            if command[:-4] != "\n":
                command = command[:-3] + "\n```"
            command = '\n'.join(command.split('\n')[1:-1])
        else:
            command = command.strip('` \n')

        try:
            async with ctx.typing():
                result = await run_command(command)
        except GenericError as e:
            kwargs = {}
            if len(e.text) > 2000:
                kwargs["file"] = string_to_file(e.text, filename="error.txt")
            else:
                kwargs["content"] = f"```py\n{e.text}```"

            try:
                await ctx.author.send(**kwargs)
                await ctx.message.add_reaction("⚠️")
            except disnake.Forbidden:
                traceback.print_exc()
                raise GenericError(
                    "**エラーが発生しました（ログ/ターミナルを確認するか、次の結果をDMに直接送信するために"
                    "DMを有効にしてください）。**"
                )

        else:

            kwargs = {}
            if len(result) > 2000:
                kwargs["file"] = string_to_file(result, filename=f"shell_result_{ctx.message.id}.txt")
            else:
                kwargs["content"] = f"```py\n{result}```"

            await ctx.reply(
                components=[
                    disnake.ui.Button(label="シェルを閉じる", custom_id="close_shell_result", emoji="♻️")
                ],
                mention_author=False, fail_if_not_exists=False,
                **kwargs
            )

    @check_voice()
    @commands.cooldown(1, 15, commands.BucketType.guild)
    @commands.command(description='サーバーでプレイヤーを初期化します。', aliases=["spawn", "sp", "spw", "smn"])
    async def summon(self, ctx: CustomContext):

        try:
            ctx.bot.music.players[ctx.guild.id]  # type ignore
            raise GenericError("**このサーバーではすでにプレイヤーが起動しています。**")
        except KeyError:
            pass

        bot = ctx.bot
        guild = ctx.guild
        channel = ctx.channel
        msg = None

        if bot.user.id not in ctx.author.voice.channel.voice_states:

            free_bots = []

            for b in self.bot.pool.get_guild_bots(ctx.guild.id):

                if not b.bot_ready:
                    continue

                g = b.get_guild(ctx.guild_id)

                if not g:
                    continue

                p = b.music.players.get(ctx.guild_id)

                if p and ctx.author.id not in p.last_channel.voice_states:
                    continue

                free_bots.append(b)

            if len(free_bots) > 1:

                v = SelectBotVoice(ctx, guild, free_bots)

                msg = await ctx.send(
                    embed=disnake.Embed(
                        description=f"**チャンネル {ctx.author.voice.channel.mention} で使用するボットを選択してください**",
                        color=self.bot.get_color(guild.me)), view=v
                )

                ctx.store_message = msg

                await v.wait()

                if v.status is None:
                    await msg.edit(embed=disnake.Embed(description="### タイムアウトしました...", color=self.bot.get_color(guild.me)), view=None)
                    return

                if v.status is False:
                    await msg.edit(embed=disnake.Embed(description="### 操作がキャンセルされました。",
                                                   color=self.bot.get_color(guild.me)), view=None)
                    return

                if not v.inter.author.voice:
                    await msg.edit(embed=disnake.Embed(description="### ボイスチャンネルに接続していません...",
                                                   color=self.bot.get_color(guild.me)), view=None)
                    return

                if not v.inter.author.voice:
                    raise NoVoice()

                bot = v.bot
                ctx = v.inter
                guild = v.guild
                channel = bot.get_channel(ctx.channel.id)

        can_connect(channel=ctx.author.voice.channel, guild=guild)

        node: wavelink.Node = bot.music.get_best_node()

        if not node:
            raise GenericError("**利用可能な音楽サーバーがありません！**")

        player: LavalinkPlayer = await bot.get_cog("Music").create_player(
            inter=ctx, bot=bot, guild=guild, channel=channel
        )

        await player.connect(ctx.author.voice.channel.id)

        if msg:
            await msg.edit(
                f"音楽セッションがチャンネル {ctx.author.voice.channel.mention} で開始されました\n経由: {bot.user.mention}{player.controller_link}",
                components=None, embed=None
            )
        else:
            self.bot.loop.create_task(ctx.message.add_reaction("👍"))

        while not ctx.guild.me.voice:
            await asyncio.sleep(1)

        if isinstance(ctx.author.voice.channel, disnake.StageChannel):

            stage_perms = ctx.author.voice.channel.permissions_for(guild.me)
            if stage_perms.manage_permissions:
                await guild.me.edit(suppress=False)

            await asyncio.sleep(1.5)

        await player.process_next()

    @commands.is_owner()
    @commands.command(hidden=True, aliases=["setbotbanner"], description="添付ファイルまたはjpg/gif画像の直接リンクを使用してボットのバナーを変更します。")
    async def setbanner(self, ctx: CustomContext, url: str = ""):
        await self.setavatar.callback(self=self, ctx=ctx, url=url, mode="banner")

    @commands.is_owner()
    @commands.command(hidden=True, aliases=["setbotavatar"], description="添付ファイルまたはjpg/gif画像の直接リンクを使用してボットのアバターを変更します。")
    async def setavatar(self, ctx: CustomContext, url: str = "", mode="avatar"):

        use_hyperlink = False

        if re.match(r'^<.*>$', url):
            use_hyperlink = True
            url = url.strip("<>")

        if not url:

            if not ctx.message.attachments:
                raise GenericError("コマンドで画像またはgifのリンクを指定するか、添付してください。")

            url = ctx.message.attachments[0].url

            if not url.split("?ex=")[0].endswith((".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")):
                raise GenericError("有効なファイルを添付してください: png, jpg, jpeg, webp, gif, bmp.")

        elif not URL_REG.match(url):
            raise GenericError("無効なリンクが指定されました。")

        inter, bot = await select_bot_pool(ctx, return_new=True)

        if not bot:
            return

        if isinstance(inter, CustomContext):
            try:
                func = inter.store_message.edit
            except AttributeError:
                func = inter.send
        else:
            await inter.response.defer(ephemeral=True)
            func = inter.edit_original_message

        await func(f"ボット {bot.user.mention} の新しい{mode}を処理しています。お待ちください...", embed=None, view=None)

        async with ctx.bot.session.get(url) as r:
            if r.status != 200:
                raise GenericError(f"エラー {r.status}: {await r.text()}")
            image_bytes = await r.read()

        payload = {mode: await disnake.utils._assetbytes_to_base64_data(image_bytes)}
        await bot.http.edit_profile(payload)

        if mode == "avatar":
            await bot.http.request(Route('PATCH', '/applications/@me'), json={
                "icon": disnake.utils._bytes_to_base64_data(image_bytes)
            })

        try:
            func = inter.store_message.edit
        except AttributeError:
            try:
                func = inter.edit_original_message
            except AttributeError:
                try:
                    func = inter.response.edit_message
                except AttributeError:
                    func = inter.send

        avatar_txt = mode if not use_hyperlink else f"[{mode}]({url})"

        await func(f"ボット {bot.user.mention} の{avatar_txt}が正常に変更されました。", view=None, embed=None)

    async def cog_check(self, ctx: CustomContext) -> bool:
        return await check_requester_channel(ctx)

    async def cog_load(self) -> None:
        self.owner_view = PanelView(self.bot)

    async def download_lavalink_serverlist(self):
        async with ClientSession() as session:
            async with session.get(self.bot.config["LAVALINK_SERVER_LIST"]) as r:
                ini_file = await r.read()
                with open("lavalink.ini", "wb") as f:
                    f.write(ini_file)

def setup(bot: BotCore):
    bot.add_cog(Owner(bot))
