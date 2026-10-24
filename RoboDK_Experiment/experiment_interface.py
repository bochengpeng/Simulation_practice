"""Simple graphical launcher for the adaptive RoboDK experiment."""

from __future__ import annotations

import os
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from robodk_adaptive.planner import Planner


ROOT = Path(__file__).resolve().parent
MAIN = ROOT / "main.py"
STATION = ROOT / "Doosan_M0609_SSM3_Experiment.rdk"
LOG_DIR = ROOT / "logs"


class ExperimentInterface(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("RoboDK Adaptive Experiment")
        self.geometry("720x560")
        self.minsize(650, 480)
        self.process = None
        self.part_vars = {
            letter: tk.BooleanVar(value=True)
            for letter in ("A", "B", "C")
        }
        self.mode_var = tk.StringVar(value="deterministic")
        self.model_var = tk.StringVar(value="")
        self.replanning_var = tk.StringVar(value="hybrid-fast")
        self.allow_local_plan_var = tk.BooleanVar(value=True)
        self.scenario_var = tk.StringVar(value="sequential-relocation")
        self.disturbance_var = tk.StringVar(value="automatic")
        self.trials_var = tk.IntVar(value=1)
        self._build()

    def _build(self):
        outer = ttk.Frame(self, padding=14)
        outer.pack(fill="both", expand=True)

        ttk.Label(
            outer,
            text="RoboDK adaptive pick-and-place",
            font=("Segoe UI", 16, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            outer,
            text="Choose the parts and experiment settings, then press a button.",
        ).pack(anchor="w", pady=(2, 12))

        settings = ttk.LabelFrame(outer, text="Experiment settings", padding=10)
        settings.pack(fill="x")

        parts = ttk.Frame(settings)
        parts.pack(fill="x", pady=(0, 8))
        ttk.Label(parts, text="Parts:").pack(side="left")
        for letter in ("A", "B", "C"):
            ttk.Checkbutton(
                parts,
                text=f"Part {letter}",
                variable=self.part_vars[letter],
            ).pack(side="left", padx=(12, 0))

        row = ttk.Frame(settings)
        row.pack(fill="x", pady=3)
        ttk.Label(row, text="Planner:").pack(side="left")
        ttk.Combobox(
            row,
            textvariable=self.mode_var,
            values=("deterministic", "llm"),
            state="readonly",
            width=18,
        ).pack(side="left", padx=(8, 25))
        ttk.Label(row, text="Feedback replanning:").pack(side="left")
        self.replanning_combo = ttk.Combobox(
            row,
            textvariable=self.replanning_var,
            values=("hybrid-fast", "full-llm"),
            state="readonly",
            width=14,
        )
        self.replanning_combo.pack(side="left", padx=8)
        ttk.Label(row, text="Disturbance:").pack(side="left")
        ttk.Combobox(
            row,
            textvariable=self.disturbance_var,
            values=("automatic", "manual"),
            state="readonly",
            width=14,
        ).pack(side="left", padx=8)

        row = ttk.Frame(settings)
        row.pack(fill="x", pady=3)
        ttk.Label(row, text="AI model:").pack(side="left")
        self.model_combo = ttk.Combobox(
            row,
            textvariable=self.model_var,
            state="readonly",
            width=42,
        )
        self.model_combo.pack(side="left", padx=8)
        ttk.Button(row, text="Refresh", command=self.refresh_models).pack(side="left")
        ttk.Checkbutton(
            row,
            text="Allow local safe plan",
            variable=self.allow_local_plan_var,
        ).pack(side="left", padx=(20, 0))

        row = ttk.Frame(settings)
        row.pack(fill="x", pady=3)
        ttk.Label(row, text="Scenario:").pack(side="left")
        ttk.Combobox(
            row,
            textvariable=self.scenario_var,
            values=(
                "sequential-relocation",
                "no-change",
                "missing-b",
                "unreachable-c",
            ),
            state="readonly",
            width=24,
        ).pack(side="left", padx=8)
        ttk.Label(row, text="Trials:").pack(side="left", padx=(20, 0))
        ttk.Spinbox(
            row,
            from_=1,
            to=100,
            textvariable=self.trials_var,
            width=6,
        ).pack(side="left", padx=8)

        actions = ttk.LabelFrame(outer, text="Actions", padding=10)
        actions.pack(fill="x", pady=12)
        ttk.Button(actions, text="Check robot (doctor)", command=self.doctor).pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(actions, text="Reset experiment", command=self.reset).pack(
            side="left", padx=8
        )
        ttk.Button(actions, text="Run selected parts", command=self.run_parts).pack(
            side="left", padx=8
        )
        ttk.Button(
            actions,
            text="Run experiment",
            command=self.run_experiment,
        ).pack(side="left", padx=8)

        links = ttk.Frame(outer)
        links.pack(fill="x")
        ttk.Button(
            links, text="Open RoboDK station", command=self.open_station
        ).pack(side="left")
        ttk.Button(
            links, text="Open logs folder", command=self.open_logs
        ).pack(side="left", padx=8)
        ttk.Button(links, text="Clear output", command=self.clear_output).pack(
            side="right"
        )

        self.output = tk.Text(
            outer,
            height=16,
            wrap="word",
            state="disabled",
            background="#1e1e1e",
            foreground="#f0f0f0",
        )
        self.output.pack(fill="both", expand=True, pady=(12, 0))
        self._write("Ready. Reset the experiment before a new trial.\n")
        self.refresh_models()
        self.protocol("WM_DELETE_WINDOW", self.close)

    def selected_parts(self):
        return [letter for letter in "ABC" if self.part_vars[letter].get()]

    def selected_model(self):
        value = self.model_var.get().strip()
        return "" if value.startswith("(") else value

    def local_plan_flag(self):
        return (
            "--allow-local-safe-plan"
            if self.allow_local_plan_var.get()
            else "--no-allow-local-safe-plan"
        )

    def _command(self, *arguments):
        return [sys.executable, str(MAIN), *arguments]

    def start(self, *arguments):
        if self.process is not None and self.process.poll() is None:
            messagebox.showinfo("Experiment running", "Wait for the current operation to finish.")
            return
        self._write(f"Started: {' '.join(arguments)}\n")
        self.process = subprocess.Popen(
            self._command(*arguments),
            cwd=ROOT,
            # Keep the detailed experiment log in the terminal that launched
            # the interface. The GUI should not decode or mirror child output.
            stdout=None,
            stderr=None,
        )
        self._poll_process()

    def _poll_process(self):
        if self.process is None:
            return
        code = self.process.poll()
        if code is None:
            self.after(250, self._poll_process)
            return
        self._write(f"Finished with exit code {code}.\n")

    def doctor(self):
        self.start("doctor")

    def reset(self):
        self.start("reset")

    def run_parts(self):
        parts = self.selected_parts()
        if not parts:
            messagebox.showwarning("No parts selected", "Select at least one part.")
            return
        self.start(
            "run",
            "--parts",
            *parts,
            "--mode",
            self.mode_var.get(),
            "--model",
            self.selected_model(),
            "--replanning-mode",
            self.replanning_var.get(),
            "--disturbance-mode",
            self.disturbance_var.get(),
            self.local_plan_flag(),
        )

    def run_experiment(self):
        parts = self.selected_parts()
        if not parts:
            messagebox.showwarning("No parts selected", "Select at least one part.")
            return
        self.start(
            "experiment",
            "--scenario",
            self.scenario_var.get(),
            "--parts",
            *parts,
            "--mode",
            self.mode_var.get(),
            "--model",
            self.selected_model(),
            "--replanning-mode",
            self.replanning_var.get(),
            "--disturbance-mode",
            self.disturbance_var.get(),
            "--trials",
            str(self.trials_var.get()),
            self.local_plan_flag(),
        )

    def open_station(self):
        if not STATION.exists():
            messagebox.showerror("Station missing", str(STATION))
            return
        os.startfile(STATION)

    def open_logs(self):
        LOG_DIR.mkdir(exist_ok=True)
        os.startfile(LOG_DIR)

    def clear_output(self):
        self.output.configure(state="normal")
        self.output.delete("1.0", "end")
        self.output.configure(state="disabled")

    def refresh_models(self):
        models = Planner.available_models()
        current = self.model_var.get()
        values = models or ["(no Ollama models found - is Ollama running?)"]
        self.model_combo["values"] = values
        if current in values:
            self.model_var.set(current)
        else:
            self.model_var.set(values[0])

    def _write(self, text):
        self.output.configure(state="normal")
        self.output.insert("end", text)
        self.output.see("end")
        self.output.configure(state="disabled")

    def close(self):
        if self.process is not None and self.process.poll() is None:
            if not messagebox.askyesno(
                "Operation running",
                "An operation is still running. Close the interface anyway?",
            ):
                return
        self.destroy()


if __name__ == "__main__":
    ExperimentInterface().mainloop()
