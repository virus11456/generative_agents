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
