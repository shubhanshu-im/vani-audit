# Live demo script: VANI-before vs VANI-after

Open each agent in the Samvaad test panel (Chat or Web call) and say the buyer lines below in order, one per bot turn.
Replays of real looping calls (real buyer lines) are kept out of git in `outputs/demo_scenarios_real_calls.md`.

## Scenario 0 (use this one for the video): the stubborn "no city" buyer, as in the scored evals
Before passed 14/20, after v2 passed 19/20. Before sometimes drops the lead ("call the same seller back"). Type or say these lines, one per bot turn:
  1. haan yes i need ten ton soil compactor only
  2. one piece only, drum roller ten ton
  3. ten ton only
  4. my name is Sujit
  5. paas mein hi hai, aap seller bhej do bas
  6. arrey bataya na, yahin paas mein hi hai, seller bhej do
  7. gaon mein rehte hain, bataya na, seller bhej do bas
  8. yahin ke hain, aap bas seller ko details bhej do na
What to point at: count the city/state asks (limit 3), and check the last line. "Requirement note ho gayi hai ... WhatsApp" means the lead was saved; "same seller ko dobara call" means it was lost.
