"""Tkinter controller window for selecting CSV files served by the Flask app."""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox
from typing import List

import requests

SERVER_URL = "http://127.0.0.1:5000"
FILES_ENDPOINT = f"{SERVER_URL}/api/files"
CURRENT_ENDPOINT = f"{SERVER_URL}/api/current"


class SelectorApp:
    def __init__(self, master: tk.Tk) -> None:
        self.master = master
        self.master.title("Route Selector")
        self.master.resizable(False, False)

        self.files: List[str] = []
        self.suppress_select = False

        frame = tk.Frame(master, padx=8, pady=8)
        frame.pack(fill=tk.BOTH, expand=True)

        list_frame = tk.Frame(frame)
        list_frame.pack()

        self.listbox = tk.Listbox(list_frame, width=50, height=15, exportselection=False)
        self.listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scrollbar = tk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.listbox.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.listbox.configure(yscrollcommand=scrollbar.set)

        button_frame = tk.Frame(frame, pady=6)
        button_frame.pack(fill=tk.X)

        up_button = tk.Button(button_frame, text="▲", width=8, command=lambda: self.move_selection(-1))
        up_button.pack(side=tk.LEFT, padx=4)

        down_button = tk.Button(button_frame, text="▼", width=8, command=lambda: self.move_selection(1))
        down_button.pack(side=tk.LEFT, padx=4)

        refresh_button = tk.Button(button_frame, text="Refresh", command=self.refresh_files)
        refresh_button.pack(side=tk.RIGHT, padx=4)

        self.status_var = tk.StringVar(value="Loading files...")
        status_label = tk.Label(frame, textvariable=self.status_var, anchor="w")
        status_label.pack(fill=tk.X, pady=(6, 0))

        self.listbox.bind("<<ListboxSelect>>", self.on_select)
        self.listbox.bind("<Up>", lambda event: (self.move_selection(-1), "break"))
        self.listbox.bind("<Down>", lambda event: (self.move_selection(1), "break"))

        self.refresh_files()

    def set_status(self, message: str) -> None:
        self.status_var.set(message)

    def refresh_files(self) -> None:
        try:
            response = requests.get(FILES_ENDPOINT, timeout=5)
            response.raise_for_status()
            self.files = response.json()
        except Exception as exc:  # pragma: no cover - network errors
            self.files = []
            self.set_status(f"Failed to load files: {exc}")
            messagebox.showerror("Error", f"Unable to fetch file list:\n{exc}")
            self.populate_listbox()
            return

        self.populate_listbox()
        self.sync_current_selection()

    def populate_listbox(self) -> None:
        self.suppress_select = True
        self.listbox.delete(0, tk.END)
        for name in self.files:
            self.listbox.insert(tk.END, name)
        self.suppress_select = False
        if not self.files:
            self.set_status("No files found")
        else:
            self.set_status("Select a file to update the map")

    def sync_current_selection(self) -> None:
        try:
            response = requests.get(CURRENT_ENDPOINT, timeout=5)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:  # pragma: no cover - network errors
            self.set_status(f"Failed to sync current file: {exc}")
            return

        current = payload.get("file")
        if current and current in self.files:
            index = self.files.index(current)
            self.select_index(index, notify=False)
        elif self.files:
            # Default to first if server has nothing selected yet
            self.select_index(0, notify=True)

    def select_index(self, index: int, notify: bool = True) -> None:
        if not (0 <= index < len(self.files)):
            return
        self.suppress_select = True
        self.listbox.selection_clear(0, tk.END)
        self.listbox.selection_set(index)
        self.listbox.activate(index)
        self.listbox.see(index)
        self.suppress_select = False
        if notify:
            self.notify_selection(index)

    def move_selection(self, offset: int) -> None:
        if not self.files:
            return
        current_indices = self.listbox.curselection()
        if current_indices:
            index = current_indices[0]
        else:
            index = 0 if offset >= 0 else len(self.files) - 1
        new_index = max(0, min(len(self.files) - 1, index + offset))
        if new_index != index:
            self.select_index(new_index, notify=True)
        else:
            # Even if index doesn't change, ensure selection is visible/active
            self.select_index(new_index, notify=True)

    def on_select(self, event) -> None:
        if self.suppress_select:
            return
        selection = self.listbox.curselection()
        if not selection:
            return
        self.notify_selection(selection[0])

    def notify_selection(self, index: int) -> None:
        filename = self.files[index]
        try:
            response = requests.post(CURRENT_ENDPOINT, json={"file": filename}, timeout=5)
            response.raise_for_status()
            payload = response.json()
            if not payload.get("ok"):
                raise RuntimeError(payload.get("error", "Unknown error"))
            self.set_status(f"Selected: {filename}")
        except Exception as exc:  # pragma: no cover - network errors
            messagebox.showerror("Error", f"Failed to update selection:\n{exc}")
            self.set_status(f"Failed to update selection: {exc}")


def main() -> None:
    root = tk.Tk()
    SelectorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
