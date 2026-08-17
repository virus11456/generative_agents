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
