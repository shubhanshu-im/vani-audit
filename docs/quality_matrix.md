# VANI Call Quality Matrix

Transcribed from [quality_matrix.png](quality_matrix.png) ("Current Definitions of Call Quality Matrix being used in VANI"). Spelling is corrected and wording is otherwise kept as in the source. BFD = buyer-filled details; BL = buy lead; ISQ = category specification questions; MCAT = IndiaMART category.

| Parameter | Definition | Fatal | Non-Fatal |
|---|---|---|---|
| Outcome Accuracy | Buyer intent is buying or not | Always fatal | NA |
| Quantity Accuracy (Numeric) | Qty value is correctly captured or not | Subject to the category; fatal if the value matters | Minor difference that can be overlooked (difference of 10-20) |
| Quantity Unit Accuracy | Qty unit shared by buyer is correctly captured or not | Subject to the category; fatal if it impacts the BL | Similar-value units |
| Quantity Probing Error | Qty probed when already defined / Qty not probed when not defined / nudge is not proper | Fatal if not defined and not asked | Non-fatal if defined and still asked |
| Qty Missed Opportunity | Qty shared but missed in capture | Fatal | If the voice is not clear, then non-fatal |
| Spec1 Accuracy | Input X captured as Y and it affects the buy lead | Subject to the category | Minor difference that can be overlooked |
| Spec2 Accuracy | Input X captured as Y and it affects the buy lead | Subject to the category | Minor difference that can be overlooked |
| Spec Missed Opportunity | Spec 1 or 2 shared by buyer is not captured / not even added in BFD | Spec 1 or 2 shared by buyer is not captured / not even added in BFD | Spec 3 missed in capture and not even in BFD |
| Looping Behavior | Probing more than 1 + 2 times for any parameter, like qty or spec | Buyer gets irritated, leading to call disconnection or a missed opportunity for qty/spec | No impact on the call |
| WER | Words getting wrongly captured | Impact on the buy lead, where the word was captured differently | Word is wrongly captured but with no major impact on the BL |
| Additional_details | Buyer-filled details captured | Grossly wrong / irrelevant / BL-impacting BFD captured | Duplicate BFD captured / very generic, non-impacting BFD captured. Pass if generic BFD is there |
| OnCall AstBuy Error | Seller present but not pitched (fatal) / not pitched but disposition wrongly shared (non-fatal) | Seller present but not pitched | Not pitched, but disposition wrongly shared |
| Product Change / No Product Accuracy | Product wrongly captured | Fatal if wrong product approved | — |
| Category ISQ's Error | Contradicting or confusing ISQ | If ISQs are not contradicting | — |
| MCAT / Search errors | Wrong MCAT selected | — | — |
| Dead air | Dead air of more than 5 seconds in a normal case; 8 seconds when there is noise or interruption | — | — |
| Overall Call | Pass, Fatal or Non-Fatal, based on the parameters above | — | — |
| Additional Observation | Any other observation apart from the parameters above, like pronunciation | — | — |

> Note: the "Category ISQ's Error" Fatal cell reads "if ISQs are not contradicting" in the source, which looks inverted. Confirm the intended rule with the organisers.
