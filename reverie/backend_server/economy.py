"""
File: economy.py
Description: A lightweight "narrative economy with real bookkeeping".

Every persona has a wallet. Money moves four ways:
  - Wages: a flat daily wage credited at every game-day boundary.
  - Spending: when a persona's current action is a consumption at a known
    venue ("having coffee at Hobbs Cafe"), the menu price is deducted --
    deterministic string matching, no LLM cost. Working at a venue never
    charges (the verb list only matches consumption).
  - Trades: when two personas finish a conversation, one LLM call (fed
    both parties' personality traits and balances) conservatively decides
    whether money changed hands -- a loan, a treat, a purchase -- and for
    how much. This is where the trait system touches the economy: the
    generous treat people, the stingy split bills, the freeloading borrow.
  - Poverty loop: dropping below the threshold plants a "you are running
    low on money" whisper, which shapes the persona's plans and
    conversations until they recover.

State (balances, a full transaction ledger, dedup bookkeeping) lives in
<sim>/reverie/economy.json and survives save/fork. whisper_fn and
trade_llm_fn are injectable for offline testing.
"""
import datetime
import hashlib
import json
import os

DT_FORMAT = "%B %d, %Y, %H:%M:%S"

# (address keyword, price, note). Checked against the lowercase location
# part of the action description; first match wins.
VENUE_PRICES = [
  ("hobbs cafe", 8, "food & drink at Hobbs Cafe"),
  ("the rose and crown pub", 12, "food & drink at The Rose and Crown Pub"),
  ("willows market", 15, "groceries at The Willows Market and Pharmacy"),
  ("pharmacy", 10, "supplies at the pharmacy"),
  ("harvey oak supply store", 20, "goods at Harvey Oak Supply Store"),
]

# A venue visit only costs money when the action reads as consumption.
_CONSUME_WORDS = ["eat", "drink", "coffee", "tea", "breakfast", "lunch",
                  "dinner", "meal", "snack", "beer", "wine", "buy",
                  "shopping", "groceries", "ordering", "brunch", "dessert",
                  "pastry", "purchas"]

_MAX_SEEN_CHATS = 500
_MAX_TRANSACTIONS = 2000


class EconomyManager:
  def __init__(self, sim_folder, whisper_fn=None, trade_llm_fn=None,
               starting_balance=100.0, daily_wage=80.0,
               poverty_threshold=10.0):
    self.sim_folder = sim_folder
    self.state_file = f"{sim_folder}/reverie/economy.json"
    self.whisper_fn = whisper_fn or self._default_whisper
    self.trade_llm_fn = trade_llm_fn or self._default_trade_llm
    self.starting_balance = float(starting_balance)
    self.daily_wage = float(daily_wage)
    self.poverty_threshold = float(poverty_threshold)
    self._load()

  # ------------------------------------------------------------- storage

  def _load(self):
    try:
      with open(self.state_file) as f:
        state = json.load(f)
    except (OSError, ValueError):
      state = {}
    self.balances = state.get("balances", {})
    self.transactions = state.get("transactions", [])
    self.last_charged_act = state.get("last_charged_act", {})
    self.seen_chats = state.get("seen_chats", [])
    self.last_wage_date = state.get("last_wage_date", "")
    self.poverty_flagged = state.get("poverty_flagged", {})

  def save(self):
    os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
    state = {"balances": self.balances,
             "transactions": self.transactions[-_MAX_TRANSACTIONS:],
             "last_charged_act": self.last_charged_act,
             "seen_chats": self.seen_chats[-_MAX_SEEN_CHATS:],
             "last_wage_date": self.last_wage_date,
             "poverty_flagged": self.poverty_flagged}
    with open(self.state_file, "w") as f:
      f.write(json.dumps(state, indent=2))

  def ensure_personas(self, persona_names):
    for name in persona_names:
      self.balances.setdefault(name, self.starting_balance)

  def _log(self, curr_time, kind, payer, payee, amount, note):
    self.transactions += [{"at": curr_time.strftime(DT_FORMAT),
                           "type": kind, "from": payer, "to": payee,
                           "amount": round(float(amount), 2), "note": note}]

  # -------------------------------------------------------------- wages

  def on_new_day(self, curr_time, personas):
    """Credit the daily wage once per game day."""
    date_str = curr_time.strftime("%B %d, %Y")
    if self.last_wage_date == date_str:
      return
    self.last_wage_date = date_str
    self.ensure_personas(personas.keys())
    for name in personas:
      self.balances[name] = round(self.balances[name] + self.daily_wage, 2)
      self._log(curr_time, "wage", "town", name, self.daily_wage,
                "daily wage")
    self.save()

  # ------------------------------------------------------------ spending

  @staticmethod
  def _match_venue(description):
    if not description:
      return None
    lowered = description.lower()
    if " @ " in lowered:
      action_part, _, location_part = lowered.partition(" @ ")
    else:
      action_part, location_part = lowered, lowered
    if not any(word in action_part for word in _CONSUME_WORDS):
      return None
    for keyword, price, note in VENUE_PRICES:
      if keyword in location_part or keyword in action_part:
        return price, note
    return None

  def _charge_venues(self, movements_personas, curr_time):
    for name, info in movements_personas.items():
      description = info.get("description") or ""
      if self.last_charged_act.get(name) == description:
        continue
      self.last_charged_act[name] = description
      matched = self._match_venue(description)
      if not matched:
        continue
      price, note = matched
      if self.balances.get(name, 0) < price:
        continue  # can't afford it; the poverty whisper handles the mood
      self.balances[name] = round(self.balances[name] - price, 2)
      self._log(curr_time, "purchase", name, "shop", price, note)

  # -------------------------------------------------------------- trades

  def _evaluate_trades(self, personas, movements_personas, curr_time,
                       traits_registry):
    for name, info in movements_personas.items():
      chat = info.get("chat")
      if not chat or len(chat) < 2:
        continue
      transcript = "\n".join(f"{speaker}: {utterance}"
                             for speaker, utterance in chat)
      digest = hashlib.sha256(transcript.encode("utf-8")).hexdigest()[:16]
      if digest in self.seen_chats:
        continue
      self.seen_chats += [digest]

      speakers = []
      for speaker, _ in chat:
        if speaker not in speakers:
          speakers += [speaker]
      participants = [s for s in speakers if s in personas][:2]
      if len(participants) < 2:
        continue

      result = self._ask_trade(participants, transcript, traits_registry)
      if not result:
        continue
      payer, payee = result["payer"], result["payee"]
      amount = result["amount"]
      self.ensure_personas(participants)
      self.balances[payer] = round(self.balances[payer] - amount, 2)
      self.balances[payee] = round(self.balances[payee] + amount, 2)
      self._log(curr_time, "trade", payer, payee, amount, result["note"])
      self.whisper_fn(personas, payer,
                      f"You gave {payee} ${amount:g} ({result['note']})")
      self.whisper_fn(personas, payee,
                      f"You received ${amount:g} from {payer} "
                      f"({result['note']})")
      print (f"[economy] trade: {payer} -> {payee} ${amount:g} "
             f"({result['note']})")

  def _ask_trade(self, participants, transcript, traits_registry):
    a, b = participants
    context_lines = []
    for name in participants:
      traits = ", ".join(traits_registry.get(name, [])) or "unknown"
      balance = self.balances.get(name, self.starting_balance)
      context_lines += [f"- {name}: traits [{traits}], has ${balance:g}"]
    prompt = f"""Two residents of a small town just had this conversation:

\"\"\"{transcript}\"\"\"

Participants:
{chr(10).join(context_lines)}

Did money or goods-for-money actually change hands in THIS conversation
(a purchase, a loan, repaying a debt, treating the other, splitting a
bill)? Only say yes if the conversation makes the exchange explicit or
strongly implied AND it fits both personalities. Vague plans or offers
for later do NOT count. Amounts must be small-town realistic (1-100).

Output json: {{"trade": true/false, "payer": "<name or null>",
"payee": "<name or null>", "amount": <number or 0>,
"note": "<five words describing the exchange>"}}"""

    for attempt in range(2):
      try:
        response = json.loads(self.trade_llm_fn(prompt, attempt == 0))
        if not response.get("trade"):
          return None
        payer, payee = response.get("payer"), response.get("payee")
        amount = float(response.get("amount") or 0)
        if (payer in participants and payee in participants
            and payer != payee and 0 < amount <= 100):
          return {"payer": payer, "payee": payee,
                  "amount": round(amount, 2),
                  "note": str(response.get("note", "exchange"))[:80]}
        return None
      except Exception:
        continue
    return None

  # ------------------------------------------------------------- poverty

  def _check_poverty(self, personas):
    for name in personas:
      balance = self.balances.get(name, self.starting_balance)
      if balance < self.poverty_threshold:
        if not self.poverty_flagged.get(name):
          self.poverty_flagged[name] = True
          try:
            self.whisper_fn(
              personas, name,
              f"You are running low on money (only ${balance:g} left); "
              f"it worries you and you want to be careful about spending "
              f"or find a way to earn more")
            print (f"[economy] {name} is running low on money "
                   f"(${balance:g})")
          except Exception:
            pass
      elif balance >= self.poverty_threshold * 3:
        self.poverty_flagged[name] = False

  # ---------------------------------------------------------- step hook

  def on_step(self, personas, movements_personas, curr_time,
              traits_registry=None):
    """Called once per simulation step with the step's movements dict."""
    self.ensure_personas(personas.keys())
    self._charge_venues(movements_personas, curr_time)
    self._evaluate_trades(personas, movements_personas, curr_time,
                          traits_registry or {})
    self._check_poverty(personas)
    self.save()

  # -------------------------------------------------------------- report

  def summary_str(self):
    if not self.balances:
      return "No economy data yet -- run some steps first."
    lines = ["Balances:"]
    for name, balance in sorted(self.balances.items(),
                                key=lambda kv: -kv[1]):
      lines += [f"  {name}: ${balance:g}"]
    lines += [f"Transactions recorded: {len(self.transactions)}"]
    for transaction in self.transactions[-10:]:
      lines += [f"  {transaction['at']}  {transaction['from']} -> "
                f"{transaction['to']}  ${transaction['amount']:g}  "
                f"({transaction['note']})"]
    return "\n".join(lines)

  # ----------------------------------------- default LLM-backed helpers

  @staticmethod
  def _default_whisper(personas, name, text):
    from persona.cognitive_modules.converse import load_history_via_whisper
    load_history_via_whisper(personas, [[name, text]])

  @staticmethod
  def _default_trade_llm(prompt, cache_read):
    from persona.prompt_template.gpt_structure import _chat_request
    return _chat_request(prompt, json_mode=True, cache_read=cache_read)
