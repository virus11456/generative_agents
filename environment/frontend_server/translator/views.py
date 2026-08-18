"""
Author: Joon Sung Park (joonspk@stanford.edu)
File: views.py
"""
import os
import string
import random
import json
from os import listdir
import os

import datetime
from django.shortcuts import render, redirect, HttpResponseRedirect
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from global_methods import *

from django.templatetags.static import static
from .models import *

def landing(request): 
  context = {}
  template = "landing/landing.html"
  return render(request, template, context)


def demo(request, sim_code, step, play_speed="2"): 
  move_file = f"compressed_storage/{sim_code}/master_movement.json"
  meta_file = f"compressed_storage/{sim_code}/meta.json"
  step = int(step)
  play_speed_opt = {"1": 1, "2": 2, "3": 4,
                    "4": 8, "5": 16, "6": 32}
  if play_speed not in play_speed_opt: play_speed = 2
  else: play_speed = play_speed_opt[play_speed]

  # Loading the basic meta information about the simulation.
  meta = dict() 
  with open (meta_file) as json_file: 
    meta = json.load(json_file)

  sec_per_step = meta["sec_per_step"]
  start_datetime = datetime.datetime.strptime(meta["start_date"] + " 00:00:00", 
                                              '%B %d, %Y %H:%M:%S')
  for i in range(step): 
    start_datetime += datetime.timedelta(seconds=sec_per_step)
  start_datetime = start_datetime.strftime("%Y-%m-%dT%H:%M:%S")

  # Loading the movement file
  raw_all_movement = dict()
  with open(move_file) as json_file: 
    raw_all_movement = json.load(json_file)
 
  # Loading all names of the personas
  persona_names = dict()
  persona_names = []
  persona_names_set = set()
  for p in list(raw_all_movement["0"].keys()): 
    persona_names += [{"original": p, 
                       "underscore": p.replace(" ", "_"), 
                       "initial": p[0] + p.split(" ")[-1][0]}]
    persona_names_set.add(p)

  # <all_movement> is the main movement variable that we are passing to the 
  # frontend. Whereas we use ajax scheme to communicate steps to the frontend
  # during the simulation stage, for this demo, we send all movement 
  # information in one step. 
  all_movement = dict()

  # Preparing the initial step. 
  # <init_prep> sets the locations and descriptions of all agents at the
  # beginning of the demo determined by <step>. 
  init_prep = dict() 
  for int_key in range(step+1): 
    key = str(int_key)
    val = raw_all_movement[key]
    for p in persona_names_set: 
      if p in val: 
        init_prep[p] = val[p]
  persona_init_pos = dict()
  for p in persona_names_set: 
    persona_init_pos[p.replace(" ","_")] = init_prep[p]["movement"]
  all_movement[step] = init_prep

  # Finish loading <all_movement>
  for int_key in range(step+1, len(raw_all_movement.keys())): 
    all_movement[int_key] = raw_all_movement[str(int_key)]

  context = {"sim_code": sim_code,
             "step": step,
             "persona_names": persona_names,
             "persona_init_pos": json.dumps(persona_init_pos), 
             "all_movement": json.dumps(all_movement), 
             "start_datetime": start_datetime,
             "sec_per_step": sec_per_step,
             "play_speed": play_speed,
             "mode": "demo"}
  template = "demo/demo.html"

  return render(request, template, context)


def UIST_Demo(request): 
  return demo(request, "March20_the_ville_n25_UIST_RUN-step-1-141", 2160, play_speed="3")


def home(request):
  f_curr_sim_code = "temp_storage/curr_sim_code.json"
  f_curr_step = "temp_storage/curr_step.json"

  if not check_if_file_exists(f_curr_sim_code):
    context = {}
    template = "home/error_start_backend.html"
    return render(request, template, context)

  with open(f_curr_sim_code) as json_file:
    sim_code = json.load(json_file)["sim_code"]

  # curr_step.json is consumed on read (original design); when it is absent
  # (e.g. the page was refreshed while the backend computes a step), derive
  # the current step from the sim's own files instead of erroring out.
  step = None
  if check_if_file_exists(f_curr_step):
    with open(f_curr_step) as json_file:
      step = json.load(json_file)["step"]
    os.remove(f_curr_step)
  else:
    movement_dir = f"storage/{sim_code}/movement"
    if os.path.isdir(movement_dir):
      step_numbers = [int(name.split(".")[0])
                      for name in os.listdir(movement_dir)
                      if name.endswith(".json")
                      and name.split(".")[0].isdigit()]
      if step_numbers:
        step = max(step_numbers)
  if step is None:
    if not os.path.isdir(f"storage/{sim_code}"):
      return render(request, "home/error_start_backend.html", {})
    step = 0

  persona_names = []
  persona_names_set = set()
  for i in find_filenames(f"storage/{sim_code}/personas", ""): 
    x = i.split("/")[-1].strip()
    if x[0] != ".": 
      persona_names += [[x, x.replace(" ", "_")]]
      persona_names_set.add(x)

  persona_init_pos = []
  file_count = []
  for i in find_filenames(f"storage/{sim_code}/environment", ".json"):
    x = i.split("/")[-1].strip()
    if x[0] != ".": 
      file_count += [int(x.split(".")[0])]
  curr_json = f'storage/{sim_code}/environment/{str(max(file_count))}.json'
  with open(curr_json) as json_file:  
    persona_init_pos_dict = json.load(json_file)
    for key, val in persona_init_pos_dict.items(): 
      if key in persona_names_set: 
        persona_init_pos += [[key, val["x"], val["y"]]]

  context = {"sim_code": sim_code,
             "step": step, 
             "persona_names": persona_names,
             "persona_init_pos": persona_init_pos,
             "mode": "simulate"}
  template = "home/home.html"
  return render(request, template, context)


def replay(request, sim_code, step): 
  sim_code = sim_code
  step = int(step)

  persona_names = []
  persona_names_set = set()
  for i in find_filenames(f"storage/{sim_code}/personas", ""): 
    x = i.split("/")[-1].strip()
    if x[0] != ".": 
      persona_names += [[x, x.replace(" ", "_")]]
      persona_names_set.add(x)

  persona_init_pos = []
  file_count = []
  for i in find_filenames(f"storage/{sim_code}/environment", ".json"):
    x = i.split("/")[-1].strip()
    if x[0] != ".": 
      file_count += [int(x.split(".")[0])]
  curr_json = f'storage/{sim_code}/environment/{str(max(file_count))}.json'
  with open(curr_json) as json_file:  
    persona_init_pos_dict = json.load(json_file)
    for key, val in persona_init_pos_dict.items(): 
      if key in persona_names_set: 
        persona_init_pos += [[key, val["x"], val["y"]]]

  context = {"sim_code": sim_code,
             "step": step,
             "persona_names": persona_names,
             "persona_init_pos": persona_init_pos, 
             "mode": "replay"}
  template = "home/home.html"
  return render(request, template, context)


def replay_persona_state(request, sim_code, step, persona_name): 
  sim_code = sim_code
  step = int(step)

  persona_name_underscore = persona_name
  persona_name = " ".join(persona_name.split("_"))
  memory = f"storage/{sim_code}/personas/{persona_name}/bootstrap_memory"
  if not os.path.exists(memory): 
    memory = f"compressed_storage/{sim_code}/personas/{persona_name}/bootstrap_memory"

  with open(memory + "/scratch.json") as json_file:  
    scratch = json.load(json_file)

  with open(memory + "/spatial_memory.json") as json_file:  
    spatial = json.load(json_file)

  with open(memory + "/associative_memory/nodes.json") as json_file:  
    associative = json.load(json_file)

  a_mem_event = []
  a_mem_chat = []
  a_mem_thought = []

  for count in range(len(associative.keys()), 0, -1):
    node_id = f"node_{str(count)}"
    node_details = associative[node_id]

    if node_details["type"] == "event":
      a_mem_event += [node_details]

    elif node_details["type"] == "chat":
      a_mem_chat += [node_details]

    elif node_details["type"] == "thought":
      a_mem_thought += [node_details]

  # Traditional Chinese display: identity fields, daily goals, and the
  # schedule are LLM-translated once and cached (translator/zh.py); with
  # no API key the original English is shown.
  from translator import zh as zh_translate
  ident_fields = ["innate", "learned", "currently", "lifestyle",
                  "act_description", "act_address"]
  daily_req = scratch.get("daily_req") or []
  schedule = scratch.get("f_daily_schedule") or []
  schedule_tasks = []
  for item in schedule:
    if isinstance(item, (list, tuple)) and item:
      schedule_tasks.append(str(item[0]))
    else:
      schedule_tasks.append(str(item))
  to_translate = [str(scratch.get(f) or "") for f in ident_fields]
  to_translate += [str(x) for x in daily_req]
  to_translate += schedule_tasks
  zh_map = zh_translate.translate_map(to_translate)

  def _zh_datetime(value):
    # "February 13, 2023, 08:19:50" -> "2023 年 2 月 13 日 08:19"
    try:
      dt = datetime.datetime.strptime(str(value), "%B %d, %Y, %H:%M:%S")
    except (TypeError, ValueError):
      return value
    weekday = "一二三四五六日"[dt.weekday()]
    return (f"{dt.year} 年 {dt.month} 月 {dt.day} 日"
            f"（週{weekday}）{dt.strftime('%H:%M')}")

  scratch = dict(scratch)
  for field in ident_fields:
    original = str(scratch.get(field) or "")
    scratch[field] = zh_map.get(original.strip(), original)
  for field in ("curr_time", "act_start_time"):
    scratch[field] = _zh_datetime(scratch.get(field))
  daily_req_zh = [zh_map.get(str(x).strip(), str(x)) for x in daily_req]
  schedule_zh = []
  for item, task in zip(schedule, schedule_tasks):
    task_zh = zh_map.get(task.strip(), task)
    if isinstance(item, (list, tuple)) and len(item) > 1:
      schedule_zh.append(f"{task_zh}（{item[1]} 分鐘）")
    else:
      schedule_zh.append(task_zh)

  context = {"sim_code": sim_code,
             "step": step,
             "persona_name": persona_name,
             "persona_name_underscore": persona_name_underscore,
             "scratch": scratch,
             "daily_req_zh": daily_req_zh,
             "schedule_zh": schedule_zh,
             "spatial": spatial,
             "a_mem_event": a_mem_event,
             "a_mem_chat": a_mem_chat,
             "a_mem_thought": a_mem_thought}
  template = "persona_state/persona_state.html"
  return render(request, template, context)


def control_page(request):
  """
  World control panel: pause/resume the running simulation (queued as
  one-file-per-command under temp_storage/sim_commands/, consumed by
  reverie.py between steps) and browse saved runs for replay.
  Protected by SETTINGS_TOKEN when set.
  """
  forbidden, token = _token_forbidden(request)
  if forbidden:
    return forbidden

  queued = ""
  if request.method == "POST":
    action = request.POST.get("action", "")
    if action in ("pause", "resume"):
      import uuid
      os.makedirs("temp_storage/sim_commands", exist_ok=True)
      file_stem = f"temp_storage/sim_commands/{uuid.uuid4().hex}"
      with open(file_stem + ".tmp", "w") as f:
        f.write(json.dumps({"action": action}, indent=2))
      os.replace(file_stem + ".tmp", file_stem + ".json")
      queued = action

  paused_info = None
  if os.path.isfile("temp_storage/sim_paused.json"):
    try:
      with open("temp_storage/sim_paused.json") as f:
        paused_info = json.load(f)
    except (OSError, ValueError):
      paused_info = {}

  curr_sim, _ = _current_sim_personas()

  runs = []
  for sim in _list_sims():
    chronicle_dir = f"storage/{sim}/chronicle"
    issues = 0
    if os.path.isdir(chronicle_dir):
      issues = len([n for n in os.listdir(chronicle_dir)
                    if n.endswith(".md")])
    runs.append({"sim": sim,
                 "is_live": sim == curr_sim,
                 "issues": issues})
  runs.reverse()

  context = {"queued": queued,
             "paused_info": paused_info,
             "curr_sim": curr_sim,
             "runs": runs,
             "token": token}
  return render(request, "control/control.html", context)


def path_tester(request):
  context = {}
  template = "path_tester/path_tester.html"
  return render(request, template, context)


@csrf_exempt
def process_environment(request): 
  """
  <FRONTEND to BACKEND> 
  This sends the frontend visual world information to the backend server. 
  It does this by writing the current environment representation to 
  "storage/environment.json" file. 

  ARGS:
    request: Django request
  RETURNS: 
    HttpResponse: string confirmation message. 
  """
  # f_curr_sim_code = "temp_storage/curr_sim_code.json"
  # with open(f_curr_sim_code) as json_file:  
  #   sim_code = json.load(json_file)["sim_code"]

  data = json.loads(request.body)
  step = data["step"]
  sim_code = data["sim_code"]
  environment = data["environment"]

  with open(f"storage/{sim_code}/environment/{step}.json", "w") as outfile:
    outfile.write(json.dumps(environment, indent=2))

  return HttpResponse("received")


@csrf_exempt
def update_environment(request): 
  """
  <BACKEND to FRONTEND> 
  This sends the backend computation of the persona behavior to the frontend
  visual server. 
  It does this by reading the new movement information from 
  "storage/movement.json" file.

  ARGS:
    request: Django request
  RETURNS: 
    HttpResponse
  """
  # f_curr_sim_code = "temp_storage/curr_sim_code.json"
  # with open(f_curr_sim_code) as json_file:  
  #   sim_code = json.load(json_file)["sim_code"]

  data = json.loads(request.body)
  step = data["step"]
  sim_code = data["sim_code"]

  response_data = {"<step>": -1}
  if (check_if_file_exists(f"storage/{sim_code}/movement/{step}.json")):
    with open(f"storage/{sim_code}/movement/{step}.json") as json_file: 
      response_data = json.load(json_file)
      response_data["<step>"] = step

  return JsonResponse(response_data)


@csrf_exempt
def path_tester_update(request): 
  """
  Processing the path and saving it to path_tester_env.json temp storage for 
  conducting the path tester. 

  ARGS:
    request: Django request
  RETURNS: 
    HttpResponse: string confirmation message. 
  """
  data = json.loads(request.body)
  camera = data["camera"]

  with open(f"temp_storage/path_tester_env.json", "w") as outfile:
    outfile.write(json.dumps(camera, indent=2))

  return HttpResponse("received")











# ============================================================================
# LLM provider settings page
# ============================================================================

_LLM_CONFIG_FIELDS = ["provider", "base_url", "chat_model",
                      "smart_chat_model", "embedding_base_url",
                      "embedding_model",
                      "cost_limit_usd", "llm_cache", "parallel_personas",
                      "checkpoint_freq",
                      "headless", "sec_per_step", "real_minutes_per_day",
                      "chronicle", "chronicle_lang", "convo_lang",
                      "traits_auto", "economy", "econ_starting_balance",
                      "econ_daily_wage"]
_LLM_SECRET_FIELDS = ["api_key", "embedding_api_key"]


def _llm_config_path():
  # Shared with the backend (reverie/backend_server/utils.py): a single
  # llm_config.json at the repository root.
  return os.environ.get("LLM_CONFIG_PATH", os.path.abspath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "..",
    "llm_config.json")))


def _mask_key(key):
  if not key:
    return ""
  if len(key) <= 8:
    return "*" * len(key)
  return key[:4] + "*" * 6 + key[-4:]


def llm_settings(request):
  """
  Web UI for configuring the LLM provider (OpenAI, DeepSeek, MiniMax,
  Gemini, Ollama, or any OpenAI-compatible endpoint). Saves to a
  llm_config.json shared with the simulation backend; restart reverie.py
  to apply changes. Optionally protected by the SETTINGS_TOKEN env var.
  """
  forbidden, supplied_token = _token_forbidden(request)
  if forbidden:
    return forbidden

  cfg_path = _llm_config_path()
  try:
    with open(cfg_path) as f:
      cfg = json.load(f)
  except (OSError, ValueError):
    cfg = {}

  saved = False
  if request.method == "POST":
    for field in _LLM_CONFIG_FIELDS:
      cfg[field] = request.POST.get(field, "").strip()
    for field in _LLM_SECRET_FIELDS:
      # A blank secret field means "keep the stored key".
      posted = request.POST.get(field, "").strip()
      if posted:
        cfg[field] = posted
    with open(cfg_path, "w") as f:
      f.write(json.dumps(cfg, indent=2))
    try:
      os.chmod(cfg_path, 0o600)
    except OSError:
      pass
    saved = True

  context = {"cfg": cfg,
             "api_key_masked": _mask_key(cfg.get("api_key", "")),
             "embedding_api_key_masked": _mask_key(
               cfg.get("embedding_api_key", "")),
             "saved": saved,
             "token": supplied_token}
  return render(request, "settings/settings.html", context)


# ============================================================================
# Player intervention page (whisper injection)
# ============================================================================

def _current_sim_personas():
  """Return (sim_code, [persona names]) for the running simulation, if any."""
  try:
    with open("temp_storage/curr_sim_code.json") as f:
      sim_code = json.load(f)["sim_code"]
    with open(f"storage/{sim_code}/reverie/meta.json") as f:
      return sim_code, json.load(f)["persona_names"]
  except (OSError, ValueError, KeyError):
    return None, []


def intervene(request):
  """
  Queue a whisper for the simulation backend to inject into a persona's
  memory between steps. Whispers are appended to
  temp_storage/interventions.json, which reverie.py polls while running.
  Protected by SETTINGS_TOKEN when set.
  """
  forbidden, supplied_token = _token_forbidden(request)
  if forbidden:
    return forbidden

  sim_code, persona_names = _current_sim_personas()

  queued = False
  error = ""
  if request.method == "POST":
    persona_name = request.POST.get("persona", "").strip()
    whisper = request.POST.get("whisper", "").strip()
    if not persona_name or not whisper:
      error = "居民與耳語內容都必須填寫。"
    else:
      # One file per whisper: no read-modify-write, so the backend
      # (which consumes and deletes files) can never race with us.
      import uuid
      os.makedirs("temp_storage/interventions", exist_ok=True)
      file_stem = f"temp_storage/interventions/{uuid.uuid4().hex}"
      with open(file_stem + ".tmp", "w") as f:
        f.write(json.dumps({"persona": persona_name, "whisper": whisper},
                           indent=2))
      os.replace(file_stem + ".tmp", file_stem + ".json")
      queued = True

  context = {"sim_code": sim_code,
             "persona_names": persona_names,
             "queued": queued,
             "error": error,
             "token": supplied_token}
  return render(request, "intervene/intervene.html", context)


# ============================================================================
# Scenario editor (generate / preview / edit scenarios in the browser)
# ============================================================================

import re as _re
import subprocess
import sys as _sys

_IDENTITY_FIELDS = ["innate", "learned", "currently", "lifestyle",
                    "daily_plan_req"]
_BACKEND_DIR = os.path.abspath(os.path.join(
  os.path.dirname(os.path.abspath(__file__)), "..", "..", "..",
  "reverie", "backend_server"))


def _token_forbidden(request):
  """
  Gate for the admin pages (/settings/, /intervene/, /events/, /scenario/,
  /chronicle/). With SETTINGS_TOKEN set, the matching ?token= is required.
  Without it, only localhost may use these pages -- so a public deployment
  that forgot to set a token is closed by default rather than open.
  """
  required_token = os.environ.get("SETTINGS_TOKEN", "")
  supplied_token = request.POST.get("token", request.GET.get("token", ""))
  if required_token:
    if supplied_token != required_token:
      return HttpResponse(
        "拒絕存取：此頁面受保護。請在網址加上 ?token=<SETTINGS_TOKEN>。",
        status=403), supplied_token
    return None, supplied_token
  remote_addr = request.META.get("REMOTE_ADDR", "")
  if remote_addr not in ("127.0.0.1", "::1"):
    return HttpResponse(
      "拒絕存取：在設定 SETTINGS_TOKEN 環境變數之前，管理頁面只允許"
      "從本機（localhost）開啟；設定後請以 ?token=<值> 開啟本頁。",
      status=403), supplied_token
  return None, supplied_token


def _list_sims():
  sims = []
  try:
    for name in sorted(os.listdir("storage")):
      if os.path.isfile(f"storage/{name}/reverie/meta.json"):
        sims += [name]
  except OSError:
    pass
  return sims


def _scenario_csv_path(sim_code):
  return f"static_dirs/assets/the_ville/scenario_{sim_code}.csv"


def scenario_home(request):
  """
  Scenario editor landing page: generate a new scenario from a story
  premise (runs scenario_generator.py), or pick an existing simulation to
  edit. Protected by SETTINGS_TOKEN when set.
  """
  forbidden, token = _token_forbidden(request)
  if forbidden:
    return forbidden

  error = ""
  output = ""
  if request.method == "POST":
    fork = request.POST.get("fork", "").strip()
    name = request.POST.get("name", "").strip()
    story = request.POST.get("story", "").strip()
    if not _re.fullmatch(r"[A-Za-z0-9_-]+", name or ""):
      error = "劇本名稱只能使用英文字母、數字、「-」與「_」。"
    elif fork not in _list_sims():
      error = "找不到這個基底模擬。"
    elif name in _list_sims():
      error = f"已經有名為「{name}」的模擬了。"
    elif not story:
      error = "請填寫故事前提。"
    else:
      # scenario_generator.py lives in the backend and uses backend-relative
      # paths, so it runs as a subprocess with the backend as cwd. This can
      # take a minute or more (one LLM call per persona). The timeout stays
      # under gunicorn's --timeout 600 so the worker is not killed first.
      try:
        result = subprocess.run(
          [_sys.executable, "scenario_generator.py", "--fork", fork,
           "--name", name, "--story", story],
          cwd=_BACKEND_DIR, capture_output=True, text=True, timeout=570)
      except subprocess.TimeoutExpired:
        result = None
        error = ("生成逾時。若居民較多（例如 25 人的基底），請改在"
                 "命令列執行 scenario_generator.py。")
      if result is not None:
        if result.returncode == 0:
          url = f"/scenario/{name}/"
          if token:
            url += f"?token={token}"
          return HttpResponseRedirect(url)
        error = "生成失敗。"
        output = (result.stdout or "") + "\n" + (result.stderr or "")

  context = {"sims": _list_sims(),
             "error": error,
             "output": output.strip(),
             "token": token}
  return render(request, "scenario/scenario_home.html", context)


def scenario_edit(request, sim_code):
  """
  Edit every persona's identity fields and relationship whispers of a
  simulation directly in the browser. Saves back to the personas'
  scratch.json and the scenario whisper CSV.
  """
  forbidden, token = _token_forbidden(request)
  if forbidden:
    return forbidden

  meta_file = f"storage/{sim_code}/reverie/meta.json"
  if not os.path.isfile(meta_file):
    return HttpResponse(f"找不到模擬：{sim_code}", status=404)
  with open(meta_file) as f:
    persona_names = json.load(f)["persona_names"]

  saved = False
  if request.method == "POST":
    csv_rows = [["Name", "Whisper"]]
    for persona_name in persona_names:
      scratch_path = (f"storage/{sim_code}/personas/{persona_name}/"
                      f"bootstrap_memory/scratch.json")
      with open(scratch_path) as f:
        scratch = json.load(f)
      for field in _IDENTITY_FIELDS:
        posted = request.POST.get(f"{persona_name}__{field}")
        if posted is not None:
          scratch[field] = posted.strip()
      with open(scratch_path, "w") as f:
        f.write(json.dumps(scratch, indent=2))

      whispers = [w.strip() for w in
                  request.POST.get(f"{persona_name}__whispers",
                                   "").splitlines() if w.strip()]
      if whispers:
        csv_rows += [[persona_name, "; ".join(whispers)]]

    import csv as _csv
    with open(_scenario_csv_path(sim_code), "w", newline="") as f:
      _csv.writer(f).writerows(csv_rows)
    saved = True

  # Load current state for display.
  personas = []
  whisper_map = {}
  csv_path = _scenario_csv_path(sim_code)
  if os.path.isfile(csv_path):
    import csv as _csv
    with open(csv_path) as f:
      for row in list(_csv.reader(f))[1:]:
        if len(row) >= 2:
          whisper_map[row[0]] = "\n".join(
            w.strip() for w in row[1].split(";") if w.strip())
  for persona_name in persona_names:
    scratch_path = (f"storage/{sim_code}/personas/{persona_name}/"
                    f"bootstrap_memory/scratch.json")
    with open(scratch_path) as f:
      scratch = json.load(f)
    personas += [{"name": persona_name,
                  "fields": [(field, scratch.get(field) or "")
                             for field in _IDENTITY_FIELDS],
                  "whispers": whisper_map.get(persona_name, "")}]

  context = {"sim_code": sim_code,
             "personas": personas,
             "csv_name": f"scenario_{sim_code}.csv",
             "saved": saved,
             "token": token}
  return render(request, "scenario/scenario_edit.html", context)


# ============================================================================
# World events page (elections, festivals, rumors, custom broadcasts)
# ============================================================================

def _queue_event_command(command):
  # One file per command: no read-modify-write, so the backend (which
  # consumes and deletes files) can never race with us.
  import uuid
  os.makedirs("temp_storage/event_commands", exist_ok=True)
  file_stem = f"temp_storage/event_commands/{uuid.uuid4().hex}"
  with open(file_stem + ".tmp", "w") as f:
    f.write(json.dumps(command, indent=2))
  os.replace(file_stem + ".tmp", file_stem + ".json")


def events_page(request):
  """
  Schedule world events (elections, festivals, rumors, custom broadcasts)
  and browse their history, including per-agent election votes. Commands
  are queued for the simulation backend, which applies them between steps.
  """
  forbidden, token = _token_forbidden(request)
  if forbidden:
    return forbidden

  queued = False
  error = ""
  if request.method == "POST":
    action = request.POST.get("action", "")
    if action == "remove":
      _queue_event_command({"action": "remove",
                            "id": request.POST.get("id", "")})
      queued = True
    elif action == "add":
      event_type = request.POST.get("type", "")
      spec = {"type": event_type,
              "label": request.POST.get("label", "").strip(),
              "days_from_now": request.POST.get("days_from_now",
                                                "0").strip() or "0",
              "every_days": request.POST.get("every_days",
                                             "0").strip() or "0"}
      if event_type == "broadcast":
        spec["text"] = request.POST.get("text", "").strip()
        target = request.POST.get("target", "all").strip()
        if target not in ("all",) and not target.startswith("random:"):
          target = [t.strip() for t in target.split(";") if t.strip()]
        spec["target"] = target
        if not spec["text"]:
          error = "廣播事件必須填寫耳語內容。"
      elif event_type == "election":
        candidates = request.POST.get("candidates", "random").strip()
        if candidates.lower() != "random":
          candidates = [c.strip() for c in candidates.split(";")
                        if c.strip()]
          if len(candidates) < 2:
            error = "選舉的候選人必須是「random」或至少兩位。"
        spec["candidates"] = candidates
        spec["campaign_days"] = request.POST.get("campaign_days",
                                                 "1").strip() or "1"
      else:
        error = "未知的事件類型。"
      if not error:
        _queue_event_command({"action": "add", "spec": spec})
        queued = True

  sim_code, persona_names = _current_sim_personas()
  events = []
  if sim_code:
    try:
      with open(f"storage/{sim_code}/reverie/events.json") as f:
        events = json.load(f).get("events", [])
    except (OSError, ValueError):
      pass

  context = {"sim_code": sim_code,
             "persona_names": persona_names,
             "events": events,
             "queued": queued,
             "error": error,
             "token": token}
  return render(request, "events/events.html", context)


# ============================================================================
# The Ville Chronicle (daily newspaper) browser
# ============================================================================

def chronicle_page(request):
  """
  Browse the daily chronicles of the current (or ?sim=) simulation. Issues
  are generated by the backend at each game-day boundary (or via the
  'chronicle now' command) into storage/<sim>/chronicle/*.md.
  """
  forbidden, token = _token_forbidden(request)
  if forbidden:
    return forbidden

  sim_code = request.GET.get("sim", "").strip()
  # Reject anything that isn't a plain simulation name (path traversal).
  if sim_code and not _re.fullmatch(r"[\w-]+", sim_code):
    sim_code = ""
  if not sim_code:
    sim_code, _ = _current_sim_personas()

  issues = []
  if sim_code and os.path.isdir(f"storage/{sim_code}/chronicle"):
    issues = sorted((name for name in
                     os.listdir(f"storage/{sim_code}/chronicle")
                     if name.endswith(".md")), reverse=True)

  day = request.GET.get("day", "").strip()
  article = ""
  if day and day in issues:
    with open(f"storage/{sim_code}/chronicle/{day}") as f:
      article = f.read()
  elif issues:
    day = issues[0]
    with open(f"storage/{sim_code}/chronicle/{day}") as f:
      article = f.read()

  context = {"sim_code": sim_code,
             "sims": _list_sims(),
             "issues": issues,
             "day": day,
             "article": article,
             "token": token}
  return render(request, "chronicle/chronicle.html", context)


# ============================================================================
# Economy & social fabric page
# ============================================================================

def economy_page(request):
  """
  Town dashboard for the current (or ?sim=) simulation: wallets ranked by
  balance with each persona's personality traits, the relationship web,
  and the transaction ledger.
  """
  forbidden, token = _token_forbidden(request)
  if forbidden:
    return forbidden

  sim_code = request.GET.get("sim", "").strip()
  if sim_code and not _re.fullmatch(r"[\w-]+", sim_code):
    sim_code = ""
  if not sim_code:
    sim_code, _ = _current_sim_personas()

  def _read_json(path, default):
    try:
      with open(path) as f:
        return json.load(f)
    except (OSError, ValueError):
      return default

  economy = {}
  traits_registry = {}
  relationships = []
  trait_names = {}
  if sim_code:
    economy = _read_json(f"storage/{sim_code}/reverie/economy.json", {})
    traits_registry = _read_json(
      f"storage/{sim_code}/reverie/traits.json", {})
    relationships = _read_json(
      f"storage/{sim_code}/reverie/relationships.json", [])
    library = _read_json(os.path.join(
      os.path.dirname(os.path.abspath(__file__)), "..", "..", "..",
      "reverie", "backend_server", "traits.json"), {})
    trait_names = {t["id"]: t["name_zh"]
                   for t in library.get("traits", [])}

  wallets = []
  balances = economy.get("balances", {})
  for name in sorted(balances, key=lambda n: -balances[n]):
    trait_labels = [f"{trait_names.get(t, t)}"
                    for t in traits_registry.get(name, [])]
    wallets += [{"name": name, "balance": balances[name],
                 "traits": ", ".join(trait_labels)}]

  transactions = list(reversed(economy.get("transactions", [])))[:100]

  context = {"sim_code": sim_code,
             "sims": _list_sims(),
             "wallets": wallets,
             "relationships": relationships,
             "transactions": transactions,
             "token": token}
  return render(request, "economy/economy.html", context)
