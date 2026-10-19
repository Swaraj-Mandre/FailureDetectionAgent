# Early runs

The first test run of the slide version (`agent_v1_slide.py`), kept as development evidence for the refinement.

At that point the prompt did not yet have the revenue expression or the worked example, and the `corrupt` fault flipped numbers negative instead of multiplying them by 100. So these files are not a like-for-like comparison. The fair comparison is `../v1_slide_version/` against `../` (same prompt, same questions).

What these runs show clearly: with a generic failure notice and no retry limit, the agent sent the identical broken query 7 times (`clean.txt`) and retried a dead database 5 times (`db_down.txt`).
