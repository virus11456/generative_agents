"""
File: scenario_generator.py
Description: Turn a one-paragraph story premise (in any language) into a
ready-to-run simulation scenario.

Given a base simulation to fork and a story description, this tool asks the
LLM to rewrite every persona's identity (personality, background, current
goals, lifestyle, daily routine) to fit the premise, and generates a set of
relationship/secret "whispers" for each persona. The rewritten identities are
written into a copy of the base simulation; the whispers are saved as a CSV
that the simulation can load with the existing `call -- load history`
machinery.

Usage (from reverie/backend_server, with your API key configured):

  python scenario_generator.py \
      --fork base_the_ville_isabella_maria_klaus \
      --name startup_drama \
      --story "三個室友合租公寓：一人偷偷計畫創業並想挖另外兩人入夥..."

Then run the simulation:

  python reverie.py
    Enter the name of the forked simulation: startup_drama
    Enter the name of the new simulation: startup_drama_run1
    Enter option: run 1
    Enter option: call -- load history the_ville/scenario_startup_drama.csv
    Enter option: run 1000
"""
import argparse
import csv
import json
import os
import shutil
import sys

from utils import *
from global_methods import copyanything
from persona.prompt_template.gpt_structure import _chat_request

IDENTITY_KEYS = ["innate", "learned", "currently", "lifestyle",
                 "daily_plan_req"]


def build_identity_prompt(story, persona_name, other_names, reference):
  ref_lines = "\n".join(f"  {k}: {reference.get(k, '')}"
                        for k in IDENTITY_KEYS)
  others = ", ".join(other_names) if other_names else "none"
  return f"""You are designing characters for a social simulation set in a small town called "the Ville".

Story premise (may be written in any language; your output must be in ENGLISH):
\"\"\"{story}\"\"\"

Rewrite the identity of the character below so it fits the story premise.
Rules:
- Keep the character's name exactly: {persona_name}
- The other characters in this simulation are: {others}. Make the identities consistent with each other and give this character concrete relationships to them where the premise implies any.
- Be concrete and realistic; no fantasy elements unless the premise asks for them.
- Write in third person for identity fields ("{persona_name} is ...").

Reference (the character's ORIGINAL identity -- use it ONLY as a formatting guide):
{ref_lines}

Output a json object with exactly these keys:
- "innate": 3-5 comma-separated personality traits
- "learned": 2-4 sentences of background about who this character is
- "currently": 1-3 sentences about the character's current situation and goals in the story
- "lifestyle": one sentence of the form "{persona_name} goes to bed around 11pm, awakes up around 6am."
- "daily_plan_req": 1-2 sentences describing the character's typical daily routine
- "whispers": a list of 3-6 short facts addressed to the character as "you" (e.g. "You secretly plan to start a company"), covering relationships with the other characters, secrets, and goals from the premise"""


def generate_identity(story, persona_name, other_names, reference,
                      retries=3):
  prompt = build_identity_prompt(story, persona_name, other_names, reference)
  for _ in range(retries):
    try:
      raw = _chat_request(prompt, json_mode=True)
      identity = json.loads(raw)
      if validate_identity(identity):
        return identity
    except Exception as e:
      print(f"  retrying ({e})")
  raise RuntimeError(f"Could not generate a valid identity for "
                     f"{persona_name} after {retries} attempts.")


def validate_identity(identity):
  if not isinstance(identity, dict):
    return False
  for key in IDENTITY_KEYS:
    if not isinstance(identity.get(key), str) or not identity[key].strip():
      return False
  whispers = identity.get("whispers")
  if not isinstance(whispers, list) or not whispers:
    return False
  return all(isinstance(w, str) and w.strip() for w in whispers)


def apply_identity(scratch, identity):
  """Merge the generated identity fields into a scratch.json dict."""
  for key in IDENTITY_KEYS:
    scratch[key] = identity[key].strip()
  return scratch


def whispers_to_csv_rows(identities):
  """[(persona_name, identity), ...] -> rows for an agent-history CSV."""
  rows = [["Name", "Whisper"]]
  for persona_name, identity in identities:
    joined = "; ".join(w.strip().rstrip(";") for w in identity["whispers"])
    rows += [[persona_name, joined]]
  return rows


def generate_scenario(fork_sim_code, new_sim_code, story):
  fork_folder = f"{fs_storage}/{fork_sim_code}"
  sim_folder = f"{fs_storage}/{new_sim_code}"

  if not os.path.isdir(fork_folder):
    sys.exit(f"Base simulation not found: {fork_folder}")
  if os.path.exists(sim_folder):
    sys.exit(f"Target simulation already exists: {sim_folder}")

  copyanything(fork_folder, sim_folder)

  with open(f"{sim_folder}/reverie/meta.json") as f:
    meta = json.load(f)
  persona_names = meta["persona_names"]
  print(f"Generating identities for {len(persona_names)} personas...")

  identities = []
  try:
    for persona_name in persona_names:
      scratch_path = (f"{sim_folder}/personas/{persona_name}/"
                      f"bootstrap_memory/scratch.json")
      with open(scratch_path) as f:
        scratch = json.load(f)
      reference = {k: scratch.get(k, "") for k in IDENTITY_KEYS}
      other_names = [n for n in persona_names if n != persona_name]

      print(f"- {persona_name}...")
      identity = generate_identity(story, persona_name, other_names,
                                   reference)
      apply_identity(scratch, identity)
      with open(scratch_path, "w") as f:
        f.write(json.dumps(scratch, indent=2))
      identities += [(persona_name, identity)]
  except Exception:
    shutil.rmtree(sim_folder)
    raise

  csv_name = f"scenario_{new_sim_code}.csv"
  csv_path = f"{maze_assets_loc}/the_ville/{csv_name}"
  with open(csv_path, "w", newline="") as f:
    csv.writer(f).writerows(whispers_to_csv_rows(identities))

  print(f"""
Done. Scenario '{new_sim_code}' created.

Next steps -- run the simulation and load the relationship whispers:

  python reverie.py
    Enter the name of the forked simulation: {new_sim_code}
    Enter the name of the new simulation: <any name>
    Enter option: run 1
    Enter option: call -- load history the_ville/{csv_name}
    Enter option: run <as many steps as you like>
""")


if __name__ == "__main__":
  parser = argparse.ArgumentParser(
    description="Generate a simulation scenario from a story premise.")
  parser.add_argument("--fork", default="base_the_ville_isabella_maria_klaus",
                      help="base simulation to copy (default: the 3-persona "
                           "base sim; use base_the_ville_n25 for 25 personas)")
  parser.add_argument("--name", required=True,
                      help="name of the new scenario simulation")
  parser.add_argument("--story", required=True,
                      help="story premise, in any language")
  args = parser.parse_args()
  generate_scenario(args.fork, args.name, args.story)
