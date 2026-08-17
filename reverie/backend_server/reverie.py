"""
Author: Joon Sung Park (joonspk@stanford.edu)

File: reverie.py
Description: This is the main program for running generative agent simulations
that defines the ReverieServer class. This class maintains and records all  
states related to the simulation. The primary mode of interaction for those  
running the simulation should be through the open_server function, which  
enables the simulator to input command-line prompts for running and saving  
the simulation, among other tasks.

Release note (June 14, 2023) -- Reverie implements the core simulation 
mechanism described in my paper entitled "Generative Agents: Interactive 
Simulacra of Human Behavior." If you are reading through these lines after 
having read the paper, you might notice that I use older terms to describe 
generative agents and their cognitive modules here. Most notably, I use the 
term "personas" to refer to generative agents, "associative memory" to refer 
to the memory stream, and "reverie" to refer to the overarching simulation 
framework.
"""
import concurrent.futures
import json
import numpy
import datetime
import pickle
import random
import time
import math
import os
import shutil
import traceback

from global_methods import *
from utils import *
from maze import *
from persona.persona import *
from persona.cognitive_modules.plan import apply_pending_reactions
from events import EventManager
from economy import EconomyManager
import chronicle
import traits as traits_module
from persona.prompt_template.gpt_structure import (format_llm_stats,
                                                   get_llm_stats,
                                                   save_llm_stats)

# Performance/robustness knobs (overridable in utils.py; the globals().get
# fallbacks keep older hand-written utils.py files working).
PARALLEL_PERSONAS = globals().get("parallel_personas", True)
MAX_PARALLEL_WORKERS = globals().get("max_parallel_workers", 8)
CHECKPOINT_FREQ = globals().get("checkpoint_freq", 50)
COST_LIMIT_USD = globals().get("cost_limit_usd", 0)
HEADLESS_MODE = globals().get("headless_mode", False)
SEC_PER_STEP_OVERRIDE = globals().get("sec_per_step_override", 0)
REAL_MINUTES_PER_DAY = globals().get("real_minutes_per_day", 0.0)
CHRONICLE_ENABLED = globals().get("chronicle_enabled", True)
CHRONICLE_LANG = globals().get("chronicle_lang",
                               "Traditional Chinese (繁體中文)")
TRAITS_AUTO = globals().get("traits_auto", True)
ECONOMY_ENABLED = globals().get("economy_enabled", True)
ECON_STARTING_BALANCE = globals().get("econ_starting_balance", 100.0)
ECON_DAILY_WAGE = globals().get("econ_daily_wage", 80.0)


def write_headless_environment(sim_folder, step, movements):
  """
  In headless mode the backend plays the frontend's role: after computing
  the personas' next tiles, it writes the environment file the next step
  will consume, so the world advances without any browser attached.
  """
  env = {}
  for persona_name, info in movements["persona"].items():
    x, y = info["movement"]
    env[persona_name] = {"maze": "the_ville", "x": x, "y": y}
  with open(f"{sim_folder}/environment/{step}.json", "w") as outfile:
    outfile.write(json.dumps(env, indent=2))

##############################################################################
#                                  REVERIE                                   #
##############################################################################

class ReverieServer: 
  def __init__(self, 
               fork_sim_code,
               sim_code):
    # FORKING FROM A PRIOR SIMULATION:
    # <fork_sim_code> indicates the simulation we are forking from. 
    # Interestingly, all simulations must be forked from some initial 
    # simulation, where the first simulation is "hand-crafted".
    self.fork_sim_code = fork_sim_code
    fork_folder = f"{fs_storage}/{self.fork_sim_code}"

    # <sim_code> indicates our current simulation. The first step here is to
    # copy everything that's in <fork_sim_code>, but edit its
    # reverie/meta/json's fork variable.
    self.sim_code = sim_code
    sim_folder = f"{fs_storage}/{self.sim_code}"
    copyanything(fork_folder, sim_folder)

    # git cannot track empty directories, so a freshly-cloned base sim has
    # no movement/ folder -- create it (and environment/) so the first
    # step's writes cannot die on a missing path.
    os.makedirs(f"{sim_folder}/movement", exist_ok=True)
    os.makedirs(f"{sim_folder}/environment", exist_ok=True)

    with open(f"{sim_folder}/reverie/meta.json") as json_file:  
      reverie_meta = json.load(json_file)

    with open(f"{sim_folder}/reverie/meta.json", "w") as outfile: 
      reverie_meta["fork_sim_code"] = fork_sim_code
      outfile.write(json.dumps(reverie_meta, indent=2))

    # LOADING REVERIE'S GLOBAL VARIABLES
    # The start datetime of the Reverie: 
    # <start_datetime> is the datetime instance for the start datetime of 
    # the Reverie instance. Once it is set, this is not really meant to 
    # change. It takes a string date in the following example form: 
    # "June 25, 2022"
    # e.g., ...strptime(June 25, 2022, "%B %d, %Y")
    self.start_time = datetime.datetime.strptime(
                        f"{reverie_meta['start_date']}, 00:00:00",  
                        "%B %d, %Y, %H:%M:%S")
    # <curr_time> is the datetime instance that indicates the game's current
    # time. This gets incremented by <sec_per_step> amount everytime the world
    # progresses (that is, everytime curr_env_file is recieved). 
    self.curr_time = datetime.datetime.strptime(reverie_meta['curr_time'], 
                                                "%B %d, %Y, %H:%M:%S")
    # <sec_per_step> denotes the number of seconds in game time that each
    # step moves foward.
    self.sec_per_step = reverie_meta['sec_per_step']
    if SEC_PER_STEP_OVERRIDE:
      self.sec_per_step = SEC_PER_STEP_OVERRIDE
    # Pacing floor: with real_minutes_per_day set, each step must take at
    # least this many real seconds so a game day lasts about that long.
    # (Steps that need heavy LLM work can still take longer.)
    self.min_real_sec_per_step = 0.0
    if REAL_MINUTES_PER_DAY and self.sec_per_step:
      steps_per_day = 86400 / self.sec_per_step
      self.min_real_sec_per_step = (REAL_MINUTES_PER_DAY * 60
                                    / steps_per_day)
    
    # <maze> is the main Maze instance. Note that we pass in the maze_name
    # (e.g., "double_studio") to instantiate Maze. 
    # e.g., Maze("double_studio")
    self.maze = Maze(reverie_meta['maze_name'])
    
    # <step> denotes the number of steps that our game has taken. A step here
    # literally translates to the number of moves our personas made in terms
    # of the number of tiles. 
    self.step = reverie_meta['step']

    # SETTING UP PERSONAS IN REVERIE
    # <personas> is a dictionary that takes the persona's full name as its 
    # keys, and the actual persona instance as its values.
    # This dictionary is meant to keep track of all personas who are part of
    # the Reverie instance. 
    # e.g., ["Isabella Rodriguez"] = Persona("Isabella Rodriguezs")
    self.personas = dict()
    # <personas_tile> is a dictionary that contains the tile location of
    # the personas (!-> NOT px tile, but the actual tile coordinate).
    # The tile take the form of a set, (row, col). 
    # e.g., ["Isabella Rodriguez"] = (58, 39)
    self.personas_tile = dict()
    
    # # <persona_convo_match> is a dictionary that describes which of the two
    # # personas are talking to each other. It takes a key of a persona's full
    # # name, and value of another persona's full name who is talking to the 
    # # original persona. 
    # # e.g., dict["Isabella Rodriguez"] = ["Maria Lopez"]
    # self.persona_convo_match = dict()
    # # <persona_convo> contains the actual content of the conversations. It
    # # takes as keys, a pair of persona names, and val of a string convo. 
    # # Note that the key pairs are *ordered alphabetically*. 
    # # e.g., dict[("Adam Abraham", "Zane Xu")] = "Adam: baba \n Zane:..."
    # self.persona_convo = dict()

    # Loading in all personas. 
    init_env_file = f"{sim_folder}/environment/{str(self.step)}.json"
    init_env = json.load(open(init_env_file))
    for persona_name in reverie_meta['persona_names']: 
      persona_folder = f"{sim_folder}/personas/{persona_name}"
      p_x = init_env[persona_name]["x"]
      p_y = init_env[persona_name]["y"]
      curr_persona = Persona(persona_name, persona_folder)

      self.personas[persona_name] = curr_persona
      self.personas_tile[persona_name] = (p_x, p_y)
      self.maze.tiles[p_y][p_x]["events"].add(curr_persona.scratch
                                              .get_curr_event_and_desc())

    # REVERIE SETTINGS PARAMETERS:
    # <server_sleep> denotes the amount of time that our while loop rests each
    # cycle; this is to not kill our machine.
    self.server_sleep = 0.1

    # Whether the 80%-of-cost-limit warning has been printed yet.
    self._cost_warned = False

    # World-event engine (elections, festivals, rumors, custom broadcasts).
    # Events persist in <sim_folder>/reverie/events.json across save/fork.
    self.events = EventManager(sim_folder, fs_temp_storage)

    # Personality traits + relationship web: drawn once per simulation
    # lineage (skipped when the forked sim already carries a registry).
    if TRAITS_AUTO:
      try:
        had_registry = bool(traits_module.load_registry(sim_folder))
        traits_module.assign_to_sim(sim_folder, self.events)
        if not had_registry:
          print ("[traits] drew personality traits and relationships for "
                 "this town -- type 'traits show' to inspect them.")
      except Exception:
        traceback.print_exc()

    # Economy: wallets, wages, venue spending, conversation trades.
    self.economy = None
    if ECONOMY_ENABLED:
      self.economy = EconomyManager(
        sim_folder, starting_balance=ECON_STARTING_BALANCE,
        daily_wage=ECON_DAILY_WAGE)
      self.economy.ensure_personas(self.personas.keys())

    # Headless mode (no browser needed) and the daily chronicle.
    self.headless = HEADLESS_MODE
    self._day_start_step = self.step

    # SIGNALING THE FRONTEND SERVER: 
    # curr_sim_code.json contains the current simulation code, and
    # curr_step.json contains the current step of the simulation. These are 
    # used to communicate the code and step information to the frontend. 
    # Note that step file is removed as soon as the frontend opens up the 
    # simulation. 
    curr_sim_code = dict()
    curr_sim_code["sim_code"] = self.sim_code
    with open(f"{fs_temp_storage}/curr_sim_code.json", "w") as outfile: 
      outfile.write(json.dumps(curr_sim_code, indent=2))
    
    curr_step = dict()
    curr_step["step"] = self.step
    with open(f"{fs_temp_storage}/curr_step.json", "w") as outfile: 
      outfile.write(json.dumps(curr_step, indent=2))


  def save(self): 
    """
    Save all Reverie progress -- this includes Reverie's global state as well
    as all the personas.  

    INPUT
      None
    OUTPUT 
      None
      * Saves all relevant data to the designated memory directory
    """
    # <sim_folder> points to the current simulation folder.
    sim_folder = f"{fs_storage}/{self.sim_code}"

    # Save Reverie meta information.
    reverie_meta = dict() 
    reverie_meta["fork_sim_code"] = self.fork_sim_code
    reverie_meta["start_date"] = self.start_time.strftime("%B %d, %Y")
    reverie_meta["curr_time"] = self.curr_time.strftime("%B %d, %Y, %H:%M:%S")
    reverie_meta["sec_per_step"] = self.sec_per_step
    reverie_meta["maze_name"] = self.maze.maze_name
    reverie_meta["persona_names"] = list(self.personas.keys())
    reverie_meta["step"] = self.step
    reverie_meta_f = f"{sim_folder}/reverie/meta.json"
    with open(reverie_meta_f, "w") as outfile: 
      outfile.write(json.dumps(reverie_meta, indent=2))

    # Save the personas.
    for persona_name, persona in self.personas.items():
      save_folder = f"{sim_folder}/personas/{persona_name}/bootstrap_memory"
      persona.save(save_folder)

    # Save LLM usage/cost statistics alongside the simulation.
    try:
      save_llm_stats(f"{sim_folder}/reverie/llm_stats.json")
    except Exception:
      pass


  def _apply_pending_interventions(self):
    """
    The frontend's /intervene page queues whispers as one file per whisper
    under <fs_temp_storage>/interventions/ (one-file-per-command avoids
    read-modify-write races with the web server). Between simulation steps
    we pick them up and inject them into the personas' memory streams.
    Whispers for personas that have not taken a step yet (no curr_time)
    stay queued for the next cycle.
    """
    pending = []  # (file_path_or_None, item)

    # Legacy single-file queue, still consumed for compatibility.
    legacy_file = f"{fs_temp_storage}/interventions.json"
    if check_if_file_exists(legacy_file):
      try:
        with open(legacy_file) as f:
          pending += [(None, item) for item in json.load(f)]
        os.remove(legacy_file)
      except (OSError, ValueError):
        pass

    interventions_dir = f"{fs_temp_storage}/interventions"
    if os.path.isdir(interventions_dir):
      for file_name in sorted(os.listdir(interventions_dir)):
        if file_name.startswith(".") or not file_name.endswith(".json"):
          continue
        file_path = f"{interventions_dir}/{file_name}"
        try:
          with open(file_path) as f:
            pending += [(file_path, json.load(f))]
        except (OSError, ValueError):
          os.remove(file_path)

    for file_path, item in pending:
      persona_name = str(item.get("persona", "")).strip()
      whisper = str(item.get("whisper", "")).strip()
      if persona_name not in self.personas or not whisper:
        print (f"[intervene] dropped invalid whisper: {item}")
        if file_path:
          os.remove(file_path)
        continue
      if not self.personas[persona_name].scratch.curr_time:
        # Not started yet -- leave the file queued for the next cycle.
        if file_path is None:
          os.makedirs(interventions_dir, exist_ok=True)
          requeue_path = (f"{interventions_dir}/"
                          f"legacy_{random.randint(0, 10**9)}.json")
          with open(requeue_path, "w") as f:
            f.write(json.dumps(item))
        continue
      try:
        load_history_via_whisper(self.personas, [[persona_name, whisper]])
        print (f"[intervene] whispered to {persona_name}: {whisper}")
      except Exception:
        traceback.print_exc()
      if file_path:
        os.remove(file_path)


  def start_path_tester_server(self):
    """
    Starts the path tester server. This is for generating the spatial memory
    that we need for bootstrapping a persona's state. 

    To use this, you need to open server and enter the path tester mode, and
    open the front-end side of the browser. 

    INPUT 
      None
    OUTPUT 
      None
      * Saves the spatial memory of the test agent to the path_tester_env.json
        of the temp storage. 
    """
    def print_tree(tree): 
      def _print_tree(tree, depth):
        dash = " >" * depth

        if type(tree) == type(list()): 
          if tree:
            print (dash, tree)
          return 

        for key, val in tree.items(): 
          if key: 
            print (dash, key)
          _print_tree(val, depth+1)
      
      _print_tree(tree, 0)

    # <curr_vision> is the vision radius of the test agent. Recommend 8 as 
    # our default. 
    curr_vision = 8
    # <s_mem> is our test spatial memory. 
    s_mem = dict()

    # The main while loop for the test agent. 
    while (True): 
      try: 
        curr_dict = {}
        tester_file = fs_temp_storage + "/path_tester_env.json"
        if check_if_file_exists(tester_file): 
          with open(tester_file) as json_file: 
            curr_dict = json.load(json_file)
            os.remove(tester_file)
          
          # Current camera location
          curr_sts = self.maze.sq_tile_size
          curr_camera = (int(math.ceil(curr_dict["x"]/curr_sts)), 
                         int(math.ceil(curr_dict["y"]/curr_sts))+1)
          curr_tile_det = self.maze.access_tile(curr_camera)

          # Initiating the s_mem
          world = curr_tile_det["world"]
          if curr_tile_det["world"] not in s_mem: 
            s_mem[world] = dict()

          # Iterating throughn the nearby tiles.
          nearby_tiles = self.maze.get_nearby_tiles(curr_camera, curr_vision)
          for i in nearby_tiles: 
            i_det = self.maze.access_tile(i)
            if (curr_tile_det["sector"] == i_det["sector"] 
                and curr_tile_det["arena"] == i_det["arena"]): 
              if i_det["sector"] != "": 
                if i_det["sector"] not in s_mem[world]: 
                  s_mem[world][i_det["sector"]] = dict()
              if i_det["arena"] != "": 
                if i_det["arena"] not in s_mem[world][i_det["sector"]]: 
                  s_mem[world][i_det["sector"]][i_det["arena"]] = list()
              if i_det["game_object"] != "": 
                if (i_det["game_object"] 
                    not in s_mem[world][i_det["sector"]][i_det["arena"]]):
                  s_mem[world][i_det["sector"]][i_det["arena"]] += [
                                                         i_det["game_object"]]

        # Incrementally outputting the s_mem and saving the json file. 
        print ("= " * 15)
        out_file = fs_temp_storage + "/path_tester_out.json"
        with open(out_file, "w") as outfile: 
          outfile.write(json.dumps(s_mem, indent=2))
        print_tree(s_mem)

      except:
        pass

      time.sleep(self.server_sleep * 10)


  def start_server(self, int_counter): 
    """
    The main backend server of Reverie. 
    This function retrieves the environment file from the frontend to 
    understand the state of the world, calls on each personas to make 
    decisions based on the world state, and saves their moves at certain step
    intervals. 
    INPUT
      int_counter: Integer value for the number of steps left for us to take
                   in this iteration. 
    OUTPUT 
      None
    """
    # <sim_folder> points to the current simulation folder.
    sim_folder = f"{fs_storage}/{self.sim_code}"

    # When a persona arrives at a game object, we give a unique event
    # to that object. 
    # e.g., ('double studio[...]:bed', 'is', 'unmade', 'unmade')
    # Later on, before this cycle ends, we need to return that to its 
    # initial state, like this: 
    # e.g., ('double studio[...]:bed', None, None, None)
    # So we need to keep track of which event we added. 
    # <game_obj_cleanup> is used for that. 
    game_obj_cleanup = dict()

    # The main while loop of Reverie. 
    while (True): 
      # Done with this iteration if <int_counter> reaches 0. 
      if int_counter == 0: 
        break

      # <curr_env_file> file is the file that our frontend outputs. When the
      # frontend has done its job and moved the personas, then it will put a 
      # new environment file that matches our step count. That's when we run 
      # the content of this for loop. Otherwise, we just wait. 
      curr_env_file = f"{sim_folder}/environment/{self.step}.json"
      env_retrieved = False
      if check_if_file_exists(curr_env_file):
        # If we have an environment file, it means we have a new perception
        # input to our personas. So we first retrieve it.
        try:
          # Try and save block for robustness of the while loop.
          with open(curr_env_file) as json_file:
            new_env = json.load(json_file)
            env_retrieved = True
        except:
          pass
      
        if env_retrieved:
          step_started_at = time.time()

          # Apply any whispers queued from the frontend's /intervene page.
          self._apply_pending_interventions()

          # Fire any due world events (elections, festivals, rumors...).
          self.events.check(self.curr_time, self.personas)

          # This is where we go through <game_obj_cleanup> to clean up all
          # object actions that were used in this cylce.
          for key, val in game_obj_cleanup.items(): 
            # We turn all object actions to their blank form (with None). 
            self.maze.turn_event_from_tile_idle(key, val)
          # Then we initialize game_obj_cleanup for this cycle. 
          game_obj_cleanup = dict()

          # We first move our personas in the backend environment to match 
          # the frontend environment. 
          for persona_name, persona in self.personas.items(): 
            # <curr_tile> is the tile that the persona was at previously. 
            curr_tile = self.personas_tile[persona_name]
            # <new_tile> is the tile that the persona will move to right now,
            # during this cycle. 
            new_tile = (new_env[persona_name]["x"], 
                        new_env[persona_name]["y"])

            # We actually move the persona on the backend tile map here. 
            self.personas_tile[persona_name] = new_tile
            self.maze.remove_subject_events_from_tile(persona.name, curr_tile)
            self.maze.add_event_from_tile(persona.scratch
                                         .get_curr_event_and_desc(), new_tile)

            # Now, the persona will travel to get to their destination. *Once*
            # the persona gets there, we activate the object action.
            if not persona.scratch.planned_path: 
              # We add that new object action event to the backend tile map. 
              # At its creation, it is stored in the persona's backend. 
              game_obj_cleanup[persona.scratch
                               .get_curr_obj_event_and_desc()] = new_tile
              self.maze.add_event_from_tile(persona.scratch
                                     .get_curr_obj_event_and_desc(), new_tile)
              # We also need to remove the temporary blank action for the 
              # object that is currently taking the action. 
              blank = (persona.scratch.get_curr_obj_event_and_desc()[0], 
                       None, None, None)
              self.maze.remove_event_from_tile(blank, new_tile)

          # Then we need to actually have each of the personas perceive and
          # move. The movement for each of the personas comes in the form of
          # x y coordinates where the persona will move towards. e.g., (50, 34)
          # This is where the core brains of the personas are invoked. 
          movements = {"persona": dict(),
                       "meta": dict()}

          # <next_tile> is a x,y coordinate. e.g., (58, 9)
          # <pronunciatio> is an emoji. e.g., "\ud83d\udca4"
          # <description> is a string description of the movement. e.g.,
          #   writing her next novel (editing her novel)
          #   @ double studio:double studio:common room:sofa
          def _move_persona(persona_name):
            persona = self.personas[persona_name]
            return persona_name, persona.move(
              self.maze, self.personas, self.personas_tile[persona_name],
              self.curr_time)

          persona_names = list(self.personas.keys())
          if PARALLEL_PERSONAS and len(persona_names) > 1:
            # Persona steps run concurrently; cross-persona interactions
            # (conversations) are serialized by a lock inside plan.py.
            workers = min(MAX_PARALLEL_WORKERS, len(persona_names))
            with concurrent.futures.ThreadPoolExecutor(workers) as pool:
              move_results = list(pool.map(_move_persona, persona_names))
          else:
            move_results = [_move_persona(name) for name in persona_names]

          # Chat reactions targeting personas that were mid-move are queued
          # by plan.py; apply them now that every thread has finished, so
          # the targets enter their conversations with consistent state.
          apply_pending_reactions()

          for persona_name, (next_tile, pronunciatio,
                             description) in move_results:
            persona = self.personas[persona_name]
            movements["persona"][persona_name] = {}
            movements["persona"][persona_name]["movement"] = next_tile
            movements["persona"][persona_name]["pronunciatio"] = pronunciatio
            movements["persona"][persona_name]["description"] = description
            movements["persona"][persona_name]["chat"] = (persona
                                                          .scratch.chat)

          # Include the meta information about the current stage in the 
          # movements dictionary. 
          movements["meta"]["curr_time"] = (self.curr_time 
                                             .strftime("%B %d, %Y, %H:%M:%S"))

          # We then write the personas' movements to a file that will be sent 
          # to the frontend server. 
          # Example json output: 
          # {"persona": {"Maria Lopez": {"movement": [58, 9]}},
          #  "persona": {"Klaus Mueller": {"movement": [38, 12]}}, 
          #  "meta": {curr_time: <datetime>}}
          curr_move_file = f"{sim_folder}/movement/{self.step}.json"
          with open(curr_move_file, "w") as outfile:
            outfile.write(json.dumps(movements, indent=2))

          # Economy bookkeeping: venue spending, conversation trades,
          # poverty whispers.
          if self.economy:
            try:
              self.economy.on_step(
                self.personas, movements["persona"], self.curr_time,
                traits_module.load_registry(sim_folder))
            except Exception:
              traceback.print_exc()

          # After this cycle, the world takes one step forward, and the
          # current time moves by <sec_per_step> amount.
          prev_date = self.curr_time.date()
          self.step += 1
          self.curr_time += datetime.timedelta(seconds=self.sec_per_step)

          if self.headless:
            # Play the frontend's role so the world advances browser-free,
            # and keep curr_step.json fresh so a browser can join anytime.
            write_headless_environment(sim_folder, self.step, movements)
            with open(f"{fs_temp_storage}/curr_step.json", "w") as outfile:
              outfile.write(json.dumps({"step": self.step}, indent=2))

          # A game day just started: pay everyone's daily wage.
          if self.economy and self.curr_time.date() != prev_date:
            try:
              self.economy.on_new_day(self.curr_time, self.personas)
            except Exception:
              traceback.print_exc()

          # A game day just ended: write today's issue of the chronicle.
          if CHRONICLE_ENABLED and self.curr_time.date() != prev_date:
            try:
              path = chronicle.generate_chronicle(
                sim_folder, self._day_start_step, self.step - 1,
                lang=CHRONICLE_LANG)
              if path:
                print (f"[chronicle] daily issue written: {path}")
            except Exception:
              traceback.print_exc()
            self._day_start_step = self.step

          # Auto-checkpoint so a crash (API timeout etc.) loses at most
          # CHECKPOINT_FREQ steps of progress.
          if CHECKPOINT_FREQ and self.step % CHECKPOINT_FREQ == 0:
            self.save()

          # Spending ceiling: halt (with progress saved) once the estimated
          # LLM cost reaches COST_LIMIT_USD. Warn at 80%.
          if COST_LIMIT_USD:
            spent = get_llm_stats()["est_total_cost_usd"]
            if spent >= COST_LIMIT_USD:
              self.save()
              print (f"COST LIMIT REACHED: est. ${spent} >= "
                     f"${COST_LIMIT_USD}. Progress saved -- resume by "
                     f"forking '{self.sim_code}' (or raise COST_LIMIT_USD).")
              break
            if spent >= 0.8 * COST_LIMIT_USD and not self._cost_warned:
              self._cost_warned = True
              print (f"COST WARNING: est. ${spent} is over 80% of the "
                     f"${COST_LIMIT_USD} limit.")

          # Pacing floor (real_minutes_per_day): if this step finished
          # faster than the per-step budget, sleep off the difference so a
          # game day lasts about the configured real time.
          if self.min_real_sec_per_step:
            remaining = (self.min_real_sec_per_step
                         - (time.time() - step_started_at))
            if remaining > 0:
              time.sleep(remaining)

          int_counter -= 1

      # Sleep so we don't burn our machines.
      time.sleep(self.server_sleep)


  def open_server(self): 
    """
    Open up an interactive terminal prompt that lets you run the simulation 
    step by step and probe agent state. 

    INPUT 
      None
    OUTPUT
      None
    """
    print ("Note: The agents in this simulation package are computational")
    print ("constructs powered by generative agents architecture and LLM. We")
    print ("clarify that these agents lack human-like agency, consciousness,")
    print ("and independent decision-making.\n---")

    # <sim_folder> points to the current simulation folder.
    sim_folder = f"{fs_storage}/{self.sim_code}"

    while True: 
      sim_command = input("Enter option: ")
      sim_command = sim_command.strip()
      ret_str = ""

      try: 
        if sim_command.lower() in ["f", "fin", "finish", "save and finish"]: 
          # Finishes the simulation environment and saves the progress. 
          # Example: fin
          self.save()
          break

        elif sim_command.lower() == "start path tester mode": 
          # Starts the path tester and removes the currently forked sim files.
          # Note that once you start this mode, you need to exit out of the
          # session and restart in case you want to run something else. 
          shutil.rmtree(sim_folder) 
          self.start_path_tester_server()

        elif sim_command.lower() == "exit": 
          # Finishes the simulation environment but does not save the progress
          # and erases all saved data from current simulation. 
          # Example: exit 
          shutil.rmtree(sim_folder) 
          break 

        elif sim_command.lower() == "save": 
          # Saves the current simulation progress. 
          # Example: save
          self.save()

        elif sim_command.lower() == "run forever":
          # Runs indefinitely (until crash, cost limit, or Ctrl-C).
          # Best combined with headless mode for 24/7 operation.
          rs.start_server(10 ** 9)

        elif sim_command[:3].lower() == "run":
          # Runs the number of steps specified in the prompt.
          # Example: run 1000
          int_count = int(sim_command.split()[-1])
          rs.start_server(int_count)

        elif sim_command.lower() in ["headless on", "headless off"]:
          # Toggle browser-free stepping for this session.
          self.headless = sim_command.lower() == "headless on"
          ret_str += (f"Headless mode "
                      f"{'ON -- the world advances without a browser.' if self.headless else 'OFF -- a browser tab drives stepping.'}")

        elif sim_command.lower() == "chronicle now":
          # Write a chronicle for the current (partial) game day.
          path = chronicle.generate_chronicle(
            sim_folder, self._day_start_step, self.step - 1,
            lang=CHRONICLE_LANG)
          ret_str += (f"Chronicle written: {path}" if path
                      else "Nothing to summarize yet -- run some steps "
                           "first.")

        elif ("print persona schedule" 
              in sim_command[:22].lower()): 
          # Print the decomposed schedule of the persona specified in the 
          # prompt.
          # Example: print persona schedule Isabella Rodriguez
          ret_str += (self.personas[" ".join(sim_command.split()[-2:])]
                      .scratch.get_str_daily_schedule_summary())

        elif ("print all persona schedule" 
              in sim_command[:26].lower()): 
          # Print the decomposed schedule of all personas in the world. 
          # Example: print all persona schedule
          for persona_name, persona in self.personas.items(): 
            ret_str += f"{persona_name}\n"
            ret_str += f"{persona.scratch.get_str_daily_schedule_summary()}\n"
            ret_str += f"---\n"

        elif ("print hourly org persona schedule" 
              in sim_command.lower()): 
          # Print the hourly schedule of the persona specified in the prompt.
          # This one shows the original, non-decomposed version of the 
          # schedule.
          # Ex: print persona schedule Isabella Rodriguez
          ret_str += (self.personas[" ".join(sim_command.split()[-2:])]
                      .scratch.get_str_daily_schedule_hourly_org_summary())

        elif ("print persona current tile" 
              in sim_command[:26].lower()): 
          # Print the x y tile coordinate of the persona specified in the 
          # prompt. 
          # Ex: print persona current tile Isabella Rodriguez
          ret_str += str(self.personas[" ".join(sim_command.split()[-2:])]
                      .scratch.curr_tile)

        elif ("print persona chatting with buffer" 
              in sim_command.lower()): 
          # Print the chatting with buffer of the persona specified in the 
          # prompt.
          # Ex: print persona chatting with buffer Isabella Rodriguez
          curr_persona = self.personas[" ".join(sim_command.split()[-2:])]
          for p_n, count in curr_persona.scratch.chatting_with_buffer.items(): 
            ret_str += f"{p_n}: {count}"

        elif ("print persona associative memory (event)" 
              in sim_command.lower()):
          # Print the associative memory (event) of the persona specified in
          # the prompt
          # Ex: print persona associative memory (event) Isabella Rodriguez
          ret_str += f'{self.personas[" ".join(sim_command.split()[-2:])]}\n'
          ret_str += (self.personas[" ".join(sim_command.split()[-2:])]
                                       .a_mem.get_str_seq_events())

        elif ("print persona associative memory (thought)" 
              in sim_command.lower()): 
          # Print the associative memory (thought) of the persona specified in
          # the prompt
          # Ex: print persona associative memory (thought) Isabella Rodriguez
          ret_str += f'{self.personas[" ".join(sim_command.split()[-2:])]}\n'
          ret_str += (self.personas[" ".join(sim_command.split()[-2:])]
                                       .a_mem.get_str_seq_thoughts())

        elif ("print persona associative memory (chat)" 
              in sim_command.lower()): 
          # Print the associative memory (chat) of the persona specified in
          # the prompt
          # Ex: print persona associative memory (chat) Isabella Rodriguez
          ret_str += f'{self.personas[" ".join(sim_command.split()[-2:])]}\n'
          ret_str += (self.personas[" ".join(sim_command.split()[-2:])]
                                       .a_mem.get_str_seq_chats())

        elif ("print persona spatial memory" 
              in sim_command.lower()): 
          # Print the spatial memory of the persona specified in the prompt
          # Ex: print persona spatial memory Isabella Rodriguez
          self.personas[" ".join(sim_command.split()[-2:])].s_mem.print_tree()

        elif sim_command.lower() == "stats":
          # Print LLM token usage and estimated cost so far.
          # Example: stats
          ret_str += format_llm_stats()

        elif sim_command.lower() in ["help", "h", "?"]:
          ret_str += (
            "Commands:\n"
            "  run <count>                     -- run <count> simulation steps\n"
            "  run forever                     -- run until stopped (24/7 mode)\n"
            "  headless on|off                 -- advance without a browser tab\n"
            "  chronicle now                   -- write today's newspaper issue now\n"
            "  save                            -- save current progress\n"
            "  fin                             -- save and quit\n"
            "  exit                            -- quit WITHOUT saving (deletes this sim)\n"
            "  stats                           -- LLM token usage and estimated cost\n"
            "  whisper <persona>: <thought>    -- inject a thought into a persona's memory\n"
            "                                     e.g. whisper Isabella Rodriguez: I am "
            "planning a party tonight\n"
            "  interview <persona>             -- chat with a persona (does not alter "
            "its memory)\n"
            "  traits show                     -- personas' personality traits & relationships\n"
            "  traits assign                   -- draw traits for a sim that has none\n"
            "  economy                         -- balances and recent transactions\n"
            "  event list                      -- show scheduled world events\n"
            "  event remove <id>               -- cancel a scheduled event\n"
            "  election start <days>: <A>; <B> -- one-shot election, vote in <days> game days\n"
            "                                     (use 'random' for random candidates)\n"
            "  election auto <interval> <campaign>: <A>; <B>\n"
            "                                  -- recurring election every <interval> days\n"
            "  election vote now               -- force the pending election to fire next step\n"
            "  election off                    -- cancel all elections\n"
            "  print persona schedule <persona>\n"
            "  print all persona schedule\n"
            "  print persona current tile <persona>\n"
            "  print persona associative memory (event|thought|chat) <persona>\n"
            "  print persona spatial memory <persona>\n"
            "  print current time\n"
            "  print tile event <x>, <y>\n"
            "  call -- load history the_ville/<file>.csv\n")

        elif sim_command[:7].lower() == "whisper":
          # Inject a thought straight into a persona's memory stream. This is
          # the same mechanism as "call -- load history" but for a single
          # whisper, e.g.:
          # whisper Isabella Rodriguez: I am throwing a party tonight
          body = sim_command[7:].strip()
          persona_name, sep, whisper = body.partition(":")
          persona_name = persona_name.strip()
          whisper = whisper.strip()
          if not sep or persona_name not in self.personas or not whisper:
            ret_str += ("Usage: whisper <persona name>: <thought>\n"
                        f"Known personas: {', '.join(self.personas.keys())}")
          elif not self.personas[persona_name].scratch.curr_time:
            ret_str += ("This simulation has not taken a step yet -- "
                        "run at least 1 step before whispering.")
          else:
            load_history_via_whisper(self.personas,
                                     [[persona_name, whisper]])
            ret_str += f"Whispered to {persona_name}: {whisper}"

        elif sim_command.lower() == "traits show":
          # Show every persona's drawn traits and the relationship web.
          ret_str += traits_module.format_registry(sim_folder)

        elif sim_command.lower() == "traits assign":
          # Draw traits/relationships if this sim doesn't have them yet.
          had_registry = bool(traits_module.load_registry(sim_folder))
          traits_module.assign_to_sim(sim_folder, self.events)
          ret_str += ("This simulation already has traits assigned."
                      if had_registry else
                      "Traits and relationships assigned -- they will be "
                      "whispered into memories on the next step.\n"
                      + traits_module.format_registry(sim_folder))

        elif sim_command.lower() == "economy":
          # Show balances and recent transactions.
          ret_str += (self.economy.summary_str() if self.economy
                      else "Economy is disabled (ECONOMY=0).")

        elif sim_command.lower() == "event list":
          # Show all scheduled world events.
          ret_str += self.events.status_str()

        elif sim_command[:12].lower() == "event remove":
          # Cancel a scheduled event by id. Example: event remove 2
          if self.events.remove_event(sim_command[12:].strip()):
            ret_str += "Event removed."
          else:
            ret_str += "No such event id."

        elif sim_command[:14].lower() == "election start":
          # One-shot election. Examples:
          #   election start 2: Sam Moore; Tom Moreno
          #   election start 2: random
          head, sep, cand = sim_command[14:].partition(":")
          campaign_days = float(head.strip() or 1)
          cand = cand.strip()
          candidates = ("random" if cand.lower() in ("", "random")
                        else [c.strip() for c in cand.split(";")
                              if c.strip()])
          event = self.events.add_event(
            {"type": "election", "label": "mayoral election",
             "campaign_days": campaign_days, "candidates": candidates},
            self.curr_time)
          ret_str += (f"Election #{event['id']} scheduled -- campaign "
                      f"starts next step, vote in {campaign_days:g} game "
                      f"day(s).")

        elif sim_command[:13].lower() == "election auto":
          # Recurring election. Example:
          #   election auto 7 2: random
          head, sep, cand = sim_command[13:].partition(":")
          parts = head.split()
          interval_days = float(parts[0]) if parts else 7
          campaign_days = float(parts[1]) if len(parts) > 1 else 1
          cand = cand.strip()
          candidates = ("random" if cand.lower() in ("", "random")
                        else [c.strip() for c in cand.split(";")
                              if c.strip()])
          event = self.events.add_event(
            {"type": "election", "label": "recurring mayoral election",
             "campaign_days": campaign_days, "candidates": candidates,
             "every_days": interval_days}, self.curr_time)
          ret_str += (f"Recurring election #{event['id']} scheduled -- "
                      f"every {interval_days:g} game days, "
                      f"{campaign_days:g}-day campaign.")

        elif sim_command.lower() == "election vote now":
          # Pull every pending election's next phase to the next step.
          n = 0
          for event in self.events.events:
            if event["type"] == "election":
              event["fire_at"] = self.curr_time.strftime(
                "%B %d, %Y, %H:%M:%S")
              n += 1
          self.events.save()
          ret_str += (f"{n} election event(s) will fire on the next step."
                      if n else "No election events scheduled.")

        elif sim_command.lower() == "election off":
          election_ids = [e["id"] for e in self.events.events
                          if e["type"] == "election"]
          for event_id in election_ids:
            self.events.remove_event(event_id)
          ret_str += f"Removed {len(election_ids)} election event(s)."

        elif sim_command[:9].lower() == "interview":
          # Stateless chat session with a persona (alias for
          # "call -- analysis"). Nothing is saved to the persona's memory.
          # Example: interview Isabella Rodriguez
          persona_name = sim_command[9:].strip()
          if persona_name not in self.personas:
            ret_str += ("Usage: interview <persona name>\n"
                        f"Known personas: {', '.join(self.personas.keys())}")
          else:
            self.personas[persona_name].open_convo_session("analysis")

        elif ("print current time"
              in sim_command[:18].lower()):
          # Print the current time of the world. 
          # Ex: print current time
          ret_str += f'{self.curr_time.strftime("%B %d, %Y, %H:%M:%S")}\n'
          ret_str += f'steps: {self.step}'

        elif ("print tile event" 
              in sim_command[:16].lower()): 
          # Print the tile events in the tile specified in the prompt 
          # Ex: print tile event 50, 30
          cooordinate = [int(i.strip()) for i in sim_command[16:].split(",")]
          for i in self.maze.access_tile(cooordinate)["events"]: 
            ret_str += f"{i}\n"

        elif ("print tile details" 
              in sim_command.lower()): 
          # Print the tile details of the tile specified in the prompt 
          # Ex: print tile event 50, 30
          cooordinate = [int(i.strip()) for i in sim_command[18:].split(",")]
          for key, val in self.maze.access_tile(cooordinate).items(): 
            ret_str += f"{key}: {val}\n"

        elif ("call -- analysis" 
              in sim_command.lower()): 
          # Starts a stateless chat session with the agent. It does not save 
          # anything to the agent's memory. 
          # Ex: call -- analysis Isabella Rodriguez
          persona_name = sim_command[len("call -- analysis"):].strip() 
          self.personas[persona_name].open_convo_session("analysis")

        elif ("call -- load history" 
              in sim_command.lower()): 
          curr_file = maze_assets_loc + "/" + sim_command[len("call -- load history"):].strip() 
          # call -- load history the_ville/agent_history_init_n3.csv

          rows = read_file_to_list(curr_file, header=True, strip_trail=True)[1]
          clean_whispers = []
          for row in rows: 
            agent_name = row[0].strip() 
            whispers = row[1].split(";")
            whispers = [whisper.strip() for whisper in whispers]
            for whisper in whispers: 
              clean_whispers += [[agent_name, whisper]]

          load_history_via_whisper(self.personas, clean_whispers)

        print (ret_str)

      except:
        traceback.print_exc()
        # Emergency checkpoint: a crash mid-run (commonly an API error)
        # should not lose completed steps. The saved state can be resumed by
        # forking from this simulation.
        try:
          self.save()
          print (f"Error. Progress saved -- resume by forking "
                 f"'{self.sim_code}'.")
        except Exception:
          print ("Error. (Emergency save also failed.)")
        pass


def resolve_autorun_sims(origin, target, storage_dir):
  """
  Restart-safe sim naming for autorun: if <target> already exists (the
  container restarted or was updated), resume from the newest run in the
  lineage (<target>, <target>-r2, <target>-r3, ...) by forking it into the
  next free -rN name, instead of crashing on the existing folder.
  """
  if not os.path.isdir(f"{storage_dir}/{target}"):
    return origin, target
  n = 2
  latest = target
  while os.path.isdir(f"{storage_dir}/{target}-r{n}"):
    latest = f"{target}-r{n}"
    n += 1
  return latest, f"{target}-r{n}"


if __name__ == '__main__':
  import signal
  import sys

  # Non-interactive mode for 24/7 operation (e.g. `docker compose up -d
  # autorun`): set REVERIE_FORK_SIM and REVERIE_NEW_SIM, and optionally
  # REVERIE_AUTORUN to a step count or "forever". Autorun implies headless.
  origin = os.environ.get("REVERIE_FORK_SIM", "").strip()
  target = os.environ.get("REVERIE_NEW_SIM", "").strip()
  autorun = os.environ.get("REVERIE_AUTORUN", "").strip()

  if not (origin and target):
    origin = input("Enter the name of the forked simulation: ").strip()
    target = input("Enter the name of the new simulation: ").strip()

  if autorun:
    # Restarts (redeploys, crashes, `docker compose restart`) resume the
    # lineage instead of failing on the existing sim folder.
    resolved_origin, resolved_target = resolve_autorun_sims(
      origin, target, fs_storage)
    if (resolved_origin, resolved_target) != (origin, target):
      print (f"Autorun: '{target}' exists -- resuming from "
             f"'{resolved_origin}' as '{resolved_target}'.")
    origin, target = resolved_origin, resolved_target

    # A stopping container sends SIGTERM; convert it to a normal exit so
    # the finally-save below runs and no progress is lost.
    signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(0))

  rs = ReverieServer(origin, target)

  if autorun:
    rs.headless = True
    steps = 10 ** 9 if autorun.lower() == "forever" else int(autorun)
    print (f"Autorun: headless, {autorun} steps. "
           f"Watch at /simulator_home; read /chronicle/.")
    try:
      rs.start_server(steps)
    finally:
      rs.save()
  else:
    rs.open_server()




















































