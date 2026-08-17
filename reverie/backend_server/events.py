"""
File: events.py
Description: A world-event engine for Reverie simulations.

The original release had no autonomous world events -- the famous Valentine's
party and mayoral candidacy in the paper were memories seeded by hand. This
module adds a general event system that fires on the GAME clock (not real
time), with two event types:

  broadcast -- at fire time, a "whisper" (a planted thought) is injected
      into the memory of all personas, K random personas, or a named list.
      Optionally recurs every N game days. Covers festivals, town news,
      rumors, and custom scheduled happenings.

  election -- a two-phase mayoral election. At announce time every persona
      learns that the election is coming and who the candidates are (the
      candidates learn they are running and want to campaign); the town then
      discusses it organically through the normal conversation machinery.
      At vote time every persona is polled individually -- an LLM call that
      uses the persona's own identity and retrieved memories about the
      candidates -- the votes are tallied, and the results are planted back
      into everyone's memory (personalized: winner, losers, and each voter's
      own choice), which triggers reactions and discussion. Optionally
      recurs every N game days.

Events are stored in <sim_folder>/reverie/events.json, so they survive
save/fork like everything else. The frontend's /events/ page queues
add/remove commands through <fs_temp_storage>/event_command.json, which
the simulation server consumes between steps.
"""
import concurrent.futures
import datetime
import json
import os
import random

DT_FORMAT = "%B %d, %Y, %H:%M:%S"


def _dt(s):
  return datetime.datetime.strptime(s, DT_FORMAT)


def _dts(dt):
  return dt.strftime(DT_FORMAT)


def tally_votes(votes, candidates):
  """
  votes: {voter_name: {"vote": str, "reason": str}}
  Returns (counts dict incl. zero-vote candidates, winners list -- more than
  one on a tie).
  """
  counts = {c: 0 for c in candidates}
  for record in votes.values():
    vote = record.get("vote")
    if vote in counts:
      counts[vote] += 1
  top = max(counts.values()) if counts else 0
  winners = [c for c, n in counts.items() if n == top and top > 0]
  return counts, winners


class EventManager:
  """
  Manages scheduled world events for one simulation. whisper_fn and vote_fn
  are injectable for testing; the defaults use the real LLM machinery.
  """

  def __init__(self, sim_folder, temp_storage=None,
               whisper_fn=None, vote_fn=None):
    self.sim_folder = sim_folder
    self.temp_storage = temp_storage
    self.events_file = f"{sim_folder}/reverie/events.json"
    self.whisper_fn = whisper_fn or self._default_whisper
    self.vote_fn = vote_fn or self._default_vote
    self.events = []
    self._next_id = 1
    self._load()

  # -------------------------------------------------------------- storage

  def _load(self):
    try:
      with open(self.events_file) as f:
        data = json.load(f)
      self.events = data.get("events", [])
      self._next_id = data.get("next_id", 1)
    except (OSError, ValueError):
      self.events = []
      self._next_id = 1

  def save(self):
    os.makedirs(os.path.dirname(self.events_file), exist_ok=True)
    with open(self.events_file, "w") as f:
      f.write(json.dumps({"events": self.events,
                          "next_id": self._next_id}, indent=2))

  # ------------------------------------------------------------- commands

  def add_event(self, spec, curr_time):
    """
    spec (broadcast): {type, label?, text, target ("all"|"random:K"|[names]),
                       days_from_now, every_days?}
    spec (election): {type, label?, candidates ("random"|[names]),
                      campaign_days, days_from_now?, every_days?}
    Times are GAME days (floats allowed). Returns the stored event.
    """
    event = {"id": self._next_id,
             "type": spec["type"],
             "label": spec.get("label", "") or spec["type"],
             "every_days": float(spec.get("every_days") or 0),
             "history": []}
    self._next_id += 1

    days_from_now = float(spec.get("days_from_now") or 0)
    fire_at = curr_time + datetime.timedelta(days=days_from_now)

    if spec["type"] == "broadcast":
      event["text"] = spec["text"]
      event["target"] = spec.get("target", "all")
    elif spec["type"] == "election":
      event["candidates"] = spec.get("candidates", "random")
      event["campaign_days"] = float(spec.get("campaign_days") or 1)
      event["phase"] = "announce"
    else:
      raise ValueError(f"Unknown event type: {spec['type']}")

    event["fire_at"] = _dts(fire_at)
    self.events += [event]
    self.save()
    return event

  def remove_event(self, event_id):
    before = len(self.events)
    self.events = [e for e in self.events if e["id"] != int(event_id)]
    self.save()
    return len(self.events) < before

  def _process_command_file(self, curr_time):
    if not self.temp_storage:
      return
    commands = []

    # Legacy single-file queue, still consumed for compatibility.
    legacy_file = f"{self.temp_storage}/event_command.json"
    if os.path.isfile(legacy_file):
      try:
        with open(legacy_file) as f:
          commands += json.load(f)
        os.remove(legacy_file)
      except (OSError, ValueError):
        pass

    # One-file-per-command queue written by the /events/ page (avoids
    # read-modify-write races with the web server).
    commands_dir = f"{self.temp_storage}/event_commands"
    if os.path.isdir(commands_dir):
      for file_name in sorted(os.listdir(commands_dir)):
        if file_name.startswith(".") or not file_name.endswith(".json"):
          continue
        file_path = f"{commands_dir}/{file_name}"
        try:
          with open(file_path) as f:
            commands += [json.load(f)]
        except (OSError, ValueError):
          pass
        os.remove(file_path)

    for command in commands:
      try:
        if command.get("action") == "add":
          event = self.add_event(command["spec"], curr_time)
          print (f"[events] added #{event['id']} {event['label']} "
                 f"(fires {event['fire_at']})")
        elif command.get("action") == "remove":
          if self.remove_event(command["id"]):
            print (f"[events] removed #{command['id']}")
      except (KeyError, ValueError, TypeError) as e:
        print (f"[events] bad command {command}: {e}")

  # --------------------------------------------------------------- status

  def status_str(self):
    if not self.events:
      return ("No scheduled events. Add them with the /events/ web page "
              "or the 'election ...' commands.")
    lines = []
    for event in self.events:
      recur = (f", every {event['every_days']:g} days"
               if event["every_days"] else "")
      extra = ""
      if event["type"] == "election":
        extra = (f" [phase: {event.get('phase')}, candidates: "
                 f"{event.get('resolved_candidates') or event['candidates']}]")
      lines += [f"#{event['id']} {event['type']} '{event['label']}' -- "
                f"next: {event['fire_at']}{recur}{extra} "
                f"({len(event['history'])} past firings)"]
    return "\n".join(lines)

  # --------------------------------------------------------------- firing

  def check(self, curr_time, personas):
    """
    Called between simulation steps. Processes queued web commands, then
    fires every due event. Personas that have not taken a step yet cannot
    receive whispers, so we wait until the sim is warmed up.
    """
    self._process_command_file(curr_time)
    if not personas or any(not p.scratch.curr_time
                           for p in personas.values()):
      return
    for event in list(self.events):
      if _dt(event["fire_at"]) <= curr_time:
        try:
          if event["type"] == "broadcast":
            self._fire_broadcast(event, personas, curr_time)
          elif event["type"] == "election":
            if event.get("phase") == "announce":
              self._fire_election_announce(event, personas, curr_time)
            else:
              self._fire_election_vote(event, personas, curr_time)
        except Exception:
          import traceback
          traceback.print_exc()
          # Push the event forward a day so a persistent failure cannot
          # wedge every subsequent step.
          event["fire_at"] = _dts(curr_time + datetime.timedelta(days=1))
    self.save()

  def _finish_or_recur(self, event, fired_at):
    if event["every_days"]:
      event["fire_at"] = _dts(fired_at
                              + datetime.timedelta(days=event["every_days"]))
      if event["type"] == "election":
        event["phase"] = "announce"
        event.pop("resolved_candidates", None)
    else:
      self.events = [e for e in self.events if e["id"] != event["id"]]

  def _resolve_targets(self, target, personas):
    names = list(personas.keys())
    if isinstance(target, list):
      return [n for n in target if n in personas]
    if isinstance(target, str) and target.startswith("random:"):
      k = min(int(target.split(":")[1]), len(names))
      return random.sample(names, k)
    return names

  # broadcast ------------------------------------------------------------

  def _fire_broadcast(self, event, personas, curr_time):
    targets = self._resolve_targets(event.get("target", "all"), personas)
    for name in targets:
      self.whisper_fn(personas, name, event["text"])
    print (f"[events] broadcast '{event['label']}' delivered to "
           f"{len(targets)} personas")
    event["history"] += [{"at": _dts(curr_time), "targets": targets}]
    self._finish_or_recur(event, curr_time)

  # election -------------------------------------------------------------

  def _fire_election_announce(self, event, personas, curr_time):
    candidates = event.get("candidates", "random")
    if candidates == "random" or not isinstance(candidates, list):
      candidates = random.sample(list(personas.keys()),
                                 min(2, len(personas)))
    else:
      valid = [c for c in candidates if c in personas]
      if len(valid) < len(candidates):
        missing = [c for c in candidates if c not in personas]
        print (f"[events] election candidates not found in this sim: "
               f"{missing} -- falling back to random candidates")
        valid = random.sample(list(personas.keys()),
                              min(2, len(personas)))
      candidates = valid
    if len(candidates) < 2:
      # A sim with fewer than 2 personas cannot hold an election; postpone
      # a day rather than silently cancelling a recurring event.
      print ("[events] election needs at least 2 personas; postponing "
             "a day")
      event["fire_at"] = _dts(curr_time + datetime.timedelta(days=1))
      return

    vote_at = curr_time + datetime.timedelta(days=event["campaign_days"])
    vote_date = vote_at.strftime("%B %d")
    candidate_str = " and ".join(candidates)

    for name in personas:
      if name in candidates:
        self.whisper_fn(
          personas, name,
          f"You are running for mayor of the Ville; the election vote is "
          f"on {vote_date}. You want to campaign, tell everyone you meet "
          f"about your candidacy, and win their support")
      else:
        self.whisper_fn(
          personas, name,
          f"The Ville is holding a mayoral election: {candidate_str} are "
          f"running for mayor, and the vote is on {vote_date}. You have "
          f"your own opinion about the candidates and you want to discuss "
          f"the election with others")

    event["resolved_candidates"] = candidates
    event["announced_at"] = _dts(curr_time)
    event["phase"] = "vote"
    event["fire_at"] = _dts(vote_at)
    print (f"[events] election announced: {candidate_str}, vote on "
           f"{vote_date}")

  def _fire_election_vote(self, event, personas, curr_time):
    candidates = event.get("resolved_candidates") or []
    print (f"[events] election day! Polling {len(personas)} personas...")

    votes = {}
    with concurrent.futures.ThreadPoolExecutor(
        min(8, max(1, len(personas)))) as pool:
      futures = {pool.submit(self.vote_fn, persona, candidates): name
                 for name, persona in personas.items()}
      for future in concurrent.futures.as_completed(futures):
        name = futures[future]
        try:
          votes[name] = future.result()
        except Exception as e:
          votes[name] = {"vote": None, "reason": f"(abstained: {e})"}

    counts, winners = tally_votes(votes, candidates)
    total = len(votes)
    if len(winners) == 1:
      outcome = f"{winners[0]} won the mayoral election"
    elif winners:
      outcome = f"the mayoral election ended in a tie between " \
                f"{' and '.join(winners)}"
    else:
      outcome = "the mayoral election ended with no valid votes"
    counts_str = ", ".join(f"{c}: {n} votes" for c, n in counts.items())

    for name in personas:
      if name in winners and len(winners) == 1:
        text = (f"You won the mayoral election with {counts[name]} of "
                f"{total} votes! You feel grateful and want to celebrate "
                f"and thank your supporters")
      elif name in candidates:
        text = (f"The election results are out: {outcome} ({counts_str}). "
                f"You were a candidate and you have strong feelings about "
                f"this outcome that you want to share")
      else:
        own = votes.get(name, {}).get("vote")
        own_str = f"you voted for {own}" if own else "you abstained"
        text = (f"The election results are out: {outcome} ({counts_str}); "
                f"{own_str}. You have feelings about this outcome and want "
                f"to talk about it with others")
      self.whisper_fn(personas, name, text)

    print (f"[events] election results: {counts_str} -> {outcome}")
    event["history"] += [{"at": _dts(curr_time),
                          "candidates": candidates,
                          "counts": counts,
                          "winners": winners,
                          "votes": votes}]
    self._finish_or_recur(event, curr_time)

  # default LLM-backed implementations -----------------------------------

  @staticmethod
  def _default_whisper(personas, name, text):
    from persona.cognitive_modules.converse import load_history_via_whisper
    load_history_via_whisper(personas, [[name, text]])

  @staticmethod
  def _default_vote(persona, candidates):
    from persona.cognitive_modules.retrieve import new_retrieve
    from persona.prompt_template.gpt_structure import _chat_request

    focal = (f"the mayoral election and the candidates "
             f"{', '.join(candidates)}")
    memory_lines = []
    try:
      retrieved = new_retrieve(persona, [focal], n_count=15)
      for node in retrieved.get(focal, []):
        memory_lines += [f"- {node.description}"]
    except Exception:
      pass
    memories = "\n".join(memory_lines) or "- (no relevant memories)"

    name = persona.scratch.name
    prompt = f"""Here is a brief description of {name}:
{persona.scratch.get_str_iss()}

Here are {name}'s memories relevant to the mayoral election:
{memories}

Today is election day in the Ville. The candidates for mayor are:
{', '.join(candidates)}.
As {name}, decide who to vote for based on your own memories, relationships
and personality.
Output json: {{"vote": "<exact candidate full name>", "reason": "<one
sentence, in {name}'s voice, explaining the choice>"}}"""

    for attempt in range(3):
      try:
        response = json.loads(_chat_request(prompt, json_mode=True,
                                            cache_read=(attempt == 0)))
        if response.get("vote") in candidates:
          return {"vote": response["vote"],
                  "reason": str(response.get("reason", ""))[:500]}
      except Exception:
        pass
    return {"vote": None, "reason": "(abstained: no valid response)"}
