"""
Offline unit tests for the modernized LLM layer. No API key or network
access is required. Run from reverie/backend_server:

    python -m unittest discover tests
"""
import json
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


class _FakeScratch:
  def __init__(self, name):
    self.name = name
    self.curr_time = __import__("datetime").datetime(2023, 2, 13, 9, 0, 0)


class _FakePersona:
  def __init__(self, name):
    self.scratch = _FakeScratch(name)


class TestEventManager(unittest.TestCase):
  def setUp(self):
    import datetime
    self.sim_folder = tempfile.mkdtemp(prefix="evt_sim_")
    os.makedirs(f"{self.sim_folder}/reverie", exist_ok=True)
    self.temp_storage = tempfile.mkdtemp(prefix="evt_tmp_")
    self.whispers = []
    self.now = datetime.datetime(2023, 2, 13, 9, 0, 0)
    self.personas = {n: _FakePersona(n)
                     for n in ["Ana", "Bo", "Cy", "Di"]}

    import events
    self.manager = events.EventManager(
      self.sim_folder, self.temp_storage,
      whisper_fn=lambda personas, name, text:
        self.whispers.append((name, text)),
      vote_fn=lambda persona, candidates:
        {"vote": candidates[0], "reason": "test reason"})

  def tearDown(self):
    import shutil
    shutil.rmtree(self.sim_folder, ignore_errors=True)
    shutil.rmtree(self.temp_storage, ignore_errors=True)

  def test_tally_votes(self):
    from events import tally_votes
    votes = {"a": {"vote": "X"}, "b": {"vote": "X"}, "c": {"vote": "Y"},
             "d": {"vote": "nobody"}, "e": {"vote": None}}
    counts, winners = tally_votes(votes, ["X", "Y"])
    self.assertEqual(counts, {"X": 2, "Y": 1})
    self.assertEqual(winners, ["X"])

  def test_tally_tie(self):
    from events import tally_votes
    counts, winners = tally_votes(
      {"a": {"vote": "X"}, "b": {"vote": "Y"}}, ["X", "Y"])
    self.assertEqual(sorted(winners), ["X", "Y"])

  def test_add_save_load_roundtrip(self):
    import events
    self.manager.add_event({"type": "broadcast", "text": "hello",
                            "days_from_now": 1}, self.now)
    reloaded = events.EventManager(self.sim_folder)
    self.assertEqual(len(reloaded.events), 1)
    self.assertEqual(reloaded.events[0]["text"], "hello")

  def test_broadcast_fires_when_due_and_one_shot_removes(self):
    self.manager.add_event({"type": "broadcast", "text": "market day",
                            "target": "all"}, self.now)
    self.manager.check(self.now, self.personas)
    self.assertEqual(len(self.whispers), 4)
    self.assertIn(("Ana", "market day"), self.whispers)
    self.assertEqual(self.manager.events, [])

  def test_broadcast_not_due_does_not_fire(self):
    self.manager.add_event({"type": "broadcast", "text": "later",
                            "days_from_now": 2}, self.now)
    self.manager.check(self.now, self.personas)
    self.assertEqual(self.whispers, [])
    self.assertEqual(len(self.manager.events), 1)

  def test_broadcast_recurrence_reschedules(self):
    import datetime
    self.manager.add_event({"type": "broadcast", "text": "weekly",
                            "every_days": 7}, self.now)
    self.manager.check(self.now, self.personas)
    self.assertEqual(len(self.manager.events), 1)
    from events import _dt
    self.assertEqual(_dt(self.manager.events[0]["fire_at"]),
                     self.now + datetime.timedelta(days=7))

  def test_broadcast_random_target_count(self):
    self.manager.add_event({"type": "broadcast", "text": "psst",
                            "target": "random:2"}, self.now)
    self.manager.check(self.now, self.personas)
    self.assertEqual(len(self.whispers), 2)

  def test_election_full_cycle(self):
    import datetime
    self.manager.add_event({"type": "election", "campaign_days": 1,
                            "candidates": ["Ana", "Bo"]}, self.now)
    # Announce fires now: everyone hears about it, candidates campaign.
    self.manager.check(self.now, self.personas)
    self.assertEqual(len(self.whispers), 4)
    candidate_whispers = [t for n, t in self.whispers
                          if "running for mayor" in t and "You are" in t]
    self.assertEqual(len(candidate_whispers), 2)
    event = self.manager.events[0]
    self.assertEqual(event["phase"], "vote")

    # Vote fires one game day later: everyone voted Ana (mock vote_fn).
    self.whispers.clear()
    vote_day = self.now + datetime.timedelta(days=1)
    self.manager.check(vote_day, self.personas)
    self.assertEqual(self.manager.events, [])  # one-shot: removed
    self.assertEqual(len(self.whispers), 4)
    winner_whispers = [t for n, t in self.whispers
                       if n == "Ana" and "You won" in t]
    self.assertEqual(len(winner_whispers), 1)

  def test_recurring_election_resets_phase(self):
    import datetime
    self.manager.add_event({"type": "election", "campaign_days": 1,
                            "candidates": ["Ana", "Bo"],
                            "every_days": 7}, self.now)
    self.manager.check(self.now, self.personas)                # announce
    self.manager.check(self.now + datetime.timedelta(days=1),
                       self.personas)                          # vote
    self.assertEqual(len(self.manager.events), 1)
    event = self.manager.events[0]
    self.assertEqual(event["phase"], "announce")
    self.assertNotIn("resolved_candidates", event)
    self.assertEqual(len(event["history"]), 1)
    self.assertEqual(event["history"][0]["winners"], ["Ana"])

  def test_web_command_legacy_file_processed(self):
    with open(f"{self.temp_storage}/event_command.json", "w") as f:
      json.dump([{"action": "add",
                  "spec": {"type": "broadcast", "text": "from web",
                           "days_from_now": "0.5"}}], f)
    self.manager.check(self.now, self.personas)
    self.assertEqual(len(self.manager.events), 1)
    self.assertFalse(
      os.path.exists(f"{self.temp_storage}/event_command.json"))

  def test_web_command_dir_processed(self):
    commands_dir = f"{self.temp_storage}/event_commands"
    os.makedirs(commands_dir, exist_ok=True)
    with open(f"{commands_dir}/abc.json", "w") as f:
      json.dump({"action": "add",
                 "spec": {"type": "broadcast", "text": "from dir",
                          "days_from_now": "0.5"}}, f)
    self.manager.check(self.now, self.personas)
    self.assertEqual(len(self.manager.events), 1)
    self.assertEqual(self.manager.events[0]["text"], "from dir")
    self.assertEqual(os.listdir(commands_dir), [])

  def test_election_bad_candidates_falls_back_to_random(self):
    self.manager.add_event({"type": "election", "campaign_days": 1,
                            "candidates": ["Ana", "Nobody Real"]},
                           self.now)
    self.manager.check(self.now, self.personas)
    event = self.manager.events[0]
    self.assertEqual(event["phase"], "vote")
    self.assertEqual(len(event["resolved_candidates"]), 2)
    for candidate in event["resolved_candidates"]:
      self.assertIn(candidate, self.personas)


class TestChronicle(unittest.TestCase):
  def setUp(self):
    self.sim_folder = tempfile.mkdtemp(prefix="chr_sim_")
    os.makedirs(f"{self.sim_folder}/movement", exist_ok=True)

  def tearDown(self):
    import shutil
    shutil.rmtree(self.sim_folder, ignore_errors=True)

  def _write_step(self, step, hhmmss, personas):
    data = {"persona": personas,
            "meta": {"curr_time": f"February 13, 2023, {hhmmss}"}}
    with open(f"{self.sim_folder}/movement/{step}.json", "w") as f:
      json.dump(data, f)

  def test_collect_day_log_dedupes_and_captures_chat(self):
    import chronicle
    self._write_step(0, "08:00:00", {
      "Ana": {"movement": [1, 1], "description": "making coffee @ cafe",
              "chat": None}})
    self._write_step(1, "08:00:10", {
      "Ana": {"movement": [1, 2], "description": "making coffee @ cafe",
              "chat": [["Ana", "Good morning!"], ["Bo", "Morning, Ana."]]}})
    self._write_step(2, "08:00:20", {
      "Ana": {"movement": [1, 3], "description": "serving customers @ cafe",
              "chat": None}})
    digest, date_str = chronicle.collect_day_log(self.sim_folder, 0, 2)
    self.assertEqual(date_str, "February 13, 2023")
    # consecutive identical actions collapse to one entry
    self.assertEqual(digest.count("making coffee"), 1)
    self.assertIn("serving customers", digest)
    self.assertIn("Ana: Good morning!", digest)

  def test_collect_day_log_empty_range(self):
    import chronicle
    digest, date_str = chronicle.collect_day_log(self.sim_folder, 0, 5)
    self.assertEqual((digest, date_str), ("", ""))

  def test_generate_chronicle_writes_dated_markdown(self):
    import chronicle
    self._write_step(0, "09:00:00", {
      "Ana": {"movement": [1, 1], "description": "jogging @ park",
              "chat": None}})
    captured = {}
    def fake_llm(prompt):
      captured["prompt"] = prompt
      return "# 小鎮日報\n今天 Ana 去慢跑了。"
    path = chronicle.generate_chronicle(self.sim_folder, 0, 0,
                                        lang="繁體中文", llm_fn=fake_llm)
    self.assertTrue(path.endswith("2023-02-13.md"))
    self.assertIn("jogging", captured["prompt"])
    self.assertIn("繁體中文", captured["prompt"])
    with open(path) as f:
      self.assertIn("慢跑", f.read())

  def test_generate_chronicle_none_when_no_logs(self):
    import chronicle
    self.assertIsNone(chronicle.generate_chronicle(
      self.sim_folder, 0, 3, llm_fn=lambda p: "x"))


class TestHeadlessEnvironment(unittest.TestCase):
  def test_write_headless_environment(self):
    import reverie
    sim_folder = tempfile.mkdtemp(prefix="hl_sim_")
    os.makedirs(f"{sim_folder}/environment", exist_ok=True)
    movements = {"persona": {
      "Ana": {"movement": [10, 20], "pronunciatio": "x",
              "description": "d", "chat": None},
      "Bo": {"movement": [3, 4], "pronunciatio": "y",
             "description": "d", "chat": None}}}
    reverie.write_headless_environment(sim_folder, 7, movements)
    env = json.load(open(f"{sim_folder}/environment/7.json"))
    self.assertEqual(env["Ana"], {"maze": "the_ville", "x": 10, "y": 20})
    self.assertEqual(env["Bo"]["x"], 3)
    import shutil
    shutil.rmtree(sim_folder, ignore_errors=True)


class TestTraits(unittest.TestCase):
  def setUp(self):
    import traits
    self.traits = traits
    self.library = traits.load_trait_library()

  def test_library_size_and_integrity(self):
    ids = [t["id"] for t in self.library]
    self.assertEqual(len(self.library), 100)
    self.assertEqual(len(ids), len(set(ids)))
    for t in self.library:
      self.assertIn(t["polarity"], ("positive", "negative", "quirk"))
      self.assertTrue(t["behavior"])
      for c in t["conflicts"]:
        self.assertIn(c, ids)

  def test_draw_respects_counts_and_conflicts(self):
    import random as random_module
    rng = random_module.Random(42)
    by_id = {t["id"]: t for t in self.library}
    for _ in range(50):
      hand = self.traits.draw_traits(self.library, rng=rng)
      self.assertEqual(len(hand), 4)  # 2 positive + 1 negative + 1 quirk
      ids = [t["id"] for t in hand]
      self.assertEqual(len(ids), len(set(ids)))
      for t in hand:
        for c in t["conflicts"]:
          self.assertNotIn(c, ids,
                           f"conflicting pair drawn: {t['id']} + {c}")
      polarities = [t["polarity"] for t in hand]
      self.assertEqual(polarities.count("positive"), 2)
      self.assertEqual(polarities.count("negative"), 1)

  def test_relationships_rules(self):
    import random as random_module
    rng = random_module.Random(7)
    names = ["Ana Lin", "Bo Lin", "Cy Park", "Di Park", "Ed Moore",
             "Fay Chen", "Gil Ortiz", "Hana Sato", "Ivo Reyes",
             "Joy Walsh", "Kai Doyle", "Lia Marsh"]
    relationships = self.traits.generate_relationships(names, rng=rng)
    partnered = []
    pairs = set()
    for r in relationships:
      self.assertIn(r["type"], self.traits.REL_TYPES)
      pair = frozenset((r["a"], r["b"]))
      self.assertNotIn(pair, pairs, "pair holds two relationships")
      pairs.add(pair)
      same_surname = (r["a"].split()[-1] == r["b"].split()[-1])
      if r["type"] in ("partner", "crush"):
        self.assertFalse(same_surname, "romance within a family")
      if r["type"] == "sibling":
        self.assertTrue(same_surname)
      if r["type"] == "partner":
        partnered += [r["a"], r["b"]]
    self.assertEqual(len(partnered), len(set(partnered)),
                     "someone has two partners")

  def test_crush_whisper_is_one_way(self):
    whispers = self.traits.relationship_whispers(
      {"type": "crush", "a": "Ana", "b": "Bo"})
    self.assertEqual(len(whispers), 1)
    self.assertEqual(whispers[0][0], "Ana")
    self.assertIn("secret crush on Bo", whispers[0][1])

  def test_assign_to_sim_and_idempotence(self):
    import random as random_module
    import events
    sim_folder = tempfile.mkdtemp(prefix="tr_sim_")
    os.makedirs(f"{sim_folder}/reverie", exist_ok=True)
    for name in ["Kim Tester", "Lee Tester", "Mo Vance"]:
      d = f"{sim_folder}/personas/{name}/bootstrap_memory"
      os.makedirs(d, exist_ok=True)
      with open(f"{d}/scratch.json", "w") as f:
        json.dump({"name": name, "innate": "kind"}, f)
    with open(f"{sim_folder}/reverie/meta.json", "w") as f:
      json.dump({"persona_names": ["Kim Tester", "Lee Tester",
                                   "Mo Vance"]}, f)

    manager = events.EventManager(sim_folder,
                                  whisper_fn=lambda *a: None)
    registry, relationships = self.traits.assign_to_sim(
      sim_folder, manager, rng=random_module.Random(3))
    self.assertEqual(len(registry), 3)
    scratch = json.load(open(f"{sim_folder}/personas/Kim Tester/"
                             f"bootstrap_memory/scratch.json"))
    self.assertTrue(scratch["innate"].startswith("kind, "))
    self.assertEqual(len(scratch["innate"].split(",")), 1 + 4)
    # one seed-whisper broadcast event queued per persona
    seed_events = [e for e in manager.events
                   if e["label"].startswith("persona seed")]
    self.assertEqual(len(seed_events), 3)
    # second call is a no-op (registry already exists)
    registry2, _ = self.traits.assign_to_sim(sim_folder, manager)
    self.assertEqual(registry, registry2)
    self.assertEqual(len([e for e in manager.events
                          if e["label"].startswith("persona seed")]), 3)
    import shutil
    shutil.rmtree(sim_folder, ignore_errors=True)


class TestEconomy(unittest.TestCase):
  def setUp(self):
    import datetime
    from economy import EconomyManager
    self.sim_folder = tempfile.mkdtemp(prefix="eco_sim_")
    os.makedirs(f"{self.sim_folder}/reverie", exist_ok=True)
    self.now = datetime.datetime(2023, 2, 13, 12, 0, 0)
    self.whispers = []
    self.trade_responses = []
    self.manager = EconomyManager(
      self.sim_folder,
      whisper_fn=lambda personas, name, text:
        self.whispers.append((name, text)),
      trade_llm_fn=lambda prompt, cache_read:
        self.trade_responses.pop(0))
    self.personas = {"Ana": object(), "Bo": object()}

  def tearDown(self):
    import shutil
    shutil.rmtree(self.sim_folder, ignore_errors=True)

  def _mv(self, desc, chat=None):
    return {"movement": [0, 0], "pronunciatio": "", "description": desc,
            "chat": chat}

  def test_venue_charge_on_consumption_only(self):
    movements = {
      "Ana": self._mv("drinking coffee @ the Ville:Hobbs Cafe:cafe"),
      "Bo": self._mv("working at the counter @ the Ville:Hobbs Cafe:cafe")}
    self.manager.on_step(self.personas, movements, self.now)
    self.assertEqual(self.manager.balances["Ana"], 92.0)   # -8 coffee
    self.assertEqual(self.manager.balances["Bo"], 100.0)   # working: free

  def test_same_action_charged_once(self):
    movements = {
      "Ana": self._mv("eating lunch @ the Ville:Hobbs Cafe:cafe")}
    self.manager.on_step(self.personas, movements, self.now)
    self.manager.on_step(self.personas, movements, self.now)
    self.assertEqual(self.manager.balances["Ana"], 92.0)

  def test_daily_wage_once_per_day(self):
    self.manager.on_new_day(self.now, self.personas)
    self.manager.on_new_day(self.now, self.personas)
    self.assertEqual(self.manager.balances["Ana"], 180.0)

  def test_trade_applied_and_whispered(self):
    self.trade_responses = [json.dumps(
      {"trade": True, "payer": "Ana", "payee": "Bo", "amount": 20,
       "note": "Ana lent Bo twenty"})]
    chat = [["Ana", "Here, take $20 until payday."],
            ["Bo", "Thanks, I'll pay you back!"]]
    movements = {"Ana": self._mv("chatting", chat)}
    self.manager.on_step(self.personas, movements, self.now,
                         {"Ana": ["generous"], "Bo": ["freeloading"]})
    self.assertEqual(self.manager.balances["Ana"], 80.0)
    self.assertEqual(self.manager.balances["Bo"], 120.0)
    self.assertEqual(len([w for w in self.whispers if w[0] == "Ana"]), 1)

  def test_same_chat_evaluated_once(self):
    self.trade_responses = [json.dumps({"trade": False}),
                            json.dumps({"trade": True, "payer": "Ana",
                                        "payee": "Bo", "amount": 5,
                                        "note": "x"})]
    chat = [["Ana", "hello"], ["Bo", "hi"]]
    movements = {"Ana": self._mv("chatting", chat)}
    self.manager.on_step(self.personas, movements, self.now)
    self.manager.on_step(self.personas, movements, self.now)
    self.assertEqual(len(self.trade_responses), 1)  # second never consumed
    self.assertEqual(self.manager.balances["Ana"], 100.0)

  def test_poverty_whisper_fires_once(self):
    self.manager.balances = {"Ana": 5.0, "Bo": 100.0}
    self.manager.on_step(self.personas, {}, self.now)
    self.manager.on_step(self.personas, {}, self.now)
    poverty = [w for w in self.whispers if "low on money" in w[1]]
    self.assertEqual(len(poverty), 1)
    self.assertEqual(poverty[0][0], "Ana")

  def test_persistence_roundtrip(self):
    from economy import EconomyManager
    self.manager.balances = {"Ana": 55.5}
    self.manager._log(self.now, "trade", "Ana", "Bo", 5, "test")
    self.manager.save()
    reloaded = EconomyManager(self.sim_folder,
                              whisper_fn=lambda *a: None,
                              trade_llm_fn=lambda *a: "{}")
    self.assertEqual(reloaded.balances["Ana"], 55.5)
    self.assertEqual(len(reloaded.transactions), 1)


if __name__ == "__main__":
  unittest.main()
