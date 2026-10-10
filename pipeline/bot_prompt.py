"""Render the VANI bot prompt template for one simulated call (used by test_fix.py).

The prompt (docs/vani_buyer_prompt.md, or a fixed version in outputs/prompts/) is a Jinja
template: {{ product_name }} etc. are filled per call and {% if ast_seller_pns != "" %}
style blocks pick the right flow. Values come from the real call we are replaying:
product and opening line from the bot's first turn, live-seller flow if the bot offered
to connect a seller, spec / unit options from the questions the bot asked.

The bot normally calls tools and sets @variables. In simulation it speaks plain text and
writes actions on separate "ACTION:" lines, which are stripped before the buyer sees them.
"""
import re

import jinja2
from jinja2 import meta

SIM_INSTRUCTIONS = """

## SIMULATION MODE (test harness, overrides output format only)
You are on a live phone call with the buyer. The predefined opening message has already been spoken.
- Reply with ONLY the words you say aloud in this turn: short, natural, in the buyer's language.
- Never speak variable names, tool names, JSON or notes to yourself.
- If your instructions say to set a variable or call a tool (end_interaction, ast_buy_confirmed_tool, ...),
  write each action on its own line at the end, starting with "ACTION:", e.g. "ACTION: end_interaction".
- When the call ends, speak the closing line and add "ACTION: end_interaction".
"""
ENDING_ACTIONS = re.compile(r"end_interaction|ast_buy_confirmed_tool|connect_ast_call", re.IGNORECASE)
_env = jinja2.Environment(keep_trailing_newline=True)


def template_vars(template_text):
    return sorted(meta.find_undeclared_variables(_env.parse(template_text)))


def render(template_text, context):
    """Fill the template. Variables not in context render as "" (and compare as "")."""
    ctx = {v: "" for v in template_vars(template_text)}
    ctx.update({k: v for k, v in context.items() if v is not None})
    return _env.from_string(template_text).render(**ctx) + SIM_INSTRUCTIONS


def first_bot_line(clean):
    for t in clean["turns"]:
        if t["role"] == "BOT":
            return t["text"]
    return ""


def opening_message(clean):
    """The bot's predefined opening: its first turn up to and including the first question."""
    text = first_bot_line(clean)
    m = re.search(r"^.*?\?", text)
    return (m.group(0) if m else text).strip()


def context_for_call(clean, signals=None, lead=None):
    """Template values for replaying this call."""
    opening = opening_message(clean)
    bot_text = " ".join(t["text"] for t in clean["turns"] if t["role"] == "BOT")
    product = (lead or {}).get("product")
    if not product:
        m = re.search(r"(?:आपको|आपकी)\s+(.{3,80}?)\s+की\s+(?:requirement|request)", opening)
        product = m.group(1).strip() if m else ""
    live_seller = bool(re.search(r"sellers?\s*available|connect\s*(करवा|करा)", bot_text, re.IGNORECASE))
    enrich = bool(re.search(r"request\s*.{0,25}(raised|placed|raise)", opening, re.IGNORECASE))
    spec_names = sorted({k.split(":", 1)[1] for k in ((signals or {}).get("ask_counts") or {}) if k.startswith("spec:")}
                        - {"unnamed"})
    units = sorted(set(re.findall(r"कितन[ेीा]\s+(\w+)\s+(?:चाहिए|की)", bot_text)) - {"quantity"})
    name = re.match(r"^(?:hello|हेलो|हैलो)[,\s]+([^\s,]+)\s+जी", opening, re.IGNORECASE)
    return {
        "bot_name": "Tania",
        "initial_message": opening,
        "product_name": product,
        "buyer_name": name.group(1) if name else "",
        "is_enrich": "1" if enrich else "0",
        "ast_seller_pns": "9000000000" if live_seller else "",
        "ast_flow_live": "true" if live_seller else "false",
        "ast_seller_company": "an IndiaMART verified seller" if live_seller else "",
        "ast_seller_city": "",
        "lead_type": "LIVE" if live_seller else "",
        "pcd_pitch": "false",
        "language_name": "hindi",
        "specification_options": str({n: [] for n in spec_names}) if spec_names else "",
        "quantity_unit_options": str(units) if units else "",
        "identity_challenge_count": "0", "identity_revealed": "0", "off_topic_counter": "0",
        "seller_ask_count": "0", "current_phase": "REQUIREMENT_CONFIRMATION",
    }


def split_actions(reply):
    """(spoken text, [actions], call_ended) from a simulated bot reply."""
    spoken, actions = [], []
    for line in reply.splitlines():
        if line.strip().upper().startswith("ACTION:"):
            actions.append(line.split(":", 1)[1].strip())
        elif line.strip():
            spoken.append(line.strip())
    text = re.sub(r"^\s*(BOT|VANI|Tania)\s*:\s*", "", " ".join(spoken), flags=re.IGNORECASE)
    return text, actions, any(ENDING_ACTIONS.search(a) for a in actions)
