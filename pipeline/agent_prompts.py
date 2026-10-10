"""Build the prompt text for the two live Sarvam voice agents (VANI-before / VANI-after).

    docs/vani_buyer_prompt.md            -> outputs/agents/vani_before.txt
    outputs/prompts/vani_prompt_final.md -> outputs/agents/vani_after.txt

Both are rendered from the Jinja template with the SAME demo context, so the only difference
between the two agents is the reviewed prompt change. Demo context: a buyer enquiry routed to the
help desk, no live seller (this workspace has no IndiaMART seller-transfer tools), product kept as
the platform variable {{product_name}}. A short note maps VANI's IndiaMART-only tools to what this
demo can do, so the agent never "calls" a tool that does not exist here.

Usage: .venv/bin/python pipeline/agent_prompts.py
"""
import re

from bot_prompt import _env, template_vars
from common import BOT_PROMPT_MD, OUTPUTS, PROMPTS_OUT, setup_logging

OUT = OUTPUTS / "agents"
CONTEXT = {
    "bot_name": "Tania",
    "product_name": "{{product_name}}",            # stays a platform variable
    "initial_message": "Hello, aapko {{product_name}} ki requirement hai na?",
    "buyer_name": "", "buyer_city": "", "is_enrich": "0", "ast_seller_pns": "", "ast_flow_live": "false",
    "lead_type": "", "pcd_pitch": "false", "language_name": "hindi",
    "specification_options": "{'Type': ['Drum roller', 'Plate compactor'], 'Operating weight': ['3 ton', '10 ton']}",
    "quantity_unit_options": "['piece']",
    "identity_challenge_count": "0", "identity_revealed": "0", "off_topic_counter": "0", "seller_ask_count": "0",
    "current_phase": "REQUIREMENT_CONFIRMATION", "turn_intent": "",
}
DEMO_NOTE = """

## Demo platform note
This agent runs the real VANI buyer-side prompt in a demo workspace. The IndiaMART-only tools named above (ast_buy_confirmed_tool, update_ast_buy_variables_by_city_tool, requested_another_product_tool) are not available here, and no live seller is available. Wherever the instructions say to call one of them, continue the conversation as instructed as if the call succeeded. When the conversation reaches its closing, say the closing line and call tool:end_interaction .
"""
MAIN_STATE_END = "## Enrichment - CONVERSATION CONTEXT"
IM_TOOLS = ["ast_buy_confirmed_tool", "update_ast_buy_variables_by_city_tool", "requested_another_product_tool"]
# Variables VANI updates during the call (the prompt's @references); declared agent-updatable on the demo agents
AGENT_VARIABLES = ["call_outcome", "buyer_disposition", "current_phase", "turn_intent", "additional_details",
                   "specifications", "seller_ask_count", "quantity", "quantity_unit", "quantity_asked_count",
                   "off_topic_counter", "image_reference", "identity_revealed", "call_step",
                   "identity_challenge_count", "connect_ast_call", "bl_converted"]
log = setup_logging()


def render(path):
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"^_Converted from .*?_\n", "", text, flags=re.M)   # our header, not VANI's
    ctx = {v: "" for v in template_vars(text)}
    ctx.update(CONTEXT)
    out = _env.from_string(text).render(**ctx)
    # The .docx holds several agent states one after another (main redirect flow, then Enrichment,
    # Redial - No Name City, Redial_AST). The demo agent is single-state, so keep only the main flow.
    out = out.split(MAIN_STATE_END, 1)[0]
    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    # On the Sarvam platform "@name" is a live reference to an agent variable or tool. Variables are
    # declared on the agent (AGENT_VARIABLES); the IndiaMART-only tools do not exist here, so they
    # are written as plain names.
    out = re.sub(r"@(" + "|".join(IM_TOOLS) + r")\b", r"\1", out)
    return out + DEMO_NOTE


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    before, after = render(BOT_PROMPT_MD), render(PROMPTS_OUT / "vani_prompt_final.md")
    (OUT / "vani_before.txt").write_text(before, encoding="utf-8")
    (OUT / "vani_after.txt").write_text(after, encoding="utf-8")
    import difflib
    diff = [l for l in difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm="", n=0)
            if l.startswith(("+", "-")) and not l.startswith(("+++", "---"))]
    log.info("before %d chars, after %d chars; difference: %s", len(before), len(after), diff)


if __name__ == "__main__":
    main()
