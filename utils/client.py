# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import datetime
import gc
import json
import logging
import os
import pickle
import subprocess
import traceback
from configparser import ConfigParser
from copy import deepcopy
from importlib import import_module
from subprocess import check_output
from typing import Optional, Union, List, Dict

import aiofiles
import aiohttp
import disnake
import requests
from aiohttp import ClientSession
from async_timeout import timeout
from cachetools import TTLCache
from disnake.ext import commands
from disnake.http import Route
from user_agent import generate_user_agent

import wavelink
from config_loader import load_config
from utils.db import MongoDatabase, LocalDatabase, get_prefix, DBModel, global_db_models
from utils.music.audio_sources.deezer import DeezerClient
from utils.music.audio_sources.spotify import SpotifyClient
from utils.music.checks import check_pool_bots
from utils.music.errors import GenericError
from utils.music.lastfm_tools import LastFM
from utils.music.local_lavalink import run_lavalink
from utils.music.models import music_mode, LavalinkPlayer, LavalinkPlaylist, LavalinkTrack, PartialTrack, \
    native_sources, CustomYTDL
from utils.music.remote_lavalink_serverlist import get_lavalink_servers
from utils.others import CustomContext, token_regex, sort_dict_recursively
from utils.owner_panel import PanelView
from web_app import WSClient, start

if os.name != "nt":
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())

class BotPool:

    bots: List[BotCore] = []
    guild_bots: Dict[str, List[BotCore]] = {}
    killing_state = False
    command_sync_config = commands.CommandSyncFlags(
                    allow_command_deletion=True,
                    sync_commands=True,
                    sync_commands_debug=True,
                    sync_global_commands=True,
                    sync_guild_commands=True
                )

    add_fav_embed_cooldown = commands.CooldownMapping.from_cooldown(rate=1, per=15,
                                                                         type=commands.BucketType.user)

    enqueue_track_embed_cooldown = commands.CooldownMapping.from_cooldown(rate=2, per=9,
                                                                               type=commands.BucketType.user)

    enqueue_playlist_embed_cooldown = commands.CooldownMapping.from_cooldown(rate=1, per=14,
                                                                                  type=commands.BucketType.user)

    song_select_cooldown = commands.CooldownMapping.from_cooldown(rate=2, per=15, type=commands.BucketType.member)

    def __init__(self):
        self.user_prefix_cache = {}
        self.guild_prefix_cache = {}
        self.mongo_database: Optional[MongoDatabase] = None
        self.local_database: Optional[LocalDatabase] = None
        self.ws_client: Optional[WSClient] = None
        self.emoji_data = {}
        self.config = self.load_cfg()
        self.playlist_cache = TTLCache(maxsize=self.config["PLAYLIST_CACHE_SIZE"], ttl=self.config["PLAYLIST_CACHE_TTL"])
        self.partial_track_cache =  TTLCache(maxsize=1000, ttl=80400)
        self.integration_cache = TTLCache(maxsize=500, ttl=7200)
        self.spotify: Optional[SpotifyClient] = None
        self.deezer = DeezerClient(self.playlist_cache)
        self.lavalink_instance: Optional[subprocess.Popen] = None
        self.commit = ""
        self.remote_git_url = ""
        self.max_counter: int = 0
        self.message_ids = TTLCache(ttl=30, maxsize=20000)
        self.bot_mentions = set()
        self.single_bot = True
        self.loop: Optional[asyncio.EventLoop] = None
        self.failed_bots: dict = {}
        self.current_useragent = self.reset_useragent()
        self.processing_gc: bool = False
        self.lavalink_connect_queue = {}
        self.last_fm: Optional[LastFM] = None
        self.lastfm_sessions = {}
        self.player_skins = {}
        self.player_static_skins = {}
        self.default_skin = self.config.get("DEFAULT_SKIN", "default")
        self.default_static_skin = self.config.get("DEFAULT_STATIC_SKIN", "default")
        self.default_controllerless_skin = self.config.get("DEFAULT_CONTROLLERLESS_SKIN", "default")
        self.default_idling_skin = self.config.get("DEFAULT_IDLING_SKIN", "default")
        self.cache_updater_task: Optional[asyncio.Task] = None
        self.lyric_data_cache = TTLCache(maxsize=30000, ttl=600*10)
        self.ytdl = CustomYTDL(
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
                        'player_client': [
                            'web',
                            'android',
                            'android_creator',
                            'web_creator',
                        ],
                        'max_comments': [0],
                    },
                    'youtubetab': {
                        "skip": ["webpage", "authcheck"]
                    }
                }
            }
        )

        self.load_cache()

    def reset_useragent(self):
        self.current_useragent = generate_user_agent()

    def load_cache(self):

        if os.path.exists("./local_database/playlist_cache.pkl"):
            with open("./local_database/playlist_cache.pkl", 'rb') as f:
                try:
                    self.playlist_cache.update(pickle.load(f))
                except EOFError:
                    pass

        if os.path.exists("./local_database/partial_track_cache.pkl"):
            with open("./local_database/partial_track_cache.pkl", 'rb') as f:
                try:
                    self.partial_track_cache.update(pickle.load(f))
                except EOFError:
                    pass

        if os.path.exists("./local_database/.lyric_cache_data"):
            with open("./local_database/.lyric_cache_data", 'rb') as f:
                try:
                    self.lyric_data_cache.update(pickle.load(f))
                except EOFError:
                    pass

    async def cache_updater(self):
        while True:
            await asyncio.sleep(300)
            async with aiofiles.open("./local_database/playlist_cache.pkl", 'wb') as f:
                await f.write(pickle.dumps(self.playlist_cache))

    async def connect_lavalink_queue_task(self, identifier: str):

        delay_secs = int(self.config.get("LAVALINK_QUEUE_DELAY", 1.5))

        try:
            while True:
                async with timeout(600):
                    bot, data = await self.lavalink_connect_queue[identifier].get()
                    await bot.get_cog("Music").connect_node(data)
                    await asyncio.sleep(delay_secs)
        except asyncio.TimeoutError:
            pass

    def get_guild_bots(self, guild_id: int) -> list:
        return self.bots + self.guild_bots.get(str(guild_id), [])

    def get_all_bots(self) -> list:

        allbots = set()

        for botlist in self.guild_bots.values():

            allbots.update(botlist)

        allbots.update(self.bots)

        return list(allbots)

    @property
    def database(self) -> Union[LocalDatabase, MongoDatabase]:

        if self.config["MONGO"]:
            return self.mongo_database

        return self.local_database

    async def start_lavalink(self):

        if self.lavalink_instance:
            try:
                self.lavalink_instance.kill()
            except:
                traceback.print_exc()

        if not self.loop:
            self.loop = asyncio.get_event_loop()

        try:
            self.lavalink_instance = await self.loop.run_in_executor(
                None, lambda: run_lavalink(
                    lavalink_file_url=self.config['LAVALINK_FILE_URL'],
                    lavalink_initial_ram=self.config['LAVALINK_INITIAL_RAM'],
                    lavalink_ram_limit=self.config['LAVALINK_RAM_LIMIT'],
                    lavalink_additional_sleep=int(self.config['LAVALINK_ADDITIONAL_SLEEP']),
                    use_jabba=self.config["USE_JABBA"]
                )
            )
        except Exception:
            traceback.print_exc()

    async def start_bot(self, bot: BotCore):

        e = None

        try:
            await bot.start(bot.http.token)
        except disnake.HTTPException as error:

            if error.status == 429 or "429 Too Many Requests" in str(e):

                if not self.config["KILL_ON_429"]:

                    if self.killing_state == "ratelimit":
                        return

                    self.killing_state = "ratelimit"
                    print("Discordのレート制限が発生しました！")
                    await asyncio.sleep(10)
                    raise e

                if self.killing_state is True:
                    return

                print(
                    "Discordのレート制限が発生しました！\n"
                    "5秒後にプロセスを終了/再起動します..."
                )

                self.killing_state = True

                await asyncio.sleep(5)

                await asyncio.create_subprocess_shell("kill 1")

                return

            e = error

        except Exception as error:
            e = error

        if e:

            if isinstance(e, disnake.PrivilegedIntentsRequired):
                e = "アプリケーションでPrivileged Intentsが有効になっていません<br>" \
                    "Discord Developer Portalにアクセスしてください:<br>" \
                    "https://discord.com/developers/applications/<br>" \
                    "アプリケーションをクリックし、\"bot\"メニューをクリックして<br>" \
                    "すべてのIntentsを有効にしてください。<br>" \
                    "参考画像: https://i.imgur.com/a9c1DHT.png<br>" \
                    "修正後、アプリケーションを再起動してください。"

                print(("=" * 30) + f"\nボットの起動に失敗しました: {bot.identifier}\n" + e.replace('<br>', '\n') + "\n" + ("=" * 30))

            elif isinstance(e, disnake.LoginFailure) and "Improper token" in str(e):
                e = "無効なトークンが使用されました。<br>" \
                    "入力されたトークンが正しいか確認してください<br>" \
                    "またはトークンがリセットされていないか<br>" \
                    "正しい場所からコピーされているか確認してください（例: https://i.imgur.com/k894c1q.png）<br>" \
                    "修正後、アプリケーションを再起動してください。"

                print(("=" * 30) + f"\nボットの起動に失敗しました: {bot.identifier}\n" + e.replace('<br>', '\n') + "\n" + ( "=" * 30))

            else:
                traceback.print_tb(e.__traceback__)
                e = repr(e)
            self.failed_bots[bot.identifier] = e
            try:
                self.bots.remove(bot)
            except:
                pass

    async def run_bots(self, bots: List[BotCore]):
        await asyncio.gather(
            *[self.start_bot(bot) for bot in bots]
        )

    async def connect_node(self, bot: BotCore, data: dict):

        while not bot.bot_ready:
            await asyncio.sleep(3)

        music_cog = bot.get_cog("Music")
        if music_cog:
            await music_cog.connect_node(data)

    async def check_node(self, data: dict):

        data = deepcopy(data)

        data['rest_uri'] = ("https" if data.get('secure') else "http") + f"://{data['host']}:{data['port']}"

        try:
            max_retries = int(data.get('retries')) or 1
        except (TypeError, KeyError):
            max_retries = 1

        headers = {'Authorization': data['password']}

        backoff = 9
        retries = 0
        exception = None

        print(f"📶 - 音楽サーバー [{data['identifier']}] の可用性を確認しています。")

        while True:
            if retries >= max_retries:
                print(
                    f"❌ - サーバー [{data['identifier']}] の確認がすべて失敗しました。原因: {repr(exception)}")
                return
            else:
                await asyncio.sleep(backoff)
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(f"{data['rest_uri']}/v4/info", timeout=45,
                                                        headers=headers) as r:
                            if r.status == 200:
                                data["info"] = await r.json()
                                data["info"]["check_version"] = 4
                            elif r.status == 403:
                                print(f"❌ - Lavalinkサーバー [{data['identifier']}] - パスワードが間違っています！")
                                return
                            elif r.status != 404:
                                raise Exception(f"❌ - [{r.status}]: {await r.text()}"[:300])
                            else:
                                data["info"] = {
                                    "check_version": 3,
                                    "sourceManagers": ["youtube", "soundcloud", "http"]
                                }
                            break
                except Exception as e:
                    exception = e
                    if data["identifier"] != "LOCAL":
                        print(f'⚠️ - サーバー [{data["identifier"]}] の確認に失敗しました。'
                              f'再試行 [{retries}/{max_retries}] を{backoff}秒後に行います。')
                    backoff += 2
                    retries += 1

        if data['identifier'] == 'LOCAL' and self.mongo_database and data["info"]["check_version"] > 3 and [i for i in data["info"].get("plugins", {}) if i["name"] == "youtube-plugin"]:

            try:
                mongo_data = await self.mongo_database._connect["global"]["global"].find_one({"_id": "youtube_data"}) or {}
            except Exception:
                traceback.print_exc()
            else:
                if tokens:=mongo_data.get("refresh_tokens"):
                    for v in tokens.values():
                        try:
                            async with ClientSession() as session:
                                resp = await session.post(
                                    f"{data['rest_uri']}/youtube", headers=headers,
                                    json={"refreshToken": v}
                                )
                                if resp.status != 204:
                                    resp.raise_for_status()
                        except Exception as e:
                            print(f"🌋 - LavalinkサーバーへのYoutube refreshTokenの適用に失敗しました: {data['identifier']} - {repr(e)}")
                            break
                        else:
                            print(f"🌋 - LavalinkサーバーにYoutube refreshTokenを適用しました: {data['identifier']}")
                            break

        for bot in self.get_all_bots():
            self.loop.create_task(self.connect_node(bot, data))
            await asyncio.sleep(1)

    def node_check(self, lavalink_servers: dict, start_local=True):

        if start_local and "LOCAL" not in lavalink_servers:
            localnode = {
                'host': '127.0.0.1',
                'port': os.environ.get("SERVER_PORT") or 8090,
                'password': 'youshallnotpass',
                'identifier': 'LOCAL',
                'region': 'us_central',
                'retries': 120,
                'prefer_youtube_native_playback': self.config["PREFER_YOUTUBE_NATIVE_PLAYBACK"],
                'only_use_native_search_providers': self.config["ONLY_USE_NATIVE_SEARCH_PROVIDERS"],
                'search_providers': self.config["SEARCH_PROVIDERS"].strip().split() or ["amsearch", "tdsearch", "spsearch", "ytsearch", "scsearch"]
            }
            self.loop.create_task(self.check_node(localnode))

        for data in lavalink_servers.values():
            self.loop.create_task(self.check_node(data))

    def process_track_cls(self, data: list, playlists: dict = None):

        if not playlists:
            playlists = {}

        tracks = []

        for info in data:

            if playlist := info.pop("playlist", None):

                try:
                    playlist = playlists[playlist["url"]]
                except KeyError:
                    playlist_cls = LavalinkPlaylist(
                        {
                            'loadType': 'PLAYLIST_LOADED',
                            'playlistInfo': {
                                'name': playlist["name"],
                                'selectedTrack': -1
                            },
                            'tracks': []
                        }, url=playlist["url"]
                    )
                    playlists[playlist["url"]] = playlist_cls
                    playlist = playlist_cls

            if info["sourceName"] not in native_sources:
                try:
                    del info["id"]
                except KeyError:
                    pass

            if info.get("is_partial"):
                track = PartialTrack(info=info)
            else:
                track = LavalinkTrack(id_=info.get("id", ""), info=info, playlist=playlist, requester=info["extra"]["requester"], pluginInfo=info.get("pluginInfo", {}))
            tracks.append(track)

        return tracks, playlists

    async def connect_rpc_ws(self):

        if not self.config["RUN_RPC_SERVER"] and (
                not self.config["RPC_SERVER"] or self.config["RPC_SERVER"].replace("$PORT", port := os.environ.get("PORT", "80")) == f"ws://localhost:{port}/ws"):
            pass
        else:
            await self.ws_client.ws_loop()

    def load_cfg(self):

        config = load_config()

        try:
            with open("./emojis.json") as f:
                self.emoji_data = json.load(f)
        except FileNotFoundError:
            pass
        except:
            traceback.print_exc()

        if not config["DEFAULT_PREFIX"]:
            config["DEFAULT_PREFIX"] = "!!"

        return config

    def load_skins(self):

        for skin in os.listdir("./utils/music/skins/normal_player"):
            if not skin.endswith(".py"):
                continue

            skin = skin[:-3]

            if skin in self.config["IGNORE_SKINS"].split() and skin != "default":
                print(f"スキン {skin}.py を無視しました")
                continue

            try:
                skin_file = import_module(f"utils.music.skins.normal_player.{skin}")
                if not hasattr(skin_file, "load"):
                    print(f"スキンを無視しました: {skin}.py | load()関数が設定/見つかりません...")
                    continue
                self.player_skins[skin] = skin_file.load()
            except Exception:
                print(f"スキン [normal_player] の読み込みに失敗しました: {traceback.format_exc()}")

        if self.default_skin not in self.player_skins:
            self.default_skin = "default"

        for skin in os.listdir("./utils/music/skins/static_player"):
            if not skin.endswith(".py"):
                continue

            skin = skin[:-3]

            if skin in self.config["IGNORE_STATIC_SKINS"].split() and skin != "default":
                print(f"スキン {skin}.py を無視しました")
                continue

            try:
                skin_file = import_module(f"utils.music.skins.static_player.{skin}")
                if not hasattr(skin_file, "load"):
                    print(f"スキンを無視しました: {skin}.py | load()関数が設定/見つかりません...")
                    continue
                self.player_static_skins[skin] = skin_file.load()
            except Exception:
                print(f"スキン [static_player] の読み込みに失敗しました: {traceback.format_exc()}")
        if self.default_static_skin not in self.player_static_skins:
            self.default_static_skin = "default"

    def check_skin(self, skin: str):

        if skin is None:
            return self.default_skin

        if skin.startswith("> custom_skin: "):
            return skin

        if skin not in self.player_skins:
            return self.default_skin

        return skin

    def check_static_skin(self, skin: str):

        if skin is None:
            return self.default_static_skin

        if skin.startswith("> custom_skin: "):
            return skin

        if skin is None or skin not in self.player_static_skins:
            return self.default_static_skin

        return skin

    async def setup_pool_extras(self):

        try:
            from dev.pool_dev import PoolDev
            await PoolDev(self).run()
        except ImportError:
            pass
        except Exception:
            print(traceback.format_exc())

    def setup(self):

        self.load_skins()

        if self.config['ENABLE_LOGGER']:

            if not os.path.isdir("./.logs"):
                os.makedirs("./.logs")

            logger = logging.getLogger()
            logger.setLevel(logging.DEBUG)
            handler = logging.FileHandler(filename='./.logs/disnake.log', encoding='utf-8', mode='w')
            handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s %(message)s'))
            logger.addHandler(handler)

        LAVALINK_SERVERS = {}

        if self.config["AUTO_DOWNLOAD_LAVALINK_SERVERLIST"]:

            try:
                LAVALINK_SERVERS = get_lavalink_servers()
                ini_file = None
            except Exception:
                traceback.print_exc()
                ini_file = "auto_lavalink.ini"
                print(f"Lavalinkサーバーリストをダウンロードしています（ファイル: {ini_file}）")
                try:
                    r = requests.get(self.config["LAVALINK_SERVER_LIST"], allow_redirects=False)
                    with open("auto_lavalink.ini", 'wb') as f:
                        f.write(r.content)
                    r.close()
                except Exception:
                    traceback.print_exc()
        else:
            ini_file = "lavalink.ini"

        for key, value in self.config.items():

            if key.lower().startswith("lavalink_node_"):
                try:
                    LAVALINK_SERVERS[key] = json.loads(value)
                except Exception as e:
                    print(f"ノードの追加に失敗しました: {key}、エラー: {repr(e)}")

        if ini_file:
            config = ConfigParser()
            try:
                config.read(ini_file)
            except FileNotFoundError:
                pass
            except Exception:
                traceback.print_exc()
            else:
                for key, value in {section: dict(config.items(section)) for section in config.sections()}.items():
                    value["identifier"] = key
                    value["secure"] = value.get("secure", "").lower() == "true"
                    value["port"] = value["port"].replace("{SERVER_PORT}", os.environ.get("SERVER_PORT") or "8090")
                    value["search"] = value.get("search", "").lower() != "false"
                    value["prefer_youtube_native_playback"] = value.get("prefer_youtube_native_playback", "").lower() == "true"
                    value["only_use_native_search_providers"] = value.get("only_use_native_search_providers", "").lower() == "true"
                    value["search_providers"] = value.get("search_providers", "").strip().split()
                    LAVALINK_SERVERS[key] = value

        if start_local := (self.config['RUN_LOCAL_LAVALINK'] is True or not LAVALINK_SERVERS):
            pass
        else:
            start_local = False

        intents = disnake.Intents(**{i[:-7].lower(): v for i, v in self.config.items() if i.lower().endswith("_intent")})
        intents.members = True
        intents.guilds = True

        mongo_key = self.config.get("MONGO")

        if mongo_key:
            self.mongo_database = MongoDatabase(mongo_key, timeout=self.config["MONGO_TIMEOUT"],
                                                cache_maxsize=self.config["DBCACHE_SIZE"],
                                                cache_ttl=self.config["DBCACHE_TTL"])
            print("🍃 - 使用中のデータベース: MongoDB")
        else:
            print("🎲 - 使用中のデータベース: TinyMongo | 注意: データベースファイルはlocal_databaseフォルダにローカル保存されます")

        self.local_database = LocalDatabase(cache_maxsize=self.config["DBCACHE_SIZE"],
                                            cache_ttl=self.config["DBCACHE_TTL"])

        os.environ.update(
            {
                "GIT_DIR": self.config["GIT_DIR"],
                "JISHAKU_HIDE": "true",
                "JISHAKU_NO_DM_TRACEBACK": "true",
                "JISHAKU_NO_UNDERSCORE": "true",
             }
        )

        try:
            self.commit = check_output(['git', 'rev-parse', 'HEAD']).decode('ascii').strip()
            print(f"📥 - コミットバージョン: {self.commit}")
        except:
            self.commit = None

        try:
            self.remote_git_url = check_output(['git', 'remote', '-v']).decode(
                'ascii').strip().split("\n")[0][7:].replace(".git", "").replace(" (fetch)", "")
        except:
            pass

        if not self.remote_git_url:
            self.remote_git_url = self.config["SOURCE_REPO"]

        prefix = get_prefix if intents.message_content else commands.when_mentioned

        self.ws_client = WSClient(self.config["RPC_SERVER"], pool=self)

        try:
            spotify_client = SpotifyClient(
                client_id=self.config['SPOTIFY_CLIENT_ID'],
                client_secret=self.config['SPOTIFY_CLIENT_SECRET'],
                playlist_extra_page_limit=self.config['SPOTIFY_PLAYLIST_EXTRA_PAGE_LIMIT']
            )
        except Exception as e:
            print(f"⚠️ - Spotifyの内部サポートが無効になりました: {repr(e)}")
            spotify_client = None

        self.spotify = spotify_client

        if self.config["LASTFM_KEY"] and self.config["LASTFM_SECRET"]:
            self.last_fm = LastFM(api_key=self.config["LASTFM_KEY"], api_secret=self.config["LASTFM_SECRET"])

        all_tokens = {}

        for k, v in dict(os.environ, **self.config).items():

            if not isinstance(v, str):
                continue

            if not (tokens := token_regex.findall(v)):
                continue

            if len(tokens) > 1:
                counter = 1
                for t in tokens:

                    if t in all_tokens.values():
                        continue

                    all_tokens[f"{k}_{counter}"] = t
                    counter += 1

            elif (token := tokens.pop()) not in all_tokens.values():
                all_tokens[k] = token

        def load_bot(bot_name: str, token: str, guild_id: str = None, load_modules_log: bool = False):

            try:
                token = token.split().pop()
            except:
                pass

            if not token:
                print(f"{bot_name} をスキップしました（トークンが指定されていません）...")
                return

            try:
                test_guilds = list([int(i) for i in self.config[f"TEST_GUILDS_{bot_name}"].split("||")])
            except:
                test_guilds = None

            bot = BotCore(
                command_prefix=prefix,
                case_insensitive=True,
                intents=intents,
                identifier=bot_name,
                test_guilds=test_guilds,
                command_sync_flags=commands.CommandSyncFlags.none(),
                embed_color=self.config["EMBED_COLOR"],
                default_prefix=self.config["DEFAULT_PREFIX"],
                pool=self,
                number=int(self.max_counter)
            )

            bot.http.token = token

            bot.load_extension("jishaku")

            if bot.config['INTERACTION_COMMAND_ONLY']:

                @bot.check
                async def check_commands(ctx: CustomContext):

                    if not (await bot.is_owner(ctx.author)):
                        raise GenericError("**テキストコマンドは無効になっています！\n"
                                           "スラッシュコマンド / を使用してください**", self_delete=True, delete_original=15)

                    return True

            @bot.listen("on_resumed")
            async def clear_gc():

                if self.processing_gc:
                    return

                self.processing_gc = True
                await asyncio.sleep(2)
                gc.collect()
                self.processing_gc = False

            @bot.application_command_check(slash_commands=True, message_commands=True, user_commands=True)
            async def check(inter: disnake.ApplicationCommandInteraction):

                kwargs = {}

                try:
                    kwargs["only_voiced"] = inter.application_command.extras["only_voiced"]
                except KeyError:
                    pass

                try:
                    kwargs["check_player"] = inter.application_command.extras["check_player"]
                except KeyError:
                    pass

                try:
                    kwargs["return_first"] = inter.application_command.extras["return_first"]
                except KeyError:
                    pass

                try:
                    allow_private = inter.application_command.extras["allow_private"]
                except KeyError:
                    allow_private = False

                if inter.bot.exclusive_guild_id and inter.guild_id != inter.bot.exclusive_guild_id:
                    raise GenericError("このサーバーは私のコマンドを使用する権限がありません...")

                if self.config["COMMAND_LOG"] and inter.guild and not (await inter.bot.is_owner(inter.author)):
                    try:
                        print(
                            f"cmd log: [user: {inter.author} - {inter.author.id}] - [guild: {inter.guild.name} - {inter.guild.id}]"
                            f" - [cmd: {inter.data.name}] {datetime.datetime.utcnow().strftime('%d/%m/%Y - %H:%M:%S')} (UTC) - {inter.filled_options}\n" + (
                                        "-" * 15))
                    except:
                        traceback.print_exc()

                if not inter.guild_id:

                    if allow_private:
                        return True

                    raise GenericError("このコマンドはプライベートメッセージでは実行できません。\n"
                                     "互換性のあるボットが追加されているサーバーで使用してください。")

                if not allow_private and not isinstance(inter.guild, disnake.Guild):

                    available_bot = False

                    for b in inter.bot.pool.get_guild_bots(inter.guild_id):
                        if b.appinfo and (
                                b.appinfo.bot_public or await b.is_owner(inter.author)) and b.get_guild(
                                inter.guild_id):
                            available_bot = True
                            break

                    if not available_bot:
                        raise GenericError(
                            "**サーバーに利用可能なボットがありません。下のボタンをクリックして少なくとも1つ追加してください。**",
                            components=[disnake.ui.Button(custom_id="bot_invite", label="ボットを追加")])

                if not kwargs:
                    kwargs["return_first"] = True

                try:
                    await check_pool_bots(inter, **kwargs)
                except Exception as e:
                    if not inter.guild_id:
                        bot.dispatch("custom_slash_command_error", inter, e, no_log=True)
                    raise e

                return True

            if self.config["COMMAND_LOG"]:

                @bot.listen("on_command")
                async def command_log(ctx: CustomContext):

                    if (await bot.is_owner(ctx.author)):
                        return

                    print(
                        f"cmd (prefix) log: [user: {ctx.author} - {ctx.author.id}] - [guild: {ctx.guild.name} - {ctx.guild.id}]"
                        f" - [cmd: {ctx.message.content}] {datetime.datetime.utcnow().strftime('%d/%m/%Y - %H:%M:%S')} (UTC)\n" + ("-" * 15)
                    )

            @bot.event
            async def on_ready():
                print(f'🟢 - {bot.user} - [{bot.user.id}] オンラインになりました。')

            async def initial_setup():

                await bot.wait_until_ready()

                if bot.session is None:
                    bot.session = aiohttp.ClientSession()

                bot.music.session = bot.session

                try:
                    bot.interaction_id = bot.user.id

                    bot.load_modules(load_modules_log=load_modules_log)

                    bot.sync_command_cooldowns()

                    if bot.config["AUTO_SYNC_COMMANDS"]:
                        await bot.sync_app_commands(force=True)

                    bot.add_view(PanelView(bot))

                    self.bot_mentions.update((f"<@!{bot.user.id}>", f"<@{bot.user.id}>"))

                except Exception:
                    traceback.print_exc()

                await bot.update_appinfo()

                bot.bot_ready = True

            bot.loop.create_task(initial_setup())

            if guild_id:
                bot.exclusive_guild_id = int(guild_id)
                try:
                    self.guild_bots[guild_id].append(bot)
                except KeyError:
                    self.guild_bots = {guild_id: [bot]}
            else:
                self.bots.append(bot)

        if len(all_tokens) > 1:
            self.single_bot = False

        load_modules_log = True

        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

        for k, v in all_tokens.items():
            load_bot(k, v, load_modules_log=load_modules_log)
            load_modules_log = False

        try:
            with open("guild_bots.json") as f:
                guild_bots = json.load(f)
        except FileNotFoundError:
            pass
        except Exception:
            traceback.print_exc()
        else:
            for guild_id, guildbotsdata in guild_bots.items():
                for n, guildbottoken in enumerate(guildbotsdata):
                    load_bot(f"{guild_id}_{n}", guildbottoken, guild_id, load_modules_log=load_modules_log)
                    load_modules_log = False

        message = ""

        self.loop.create_task(self.setup_pool_extras())

        if not self.bots:

            message = "ボットのトークンが正しく設定されていません！"

            if os.environ.get("REPL_SLUG"):
                message += f"トークンがReplitのsecretsに追加されているか確認してください"

                print(message + ": 設定ガイド: https://gist.github.com/zRitsu/70737984cbe163f890dae05a80a3ddbe#2---com-o-projeto-j%C3%A1-criado-prossiga-as-etapas-abaixo")

                message += f'. <a href="https://gist.github.com/zRitsu/70737984cbe163f890dae05a80a3ddbe#2---com-o-projeto-j%C3%A1-criado-prossiga-as-etapas-abaixo" target="_blank">ここをクリック</a>して設定ガイドをご覧ください。'

            else:
                message += " トークンがENV/ENVIRONMENTまたは.envファイルに設定されているか確認してください"

                print(f"⚠️ - {message}")

            message = f"\n\n{message}"

        else:

            if start_local:
                self.loop.create_task(self.start_lavalink())

            if self.spotify and not self.spotify.spotify_cache:
                self.loop.create_task(self.spotify.get_access_token())

            self.node_check(LAVALINK_SERVERS, start_local=start_local)

        if self.config["RUN_RPC_SERVER"]:

            self.cache_updater_task = self.loop.create_task(self.cache_updater())

            if not message:
                self.loop.create_task(self.run_bots(self.get_all_bots()))
                self.loop.create_task(self.connect_rpc_ws())

            try:
                start(self, message=message)
            except KeyboardInterrupt:
                return

        elif message:
            raise Exception(message)

        else:

            self.cache_updater_task = self.loop.create_task(self.cache_updater())

            self.loop.create_task(self.connect_rpc_ws())

            try:
                self.loop.run_until_complete(
                    self.run_bots(self.get_all_bots())
                )
            except KeyboardInterrupt:
                return


class BotCore(commands.AutoShardedBot):

    def __init__(self, *args, **kwargs):
        self.session: Optional[aiohttp.ClientError] = None
        self.pool: BotPool = kwargs.pop('pool')
        self.default_prefix = kwargs.pop("default_prefix", "!!")
        self.session: Optional[aiohttp.ClientSession] = None
        self.color = kwargs.pop("embed_color", None)
        self.identifier = kwargs.pop("identifier", "")
        self.appinfo: Optional[disnake.AppInfo] = None
        self.exclusive_guild_id: Optional[int] = None
        self.bot_ready = False
        self.uptime = disnake.utils.utcnow()
        self.env_owner_ids = set()
        self.dm_cooldown = commands.CooldownMapping.from_cooldown(rate=2, per=30, type=commands.BucketType.member)
        self.number = kwargs.pop("number", 0)
        super().__init__(*args, **kwargs)
        self.music: wavelink.Client = music_mode(self)
        self.interaction_id: Optional[int] = None
        self.wavelink_node_reconnect_tasks = {}

        for i in self.config["OWNER_IDS"].split("||"):

            if not i:
                continue

            try:
                self.env_owner_ids.add(int(i))
            except ValueError:
                print(f"無効なOwner_ID: {i}")

    async def edit_voice_channel_status(
            self, status: Optional[str], *, channel_id: int, reason: Optional[str] = None
    ):
        # discord.pyから取得: https://github.com/Rapptz/discord.py/blob/9ce733321b445db245924bfd21fedf20a01a570b/discord/http.py#L1166
        r = Route('PUT', '/channels/{channel_id}/voice-status', channel_id=channel_id)
        payload = {'status': status}
        return await self.http.request(r, reason=reason, json=payload)

    @property
    def player_skins(self):
        return self.pool.player_skins

    @property
    def player_static_skins(self):
        return self.pool.player_static_skins

    @property
    def config(self):
        return self.pool.config

    @property
    def last_fm(self):
        return self.pool.last_fm

    @property
    def deezer(self):
        return self.pool.deezer

    @property
    def spotify(self):
        return self.pool.spotify

    @property
    def emoji_data(self):
        return self.pool.emoji_data

    @property
    def ws_client(self):
        return self.pool.ws_client

    async def get_data(self, id_: int, *, db_name: Union[DBModel.guilds, DBModel.users]):
        return await self.pool.database.get_data(
            id_=id_, db_name=db_name, collection=str(self.user.id)
        )

    async def update_data(self, id_, data: dict, *, db_name: Union[DBModel.guilds, DBModel.users]):
        return await self.pool.database.update_data(
            id_=id_, data=data, db_name=db_name, collection=str(self.user.id)
        )

    async def get_global_data(self, id_: int, *, db_name: Union[DBModel.guilds, DBModel.users]):

        data = await self.pool.database.get_data(
            id_=id_, db_name=db_name, collection="global", default_model=global_db_models
        )

        return data

    async def update_global_data(self, id_, data: dict, *, db_name: Union[DBModel.guilds, DBModel.users]):

        return await self.pool.database.update_data(
            id_=id_, data=data, db_name=db_name, collection="global", default_model=global_db_models
        )

    async def is_owner(self, user: Union[disnake.User, disnake.Member]) -> bool:

        if user.id in self.env_owner_ids:
            return True

        return await super().is_owner(user)

    async def sync_app_commands(self, force=False):

        await self.wait_until_ready()

        if not self.command_sync_flags.sync_commands and not force:
            return

        current_cmds = sorted([sort_dict_recursively(cmd.body.to_dict()) for cmd in self.application_commands], key=lambda k: k["name"])

        try:
            with open(f"./.app_commands_sync_data/{self.user.id}.json") as f:
                synced_cmds = await self.loop.run_in_executor(None, lambda: json.load(f))
        except FileNotFoundError:
            synced_cmds = None

        if current_cmds == synced_cmds:
            if current_cmds:
                print(f"⚠️ - {self.user} - コマンドは既に同期されています。")
            return

        self._command_sync_flags = self.pool.command_sync_config
        await self._sync_application_commands()
        self._command_sync_flags = commands.CommandSyncFlags.none()

        try:
            if not os.path.isdir("./.app_commands_sync_data/"):
                os.makedirs("./.app_commands_sync_data/")

            with open(f"./.app_commands_sync_data/{self.user.id}.json", "w", encoding="utf-8") as f:
                await self.loop.run_in_executor(None, lambda: json.dump(current_cmds, f, indent=4))

            with open(f"./.app_commands_sync_data/{self.user.id}-old.json", "w", encoding="utf-8") as f:
                await self.loop.run_in_executor(None, lambda: json.dump(synced_cmds, f, indent=4))

        except:
            traceback.print_exc()

    def sync_command_cooldowns(self, force=False):

        for b in self.pool.get_all_bots():

            if b == self and force is False:
                continue

            for cmd in b.commands:
                cmd.ignore_extra = False
                if cmd.extras.get("exclusive_cooldown"): continue
                c = self.get_command(cmd.name)
                if not c: continue
                c.ignore_extra = False
                if self.pool.config["ENABLE_COMMANDS_COOLDOWN"] is False:
                    c._buckets._cooldown = None
                else:
                    c._buckets = cmd._buckets

            for cmd in b.slash_commands:
                c = self.get_slash_command(cmd.name)
                if not c: continue
                if self.pool.config["ENABLE_COMMANDS_COOLDOWN"] is False:
                    c._buckets._cooldown = None
                else:
                    if c.extras.get("exclusive_cooldown"): continue
                    c._buckets = cmd._buckets

            for cmd in b.user_commands:
                c = self.get_user_command(cmd.name)
                if not c: continue
                if self.pool.config["ENABLE_COMMANDS_COOLDOWN"] is False:
                    c._buckets._cooldown = None
                else:
                    if c.extras.get("exclusive_cooldown"): continue
                    c._buckets = cmd._buckets

            for cmd in b.message_commands:
                c = self.get_message_command(cmd.name)
                if not c: continue
                if self.pool.config["ENABLE_COMMANDS_COOLDOWN"] is False:
                    c._buckets._cooldown = None
                else:
                    if c.extras.get("exclusive_cooldown"): continue
                    c._buckets = cmd._buckets

    async def can_send_message(self, message: disnake.Message):

        if isinstance(message.channel, disnake.Thread):
            perm_check = message.channel.parent.permissions_for(message.guild.me).send_messages_in_threads
        else:
            perm_check = message.channel.permissions_for(message.guild.me).send_messages

        if not perm_check:
            print(f"メッセージを送信できません: {message.channel.name} [{message.channel.id}] (権限がありません)")
            return

        return True

    async def on_message(self, message: disnake.Message):

        if not self.bot_ready or not self.appinfo or self.is_closed():
            return

        if not message.guild:
            return

        if self.exclusive_guild_id and message.guild.id != self.exclusive_guild_id:
            return

        try:
            player: LavalinkPlayer = self.music.players[message.guild.id]
            if player.text_channel == message.channel and not message.flags.ephemeral:
                player.last_message_id = message.id
        except (KeyError, AttributeError):
            pass

        if isinstance(message.channel, disnake.StageChannel):
            pass

        elif message.author.bot:
            return

        elif message.content in (f"<@{self.user.id}>",  f"<@!{self.user.id}>"):

            if message.author.bot:
                return

            if not await self.can_send_message(message):
                return

            embed = disnake.Embed(color=self.get_color(message.guild.me))

            prefix = (await self.get_prefix(message))

            if not isinstance(prefix, str):
                prefix = prefix[-1]

            embed.description = f"**こんにちは {message.author.mention}さん。**"

            if not self.config["INTERACTION_COMMAND_ONLY"]:
                embed.description += f"\n\nこのサーバーでのプレフィックスは: **{prefix}** `（メンションもプレフィックスとして機能します）`\n"\
                                    f"すべてのコマンドを見るには **{prefix}help** を使用してください"

            embed.description += "\n\n**すべてのコマンドを見るには: / を使用してください**"

            kwargs = {
                "components": [
                    disnake.ui.Button(
                        label="あなたのサーバーに追加してください",
                        url=disnake.utils.oauth_url(self.user.id, permissions=disnake.Permissions(self.config['INVITE_PERMISSIONS']), scopes=('bot',))
                    )
                ]
            }

            if message.channel.permissions_for(message.guild.me).read_message_history:
                await message.reply(embed=embed, fail_if_not_exists=False, **kwargs)
            else:
                await message.channel.send(message.author.mention, embed=embed, **kwargs)
            return

        ctx: CustomContext = await self.get_context(message, cls=CustomContext)

        try:
            ctx.player = self.music.players[message.guild.id]
        except:
            pass

        if self.config["ENABLE_SONGREQUEST_MENTION"] and not ctx.valid and message.content.startswith(self.user.mention) and message.author.voice:

            query = str(message.content)

            for m in message.mentions:
                query = query.replace(m.mention, "", 1)

            query = query.strip()

            if query:
                play_cmd = self.get_slash_command("play")
                self.dispatch("pool_dispatch", ctx, self.user.id)
                try:
                    await play_cmd.callback(
                        inter=ctx, query=query, self=play_cmd.cog, position=0, options=False, force_play="no",
                        manual_selection=False, server=None
                    )
                except Exception as e:
                    self.dispatch("command_error", ctx, e)
                return

        self.dispatch("song_request", ctx, message)

        if not ctx.valid:
            return

        if not await self.can_send_message(message):
            return

        try:
            kwargs = {
                "only_voiced": ctx.command.pool_only_voiced,
                "check_player": ctx.command.pool_check_player,
                "return_first": ctx.command.pool_return_first,
            }
        except AttributeError:
            kwargs = {"return_first": True}

        try:
            await check_pool_bots(ctx, **kwargs)
        except Exception as e:
            self.dispatch("command_error", ctx, e)
            return

        await self.invoke(ctx)

    def check_bot_forum_post(
            self,
            channel: Union[disnake.ForumChannel, disnake.TextChannel, disnake.VoiceChannel, disnake.Thread],
            raise_error=False,
    ):

        try:
            if isinstance(channel.parent, disnake.ForumChannel):

                if channel.owner_id in (bot.user.id for bot in self.pool.get_guild_bots(channel.guild.id) if bot.bot_ready):

                    if raise_error is False:
                        return False

                    raise GenericError("**この投稿ではプレフィックスコマンドを使用できません...**\n"
                                       "`ここではスラッシュコマンド (/) を使用してください。`", self_delete=True)
        except AttributeError:
            pass

        return True

    def get_color(self, me: Optional[disnake.Member] = None):

        if not me:
            return self.color or 0x2b2d31

        if self.color:
            return self.color

        if me.color.value == 0:
            return 0x2b2d31

        return me.color

    async def update_appinfo(self):

        await self.wait_until_ready()

        self.appinfo = await self.application_info()

        try:
            self.owner = self.appinfo.team.owner
            owners =self.appinfo.team.members
        except AttributeError:
            self.owner = self.appinfo.owner
            owners = [self.appinfo.owner]

        if self.appinfo.bot_public and not self.config.get("SILENT_PUBLICBOT_WARNING"):

            def check_member(u: disnake.User, g: disnake.Guild):
                member = g.get_member(u.id)
                return member and member.guild_permissions.manage_guild

            guilds = set()
            for guild in self.guilds:
                if not [dev for dev in owners if check_member(dev, guild)]:
                    guilds.add(guild)

            warn_msg = f"注意: ボット [{self.user}] (ID: {self.user.id}) はDeveloper Portalで" \
                  "公開ボットとして設定されています\n" \
                  "ボットが公開的に追加されるよう宣伝される場合、" \
                  "GPL-2ライセンスの条件に従う必要があります: " \
                  "https://github.com/zRitsu/MuseHeart-MusicBot/blob/main/LICENSE\n" \
                  "ライセンスの条件に従いたくない場合は、以下のリンクからpublic botオプションの" \
                  f"チェックを外してボットをプライベートにできます: https://discord.com/developers/applications/{self.user.id}/bot"

            if guilds:
                warn_msg += "\n\n現在、ボットはボットの所有者（またはチームメンバー）が存在しないか、" \
                            f"ボット [{self.user}] を追加するためのサーバー管理権限を持っていないサーバーに存在しています:\n\n" + "\n".join(f"{g.name} [ID: {g.id}]" for g in list(guilds)[:10])

                if (gcount:=len(guilds)) > 10:
                    warn_msg += F"\n他に{gcount-10}個のサーバーにも存在しています。"

            print(("="*50) + f"\n{warn_msg}\n" + ("="*50))

    async def on_application_command_autocomplete(self, inter: disnake.ApplicationCommandInteraction):

        if not self.bot_ready or not self.is_ready():
            return []

        if not inter.guild_id:
            return []

        if self.exclusive_guild_id and inter.guild_id != self.exclusive_guild_id:
            return []

        await super().on_application_command_autocomplete(inter)

    async def on_application_command(self, inter: disnake.ApplicationCommandInteraction):

        if not self.bot_ready or self.is_closed():
            await inter.send("まだ初期化中です...\nもう少しお待ちください...", ephemeral=True)
            return

        await super().on_application_command(inter)

    def load_modules(self, module_list: list = None, load_modules_log=False):

        modules_dir = ["modules", "modules_dev"]

        load_status = {
            "reloaded": [],
            "loaded": [],
            "failed": [],
        }

        bot_name = self.user or self.identifier

        for module_dir in modules_dir:

            for item in os.walk(module_dir):
                files = filter(lambda f: f.endswith('.py'), item[-1])

                for file in files:

                    if module_list:
                        if not [i for i in module_list if file.endswith(i)]:
                            continue
                    filename, _ = os.path.splitext(file)
                    module_filename = os.path.join(module_dir, filename).replace('\\', '.').replace('/', '.')
                    try:
                        self.unload_extension(module_filename)
                        self.load_extension(module_filename)
                        if not self.bot_ready and load_modules_log:
                            print(f"🟦 - {bot_name} - {filename}.py 再読み込みしました。")
                        load_status["reloaded"].append(f"{filename}.py")
                    except (commands.ExtensionAlreadyLoaded, commands.ExtensionNotLoaded):
                        try:
                            self.load_extension(module_filename)
                            if not self.bot_ready and load_modules_log:
                                print(f"🟩 - {bot_name} - {filename}.py 読み込みました。")
                            load_status["loaded"].append(f"{filename}.py")
                        except Exception as e:
                            print(f"❌- {bot_name} - モジュールの読み込み/再読み込みに失敗しました: {filename}")
                            if not self.bot_ready:
                                raise e
                            load_status["failed"].append(f"{filename}.py")
                            traceback.print_exc()
                    except Exception as e:
                        print(f"❌ - {bot_name} - モジュールの読み込み/再読み込みに失敗しました: {filename}")
                        if not self.bot_ready:
                            raise e
                        load_status["failed"].append(f"{filename}.py")
                        traceback.print_exc()

        if not self.config["ENABLE_DISCORD_URLS_PLAYBACK"]:
            self.remove_slash_command("play_music_file")

        for c in self.slash_commands:
            if (desc:=len(c.description)) > 100:
                raise Exception(f"コマンド {c.name} の説明がDiscordで許可されている文字数（100）を超えています。"
                                f"現在の文字数: {desc}")

        return load_status
