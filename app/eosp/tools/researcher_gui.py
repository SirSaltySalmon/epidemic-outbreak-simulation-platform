"""Tkinter researcher console: same pipeline as the web drawer, writes to ``EOSP_DATABASE_URL``."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
import tkinter as tk
from datetime import date
from tkinter import filedialog, messagebox, ttk
from typing import Any
from uuid import UUID

# ``python app/eosp/tools/researcher_gui.py`` (or an absolute path to this file) does not set
# PYTHONPATH; the importable package root is the ``app`` directory.
_APP_DIR = Path(__file__).resolve().parent.parent.parent
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))


def _ensure_project_dependencies() -> None:
    try:
        import pydantic_settings  # noqa: F401
    except ImportError:
        repo_root = _APP_DIR.parent
        exe = sys.executable
        sys.stderr.write(
            "This script needs EOSP dependencies installed for the same Python you used to launch it.\n\n"
            f"From the repository root:\n  cd \"{repo_root}\"\n"
            f'  "{exe}" -m pip install -e "."\n'
            f'  "{exe}" -m pip install -e ".[simulation]"   # required for inference / forecasts\n\n'
            "Then run this script again (or use: eosp-researcher-gui after install).\n"
        )
        raise SystemExit(2) from None


_ensure_project_dependencies()

from eosp.core.bootstrap import build_repository_with_status, create_default_job_manager
from eosp.core.models import (
    CaseCreate,
    CaseRecord,
    CaseStatus,
    CaseUpdate,
    LabResult,
    ObservationKind,
    TriggerType,
)
from eosp.core.settings import get_settings
from eosp.services.forecast import ForecastNotCachedError, get_cached_forecast
from eosp.services.jobs import JobManager
from eosp.services.reference_geo import airports_for_api, countries_for_api
from eosp.services.scenarios import SCENARIO_CONFIG

_SCENARIO_ORDER_LABELS: list[tuple[str, str]] = [
    ("baseline", "Baseline"),
    ("quarantine_immediate", "Quarantine Now"),
    ("evacuation_delay_3d", "Delay +3d"),
    ("evacuation_delay_7d", "Delay +7d"),
    ("enhanced_destination_protocols", "Enhanced Protocols"),
]

_FIDELITY_OPTIONS: tuple[tuple[int, str], ...] = (
    (100, "100 (fast preview)"),
    (1000, "1,000"),
    (10000, "10,000 (full)"),
)


def _scenario_rows_for_gui() -> list[tuple[str, str]]:
    return [(sid, label) for sid, label in _SCENARIO_ORDER_LABELS if sid in SCENARIO_CONFIG]


def _parse_date_opt(value: str) -> date | None:
    s = (value or "").strip()
    if not s:
        return None
    return date.fromisoformat(s)


def _parse_date_req(value: str) -> date:
    s = (value or "").strip()
    if not s:
        raise ValueError("Date is required")
    return date.fromisoformat(s)


class ResearcherGuiApp:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("EOSP Local Researcher Console")
        self.root.geometry("920x680")

        self._repo: Any = None
        self._jobs: JobManager | None = None
        self._poll_job_id: str | None = None

        repo, db_health = build_repository_with_status()
        if db_health.get("storage") != "postgres":
            messagebox.showerror(
                "Database required",
                "EOSP_DATABASE_URL must be set (e.g. in .env) and point at PostgreSQL.\n\n"
                f"Detail: {db_health.get('detail', 'unknown')}",
                parent=self.root,
            )
            self.root.destroy()
            raise SystemExit(1)
        if not db_health.get("database_reachable"):
            messagebox.showerror(
                "Database unreachable",
                "Cannot reach PostgreSQL. Start the database and check EOSP_DATABASE_URL.\n\n"
                f"Detail: {db_health.get('detail', 'unknown')}",
                parent=self.root,
            )
            self.root.destroy()
            raise SystemExit(1)

        self._repo = repo
        self._jobs = create_default_job_manager(repo)

        self._build_menu()
        self._build_warning_banner()
        self._notebook = ttk.Notebook(self.root)
        self._notebook.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        self._build_run_tab()
        self._build_cases_tab()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_menu(self) -> None:
        menubar = tk.Menu(self.root)
        file_m = tk.Menu(menubar, tearoff=0)
        file_m.add_command(label="Export baseline forecast JSON…", command=self._export_baseline_json)
        file_m.add_separator()
        file_m.add_command(label="Quit", command=self._on_close)
        menubar.add_cascade(label="File", menu=file_m)
        self.root.config(menu=menubar)

    def _build_warning_banner(self) -> None:
        db_url = get_settings().database_url or "(unset)"
        tip = (
            "This tool runs inference and simulations locally and writes results to the database "
            "configured in EOSP_DATABASE_URL. Use a development database unless you intend to "
            "update production."
        )
        frame = ttk.Frame(self.root)
        frame.pack(fill=tk.X, padx=8, pady=8)
        ttk.Label(frame, text=tip, wraplength=880, foreground="#555").pack(anchor=tk.W)
        ttk.Label(frame, text=f"Active URL: {db_url}", wraplength=880, font=("TkDefaultFont", 9, "italic")).pack(
            anchor=tk.W
        )

    def _build_run_tab(self) -> None:
        tab = ttk.Frame(self._notebook)
        self._notebook.add(tab, text="Run forecast")

        scen_frame = ttk.LabelFrame(tab, text="Scenarios")
        scen_frame.pack(fill=tk.X, padx=8, pady=6)
        self._scenario_vars: dict[str, tk.BooleanVar] = {}
        for sid, lbl in _scenario_rows_for_gui():
            var = tk.BooleanVar(value=(sid == "baseline"))
            self._scenario_vars[sid] = var
            ttk.Checkbutton(scen_frame, text=f"{lbl} ({sid})", variable=var).pack(anchor=tk.W)

        fid_frame = ttk.LabelFrame(tab, text="Simulation fidelity (Monte Carlo trajectories)")
        fid_frame.pack(fill=tk.X, padx=8, pady=6)
        self._fidelity_var = tk.IntVar(value=100)
        for n, label in _FIDELITY_OPTIONS:
            ttk.Radiobutton(fid_frame, text=label, variable=self._fidelity_var, value=n).pack(anchor=tk.W)

        btn_row = ttk.Frame(tab)
        btn_row.pack(fill=tk.X, padx=8, pady=6)
        self._run_btn = ttk.Button(btn_row, text="Run forecast →", command=self._start_run)
        self._run_btn.pack(side=tk.LEFT)

        log_frame = ttk.LabelFrame(tab, text="Pipeline log (same stages as web console)")
        log_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=6)
        self._log = tk.Text(log_frame, height=18, wrap=tk.WORD, font=("Consolas", 9))
        scroll = ttk.Scrollbar(log_frame, command=self._log.yview)
        self._log.configure(yscrollcommand=scroll.set)
        self._log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

    def _build_cases_tab(self) -> None:
        tab = ttk.Frame(self._notebook)
        self._notebook.add(tab, text="Cases")

        btn_row = ttk.Frame(tab)
        btn_row.pack(fill=tk.X, padx=8, pady=6)
        ttk.Button(btn_row, text="Refresh list", command=self._refresh_case_table).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(btn_row, text="New case…", command=lambda: self._open_case_dialog(None)).pack(
            side=tk.LEFT, padx=(0, 6)
        )
        ttk.Button(btn_row, text="Edit selected…", command=self._edit_selected_case).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(btn_row, text="Delete selected…", command=self._delete_selected_case).pack(side=tk.LEFT)

        table_frame = ttk.Frame(tab)
        table_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        cols = ("case_id", "patient", "onset", "loc", "kind")
        self._case_tree = ttk.Treeview(table_frame, columns=cols, show="headings", height=18)
        headings = {
            "case_id": "Case ID",
            "patient": "Patient",
            "onset": "Onset",
            "loc": "Location",
            "kind": "Kind",
        }
        for c in cols:
            self._case_tree.heading(c, text=headings[c])
            self._case_tree.column(c, width=140 if c != "patient" else 180)
        vsb = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self._case_tree.yview)
        self._case_tree.configure(yscrollcommand=vsb.set)
        self._case_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        self._case_tree.bind("<Double-1>", lambda _e: self._edit_selected_case())

        self._refresh_case_table()

    def _append_log(self, line: str) -> None:
        self._log.insert(tk.END, line + "\n")
        self._log.see(tk.END)

    def _start_run(self) -> None:
        assert self._jobs is not None
        scenarios = [sid for sid, var in self._scenario_vars.items() if var.get()]
        if not scenarios:
            messagebox.showwarning("Scenarios", "Select at least one scenario.", parent=self.root)
            return

        self._log.delete("1.0", tk.END)
        self._run_btn.state(["disabled"])
        n_sim = int(self._fidelity_var.get())

        job_id = self._jobs.schedule_full_refresh(
            reason="manual_local_gui",
            trigger=TriggerType.MANUAL,
            scenarios=scenarios,
            n_simulations=n_sim,
        )
        if job_id is None and self._jobs.get_active_pipeline_job() is None:
            job_id = self._jobs.schedule_full_refresh(
                reason="manual_local_gui",
                trigger=TriggerType.MANUAL,
                scenarios=scenarios,
                n_simulations=n_sim,
            )

        if job_id is None:
            messagebox.showerror(
                "Pipeline busy",
                "Another inference run is already in progress. Wait for it to finish.",
                parent=self.root,
            )
            self._run_btn.state(["!disabled"])
            return

        self._append_log(f"Scheduled job {job_id} · scenarios={scenarios} · n_simulations={n_sim}")
        self._poll_job_id = job_id
        self._poll_job()

    def _poll_job(self) -> None:
        assert self._jobs is not None
        jid = self._poll_job_id
        if not jid:
            return

        for ev in self._jobs.drain_events(jid):
            try:
                self._append_log(json.dumps(ev, default=str))
            except TypeError:
                self._append_log(str(ev))

        rec = self._jobs.get_status(jid)
        if rec is None:
            self._finish_run("Job record missing (unexpected).")
            return
        if rec.status == "failed":
            self._append_log(f"FAILED: {rec.error or 'unknown error'}")
            self._finish_run(None)
            messagebox.showerror("Run failed", rec.error or "Unknown error", parent=self.root)
            return
        if rec.status == "completed":
            detail = rec.detail or {}
            ver = detail.get("inference_version", "—")
            scen = detail.get("scenarios_refreshed", [])
            self._append_log(f"Completed · inference_version={ver} · scenarios_refreshed={scen}")
            self._finish_run(None)
            messagebox.showinfo("Done", "Forecast run completed and saved to the database.", parent=self.root)
            return

        self.root.after(500, self._poll_job)

    def _finish_run(self, _msg: str | None) -> None:
        self._poll_job_id = None
        self._run_btn.state(["!disabled"])

    def _refresh_case_table(self) -> None:
        assert self._repo is not None
        for iid in self._case_tree.get_children():
            self._case_tree.delete(iid)
        try:
            rows = self._repo.list_cases()
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Cases list", str(exc), parent=self.root)
            return
        for c in rows:
            loc = c.location_country
            if c.location_airport_code:
                loc = f"{loc} · {c.location_airport_code}"
            self._case_tree.insert(
                "",
                tk.END,
                iid=str(c.case_id),
                values=(
                    str(c.case_id),
                    c.patient_identifier,
                    str(c.symptom_onset_date),
                    loc,
                    c.observation_kind.value,
                ),
            )

    def _selected_case_id(self) -> UUID | None:
        sel = self._case_tree.selection()
        if not sel:
            return None
        return UUID(sel[0])

    def _edit_selected_case(self) -> None:
        cid = self._selected_case_id()
        if cid is None:
            messagebox.showinfo("Edit case", "Select a case first.", parent=self.root)
            return
        assert self._repo is not None
        record = self._repo.get_case(cid)
        if record is None:
            messagebox.showerror("Edit case", "Case not found.", parent=self.root)
            self._refresh_case_table()
            return
        self._open_case_dialog(record)

    def _delete_selected_case(self) -> None:
        cid = self._selected_case_id()
        if cid is None:
            messagebox.showinfo("Delete case", "Select a case first.", parent=self.root)
            return
        if not messagebox.askyesno(
            "Delete case",
            "Delete this case from the database? This soft-deletes the row.",
            parent=self.root,
        ):
            return
        assert self._repo is not None
        ok = self._repo.delete_case(cid)
        if not ok:
            messagebox.showerror("Delete case", "Case not found.", parent=self.root)
        self._refresh_case_table()

    def _maybe_schedule_refit(self, case: CaseRecord, *, is_new: bool) -> None:
        assert self._jobs is not None
        reason = f"new_case:{case.case_id}" if is_new else f"case_updated:{case.case_id}"
        self._jobs.schedule_refit(reason=reason, trigger=TriggerType.NEW_CASE)

    def _open_case_dialog(self, record: CaseRecord | None) -> None:
        assert self._repo is not None
        assert self._jobs is not None

        win = tk.Toplevel(self.root)
        win.title("Edit case" if record else "New case")
        win.transient(self.root)
        win.grab_set()

        pad = {"padx": 6, "pady": 4}
        row = 0

        def grid_label(text: str, r: int) -> ttk.Label:
            lbl = ttk.Label(win, text=text)
            lbl.grid(row=r, column=0, sticky=tk.W, **pad)
            return lbl

        grid_label("Observation kind", row)
        obs_var = tk.StringVar(value=record.observation_kind.value if record else ObservationKind.INDIVIDUAL.value)
        obs_cb = ttk.Combobox(
            win,
            textvariable=obs_var,
            values=[ObservationKind.INDIVIDUAL.value, ObservationKind.COHORT.value],
            state="readonly",
            width=38,
        )
        obs_cb.grid(row=row, column=1, sticky=tk.W, **pad)
        row += 1

        grid_label("Patient ID", row)
        patient_var = tk.StringVar(value=record.patient_identifier if record else "")
        ttk.Entry(win, textvariable=patient_var, width=40).grid(row=row, column=1, sticky=tk.W, **pad)
        row += 1

        grid_label("Symptom onset (YYYY-MM-DD)", row)
        onset_var = tk.StringVar(value=str(record.symptom_onset_date) if record else "")
        ttk.Entry(win, textvariable=onset_var, width=40).grid(row=row, column=1, sticky=tk.W, **pad)
        row += 1

        grid_label("Hospitalization (optional)", row)
        hosp_var = tk.StringVar(value=str(record.hospitalization_date) if record and record.hospitalization_date else "")
        hosp_e = ttk.Entry(win, textvariable=hosp_var, width=40)
        hosp_e.grid(row=row, column=1, sticky=tk.W, **pad)
        row += 1

        grid_label("Death date (individual only)", row)
        death_var = tk.StringVar(value=str(record.death_date) if record and record.death_date else "")
        death_e = ttk.Entry(win, textvariable=death_var, width=40)
        death_e.grid(row=row, column=1, sticky=tk.W, **pad)
        row += 1

        countries = countries_for_api()
        country_labels = [f"{c['code']} — {c['name']}" for c in countries]

        grid_label("Country", row)
        country_var = tk.StringVar()
        if record:
            cc = record.location_country.upper()
            for lab in country_labels:
                if lab.startswith(f"{cc} —") or lab.startswith(cc + " "):
                    country_var.set(lab)
                    break
            else:
                country_var.set(f"{cc} — {cc}")
        country_cb = ttk.Combobox(win, textvariable=country_var, values=country_labels, width=38)
        country_cb.grid(row=row, column=1, sticky=tk.W, **pad)
        row += 1

        grid_label("Airport IATA (optional)", row)
        apt_var = tk.StringVar(value=record.location_airport_code if record and record.location_airport_code else "")
        apt_cb = ttk.Combobox(win, textvariable=apt_var, width=10)

        def sync_airports(_evt: Any = None) -> None:
            lab = country_var.get()
            code = lab.split(" — ", 1)[0].strip().upper() if lab else ""
            apts = airports_for_api(code if len(code) == 2 else None)
            apt_cb["values"] = [""] + [a["iata"] for a in apts]

        country_cb.bind("<<ComboboxSelected>>", sync_airports)
        sync_airports()
        apt_cb.grid(row=row, column=1, sticky=tk.W, **pad)
        row += 1

        grid_label("Confirmed / suspected", row)
        status_var = tk.StringVar(
            value=record.confirmed_or_suspected.value if record else CaseStatus.SUSPECTED.value
        )
        ttk.Combobox(
            win,
            textvariable=status_var,
            values=[CaseStatus.CONFIRMED.value, CaseStatus.SUSPECTED.value],
            state="readonly",
            width=38,
        ).grid(row=row, column=1, sticky=tk.W, **pad)
        row += 1

        grid_label("Lab result", row)
        lab_var = tk.StringVar(value=record.lab_test_result.value if record else LabResult.NOT_TESTED.value)
        ttk.Combobox(
            win,
            textvariable=lab_var,
            values=[
                LabResult.NOT_TESTED.value,
                LabResult.PCR_POSITIVE.value,
                LabResult.PCR_NEGATIVE.value,
                LabResult.SEROLOGY_POSITIVE.value,
            ],
            state="readonly",
            width=38,
        ).grid(row=row, column=1, sticky=tk.W, **pad)
        row += 1

        cohort_frame = ttk.LabelFrame(win, text="Cohort / aggregate (when kind is cohort)")
        cohort_row = row
        row += 1

        ttk.Label(cohort_frame, text="Cohort size").grid(row=0, column=0, sticky=tk.W, **pad)
        cohort_n_var = tk.StringVar(value=str(record.cohort_size) if record else "1")
        ttk.Entry(cohort_frame, textvariable=cohort_n_var, width=12).grid(row=0, column=1, sticky=tk.W, **pad)
        ttk.Label(cohort_frame, text="Cohort deaths").grid(row=1, column=0, sticky=tk.W, **pad)
        cohort_d_var = tk.StringVar(value=str(record.cohort_deaths) if record else "0")
        ttk.Entry(cohort_frame, textvariable=cohort_d_var, width=12).grid(row=1, column=1, sticky=tk.W, **pad)
        ttk.Label(cohort_frame, text="Report period start").grid(row=2, column=0, sticky=tk.W, **pad)
        rps_var = tk.StringVar(
            value=str(record.report_period_start) if record and record.report_period_start else ""
        )
        ttk.Entry(cohort_frame, textvariable=rps_var, width=14).grid(row=2, column=1, sticky=tk.W, **pad)
        ttk.Label(cohort_frame, text="Report period end").grid(row=3, column=0, sticky=tk.W, **pad)
        rpe_var = tk.StringVar(
            value=str(record.report_period_end) if record and record.report_period_end else ""
        )
        ttk.Entry(cohort_frame, textvariable=rpe_var, width=14).grid(row=3, column=1, sticky=tk.W, **pad)

        grid_label("Data source", row)
        ds_var = tk.StringVar(value=record.data_source if record else "Manual_Form")
        ttk.Entry(win, textvariable=ds_var, width=40).grid(row=row, column=1, sticky=tk.W, **pad)
        row += 1

        status_lbl = ttk.Label(win, text="")
        status_lbl.grid(row=row, column=0, columnspan=2, sticky=tk.W, padx=8, pady=8)
        row += 1

        def toggle_cohort_fields(*_a: Any) -> None:
            is_cohort = obs_var.get() == ObservationKind.COHORT.value
            if is_cohort:
                cohort_frame.grid(row=cohort_row, column=0, columnspan=2, sticky=tk.EW, padx=8, pady=6)
            else:
                cohort_frame.grid_remove()
            death_e.state(["!disabled"] if not is_cohort else ["disabled"])
            if is_cohort:
                death_var.set("")

        obs_var.trace_add("write", lambda *_: toggle_cohort_fields())
        toggle_cohort_fields()

        def save() -> None:
            try:
                country_lab = country_var.get().strip()
                country_code = country_lab.split(" — ", 1)[0].strip().upper()
                if len(country_code) != 2:
                    raise ValueError("Select a valid country (ISO alpha-2).")
                apt = apt_var.get().strip().upper()
                if apt and len(apt) != 3:
                    raise ValueError("Airport must be a 3-letter IATA code or empty.")
                kind = ObservationKind(obs_var.get())
                base_common = {
                    "patient_identifier": patient_var.get().strip(),
                    "symptom_onset_date": _parse_date_req(onset_var.get()),
                    "hospitalization_date": _parse_date_opt(hosp_var.get()),
                    "location_country": country_code,
                    "location_airport_code": apt if len(apt) == 3 else None,
                    "confirmed_or_suspected": CaseStatus(status_var.get()),
                    "lab_test_result": LabResult(lab_var.get()),
                    "data_source": ds_var.get().strip() or "Manual_Form",
                    "contacts": [],
                    "updated_by": "local_researcher_gui",
                    "observation_kind": kind,
                }
                if kind == ObservationKind.COHORT:
                    payload_create = CaseCreate(
                        **base_common,
                        death_date=None,
                        cohort_size=int(cohort_n_var.get() or 1),
                        cohort_deaths=int(cohort_d_var.get() or 0),
                        report_period_start=_parse_date_opt(rps_var.get()),
                        report_period_end=_parse_date_opt(rpe_var.get()),
                        updated_reason="manual_console_intake" if record is None else "manual_console_edit",
                    )
                else:
                    payload_create = CaseCreate(
                        **base_common,
                        death_date=_parse_date_opt(death_var.get()),
                        cohort_size=1,
                        cohort_deaths=0,
                        report_period_start=None,
                        report_period_end=None,
                        updated_reason="manual_console_intake" if record is None else "manual_console_edit",
                    )

                if record is None:
                    case_out, validation = self._repo.create_case(payload_create)
                else:
                    patch = CaseUpdate(
                        patient_identifier=payload_create.patient_identifier,
                        symptom_onset_date=payload_create.symptom_onset_date,
                        hospitalization_date=payload_create.hospitalization_date,
                        death_date=payload_create.death_date,
                        location_country=payload_create.location_country,
                        location_airport_code=payload_create.location_airport_code,
                        confirmed_or_suspected=payload_create.confirmed_or_suspected,
                        lab_test_result=payload_create.lab_test_result,
                        contacts=payload_create.contacts,
                        data_source=payload_create.data_source,
                        updated_by=payload_create.updated_by,
                        updated_reason="manual_console_edit",
                        observation_kind=payload_create.observation_kind,
                        cohort_size=payload_create.cohort_size,
                        cohort_deaths=payload_create.cohort_deaths,
                        report_period_start=payload_create.report_period_start,
                        report_period_end=payload_create.report_period_end,
                    )
                    case_out, validation = self._repo.update_case(record.case_id, patch)

                accepted = validation.quality_score >= 0.80
                if accepted:
                    self._maybe_schedule_refit(case_out, is_new=record is None)
                status_lbl.configure(
                    text=(
                        "Saved; accepted for inference refit."
                        if accepted
                        else f"Saved (quality {validation.quality_score:.2f})."
                    )
                )
                self._refresh_case_table()
                win.destroy()
            except (ValueError, TypeError) as exc:
                messagebox.showerror("Case form", str(exc), parent=win)
            except Exception as exc:  # noqa: BLE001
                messagebox.showerror("Case form", str(exc), parent=win)

        btns = ttk.Frame(win)
        btns.grid(row=row, column=0, columnspan=2, pady=8)
        ttk.Button(btns, text="Save", command=save).pack(side=tk.LEFT, padx=6)
        ttk.Button(btns, text="Cancel", command=win.destroy).pack(side=tk.LEFT)

        win.columnconfigure(1, weight=1)

    def _export_baseline_json(self) -> None:
        assert self._repo is not None
        path = filedialog.asksaveasfilename(
            parent=self.root,
            defaultextension=".json",
            filetypes=[("JSON", "*.json"), ("All", "*.*")],
            title="Save baseline forecast JSON",
        )
        if not path:
            return
        try:
            fc = get_cached_forecast("baseline", repository=self._repo)
        except ForecastNotCachedError as exc:
            messagebox.showerror("Export", str(exc), parent=self.root)
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(fc.model_dump(mode="json"), fh, indent=2)
        except OSError as exc:
            messagebox.showerror("Export", str(exc), parent=self.root)
            return
        messagebox.showinfo("Export", f"Wrote {path}", parent=self.root)

    def _on_close(self) -> None:
        if self._jobs is not None:
            self._jobs.shutdown()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    ResearcherGuiApp().run()


if __name__ == "__main__":
    main()