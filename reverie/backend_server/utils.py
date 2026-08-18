"""
File: utils.py
Description: Runtime configuration for the Reverie backend server.

All secrets and knobs are read from environment variables, so this file is
safe to keep in version control. A .env file placed in this directory (or
any parent) is loaded automatically if python-dotenv is installed.

Required:
  OPENAI_API_KEY        your API key

Optional:
  OPENAI_BASE_URL       point at any OpenAI-compatible endpoint, e.g.
                        http://localhost:11434/v1 for Ollama, or
                        https://api.anthropic.com/v1/ for Anthropic
  OPENAI_CHAT_MODEL     default chat model (default: gpt-4o-mini)
  OPENAI_SMART_CHAT_MODEL   model for the highest-stakes prompts
                        (default: same as OPENAI_CHAT_MODEL)
  OPENAI_EMBEDDING_MODEL    embedding model (default: text-embedding-3-small)
  LLM_CACHE             set to 0 to disable the on-disk LLM response cache
  LLM_CACHE_PATH        where to store the cache sqlite file
  PARALLEL_PERSONAS     set to 0 to step personas sequentially
  MAX_PARALLEL_WORKERS  thread pool size for parallel persona steps
  CHECKPOINT_FREQ       auto-save the simulation every N steps (default 50)
  REVERIE_DEBUG         set to 1 for verbose prompt debugging output
"""
import json
import os

try:
  from dotenv import load_dotenv
  load_dotenv()
except ImportError:
  pass

# docker compose passes unset variables through as EMPTY STRINGS
# (VAR=${VAR:-}). The OpenAI SDK treats an empty OPENAI_BASE_URL as a real
# base URL and builds protocol-less request URLs ("UnsupportedProtocol"
# crash), so scrub empty values before anything reads them.
for _empty_var in ("OPENAI_BASE_URL", "EMBEDDING_BASE_URL",
                   "EMBEDDING_API_KEY", "OPENAI_CHAT_MODEL",
                   "OPENAI_SMART_CHAT_MODEL", "OPENAI_EMBEDDING_MODEL",
                   "CHRONICLE_LANG"):
  if os.environ.get(_empty_var) == "":
    del os.environ[_empty_var]

# A shared llm_config.json (written by the frontend's /settings page) at the
# repository root overrides environment variables, so keys entered in the web
# UI win over whatever the process was started with. Restart reverie.py after
# changing it.
llm_config_path = os.environ.get("LLM_CONFIG_PATH", os.path.join(
  os.path.dirname(os.path.abspath(__file__)), "..", "..", "llm_config.json"))


def load_llm_config(path=None):
  try:
    with open(path or llm_config_path) as f:
      return json.load(f)
  except (OSError, ValueError):
    return {}


_file_cfg = load_llm_config()


def _cfg(file_key, env_key, default=""):
  return _file_cfg.get(file_key) or os.environ.get(env_key, default)


def _cfg_num(file_key, env_key, default, cast=float):
  raw = _file_cfg.get(file_key)
  if raw in (None, ""):
    raw = os.environ.get(env_key, "")
  try:
    return cast(raw)
  except (TypeError, ValueError):
    return default


def _cfg_bool(file_key, env_key, default=True):
  raw = _file_cfg.get(file_key)
  if raw in (None, ""):
    raw = os.environ.get(env_key)
  if raw in (None, ""):
    return default
  return str(raw).lower() not in ("0", "false", "off", "no")


# LLM provider configuration
openai_api_key = _cfg("api_key", "OPENAI_API_KEY")
openai_base_url = _cfg("base_url", "OPENAI_BASE_URL")
openai_chat_model = _cfg("chat_model", "OPENAI_CHAT_MODEL", "gpt-4o-mini")
openai_smart_chat_model = _cfg("smart_chat_model", "OPENAI_SMART_CHAT_MODEL",
                               openai_chat_model)
openai_embedding_model = _cfg("embedding_model", "OPENAI_EMBEDDING_MODEL",
                              "text-embedding-3-small")
# Embeddings may come from a different provider than chat (DeepSeek/MiniMax
# have no OpenAI-compatible embedding endpoint). Empty values fall back to
# the chat provider's key/endpoint.
embedding_api_key = (_cfg("embedding_api_key", "EMBEDDING_API_KEY")
                     or openai_api_key)
embedding_base_url = _cfg("embedding_base_url", "EMBEDDING_BASE_URL")
# Owner name recorded in simulation metadata
key_owner = os.environ.get("KEY_OWNER", "Anonymous")

# Cache / performance knobs
# Hard spending ceiling in USD (estimated from token counts); 0 disables.
# When the estimate reaches this, the simulation auto-saves and halts.
# These performance knobs can also be set on the /settings web page.
cost_limit_usd = _cfg_num("cost_limit_usd", "COST_LIMIT_USD", 0.0)

# Personality traits & relationships: auto-draw a hand of traits and a
# relationship web for every persona when a brand-new simulation is created.
traits_auto = _cfg_bool("traits_auto", "TRAITS_AUTO", True)
# Economy: wallets, daily wages, venue spending, conversation trades.
economy_enabled = _cfg_bool("economy", "ECONOMY", True)
econ_starting_balance = _cfg_num("econ_starting_balance",
                                 "ECONOMY_STARTING_BALANCE", 100.0)
econ_daily_wage = _cfg_num("econ_daily_wage", "ECONOMY_DAILY_WAGE", 80.0)

# Time flow: <sec_per_step> overrides how many game-seconds each step
# advances (0 = keep the simulation's own value, originally 10).
# <real_minutes_per_day> paces stepping so one game day takes at least this
# many real minutes (0 = run as fast as the LLM allows). E.g. sec_per_step=60
# with real_minutes_per_day=15 gives a 15-minute game day.
sec_per_step_override = _cfg_num("sec_per_step", "SEC_PER_STEP", 0, int)
real_minutes_per_day = _cfg_num("real_minutes_per_day",
                                "REAL_MINUTES_PER_DAY", 0.0)

# Headless mode: the backend advances the world without a browser tab by
# writing the environment files itself. Open simulator_home anytime to watch.
headless_mode = _cfg_bool("headless", "HEADLESS", False)
# The Ville Chronicle: auto-summarize each completed game day into a
# newspaper article (one LLM call per game day).
chronicle_enabled = _cfg_bool("chronicle", "CHRONICLE", True)
chronicle_lang = _cfg("chronicle_lang", "CHRONICLE_LANG",
                      "Traditional Chinese (繁體中文)")
# Conversation language: agents SPEAK in this language (their inner
# cognition stays English). Set to "English" for original behavior.
convo_lang = _cfg("convo_lang", "CONVO_LANG",
                  "Traditional Chinese (繁體中文)")

llm_cache_enabled = _cfg_bool("llm_cache", "LLM_CACHE", True)
llm_cache_path = os.environ.get("LLM_CACHE_PATH", "")
parallel_personas = _cfg_bool("parallel_personas", "PARALLEL_PERSONAS", True)
max_parallel_workers = int(os.environ.get("MAX_PARALLEL_WORKERS", "8"))
checkpoint_freq = _cfg_num("checkpoint_freq", "CHECKPOINT_FREQ", 50, int)

maze_assets_loc = "../../environment/frontend_server/static_dirs/assets"
env_matrix = f"{maze_assets_loc}/the_ville/matrix"
env_visuals = f"{maze_assets_loc}/the_ville/visuals"

fs_storage = "../../environment/frontend_server/storage"
fs_temp_storage = "../../environment/frontend_server/temp_storage"

collision_block_id = "32125"

# Verbose
debug = os.environ.get("REVERIE_DEBUG", "0") == "1"
