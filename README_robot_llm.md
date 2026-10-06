# LLM-controlled pick-and-place prototype

This version uses two Python programs:

- `robot_controller.py` runs inside `/Robot_Arm/script`. It waits for a command and controls the arm through the existing IK, sensor, and suction routines.
- `llm_robot_client.py` runs in a separate Windows terminal. It asks the LLM for one structured action, checks the action, sends it to CoppeliaSim, and reports the result.

The only supported command is `pick_and_place(Part_A, PlaceZone)`. This is intentional: extend the allowed objects and skills only after the first end-to-end run succeeds.

## Set up

1. Download both `.py` files into the same Windows project folder. Keep a copy of your working original script.
2. Make sure `/Robot_Arm/script` loads the updated `robot_controller.py` from that Windows folder. If you use a small loader script in CoppeliaSim already, keep it and update only its path if necessary. Do not run two controllers on the same arm.
3. Install [Ollama for Windows](https://ollama.com/download/windows). In PowerShell, download the free local model once:

   ```powershell
   ollama pull qwen3:4b
   ```

   The download is about 2.5 GB. Ollama must be running while you send prompts. A better equipped computer can use `qwen3:8b`; set `$env:OLLAMA_MODEL = 'qwen3:8b'` after downloading it.

4. Install the CoppeliaSim client for the Python interpreter that runs `llm_robot_client.py`:

   ```powershell
   py -3.13 -m pip install coppeliasim-zmqremoteapi-client
   ```

5. Press **Play** in CoppeliaSim, then in PowerShell run:

   ```powershell
   py -3.13 llm_robot_client.py --demo
   ```

   `--demo` checks the command link and runs the known action without calling the LLM. Reset the cube to its starting position and restart the simulation before the next physical run.

6. Check the local model's plan without moving:

   ```powershell
   py -3.13 llm_robot_client.py 'Pick up Part_A and place it on PlaceZone' --dry-run
   ```

7. Reset the cube if needed, and run the full path:

   ```powershell
   py -3.13 llm_robot_client.py 'Pick up Part_A and place it on PlaceZone'
   ```

By default this uses `qwen3:4b` on your own computer. No OpenAI API key or API credits are needed. A local model still needs your computer's memory, processing power and electricity; it may respond slowly on a laptop without a capable GPU. If you later want to compare against OpenAI, install `openai` and `pydantic`, set `OPENAI_API_KEY` in PowerShell, and run the same command with `--provider openai`. The OpenAI default model is `gpt-4.1-mini` and can be changed through `OPENAI_MODEL`.

## What is checked

The outside program only accepts the exact supported action and object names. The scene script checks that the pickup sensor detects Part_A, that the sensor reaches the position above the cube, and that the cube reaches PlaceZone before release. It reports `ready`, `busy`, `done`, or `error` through a CoppeliaSim string signal. A `done` report means the script completed these checks, not that all possible collisions along the path were verified.

The scene's IK and `moveToPose` remain responsible for arm motion. This prototype does not implement continuous collision checking, a general waypoint planner, or visual recognition. Those require separate simulation checks before adding arbitrary new targets or locations. CoppeliaSim currently labels string signals as deprecated; they are used here as a small compatibility bridge with the existing 4.10 scene.
