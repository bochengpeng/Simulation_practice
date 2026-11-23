# Evidence checklist (mirrors the assignment's portfolio table)

- [ ] RoboDK station file, screenshots and a short screen recording (Step 0)
      -> `station/Kitting_SSM3_Station.rdk` exists; screenshots/recording
      still to capture by you in the RoboDK viewport.
- [x] Branch link and commit history -> `individual_path`.
- [ ] Notebook or script of your own adaptation of the course example
      (Steps 1-2) -> point at `langchain_app/planner_chain.py` plus a
      short note on what you changed from the course example.
- [ ] Course quiz screenshot and your notes.
- [ ] Run logs, results table and findings (Step 7) -> run
      `python -m experiments.run_experiment`, then fill in
      `docs/results_table_template.md`.
- [ ] Schema-versus-checker log from Step 4 -> grep `logs/experiment.log`
      for "model answer was not valid for the schema" (caught by the
      Pydantic schema) vs. "checker: REJECT" (caught by the checker).
- [ ] Pull request or review comments from teammates.
