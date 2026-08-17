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
import os

try:
  from dotenv import load_dotenv
  load_dotenv()
except ImportError:
  pass

# LLM provider configuration
openai_api_key = os.environ.get("OPENAI_API_KEY", "")
openai_base_url = os.environ.get("OPENAI_BASE_URL", "")
openai_chat_model = os.environ.get("OPENAI_CHAT_MODEL", "gpt-4o-mini")
openai_smart_chat_model = os.environ.get("OPENAI_SMART_CHAT_MODEL",
                                         openai_chat_model)
openai_embedding_model = os.environ.get("OPENAI_EMBEDDING_MODEL",
                                        "text-embedding-3-small")
# Owner name recorded in simulation metadata
key_owner = os.environ.get("KEY_OWNER", "Anonymous")

# Cache / performance knobs
llm_cache_enabled = os.environ.get("LLM_CACHE", "1") != "0"
llm_cache_path = os.environ.get("LLM_CACHE_PATH", "")
parallel_personas = os.environ.get("PARALLEL_PERSONAS", "1") != "0"
max_parallel_workers = int(os.environ.get("MAX_PARALLEL_WORKERS", "8"))
checkpoint_freq = int(os.environ.get("CHECKPOINT_FREQ", "50"))

maze_assets_loc = "../../environment/frontend_server/static_dirs/assets"
env_matrix = f"{maze_assets_loc}/the_ville/matrix"
env_visuals = f"{maze_assets_loc}/the_ville/visuals"

fs_storage = "../../environment/frontend_server/storage"
fs_temp_storage = "../../environment/frontend_server/temp_storage"

collision_block_id = "32125"

# Verbose
debug = os.environ.get("REVERIE_DEBUG", "0") == "1"
