"""
Author: Joon Sung Park (joonspk@stanford.edu)

File: gpt_structure.py
Description: Wrapper functions for calling LLM APIs.

Modernized to the openai>=1.0 SDK. Adds:
  - configurable models / base_url (works with OpenAI, Ollama, or any
    OpenAI-compatible endpoint such as Anthropic's compatibility API)
  - persistent on-disk response + embedding cache (sqlite)
  - retry with exponential backoff instead of bare except
  - JSON mode for structured responses where the provider supports it
  - token/cost accounting exposed via get_llm_stats()/format_llm_stats()
"""
import hashlib
import json
import os
import random
import sqlite3
import threading
import time

from openai import OpenAI

from utils import *

# ============================================================================
# ########################[SECTION 0: CONFIG / CLIENT]########################
# ============================================================================

# All of these can be overridden in utils.py (which reads env vars); the
# globals().get() calls keep older hand-written utils.py files working.
_CHAT_MODEL = globals().get("openai_chat_model", "gpt-4o-mini")
_SMART_CHAT_MODEL = globals().get("openai_smart_chat_model", _CHAT_MODEL)
_EMBEDDING_MODEL = globals().get("openai_embedding_model",
                                 "text-embedding-3-small")
_BASE_URL = globals().get("openai_base_url", None) or None
_CACHE_ENABLED = globals().get("llm_cache_enabled", True)
_CACHE_PATH = globals().get("llm_cache_path", "") or os.path.join(
  os.path.dirname(os.path.abspath(__file__)), "..", "..", "llm_cache.sqlite")

# Legacy Completions engines that appear throughout run_gpt_prompt.py are
# retired; every one of them is served by the configured chat model instead.
_LEGACY_ENGINE_MAP_PREFIXES = ("text-davinci", "davinci", "text-curie",
                               "curie", "text-babbage", "babbage",
                               "text-ada", "ada", "gpt-3.5-turbo-instruct")

# USD per 1M tokens: (input, output). Embeddings only have an input price.
_PRICE_TABLE = {
  "gpt-4o-mini": (0.15, 0.60),
  "gpt-4o": (2.50, 10.00),
  "gpt-4.1-mini": (0.40, 1.60),
  "gpt-4.1": (2.00, 8.00),
  "text-embedding-3-small": (0.02, 0.0),
  "text-embedding-3-large": (0.13, 0.0),
}
_PRICE_TABLE.update(globals().get("llm_price_table", {}))

_client = None
_client_lock = threading.Lock()


def _get_client():
  global _client
  with _client_lock:
    if _client is None:
      api_key = globals().get("openai_api_key", "") or None
      if not api_key and not _BASE_URL:
        raise RuntimeError(
          "No API key configured. Set the OPENAI_API_KEY environment "
          "variable (see reverie/backend_server/utils.py).")
      _client = OpenAI(api_key=api_key or "not-needed", base_url=_BASE_URL)
    return _client


def _resolve_model(engine):
  if not engine:
    return _CHAT_MODEL
  if engine.startswith(_LEGACY_ENGINE_MAP_PREFIXES):
    return _CHAT_MODEL
  return engine


# ============================================================================
# #####################[SECTION 0.5: CACHE + METRICS]#########################
# ============================================================================

_cache_lock = threading.Lock()
_cache_conn = None


def _get_cache():
  global _cache_conn
  if not _CACHE_ENABLED:
    return None
  if _cache_conn is None:
    os.makedirs(os.path.dirname(os.path.abspath(_CACHE_PATH)), exist_ok=True)
    _cache_conn = sqlite3.connect(_CACHE_PATH, check_same_thread=False)
    _cache_conn.execute(
      "CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT)")
    _cache_conn.commit()
  return _cache_conn


def _cache_key(kind, payload):
  raw = json.dumps([kind, payload], sort_keys=True, ensure_ascii=False)
  return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def cache_get(kind, payload):
  with _cache_lock:
    conn = _get_cache()
    if conn is None:
      return None
    row = conn.execute("SELECT value FROM kv WHERE key = ?",
                       (_cache_key(kind, payload),)).fetchone()
  return json.loads(row[0]) if row else None


def cache_set(kind, payload, value):
  with _cache_lock:
    conn = _get_cache()
    if conn is None:
      return
    conn.execute("INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)",
                 (_cache_key(kind, payload), json.dumps(value,
                                                        ensure_ascii=False)))
    conn.commit()


_stats_lock = threading.Lock()
_stats = {}


def _record_usage(model, prompt_tokens, completion_tokens,
                  cached=False, failed=False):
  with _stats_lock:
    s = _stats.setdefault(model, {"calls": 0, "cache_hits": 0, "failures": 0,
                                  "prompt_tokens": 0, "completion_tokens": 0})
    s["calls"] += 1
    if cached:
      s["cache_hits"] += 1
    if failed:
      s["failures"] += 1
    s["prompt_tokens"] += prompt_tokens
    s["completion_tokens"] += completion_tokens


def get_llm_stats():
  with _stats_lock:
    out = {m: dict(v) for m, v in _stats.items()}
  total_cost = 0.0
  for model, s in out.items():
    inp, outp = _PRICE_TABLE.get(model, (0.0, 0.0))
    s["est_cost_usd"] = round(s["prompt_tokens"] / 1e6 * inp
                              + s["completion_tokens"] / 1e6 * outp, 6)
    total_cost += s["est_cost_usd"]
  return {"models": out, "est_total_cost_usd": round(total_cost, 6)}


def format_llm_stats():
  stats = get_llm_stats()
  if not stats["models"]:
    return "No LLM calls made yet."
  lines = []
  for model, s in stats["models"].items():
    lines += [f"{model}:"]
    lines += [f"  calls: {s['calls']}  (cache hits: {s['cache_hits']}, "
              f"failures: {s['failures']})"]
    lines += [f"  tokens: {s['prompt_tokens']} in / "
              f"{s['completion_tokens']} out"]
    lines += [f"  est. cost: ${s['est_cost_usd']}"]
  lines += [f"TOTAL est. cost: ${stats['est_total_cost_usd']}"]
  return "\n".join(lines)


def save_llm_stats(path):
  with open(path, "w") as f:
    f.write(json.dumps(get_llm_stats(), indent=2))


# ============================================================================
# ####################[SECTION 0.75: CORE CHAT REQUEST]#######################
# ============================================================================

_MAX_RETRIES = 5

# The old prompts were written for text-completion models, so we pin a system
# message that keeps chat models from adding conversational filler.
_SYSTEM_MSG = ("Follow the given instructions or continue the given text "
               "directly. Output only what is requested -- no explanations, "
               "no extra commentary, no markdown fences.")


def _chat_request(prompt, model=None, temperature=None, max_tokens=None,
                  top_p=None, stop=None, json_mode=False):
  """
  Single entry point for every chat-model call. Handles caching, retry with
  exponential backoff, JSON mode fallback, and usage accounting. Raises the
  last API error if all retries fail.
  """
  model = _resolve_model(model)
  payload = {"model": model, "prompt": prompt, "temperature": temperature,
             "max_tokens": max_tokens, "top_p": top_p, "stop": stop,
             "json_mode": json_mode}

  cached = cache_get("chat", payload)
  if cached is not None:
    _record_usage(model, 0, 0, cached=True)
    return cached

  kwargs = {"model": model,
            "messages": [{"role": "system", "content": _SYSTEM_MSG},
                         {"role": "user", "content": prompt}]}
  if temperature is not None:
    kwargs["temperature"] = temperature
  if max_tokens is not None:
    kwargs["max_tokens"] = max_tokens
  if top_p is not None:
    kwargs["top_p"] = top_p
  if stop:
    kwargs["stop"] = stop
  if json_mode:
    kwargs["response_format"] = {"type": "json_object"}

  # Configuration errors (e.g. missing API key) are raised immediately;
  # only transient API errors go through the retry loop.
  client = _get_client()

  last_err = None
  for attempt in range(_MAX_RETRIES):
    try:
      completion = client.chat.completions.create(**kwargs)
      output = completion.choices[0].message.content
      usage = getattr(completion, "usage", None)
      _record_usage(model,
                    getattr(usage, "prompt_tokens", 0) or 0,
                    getattr(usage, "completion_tokens", 0) or 0)
      cache_set("chat", payload, output)
      return output
    except Exception as e:
      last_err = e
      # Some OpenAI-compatible servers reject response_format; drop it once.
      if json_mode and "response_format" in kwargs and (
          "response_format" in str(e)):
        del kwargs["response_format"]
        continue
      time.sleep(2 ** attempt + random.random())
  _record_usage(model, 0, 0, failed=True)
  raise last_err


def temp_sleep(seconds=0.1):
  time.sleep(seconds)


def ChatGPT_single_request(prompt):
  try:
    return _chat_request(prompt)
  except Exception:
    print("ChatGPT ERROR")
    return "ChatGPT ERROR"


# ============================================================================
# #####################[SECTION 1: CHATGPT-3 STRUCTURE] ######################
# ============================================================================

def GPT4_request(prompt):
  """
  Make a request to the configured high-quality chat model.
  ARGS:
    prompt: a str prompt
  RETURNS:
    a str of the model's response.
  """
  try:
    return _chat_request(prompt, model=_SMART_CHAT_MODEL)
  except Exception:
    print("ChatGPT ERROR")
    return "ChatGPT ERROR"


def ChatGPT_request(prompt):
  """
  Make a request to the configured default chat model.
  ARGS:
    prompt: a str prompt
  RETURNS:
    a str of the model's response.
  """
  try:
    return _chat_request(prompt)
  except Exception:
    print("ChatGPT ERROR")
    return "ChatGPT ERROR"


def _json_safe_generate_response(request_model,
                                 prompt,
                                 example_output,
                                 special_instruction,
                                 repeat=3,
                                 fail_safe_response="error",
                                 func_validate=None,
                                 func_clean_up=None,
                                 verbose=False):
  prompt = '"""\n' + prompt + '\n"""\n'
  prompt += ("Output the response to the prompt above in json. "
             f"{special_instruction}\n")
  prompt += "Example output json:\n"
  prompt += '{"output": "' + str(example_output) + '"}'

  if verbose:
    print("CHAT GPT PROMPT")
    print(prompt)

  for i in range(repeat):
    try:
      curr_gpt_response = _chat_request(prompt, model=request_model,
                                        json_mode=True).strip()
      end_index = curr_gpt_response.rfind('}') + 1
      curr_gpt_response = curr_gpt_response[:end_index]
      curr_gpt_response = json.loads(curr_gpt_response)["output"]

      if func_validate(curr_gpt_response, prompt=prompt):
        return func_clean_up(curr_gpt_response, prompt=prompt)

      if verbose:
        print("---- repeat count: \n", i, curr_gpt_response)
        print(curr_gpt_response)
        print("~~~~")

    except Exception:
      pass

  return False


def GPT4_safe_generate_response(prompt,
                                example_output,
                                special_instruction,
                                repeat=3,
                                fail_safe_response="error",
                                func_validate=None,
                                func_clean_up=None,
                                verbose=False):
  return _json_safe_generate_response(_SMART_CHAT_MODEL, prompt,
                                      example_output, special_instruction,
                                      repeat, fail_safe_response,
                                      func_validate, func_clean_up, verbose)


def ChatGPT_safe_generate_response(prompt,
                                   example_output,
                                   special_instruction,
                                   repeat=3,
                                   fail_safe_response="error",
                                   func_validate=None,
                                   func_clean_up=None,
                                   verbose=False):
  return _json_safe_generate_response(_CHAT_MODEL, prompt,
                                      example_output, special_instruction,
                                      repeat, fail_safe_response,
                                      func_validate, func_clean_up, verbose)


def ChatGPT_safe_generate_response_OLD(prompt,
                                       repeat=3,
                                       fail_safe_response="error",
                                       func_validate=None,
                                       func_clean_up=None,
                                       verbose=False):
  if verbose:
    print("CHAT GPT PROMPT")
    print(prompt)

  for i in range(repeat):
    try:
      curr_gpt_response = ChatGPT_request(prompt).strip()
      if func_validate(curr_gpt_response, prompt=prompt):
        return func_clean_up(curr_gpt_response, prompt=prompt)
      if verbose:
        print(f"---- repeat count: {i}")
        print(curr_gpt_response)
        print("~~~~")

    except Exception:
      pass
  print("FAIL SAFE TRIGGERED")
  return fail_safe_response


# ============================================================================
# ###################[SECTION 2: ORIGINAL GPT-3 STRUCTURE] ###################
# ============================================================================

def GPT_request(prompt, gpt_parameter):
  """
  Legacy entry point used by run_gpt_prompt.py. The retired Completions
  engines named in gpt_parameter["engine"] are transparently served by the
  configured chat model; sampling parameters are passed through.
  ARGS:
    prompt: a str prompt
    gpt_parameter: a python dictionary with the keys indicating the names of
                   the parameter and the values indicating the parameter
                   values.
  RETURNS:
    a str of the model's response.
  """
  try:
    return _chat_request(
      prompt,
      model=gpt_parameter.get("engine"),
      temperature=gpt_parameter.get("temperature"),
      max_tokens=gpt_parameter.get("max_tokens"),
      top_p=gpt_parameter.get("top_p"),
      stop=gpt_parameter.get("stop"))
  except Exception:
    print("TOKEN LIMIT EXCEEDED")
    return "TOKEN LIMIT EXCEEDED"


def generate_prompt(curr_input, prompt_lib_file):
  """
  Takes in the current input (e.g. comment that you want to classifiy) and
  the path to a prompt file. The prompt file contains the raw str prompt that
  will be used, which contains the following substr: !<INPUT>! -- this
  function replaces this substr with the actual curr_input to produce the
  final promopt that will be sent to the GPT3 server.
  ARGS:
    curr_input: the input we want to feed in (IF THERE ARE MORE THAN ONE
                INPUT, THIS CAN BE A LIST.)
    prompt_lib_file: the path to the promopt file.
  RETURNS:
    a str prompt that will be sent to OpenAI's GPT server.
  """
  if type(curr_input) == type("string"):
    curr_input = [curr_input]
  curr_input = [str(i) for i in curr_input]

  f = open(prompt_lib_file, "r")
  prompt = f.read()
  f.close()
  for count, i in enumerate(curr_input):
    prompt = prompt.replace(f"!<INPUT {count}>!", i)
  if "<commentblockmarker>###</commentblockmarker>" in prompt:
    prompt = prompt.split("<commentblockmarker>###</commentblockmarker>")[1]
  return prompt.strip()


def safe_generate_response(prompt,
                           gpt_parameter,
                           repeat=5,
                           fail_safe_response="error",
                           func_validate=None,
                           func_clean_up=None,
                           verbose=False):
  if verbose:
    print(prompt)

  for i in range(repeat):
    curr_gpt_response = GPT_request(prompt, gpt_parameter)
    if func_validate(curr_gpt_response, prompt=prompt):
      return func_clean_up(curr_gpt_response, prompt=prompt)
    if verbose:
      print("---- repeat count: ", i, curr_gpt_response)
      print(curr_gpt_response)
      print("~~~~")
  return fail_safe_response


def get_embedding(text, model=None):
  model = model or _EMBEDDING_MODEL
  text = text.replace("\n", " ")
  if not text:
    text = "this is blank"

  payload = {"model": model, "text": text}
  cached = cache_get("embedding", payload)
  if cached is not None:
    _record_usage(model, 0, 0, cached=True)
    return cached

  client = _get_client()

  last_err = None
  for attempt in range(_MAX_RETRIES):
    try:
      response = client.embeddings.create(input=[text], model=model)
      embedding = response.data[0].embedding
      usage = getattr(response, "usage", None)
      _record_usage(model, getattr(usage, "prompt_tokens", 0) or 0, 0)
      cache_set("embedding", payload, embedding)
      return embedding
    except Exception as e:
      last_err = e
      time.sleep(2 ** attempt + random.random())
  _record_usage(model, 0, 0, failed=True)
  raise last_err


if __name__ == '__main__':
  gpt_parameter = {"engine": "text-davinci-003", "max_tokens": 50,
                   "temperature": 0, "top_p": 1, "stream": False,
                   "frequency_penalty": 0, "presence_penalty": 0,
                   "stop": ['"']}
  curr_input = ["driving to a friend's house"]
  prompt_lib_file = "prompt_template/test_prompt_July5.txt"
  prompt = generate_prompt(curr_input, prompt_lib_file)

  def __func_validate(gpt_response, prompt=""):
    if len(gpt_response.strip()) <= 1:
      return False
    if len(gpt_response.strip().split(" ")) > 1:
      return False
    return True

  def __func_clean_up(gpt_response, prompt=""):
    cleaned_response = gpt_response.strip()
    return cleaned_response

  output = safe_generate_response(prompt,
                                  gpt_parameter,
                                  5,
                                  "rest",
                                  __func_validate,
                                  __func_clean_up,
                                  True)

  print(output)
