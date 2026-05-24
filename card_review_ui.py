"""Desktop interface for final human validation of generated Anki cards."""

from __future__ import annotations

import argparse
import csv
import tkinter as tk
from dataclasses import dataclass, replace
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Iterable, List, Optional, Sequence

from episode_to_anki import VocabCard, read_cards_csv, validate_cards, write_cards


DECISIONS = ("accept", "needs_review", "reject")


@dataclass
class ManualReviewItem:
    card: VocabCard
    decision: str
    origin: str
    reason: str = ""
    decision_confidence: str = ""
    translation_confidence: str = ""
    normalization_confidence: str = ""


def read_audit_csv(path: Path, *, default_decision: str) -> List[ManualReviewItem]:
    items: List[ManualReviewItem] = []
    with path.open("r", encoding="utf-8-sig", newline="") as input_file:
        reader = csv.DictReader(input_file)
        for row in reader:
            ukrainian = row.get("Ukrainian", "").strip()
            english = row.get("English", "").strip()
            if not ukrainian or not english:
                continue
            decision = row.get("Decision", "").strip().casefold()
            if decision == "reject_high_confidence":
                decision = "reject"
            if decision not in DECISIONS:
                decision = default_decision
            items.append(
                ManualReviewItem(
                    card=VocabCard(
                        ukrainian=ukrainian,
                        english=english,
                        example=row.get("Example", "").strip(),
                        example_target=row.get("ExampleTarget", "").strip(),
                        tags=row.get("Tags", "ukrainian podcast transcript vocab").strip(),
                        source=row.get("Source", str(path)).strip(),
                        episode=row.get("Episode", path.stem).strip(),
                    ),
                    decision=decision,
                    origin=path.name,
                    reason=row.get("Reason", "").strip(),
                    decision_confidence=row.get("DecisionConfidence", "").strip(),
                    translation_confidence=row.get("TranslationConfidence", "").strip(),
                    normalization_confidence=row.get("NormalizationConfidence", "").strip(),
                )
            )
    return items


def load_review_items(
    accepted_path: Path,
    *,
    needs_review_path: Optional[Path] = None,
    rejected_path: Optional[Path] = None,
) -> List[ManualReviewItem]:
    items = [
        ManualReviewItem(card=card, decision="accept", origin=accepted_path.name)
        for card in read_cards_csv(accepted_path)
    ]
    if needs_review_path and needs_review_path.exists():
        items.extend(read_audit_csv(needs_review_path, default_decision="needs_review"))
    if rejected_path and rejected_path.exists():
        items.extend(read_audit_csv(rejected_path, default_decision="reject"))
    return items


def write_manual_review(items: Iterable[ManualReviewItem], output_path: Path) -> int:
    items = list(items)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8-sig", newline="") as output_file:
        writer = csv.writer(output_file)
        writer.writerow(
            [
                "Ukrainian",
                "English",
                "Example",
                "ExampleTarget",
                "Decision",
                "ReviewerNote",
                "Origin",
                "Tags",
                "Source",
                "Episode",
            ]
        )
        for item in items:
            card = item.card
            writer.writerow(
                [
                    card.ukrainian,
                    card.english,
                    card.example,
                    card.example_target,
                    item.decision,
                    item.reason,
                    item.origin,
                    card.tags,
                    card.source,
                    card.episode,
                ]
            )
    return len(items)


def accepted_cards(items: Iterable[ManualReviewItem]) -> List[VocabCard]:
    return [item.card for item in items if item.decision == "accept"]


def export_accepted_cards(items: Iterable[ManualReviewItem], output_path: Path, card_format: str) -> int:
    return write_cards(accepted_cards(items), output_path, card_format=card_format)


class CardReviewApp(ttk.Frame):
    def __init__(
        self,
        master: tk.Tk,
        items: Sequence[ManualReviewItem],
        *,
        session_output: Path,
        export_output: Path,
        card_format: str,
    ) -> None:
        super().__init__(master, padding=12)
        self.master = master
        self.items = list(items)
        self.session_output = session_output
        self.export_output = export_output
        self.card_format = card_format
        self.current_index: Optional[int] = None
        self.filter_var = tk.StringVar(value="all")
        self.status_var = tk.StringVar()
        self.validation_var = tk.StringVar()
        self.ukrainian_var = tk.StringVar()
        self.english_var = tk.StringVar()
        self.example_target_var = tk.StringVar()
        self.decision_var = tk.StringVar(value="accept")
        self.note_var = tk.StringVar()
        self.origin_var = tk.StringVar()
        self.example_text = tk.Text(self, height=5, wrap="word")
        self.tree = ttk.Treeview(self, columns=("decision", "ukrainian", "english", "origin"), show="headings")
        self._build_ui()
        self._refresh_tree()
        if self.items:
            self._select_index(0)
        self.master.after(100, self._show_in_foreground)

    def _build_ui(self) -> None:
        self.master.title("Anki Card Final Validation")
        self.master.geometry("1180x720")
        self.grid(sticky="nsew")
        self.master.columnconfigure(0, weight=1)
        self.master.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=3)
        self.columnconfigure(1, weight=4)
        self.rowconfigure(1, weight=1)

        toolbar = ttk.Frame(self)
        toolbar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        ttk.Label(toolbar, text="Show:").pack(side="left")
        filter_box = ttk.Combobox(
            toolbar,
            textvariable=self.filter_var,
            values=("all", "accept", "needs_review", "reject"),
            state="readonly",
            width=15,
        )
        filter_box.pack(side="left", padx=(6, 12))
        filter_box.bind("<<ComboboxSelected>>", lambda _event: self._refresh_tree())
        ttk.Button(toolbar, text="Save Review", command=self._save_review).pack(side="left", padx=3)
        ttk.Button(toolbar, text="Export Accepted", command=self._export_accepted).pack(side="left", padx=3)
        ttk.Button(toolbar, text="Choose Export Path", command=self._choose_export_path).pack(side="left", padx=3)
        ttk.Label(toolbar, textvariable=self.status_var).pack(side="right")

        for column, title, width in (
            ("decision", "Decision", 100),
            ("ukrainian", "Ukrainian", 185),
            ("english", "English", 205),
            ("origin", "Source Queue", 180),
        ):
            self.tree.heading(column, text=title)
            self.tree.column(column, width=width, anchor="w")
        self.tree.grid(row=1, column=0, sticky="nsew", padx=(0, 12))
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)

        editor = ttk.Frame(self)
        editor.grid(row=1, column=1, sticky="nsew")
        editor.columnconfigure(1, weight=1)
        self._field(editor, 0, "Ukrainian", self.ukrainian_var)
        self._field(editor, 1, "English", self.english_var)
        ttk.Label(editor, text="Example").grid(row=2, column=0, sticky="nw", padx=(0, 8), pady=5)
        self.example_text.grid(row=2, column=1, sticky="ew", pady=5)
        self._field(editor, 3, "Example target", self.example_target_var)

        ttk.Label(editor, text="Decision").grid(row=4, column=0, sticky="w", padx=(0, 8), pady=5)
        decision_box = ttk.Combobox(editor, textvariable=self.decision_var, values=DECISIONS, state="readonly")
        decision_box.grid(row=4, column=1, sticky="ew", pady=5)
        self._field(editor, 5, "Reviewer note", self.note_var)
        self._field(editor, 6, "Source queue", self.origin_var, state="readonly")

        buttons = ttk.Frame(editor)
        buttons.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(12, 8))
        ttk.Button(buttons, text="Apply", command=self._apply_form).pack(side="left", padx=3)
        ttk.Button(buttons, text="Accept + Next", command=lambda: self._set_and_next("accept")).pack(side="left", padx=3)
        ttk.Button(buttons, text="Needs Review + Next", command=lambda: self._set_and_next("needs_review")).pack(
            side="left", padx=3
        )
        ttk.Button(buttons, text="Reject + Next", command=lambda: self._set_and_next("reject")).pack(side="left", padx=3)

        ttk.Label(editor, text="Validation").grid(row=8, column=0, sticky="nw", padx=(0, 8), pady=(12, 5))
        validation = ttk.Label(
            editor,
            textvariable=self.validation_var,
            wraplength=520,
            justify="left",
            foreground="#934100",
        )
        validation.grid(row=8, column=1, sticky="w", pady=(12, 5))

    def _show_in_foreground(self) -> None:
        self.master.deiconify()
        self.master.lift()
        self.master.attributes("-topmost", True)
        self.master.focus_force()
        self.master.after(1000, lambda: self.master.attributes("-topmost", False))

    def _field(self, parent: ttk.Frame, row: int, label: str, variable: tk.StringVar, state: str = "normal") -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=5)
        entry = ttk.Entry(parent, textvariable=variable, state=state)
        entry.grid(row=row, column=1, sticky="ew", pady=5)

    def _refresh_tree(self) -> None:
        selected_key = self.current_index
        self.tree.delete(*self.tree.get_children())
        filter_value = self.filter_var.get()
        for index, item in enumerate(self.items):
            if filter_value != "all" and item.decision != filter_value:
                continue
            self.tree.insert(
                "",
                "end",
                iid=str(index),
                values=(item.decision, item.card.ukrainian, item.card.english, item.origin),
            )
        if selected_key is not None and self.tree.exists(str(selected_key)):
            self.tree.selection_set(str(selected_key))
        self._update_status()

    def _on_tree_select(self, _event: object) -> None:
        selection = self.tree.selection()
        if selection:
            self._select_index(int(selection[0]))

    def _select_index(self, index: int) -> None:
        self.current_index = index
        item = self.items[index]
        card = item.card
        self.ukrainian_var.set(card.ukrainian)
        self.english_var.set(card.english)
        self.example_target_var.set(card.example_target)
        self.decision_var.set(item.decision)
        self.note_var.set(item.reason)
        self.origin_var.set(item.origin)
        self.example_text.delete("1.0", "end")
        self.example_text.insert("1.0", card.example)
        if self.tree.exists(str(index)):
            self.tree.selection_set(str(index))
            self.tree.see(str(index))
        self._update_validation(card)

    def _card_from_form(self, original: VocabCard) -> VocabCard:
        return replace(
            original,
            ukrainian=self.ukrainian_var.get().strip(),
            english=self.english_var.get().strip(),
            example=self.example_text.get("1.0", "end").strip(),
            example_target=self.example_target_var.get().strip(),
        )

    def _apply_form(self) -> None:
        if self.current_index is None:
            return
        current = self.items[self.current_index]
        card = self._card_from_form(current.card)
        if not card.ukrainian or not card.english:
            messagebox.showwarning("Missing fields", "Accepted or pending cards need Ukrainian and English values.")
            return
        self.items[self.current_index] = replace(
            current,
            card=card,
            decision=self.decision_var.get(),
            reason=self.note_var.get().strip(),
        )
        self._update_validation(card)
        self._refresh_tree()

    def _set_and_next(self, decision: str) -> None:
        self.decision_var.set(decision)
        self._apply_form()
        if self.current_index is None:
            return
        candidates = [int(item_id) for item_id in self.tree.get_children()]
        if self.current_index in candidates:
            position = candidates.index(self.current_index)
            if position + 1 < len(candidates):
                self._select_index(candidates[position + 1])

    def _update_validation(self, card: VocabCard) -> None:
        issues = validate_cards([card])
        errors = [issue for issue in issues if issue.severity in {"error", "warning"}]
        if errors:
            self.validation_var.set("\n".join(f"{issue.severity.upper()}: {issue.message}" for issue in errors))
        elif not card.example_target:
            self.validation_var.set("Word card only: no safe sentence target selected.")
        else:
            self.validation_var.set("Sentence target passes deterministic validation.")

    def _save_review(self) -> None:
        self._apply_form()
        count = write_manual_review(self.items, self.session_output)
        messagebox.showinfo("Review saved", f"Saved {count} card decisions to:\n{self.session_output}")

    def _choose_export_path(self) -> None:
        selected = filedialog.asksaveasfilename(
            title="Choose export base CSV",
            initialfile=self.export_output.name,
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv")],
        )
        if selected:
            self.export_output = Path(selected)
            self._update_status()

    def _export_accepted(self) -> None:
        self._apply_form()
        pending_count = sum(item.decision == "needs_review" for item in self.items)
        if pending_count and not messagebox.askyesno(
            "Pending cards remain",
            f"{pending_count} cards are still marked needs_review.\n\nExport accepted cards anyway?",
        ):
            return
        write_manual_review(self.items, self.session_output)
        count = export_accepted_cards(self.items, self.export_output, self.card_format)
        messagebox.showinfo("Export complete", f"Exported {count} accepted study rows from:\n{self.export_output}")

    def _update_status(self) -> None:
        counts = {decision: sum(item.decision == decision for item in self.items) for decision in DECISIONS}
        self.status_var.set(
            f"Accept: {counts['accept']}  Needs review: {counts['needs_review']}  Reject: {counts['reject']}"
        )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Visually validate reviewed flashcards before Anki export.")
    parser.add_argument("--input", type=Path, help="Accepted/reviewed fields CSV to load.")
    parser.add_argument("--needs-review", type=Path, help="Optional *_needs_review.csv audit to inspect.")
    parser.add_argument("--rejected", type=Path, help="Optional *_rejected.csv audit to inspect.")
    parser.add_argument("--resume-session", type=Path, help="Reopen a previously saved manual-review CSV.")
    parser.add_argument("--session-output", type=Path, help="Where all manual decisions are saved.")
    parser.add_argument("--output", type=Path, help="Accepted-card export base CSV.")
    parser.add_argument(
        "--card-format",
        choices=["fields", "basic", "basic-with-sentences", "bidirectional-with-sentences"],
        default="bidirectional-with-sentences",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if args.resume_session:
        items = read_audit_csv(args.resume_session, default_decision="needs_review")
        source_path = args.resume_session
    elif args.input:
        items = load_review_items(args.input, needs_review_path=args.needs_review, rejected_path=args.rejected)
        source_path = args.input
    else:
        raise SystemExit("Provide --input or --resume-session.")
    session_output = args.session_output or source_path.with_name(f"{source_path.stem}_manual_review.csv")
    output = args.output or source_path.parent / "approved" / f"{source_path.stem}_approved.csv"
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        print(f"Desktop UI unavailable ({exc}). Starting the local browser interface instead.")
        from card_review_web import main as web_main

        return web_main(argv)
    CardReviewApp(
        root,
        items,
        session_output=session_output,
        export_output=output,
        card_format=args.card_format,
    )
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
