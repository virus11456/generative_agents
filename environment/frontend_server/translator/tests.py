"""
Tests for the LLM provider settings page. Run from environment/frontend_server:

    python manage.py test translator
"""
import json
import os
import tempfile

from django.test import SimpleTestCase


class LLMSettingsPageTests(SimpleTestCase):
  def setUp(self):
    fd, self.cfg_path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.remove(self.cfg_path)
    os.environ["LLM_CONFIG_PATH"] = self.cfg_path
    os.environ.pop("SETTINGS_TOKEN", None)

  def tearDown(self):
    if os.path.exists(self.cfg_path):
      os.remove(self.cfg_path)
    os.environ.pop("LLM_CONFIG_PATH", None)
    os.environ.pop("SETTINGS_TOKEN", None)

  def test_get_renders_form(self):
    response = self.client.get("/settings/")
    self.assertEqual(response.status_code, 200)
    self.assertContains(response, "LLM Provider Settings")

  def test_post_saves_config(self):
    response = self.client.post("/settings/", {
      "provider": "deepseek",
      "base_url": "https://api.deepseek.com",
      "api_key": "sk-test-123456",
      "chat_model": "deepseek-chat",
      "smart_chat_model": "",
      "embedding_base_url": "https://api.openai.com/v1",
      "embedding_api_key": "sk-emb-654321",
      "embedding_model": "text-embedding-3-small",
    })
    self.assertEqual(response.status_code, 200)
    cfg = json.load(open(self.cfg_path))
    self.assertEqual(cfg["provider"], "deepseek")
    self.assertEqual(cfg["api_key"], "sk-test-123456")
    self.assertEqual(cfg["embedding_api_key"], "sk-emb-654321")

  def test_blank_key_keeps_stored_key(self):
    with open(self.cfg_path, "w") as f:
      json.dump({"api_key": "sk-original"}, f)
    self.client.post("/settings/", {
      "provider": "openai", "base_url": "", "api_key": "",
      "chat_model": "gpt-4o-mini", "smart_chat_model": "",
      "embedding_base_url": "", "embedding_api_key": "",
      "embedding_model": "",
    })
    cfg = json.load(open(self.cfg_path))
    self.assertEqual(cfg["api_key"], "sk-original")

  def test_key_never_rendered_back(self):
    with open(self.cfg_path, "w") as f:
      json.dump({"api_key": "sk-secret-abcdef-999999"}, f)
    response = self.client.get("/settings/")
    self.assertNotContains(response, "sk-secret-abcdef-999999")

  def test_token_protection(self):
    os.environ["SETTINGS_TOKEN"] = "s3cret"
    self.assertEqual(self.client.get("/settings/").status_code, 403)
    self.assertEqual(
      self.client.get("/settings/?token=s3cret").status_code, 200)


class SettingsPerformanceKnobTests(SimpleTestCase):
  def setUp(self):
    import tempfile as _tf
    fd, self.cfg_path = _tf.mkstemp(suffix=".json")
    os.close(fd)
    os.remove(self.cfg_path)
    os.environ["LLM_CONFIG_PATH"] = self.cfg_path
    os.environ.pop("SETTINGS_TOKEN", None)

  def tearDown(self):
    if os.path.exists(self.cfg_path):
      os.remove(self.cfg_path)
    os.environ.pop("LLM_CONFIG_PATH", None)

  def test_performance_fields_saved(self):
    self.client.post("/settings/", {
      "provider": "openai", "base_url": "", "api_key": "",
      "chat_model": "gpt-4o-mini", "smart_chat_model": "",
      "embedding_base_url": "", "embedding_api_key": "",
      "embedding_model": "",
      "cost_limit_usd": "3.5", "llm_cache": "0",
      "parallel_personas": "1", "checkpoint_freq": "25",
    })
    cfg = json.load(open(self.cfg_path))
    self.assertEqual(cfg["cost_limit_usd"], "3.5")
    self.assertEqual(cfg["llm_cache"], "0")
    self.assertEqual(cfg["checkpoint_freq"], "25")


class ScenarioEditorTests(SimpleTestCase):
  SIM = "test_scn_sim"
  CSV = f"static_dirs/assets/the_ville/scenario_test_scn_sim.csv"

  def setUp(self):
    os.environ.pop("SETTINGS_TOKEN", None)
    persona_dir = f"storage/{self.SIM}/personas/Kim Tester/bootstrap_memory"
    os.makedirs(f"storage/{self.SIM}/reverie", exist_ok=True)
    os.makedirs(persona_dir, exist_ok=True)
    with open(f"storage/{self.SIM}/reverie/meta.json", "w") as f:
      json.dump({"persona_names": ["Kim Tester"]}, f)
    with open(f"{persona_dir}/scratch.json", "w") as f:
      json.dump({"name": "Kim Tester", "innate": "old-innate",
                 "learned": "old-learned", "currently": "old-currently",
                 "lifestyle": "old-lifestyle",
                 "daily_plan_req": "old-plan"}, f)

  def tearDown(self):
    import shutil
    shutil.rmtree(f"storage/{self.SIM}", ignore_errors=True)
    if os.path.exists(self.CSV):
      os.remove(self.CSV)
    os.environ.pop("SETTINGS_TOKEN", None)

  def test_home_renders_and_lists_sim(self):
    response = self.client.get("/scenario/")
    self.assertEqual(response.status_code, 200)
    self.assertContains(response, self.SIM)

  def test_home_token_protection(self):
    os.environ["SETTINGS_TOKEN"] = "s3cret"
    self.assertEqual(self.client.get("/scenario/").status_code, 403)
    self.assertEqual(
      self.client.get("/scenario/?token=s3cret").status_code, 200)

  def test_generate_rejects_bad_name(self):
    response = self.client.post("/scenario/", {
      "fork": self.SIM, "name": "bad name!", "story": "a story"})
    self.assertContains(response, "letters, digits")

  def test_generate_rejects_duplicate_name(self):
    response = self.client.post("/scenario/", {
      "fork": self.SIM, "name": self.SIM, "story": "a story"})
    self.assertContains(response, "already exists")

  def test_edit_get_shows_fields(self):
    response = self.client.get(f"/scenario/{self.SIM}/")
    self.assertEqual(response.status_code, 200)
    self.assertContains(response, "Kim Tester")
    self.assertContains(response, "old-innate")

  def test_edit_unknown_sim_404(self):
    self.assertEqual(self.client.get("/scenario/no_such_sim/").status_code,
                     404)

  def test_edit_save_roundtrip(self):
    response = self.client.post(f"/scenario/{self.SIM}/", {
      "Kim Tester__innate": "bold, cunning",
      "Kim Tester__learned": "Kim is a spy.",
      "Kim Tester__currently": "Kim is on a mission.",
      "Kim Tester__lifestyle": "Kim sleeps at noon.",
      "Kim Tester__daily_plan_req": "Kim spies all night.",
      "Kim Tester__whispers": "You are a spy\nYou trust no one\n",
    })
    self.assertEqual(response.status_code, 200)
    scratch = json.load(open(
      f"storage/{self.SIM}/personas/Kim Tester/bootstrap_memory/"
      f"scratch.json"))
    self.assertEqual(scratch["innate"], "bold, cunning")
    self.assertEqual(scratch["name"], "Kim Tester")
    with open(self.CSV) as f:
      content = f.read()
    self.assertIn("You are a spy; You trust no one", content)


class IntervenePageTests(SimpleTestCase):
  INTERVENTIONS_FILE = "temp_storage/interventions.json"

  def setUp(self):
    os.environ.pop("SETTINGS_TOKEN", None)
    self._backup = None
    if os.path.exists(self.INTERVENTIONS_FILE):
      with open(self.INTERVENTIONS_FILE) as f:
        self._backup = f.read()
      os.remove(self.INTERVENTIONS_FILE)

  def tearDown(self):
    if os.path.exists(self.INTERVENTIONS_FILE):
      os.remove(self.INTERVENTIONS_FILE)
    if self._backup is not None:
      with open(self.INTERVENTIONS_FILE, "w") as f:
        f.write(self._backup)
    os.environ.pop("SETTINGS_TOKEN", None)

  def test_get_renders_form(self):
    response = self.client.get("/intervene/")
    self.assertEqual(response.status_code, 200)
    self.assertContains(response, "Whisper")

  def test_post_queues_whisper(self):
    response = self.client.post("/intervene/", {
      "persona": "Isabella Rodriguez",
      "whisper": "You are planning a party tonight",
    })
    self.assertEqual(response.status_code, 200)
    items = json.load(open(self.INTERVENTIONS_FILE))
    self.assertEqual(items, [{"persona": "Isabella Rodriguez",
                              "whisper": "You are planning a party tonight"}])

  def test_post_appends_to_existing_queue(self):
    self.client.post("/intervene/", {"persona": "A", "whisper": "one"})
    self.client.post("/intervene/", {"persona": "B", "whisper": "two"})
    items = json.load(open(self.INTERVENTIONS_FILE))
    self.assertEqual(len(items), 2)
    self.assertEqual(items[1]["persona"], "B")

  def test_post_rejects_empty_fields(self):
    response = self.client.post("/intervene/", {"persona": "", "whisper": ""})
    self.assertContains(response, "required")
    self.assertFalse(os.path.exists(self.INTERVENTIONS_FILE))

  def test_token_protection(self):
    os.environ["SETTINGS_TOKEN"] = "s3cret"
    self.assertEqual(self.client.get("/intervene/").status_code, 403)
    self.assertEqual(
      self.client.get("/intervene/?token=s3cret").status_code, 200)
