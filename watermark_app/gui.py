from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .processor import collect_inputs, embed_many, read_file


class WatermarkApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("隐形数字水印")
        self.geometry("900x640")
        self.minsize(780, 560)
        self.work_queue: queue.Queue[str] = queue.Queue()
        self.input_path = tk.StringVar()
        self.output_path = tk.StringVar(value=str(Path.cwd() / "output"))
        self.password = tk.StringVar()
        self.strength = tk.StringVar(value="balanced")
        self.profile = tk.StringVar(value="balanced")
        self.pdf_mode = tk.StringVar(value="both")
        self.docx_mode = tk.StringVar(value="both")
        self._build()
        self.after(100, self._drain_queue)

    def _build(self) -> None:
        tabs = ttk.Notebook(self)
        tabs.pack(fill="both", expand=True, padx=10, pady=10)
        embed_tab = ttk.Frame(tabs, padding=12)
        read_tab = ttk.Frame(tabs, padding=12)
        tabs.add(embed_tab, text="写入水印")
        tabs.add(read_tab, text="读取/验证")

        self._path_row(embed_tab, "输入文件或文件夹", self.input_path, self._pick_input, 0)
        self._path_row(embed_tab, "输出目录", self.output_path, self._pick_output, 1)

        ttk.Label(embed_tab, text="水印文本（前约 200 个中文字符会直接嵌入文件）").grid(
            row=2, column=0, sticky="w", pady=(12, 4)
        )
        self.text_box = tk.Text(embed_tab, height=8, wrap="word")
        self.text_box.grid(row=3, column=0, columnspan=3, sticky="nsew")

        options = ttk.Frame(embed_tab)
        options.grid(row=4, column=0, columnspan=3, sticky="ew", pady=10)
        ttk.Label(options, text="口令").grid(row=0, column=0, sticky="w")
        ttk.Entry(options, textvariable=self.password, show="*", width=24).grid(row=0, column=1, padx=(6, 18))
        ttk.Label(options, text="强度").grid(row=0, column=2, sticky="w")
        ttk.Combobox(
            options,
            textvariable=self.strength,
            values=["subtle", "balanced", "strong"],
            state="readonly",
            width=12,
        ).grid(row=0, column=3, padx=(6, 18))
        ttk.Label(options, text="PDF").grid(row=0, column=4, sticky="w")
        ttk.Combobox(options, textvariable=self.pdf_mode, values=["keep", "strong", "both"], state="readonly", width=10).grid(
            row=0, column=5, padx=(6, 18)
        )
        ttk.Label(options, text="DOCX").grid(row=0, column=6, sticky="w")
        ttk.Combobox(
            options, textvariable=self.docx_mode, values=["keep", "strong", "both"], state="readonly", width=10
        ).grid(row=0, column=7, padx=(6, 0))
        ttk.Label(options, text="水印策略").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Combobox(
            options,
            textvariable=self.profile,
            values=["invisible", "balanced", "durable", "benchmark", "legacy"],
            state="readonly",
            width=14,
        ).grid(row=1, column=1, padx=(6, 18), pady=(8, 0), sticky="w")

        self.embed_button = ttk.Button(embed_tab, text="开始写入", command=self._start_embed)
        self.embed_button.grid(row=5, column=0, sticky="w")

        self.read_path = tk.StringVar()
        self._path_row(read_tab, "待读取文件", self.read_path, self._pick_read_file, 0)
        ttk.Label(read_tab, text="口令").grid(row=1, column=0, sticky="w", pady=(12, 4))
        ttk.Entry(read_tab, textvariable=self.password, show="*", width=32).grid(row=2, column=0, sticky="w")
        self.read_button = ttk.Button(read_tab, text="读取/验证", command=self._start_read)
        self.read_button.grid(row=3, column=0, sticky="w", pady=10)

        output_frame = ttk.LabelFrame(self, text="处理日志", padding=8)
        output_frame.pack(fill="both", expand=False, padx=10, pady=(0, 10))
        self.progress = ttk.Progressbar(output_frame, mode="indeterminate")
        self.progress.pack(fill="x", pady=(0, 6))
        self.log = tk.Text(output_frame, height=10, wrap="word")
        self.log.pack(fill="both", expand=True)

        embed_tab.columnconfigure(0, weight=1)
        embed_tab.rowconfigure(3, weight=1)
        read_tab.columnconfigure(0, weight=1)

    def _path_row(self, parent: ttk.Frame, label: str, var: tk.StringVar, command, row: int) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(parent, textvariable=var).grid(row=row, column=1, sticky="ew", padx=8)
        ttk.Button(parent, text="选择", command=command).grid(row=row, column=2, sticky="e")
        parent.columnconfigure(1, weight=1)

    def _pick_input(self) -> None:
        path = filedialog.askopenfilename()
        if not path:
            path = filedialog.askdirectory()
        if path:
            self.input_path.set(path)

    def _pick_output(self) -> None:
        path = filedialog.askdirectory()
        if path:
            self.output_path.set(path)

    def _pick_read_file(self) -> None:
        path = filedialog.askopenfilename()
        if path:
            self.read_path.set(path)

    def _log(self, message: str) -> None:
        self.work_queue.put(message)

    def _control(self, name: str) -> None:
        self.work_queue.put(("control", name))

    def _drain_queue(self) -> None:
        while True:
            try:
                message = self.work_queue.get_nowait()
            except queue.Empty:
                break
            if isinstance(message, tuple) and message[:1] == ("control",):
                if message[1] == "busy":
                    self.progress.start(12)
                    self.embed_button.configure(state="disabled")
                    self.read_button.configure(state="disabled")
                elif message[1] == "idle":
                    self.progress.stop()
                    self.embed_button.configure(state="normal")
                    self.read_button.configure(state="normal")
                continue
            self.log.insert("end", str(message) + "\n")
            self.log.see("end")
        self.after(100, self._drain_queue)

    def _validate_common(self) -> bool:
        if not self.password.get():
            messagebox.showerror("缺少口令", "请输入水印口令。")
            return False
        return True

    def _start_embed(self) -> None:
        if not self._validate_common():
            return
        text = self.text_box.get("1.0", "end").strip()
        if not text:
            messagebox.showerror("缺少水印文本", "请输入要嵌入的水印文本。")
            return
        input_path = self.input_path.get().strip()
        if not input_path:
            messagebox.showerror("缺少输入", "请选择输入文件或文件夹。")
            return

        def worker() -> None:
            self._control("busy")
            self._log("开始写入水印...")
            try:
                inputs = collect_inputs(input_path)
                if not inputs:
                    self._log("没有找到支持的文件。")
                    return
                results = embed_many(
                    inputs,
                    self.output_path.get().strip(),
                    text,
                    self.password.get(),
                    self.strength.get(),
                    self.profile.get(),
                    self.pdf_mode.get(),
                    self.docx_mode.get(),
                )
                for result in results:
                    detail = result.output_path or result.message
                    if result.quality_psnr:
                        detail += f" | PSNR {result.quality_psnr:.2f}, SSIM {result.quality_ssim:.4f}, tiles {result.tiles_used}/{result.tiles_total}"
                    self._log(f"[{result.status}] {result.input_path} -> {detail}")
                self._log("处理完成。")
            finally:
                self._control("idle")

        threading.Thread(target=worker, daemon=True).start()

    def _start_read(self) -> None:
        if not self._validate_common():
            return
        path = self.read_path.get().strip()
        if not path:
            messagebox.showerror("缺少文件", "请选择要读取的文件。")
            return

        def worker() -> None:
            self._control("busy")
            try:
                result = read_file(path, self.password.get())
                self._log(f"[{result.status}] {result.message}")
                if result.status == "ok":
                    self._log(f"ID: {result.watermark_id}")
                    self._log(f"模式: {result.mode}")
                    if result.tiles_checked:
                        self._log(
                            f"强水印置信度: {result.confidence:.2f}，验证 tile: {result.tiles_verified}/{result.tiles_checked}"
                        )
                    self._log(f"核心文本: {result.core_text or '强水印层仅包含认证 ID；未找到同目录记录文本。'}")
            finally:
                self._control("idle")

        threading.Thread(target=worker, daemon=True).start()


def main() -> None:
    WatermarkApp().mainloop()


if __name__ == "__main__":
    main()
