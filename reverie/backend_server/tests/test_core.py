"""
Offline unit tests for the modernized LLM layer. No API key or network
access is required. Run from reverie/backend_server:

    python -m unittest discover tests
"""
import os
import sys
import tempfile
import unittest

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BACKEND_DIR)

_TMP = tempfile.mkdtemp(prefix="reverie_test_")
os.environ["LLM_CACHE_PATH"] = os.path.join(_TMP, "llm_cache.sqlite")
# Hermetic test environment: no API key and no stray llm_config.json may
# leak in, or the fail-safe tests would attempt real network calls.
os.environ["OPENAI_API_KEY"] = ""
os.environ["OPENAI_BASE_URL"] = ""
os.environ["LLM_CONFIG_PATH"] = os.path.join(_TMP, "no_such_config.json")

from persona.prompt_template import gpt_structure


class TestGeneratePrompt(unittest.TestCase):
  def _write_template(self, content):
    path = os.path.join(_TMP, "prompt.txt")
    with open(path, "w") as f:
      f.write(content)
    return path

  def test_single_input_substitution(self):
    path = self._write_template("Task: !<INPUT 0>! now")
    self.assertEqual(gpt_structure.generate_prompt("sleep", path),
                     "Task: sleep now")

  def test_multi_input_and_comment_block(self):
    path = self._write_template(
      "header notes\n<commentblockmarker>###</commentblockmarker>\n"
      "A=!<INPUT 0>! B=!<INPUT 1>!")
    self.assertEqual(gpt_structure.generate_prompt(["1", "2"], path),
                     "A=1 B=2")


class TestEngineMapping(unittest.TestCase):
  def test_legacy_engines_map_to_chat_model(self):
    for engine in ["text-davinci-003", "text-davinci-002", "davinci",
                   "gpt-3.5-turbo-instruct"]:
      self.assertEqual(gpt_structure._resolve_model(engine),
                       gpt_structure._CHAT_MODEL)

  def test_modern_model_passes_through(self):
    self.assertEqual(gpt_structure._resolve_model("gpt-4o"), "gpt-4o")

  def test_empty_engine_defaults(self):
    self.assertEqual(gpt_structure._resolve_model(None),
                     gpt_structure._CHAT_MODEL)


class TestCache(unittest.TestCase):
  def test_roundtrip(self):
    payload = {"model": "m", "prompt": "p", "temperature": 0}
    self.assertIsNone(gpt_structure.cache_get("chat", payload))
    gpt_structure.cache_set("chat", payload, "hello world")
    self.assertEqual(gpt_structure.cache_get("chat", payload), "hello world")

  def test_distinct_payloads_do_not_collide(self):
    a = {"model": "m", "prompt": "p1"}
    b = {"model": "m", "prompt": "p2"}
    gpt_structure.cache_set("chat", a, "A")
    gpt_structure.cache_set("chat", b, "B")
    self.assertEqual(gpt_structure.cache_get("chat", a), "A")
    self.assertEqual(gpt_structure.cache_get("chat", b), "B")

  def test_embedding_roundtrip(self):
    payload = {"model": "emb", "text": "hi"}
    gpt_structure.cache_set("embedding", payload, [0.1, 0.2])
    self.assertEqual(gpt_structure.cache_get("embedding", payload),
                     [0.1, 0.2])


class TestStats(unittest.TestCase):
  def test_usage_and_cost_accounting(self):
    gpt_structure._record_usage("gpt-4o-mini", 1_000_000, 1_000_000)
    gpt_structure._record_usage("gpt-4o-mini", 0, 0, cached=True)
    stats = gpt_structure.get_llm_stats()
    s = stats["models"]["gpt-4o-mini"]
    self.assertEqual(s["calls"], 2)
    self.assertEqual(s["cache_hits"], 1)
    self.assertEqual(s["prompt_tokens"], 1_000_000)
    # 1M in @ $0.15 + 1M out @ $0.60
    self.assertAlmostEqual(s["est_cost_usd"], 0.75, places=6)

  def test_format_does_not_crash(self):
    self.assertIsInstance(gpt_structure.format_llm_stats(), str)


class TestSafeGenerate(unittest.TestCase):
  def test_fail_safe_on_api_error(self):
    # With no API key configured, GPT_request must fall back gracefully and
    # safe_generate_response must return the fail-safe value.
    result = gpt_structure.safe_generate_response(
      "prompt", {"engine": "text-davinci-003"}, repeat=1,
      fail_safe_response="FAILSAFE",
      func_validate=lambda r, prompt: r not in ("TOKEN LIMIT EXCEEDED",),
      func_clean_up=lambda r, prompt: r)
    self.assertEqual(result, "FAILSAFE")


class TestConfigFilePriority(unittest.TestCase):
  """llm_config.json (written by the /settings web page) must override env
  vars, and be ignored gracefully when absent or malformed."""

  def _reload_utils(self):
    import importlib
    import utils
    return importlib.reload(utils)

  def tearDown(self):
    os.environ.pop("LLM_CONFIG_PATH", None)
    os.environ.pop("OPENAI_CHAT_MODEL", None)
    self._reload_utils()

  def test_file_overrides_env(self):
    cfg_path = os.path.join(_TMP, "llm_config.json")
    with open(cfg_path, "w") as f:
      f.write('{"api_key": "sk-from-file", "chat_model": "deepseek-chat",'
              ' "base_url": "https://api.deepseek.com"}')
    os.environ["LLM_CONFIG_PATH"] = cfg_path
    os.environ["OPENAI_CHAT_MODEL"] = "env-model"
    utils = self._reload_utils()
    self.assertEqual(utils.openai_api_key, "sk-from-file")
    self.assertEqual(utils.openai_chat_model, "deepseek-chat")
    self.assertEqual(utils.openai_base_url, "https://api.deepseek.com")

  def test_env_used_when_no_file(self):
    os.environ["LLM_CONFIG_PATH"] = os.path.join(_TMP, "missing.json")
    os.environ["OPENAI_CHAT_MODEL"] = "env-model"
    utils = self._reload_utils()
    self.assertEqual(utils.openai_chat_model, "env-model")

  def test_malformed_file_ignored(self):
    cfg_path = os.path.join(_TMP, "bad_config.json")
    with open(cfg_path, "w") as f:
      f.write("{not json")
    os.environ["LLM_CONFIG_PATH"] = cfg_path
    utils = self._reload_utils()
    self.assertEqual(utils.openai_chat_model, "gpt-4o-mini")

  def test_embedding_key_falls_back_to_chat_key(self):
    cfg_path = os.path.join(_TMP, "llm_config2.json")
    with open(cfg_path, "w") as f:
      f.write('{"api_key": "sk-chat-key"}')
    os.environ["LLM_CONFIG_PATH"] = cfg_path
    utils = self._reload_utils()
    self.assertEqual(utils.embedding_api_key, "sk-chat-key")

  def test_performance_knobs_from_file(self):
    cfg_path = os.path.join(_TMP, "llm_config3.json")
    with open(cfg_path, "w") as f:
      f.write('{"cost_limit_usd": "3.5", "llm_cache": "0",'
              ' "parallel_personas": "0", "checkpoint_freq": "25"}')
    os.environ["LLM_CONFIG_PATH"] = cfg_path
    utils = self._reload_utils()
    self.assertEqual(utils.cost_limit_usd, 3.5)
    self.assertFalse(utils.llm_cache_enabled)
    self.assertFalse(utils.parallel_personas)
    self.assertEqual(utils.checkpoint_freq, 25)

  def test_performance_knobs_defaults(self):
    os.environ["LLM_CONFIG_PATH"] = os.path.join(_TMP, "missing2.json")
    utils = self._reload_utils()
    self.assertEqual(utils.cost_limit_usd, 0.0)
    self.assertTrue(utils.llm_cache_enabled)
    self.assertTrue(utils.parallel_personas)
    self.assertEqual(utils.checkpoint_freq, 50)


class TestScenarioGenerator(unittest.TestCase):
  def _identity(self, **overrides):
    identity = {"innate": "brave, curious",
                "learned": "Kim is a baker.",
                "currently": "Kim is opening a shop.",
                "lifestyle": "Kim goes to bed around 11pm.",
                "daily_plan_req": "Kim bakes all morning.",
                "whispers": ["You love bread", "You distrust Bob"]}
    identity.update(overrides)
    return identity

  def test_validate_identity_accepts_complete(self):
    import scenario_generator as sg
    self.assertTrue(sg.validate_identity(self._identity()))

  def test_validate_identity_rejects_missing_or_empty(self):
    import scenario_generator as sg
    self.assertFalse(sg.validate_identity(self._identity(innate="")))
    bad = self._identity()
    del bad["whispers"]
    self.assertFalse(sg.validate_identity(bad))
    self.assertFalse(sg.validate_identity(self._identity(whispers=[])))
    self.assertFalse(sg.validate_identity("not a dict"))

  def test_apply_identity_merges_fields_only(self):
    import scenario_generator as sg
    scratch = {"name": "Kim", "innate": "old", "learned": "old",
               "currently": "old", "lifestyle": "old",
               "daily_plan_req": "old", "living_area": "the Ville:home"}
    sg.apply_identity(scratch, self._identity())
    self.assertEqual(scratch["innate"], "brave, curious")
    self.assertEqual(scratch["name"], "Kim")
    self.assertEqual(scratch["living_area"], "the Ville:home")

  def test_whispers_to_csv_rows(self):
    import scenario_generator as sg
    rows = sg.whispers_to_csv_rows([("Kim", self._identity())])
    self.assertEqual(rows[0], ["Name", "Whisper"])
    self.assertEqual(rows[1][0], "Kim")
    self.assertEqual(rows[1][1], "You love bread; You distrust Bob")

  def test_prompt_includes_story_and_names(self):
    import scenario_generator as sg
    prompt = sg.build_identity_prompt("A bakery rivalry", "Kim",
                                      ["Bob"], self._identity())
    self.assertIn("A bakery rivalry", prompt)
    self.assertIn("Kim", prompt)
    self.assertIn("Bob", prompt)


if __name__ == "__main__":
  unittest.main()
