"""
File: chronicle.py
Description: The Ville's daily newspaper.

Every simulation step is already logged to <sim_folder>/movement/<step>.json
(positions, action descriptions, and full conversation transcripts). This
module compresses one game day of those logs into a digest and asks the LLM
to write a readable "daily newspaper" article from it -- so you can leave
the town running unattended and catch up on the story later.

Chronicles are saved as markdown under <sim_folder>/chronicle/ and browsable
on the frontend's /chronicle/ page. The summarization language is
configurable (chronicle_lang / CHRONICLE_LANG; default Traditional Chinese).
"""
import datetime
import json
import os

DT_FORMAT = "%B %d, %Y, %H:%M:%S"

# Keep the digest within a sane prompt budget.
_MAX_DIGEST_CHARS = 14000
_MAX_CHATS = 120


def _short_desc(description):
  # "writing her novel (editing chapter 1) @ the Ville:..." -> drop location
  return description.split(" @ ")[0].strip() if description else ""


def collect_day_log(sim_folder, start_step, end_step):
  """
  Read movement/<step>.json for steps [start_step, end_step] and build a
  compressed, human-readable digest of the day: what every persona did
  (deduplicating consecutive identical actions), every unique conversation,
  and any election results that landed that day.

  Returns (digest_str, date_str) -- date_str is taken from the first
  readable step's meta.curr_time (e.g. "February 13, 2023"); both are ""
  if no logs exist in the range.
  """
  actions = {}      # persona -> [(hh:mm, description), ...]
  chats = []        # [(hh:mm, transcript_str)]
  seen_chats = set()
  date_str = ""

  for step in range(start_step, end_step + 1):
    movement_file = f"{sim_folder}/movement/{step}.json"
    try:
      with open(movement_file) as f:
        data = json.load(f)
    except (OSError, ValueError):
      continue
    try:
      curr_time = datetime.datetime.strptime(data["meta"]["curr_time"],
                                             DT_FORMAT)
    except (KeyError, ValueError):
      continue
    if not date_str:
      date_str = curr_time.strftime("%B %d, %Y")
    hhmm = curr_time.strftime("%H:%M")

    for name, info in data.get("persona", {}).items():
      description = _short_desc(info.get("description", ""))
      if description:
        seq = actions.setdefault(name, [])
        if not seq or seq[-1][1] != description:
          seq += [(hhmm, description)]

      chat = info.get("chat")
      if chat:
        transcript = "\n".join(f"  {speaker}: {utterance}"
                               for speaker, utterance in chat)
        if transcript not in seen_chats:
          seen_chats.add(transcript)
          chats += [(hhmm, transcript)]

  if not date_str:
    return "", ""

  lines = [f"DATE: {date_str}", "", "== WHAT EACH RESIDENT DID =="]
  for name, seq in actions.items():
    lines += [f"{name}:"]
    lines += [f"  {t} {d}" for t, d in seq]
  lines += ["", "== CONVERSATIONS (verbatim) =="]
  for t, transcript in chats[:_MAX_CHATS]:
    lines += [f"[{t}]", transcript]
  if len(chats) > _MAX_CHATS:
    lines += [f"(...and {len(chats) - _MAX_CHATS} more conversations)"]

  # Election results that landed on this date.
  try:
    with open(f"{sim_folder}/reverie/events.json") as f:
      events = json.load(f).get("events", [])
    election_lines = []
    for event in events:
      for h in event.get("history", []):
        if h.get("counts") and h.get("at", "").startswith(date_str):
          counts = ", ".join(f"{c}: {n}" for c, n in h["counts"].items())
          election_lines += [f"Election result at {h['at']}: {counts}; "
                             f"winner(s): {', '.join(h.get('winners', []))}"]
    if election_lines:
      lines += ["", "== ELECTION =="] + election_lines
  except (OSError, ValueError):
    pass

  digest = "\n".join(lines)
  if len(digest) > _MAX_DIGEST_CHARS:
    digest = digest[:_MAX_DIGEST_CHARS] + "\n(...log truncated)"
  return digest, date_str


def _default_llm(prompt):
  from persona.prompt_template.gpt_structure import _chat_request
  return _chat_request(prompt)


def _chronicle_filename(date_str):
  dt = datetime.datetime.strptime(date_str, "%B %d, %Y")
  return dt.strftime("%Y-%m-%d") + ".md"


def generate_chronicle(sim_folder, start_step, end_step,
                       lang="Traditional Chinese (繁體中文)", llm_fn=None):
  """
  Summarize one game day into a newspaper article and save it under
  <sim_folder>/chronicle/<YYYY-MM-DD>.md. Returns the saved path, or None
  if there was nothing to summarize.
  """
  digest, date_str = collect_day_log(sim_folder, start_step, end_step)
  if not digest:
    return None

  prompt = f"""You are the editor of "The Ville Chronicle", the daily newspaper of a
small simulated town. Below is the complete activity log for {date_str}:
every resident's actions and every conversation, verbatim.

Write today's newspaper in {lang}, in markdown, based ONLY on the log.
Structure:
- A catchy headline (# heading) and the date
- 頭條 / Top story: the most interesting or consequential thing that
  happened (an election, a party, a conflict, a budding friendship...)
- 鎮民動態 / Around town: 2-4 short items about notable interactions,
  plans, or changes, quoting memorable lines from conversations
- 居民日誌 / Resident diary: one line per resident summarizing their day
Do not invent events that are not in the log. Keep it lively but factual.

ACTIVITY LOG:
{digest}"""

  article = (llm_fn or _default_llm)(prompt)

  chronicle_dir = f"{sim_folder}/chronicle"
  os.makedirs(chronicle_dir, exist_ok=True)
  path = f"{chronicle_dir}/{_chronicle_filename(date_str)}"
  with open(path, "w") as f:
    f.write(article)
  return path
