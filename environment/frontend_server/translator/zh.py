"""
LLM-backed Traditional Chinese display translation with a persistent
sqlite cache.

The simulation engine stays English (its prompt templates and parsers
depend on it); this module translates only what a page displays. Every
unique string is translated once and cached forever, so repeat page
loads are free. Any failure -- no API key, network error, malformed
response -- falls back to the original English text.
"""
import json
import os
import re
import sqlite3
import threading

_CACHE_PATH = "temp_storage/zh_cache.sqlite"
_BATCH_MAX_LINES = 40
_BATCH_MAX_CHARS = 5000
_lock = threading.Lock()

_CJK_RE = re.compile(r"[一-鿿]")


def _llm_config():
  """
  Mirrors the backend's precedence: llm_config.json overrides env vars.
  """
  cfg = {}
  default_path = os.path.abspath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "..",
    "llm_config.json"))
  path = os.environ.get("LLM_CONFIG_PATH", default_path)
  try:
    with open(path) as f:
      cfg = json.load(f)
  except (OSError, ValueError):
    pass
  api_key = cfg.get("api_key") or os.environ.get("OPENAI_API_KEY", "")
  base_url = cfg.get("base_url") or os.environ.get("OPENAI_BASE_URL", "")
  model = (cfg.get("chat_model") or os.environ.get("OPENAI_CHAT_MODEL")
           or os.environ.get("CHAT_MODEL") or "gpt-4o-mini")
  return api_key, (base_url or None), model


def _cache_conn():
  os.makedirs(os.path.dirname(_CACHE_PATH), exist_ok=True)
  conn = sqlite3.connect(_CACHE_PATH, timeout=5)
  conn.execute("CREATE TABLE IF NOT EXISTS zh_cache "
               "(en TEXT PRIMARY KEY, zh TEXT)")
  return conn


def _cache_get_many(texts):
  found = {}
  try:
    with _lock, _cache_conn() as conn:
      for text in texts:
        row = conn.execute("SELECT zh FROM zh_cache WHERE en = ?",
                           (text,)).fetchone()
        if row:
          found[text] = row[0]
  except sqlite3.Error:
    pass
  return found


def _cache_set_many(pairs):
  try:
    with _lock, _cache_conn() as conn:
      conn.executemany(
        "INSERT OR REPLACE INTO zh_cache (en, zh) VALUES (?, ?)", pairs)
  except sqlite3.Error:
    pass


def _translate_batch(lines, api_key, base_url, model):
  """
  One LLM call for up to _BATCH_MAX_LINES lines. Returns a list the same
  length as <lines>, or None on any failure.
  """
  from openai import OpenAI
  client = OpenAI(api_key=api_key, base_url=base_url, timeout=30)
  numbered = "\n".join(f"{i}\t{line}" for i, line in enumerate(lines))
  response = client.chat.completions.create(
    model=model,
    temperature=0,
    response_format={"type": "json_object"},
    messages=[
      {"role": "system", "content":
       "你是翻譯器。把使用者給的每一行英文翻成自然的繁體中文（台灣用語）。"
       "人名保留英文原文；地名可以意譯。只回傳 JSON 物件："
       '{"lines": ["第0行譯文", "第1行譯文", ...]}，'
       "陣列長度必須與輸入行數相同，順序一致。"},
      {"role": "user", "content": numbered},
    ])
  data = json.loads(response.choices[0].message.content)
  result = data.get("lines")
  if not isinstance(result, list) or len(result) != len(lines):
    return None
  return [str(x) for x in result]


def translate_map(texts):
  """
  texts: iterable of strings. Returns {original: translated} for every
  input; untranslatable entries map to themselves.
  """
  unique = []
  seen = set()
  for text in texts:
    text = str(text or "").strip()
    if text and text not in seen:
      seen.add(text)
      unique.append(text)

  result = {t: t for t in unique}
  # Already-Chinese strings need no work.
  pending = [t for t in unique if not _CJK_RE.search(t)]
  cached = _cache_get_many(pending)
  result.update(cached)
  pending = [t for t in pending if t not in cached]
  if not pending:
    return result

  api_key, base_url, model = _llm_config()
  if not api_key:
    return result

  batch, batch_chars = [], 0
  batches = []
  for text in pending:
    if batch and (len(batch) >= _BATCH_MAX_LINES
                  or batch_chars + len(text) > _BATCH_MAX_CHARS):
      batches.append(batch)
      batch, batch_chars = [], 0
    batch.append(text)
    batch_chars += len(text)
  if batch:
    batches.append(batch)

  for batch in batches:
    try:
      translated = _translate_batch(batch, api_key, base_url, model)
    except Exception:
      translated = None
    if translated is None:
      continue
    pairs = list(zip(batch, translated))
    result.update(dict(pairs))
    _cache_set_many(pairs)
  return result
