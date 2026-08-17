"""
File: traits.py
Description: Personality traits and the social fabric of the town.

Traits: a library of ~100 positive/negative/quirk personality entries
(traits.json). When a new simulation is created, every persona draws a
hand of traits (positives + negatives + a quirk, rarity-weighted,
conflicting traits excluded). The draw is materialized twice:

  1. The trait words are appended to the persona's `innate` field, which
     flows into nearly every prompt (planning, conversations, reactions,
     election votes) automatically.
  2. Each trait's behavior sentence is planted into the persona's memory
     stream as a whisper (delivered through a one-shot broadcast event on
     the next running step), so the persona *remembers* being that person.

Relationships: a structured web -- couples, secret crushes (one-way: only
the smitten party knows), siblings, best friends, rivals -- generated for
new sims, registered in <sim>/reverie/relationships.json, and planted as
whispers the same way. Same-surname personas are never paired romantically
(they are treated as family), and siblings are only drawn from
same-surname pairs.

Both registries are stored inside the simulation folder, so they survive
save/fork like everything else.
"""
import datetime
import json
import os
import random

_LIBRARY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "traits.json")
_RARITY_WEIGHTS = {"common": 3, "uncommon": 2, "rare": 1}


def load_trait_library(path=None):
  with open(path or _LIBRARY_PATH) as f:
    return json.load(f)["traits"]


def draw_traits(library, n_positive=2, n_negative=1, n_quirk=1, rng=random):
  """
  Draw a conflict-free, rarity-weighted hand of traits. Returns a list of
  trait dicts.
  """
  drawn = []
  taken = set()
  blocked = set()

  def draw_from(pool, count):
    candidates = [t for t in pool
                  if t["id"] not in taken and t["id"] not in blocked]
    for _ in range(count):
      if not candidates:
        break
      weights = [_RARITY_WEIGHTS.get(t["rarity"], 1) for t in candidates]
      pick = rng.choices(candidates, weights=weights, k=1)[0]
      drawn.append(pick)
      taken.add(pick["id"])
      blocked.update(pick["conflicts"])
      candidates = [t for t in candidates
                    if t["id"] not in taken and t["id"] not in blocked]

  draw_from([t for t in library if t["polarity"] == "positive"], n_positive)
  draw_from([t for t in library if t["polarity"] == "negative"], n_negative)
  draw_from([t for t in library if t["polarity"] == "quirk"], n_quirk)
  return drawn


# ---------------------------------------------------------------------------
# Relationships
# ---------------------------------------------------------------------------

REL_TYPES = ["partner", "crush", "sibling", "best_friend", "rival"]


def _surname(name):
  return name.split()[-1] if name.split() else name


def generate_relationships(persona_names, rng=random):
  """
  Draw a relationship web for the town. Romantic ties (partner/crush) are
  never drawn between same-surname personas; siblings only between
  same-surname pairs. Each persona gets at most one partner; a pair holds
  at most one relationship. Crushes are one-way and may point at anyone
  romantically eligible -- including someone already taken (drama).
  """
  names = list(persona_names)
  n = len(names)
  relationships = []
  paired = set()      # personas with a partner
  used_pairs = set()  # frozensets of pairs already related

  def eligible_romantic(a, b):
    return _surname(a) != _surname(b) and frozenset((a, b)) not in used_pairs

  # Partners: ~1 couple per 6 residents.
  shuffled = names[:]
  rng.shuffle(shuffled)
  couples_wanted = max(0, n // 6)
  for a in shuffled:
    if couples_wanted <= 0:
      break
    if a in paired:
      continue
    candidates = [b for b in shuffled
                  if b != a and b not in paired and eligible_romantic(a, b)]
    if not candidates:
      continue
    b = rng.choice(candidates)
    relationships.append({"type": "partner", "a": a, "b": b})
    used_pairs.add(frozenset((a, b)))
    paired.update((a, b))
    couples_wanted -= 1

  # Siblings: reinforce same-surname pairs.
  by_surname = {}
  for name in names:
    by_surname.setdefault(_surname(name), []).append(name)
  for family in by_surname.values():
    for i in range(len(family) - 1):
      pair = frozenset((family[i], family[i + 1]))
      if pair not in used_pairs:
        relationships.append({"type": "sibling",
                              "a": family[i], "b": family[i + 1]})
        used_pairs.add(pair)

  # Secret crushes: ~1 per 5 residents, one-way.
  crushes_wanted = max(1 if n >= 2 else 0, n // 5)
  rng.shuffle(shuffled)
  for a in shuffled:
    if crushes_wanted <= 0:
      break
    candidates = [b for b in names if b != a and eligible_romantic(a, b)]
    if not candidates:
      continue
    b = rng.choice(candidates)
    relationships.append({"type": "crush", "a": a, "b": b})
    used_pairs.add(frozenset((a, b)))
    crushes_wanted -= 1

  # Best friends and rivals: ~1 of each per 8 residents.
  for rel_type, count in [("best_friend", max(0, n // 8)),
                          ("rival", max(0, n // 8))]:
    rng.shuffle(shuffled)
    remaining = count
    for a in shuffled:
      if remaining <= 0:
        break
      candidates = [b for b in names if b != a
                    and frozenset((a, b)) not in used_pairs]
      if not candidates:
        continue
      b = rng.choice(candidates)
      relationships.append({"type": rel_type, "a": a, "b": b})
      used_pairs.add(frozenset((a, b)))
      remaining -= 1

  return relationships


def relationship_whispers(relationship):
  """One relationship -> [(persona_name, whisper_text), ...]. Crushes are
  whispered only to the smitten party."""
  a, b = relationship["a"], relationship["b"]
  rel_type = relationship["type"]
  if rel_type == "partner":
    return [(a, f"You and {b} are a couple; you care deeply about {b} and "
                f"like to spend time together"),
            (b, f"You and {a} are a couple; you care deeply about {a} and "
                f"like to spend time together")]
  if rel_type == "crush":
    return [(a, f"You have a secret crush on {b}; you have not told anyone "
                f"and you get flustered around {b}")]
  if rel_type == "sibling":
    return [(a, f"You and {b} are siblings; you bicker sometimes but "
                f"always look out for each other"),
            (b, f"You and {a} are siblings; you bicker sometimes but "
                f"always look out for each other")]
  if rel_type == "best_friend":
    return [(a, f"{b} is your best friend; you tell {b} things you tell "
                f"no one else"),
            (b, f"{a} is your best friend; you tell {a} things you tell "
                f"no one else")]
  if rel_type == "rival":
    return [(a, f"You and {b} are rivals; you compete over almost "
                f"everything and it colors how you speak to each other"),
            (b, f"You and {a} are rivals; you compete over almost "
                f"everything and it colors how you speak to each other")]
  return []


# ---------------------------------------------------------------------------
# Assignment to a simulation
# ---------------------------------------------------------------------------

def registry_path(sim_folder):
  return f"{sim_folder}/reverie/traits.json"


def relationships_path(sim_folder):
  return f"{sim_folder}/reverie/relationships.json"


def load_registry(sim_folder):
  try:
    with open(registry_path(sim_folder)) as f:
      return json.load(f)
  except (OSError, ValueError):
    return {}


def load_relationships(sim_folder):
  try:
    with open(relationships_path(sim_folder)) as f:
      return json.load(f)
  except (OSError, ValueError):
    return []


def assign_to_sim(sim_folder, event_manager=None, rng=random,
                  with_relationships=True, library=None):
  """
  Draw traits for every persona in the simulation (skipped if a registry
  already exists, e.g. on a resumed fork): append trait words to each
  scratch.json's innate field, save the registry, generate the
  relationship web, and queue every behavior/relationship whisper as
  one-shot broadcast events so they land in memory on the next running
  step. Returns (registry, relationships).
  """
  if load_registry(sim_folder):
    return load_registry(sim_folder), load_relationships(sim_folder)

  with open(f"{sim_folder}/reverie/meta.json") as f:
    persona_names = json.load(f)["persona_names"]
  library = library or load_trait_library()

  registry = {}
  whispers = []  # (persona, text)
  for persona_name in persona_names:
    hand = draw_traits(library, rng=rng)
    registry[persona_name] = [t["id"] for t in hand]

    scratch_path = (f"{sim_folder}/personas/{persona_name}/"
                    f"bootstrap_memory/scratch.json")
    with open(scratch_path) as f:
      scratch = json.load(f)
    trait_words = ", ".join(t["innate_text"] for t in hand)
    scratch["innate"] = (f"{scratch.get('innate', '').strip().rstrip(',')}"
                         f", {trait_words}").strip(", ")
    with open(scratch_path, "w") as f:
      f.write(json.dumps(scratch, indent=2))

    for trait in hand:
      whispers.append((persona_name, trait["behavior"].rstrip(".")))

  with open(registry_path(sim_folder), "w") as f:
    f.write(json.dumps(registry, indent=2))

  relationships = []
  if with_relationships and len(persona_names) >= 2:
    relationships = generate_relationships(persona_names, rng=rng)
    with open(relationships_path(sim_folder), "w") as f:
      f.write(json.dumps(relationships, indent=2))
    for relationship in relationships:
      whispers += relationship_whispers(relationship)

  # Deliver all whispers through the event engine on the next running
  # step (whispers need personas to have taken a step; events handle that).
  if event_manager is not None:
    by_persona = {}
    for persona_name, text in whispers:
      by_persona.setdefault(persona_name, []).append(text)
    # A safely-parseable date far in the past makes the event due on the
    # very first check (game clocks start in 2023).
    for persona_name, texts in by_persona.items():
      event_manager.add_event(
        {"type": "broadcast",
         "label": f"persona seed: {persona_name}",
         "text": "; ".join(texts),
         "target": [persona_name]},
        curr_time=datetime.datetime(2000, 1, 1))

  return registry, relationships


def format_registry(sim_folder, library=None):
  registry = load_registry(sim_folder)
  if not registry:
    return "No traits assigned to this simulation."
  library = library or load_trait_library()
  by_id = {t["id"]: t for t in library}
  lines = []
  for persona_name, trait_ids in registry.items():
    parts = []
    for trait_id in trait_ids:
      trait = by_id.get(trait_id)
      parts += [f"{trait['name_zh']}({trait_id})" if trait else trait_id]
    lines += [f"{persona_name}: {', '.join(parts)}"]
  relationships = load_relationships(sim_folder)
  if relationships:
    lines += ["", "Relationships:"]
    for r in relationships:
      lines += [f"  {r['a']} --{r['type']}-- {r['b']}"]
  return "\n".join(lines)
