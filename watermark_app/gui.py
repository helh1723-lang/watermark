from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .processor import SUPPORTED_EXTS, collect_inputs, embed_many, read_file


FILE_TYPES = [
    (
        "Supported files",
        " ".join(f"*{suffix}" for suffix in sorted(SUPPORTED_EXTS)),
    ),
    ("Images", "*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff"),
    ("Documents", "*.pdf *.docx *.doc"),
    ("Videos", "*.mp4 *.mov *.avi *.mkv *.webm *.m4v"),
    ("All files", "*.*"),
]


class WatermarkApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("隐形数字水印工具")
        self.geometry("980x700")
        self.minsize(860, 600)
        self.work_queue: queue.Queue[object] = queue.Queue()

        self.input_path = tk.StringVar()
        self.output_path = tk.StringVar(value=str(Path.cwd() / "output"))
        self.read_path = tk.StringVar()
        self.password = tk.StringVar()
        self.strength = tk.StringVar(value="balanced")
        self.profile = tk.StringVar(value="balanced")
        self.pdf_mode = tk.StringVar(value="both")
        self.doc_mode = tk.StringVar(value="both")
        self.deep_scan = tk.BooleanVar(value=True)

        self._build()
        self.after(100, self._drain_queue)

    def _build(self) -> None:
        tabs = ttk.Notebook(self)
        tabs.pack(fill="both", expand=True, padx=10, pady=10)
        embed_tab = ttk.Frame(tabs, padding=12)
        read_tab = ttk.Frame(tabs, padding=12)
        tabs.add(embed_tab, text="添加水印")
        tabs.add(read_tab, text="读取/验证")

        self._input_row(embed_tab, "输入文件或文件夹", self.input_path, 0)
        self._output_row(embed_tab, "输出目录", self.output_path, 1)

        ttk.Label(embed_tab, text="水印内容").grid(row=2, column=0, sticky="w", pady=(12, 4))
        self.text_box = tk.Text(embed_tab, height=8, wrap="word")
        self.text_box.insert("1.0", "内部资料，仅授权本人使用。")
        self.text_box.grid(row=3, column=0, columnspan=4, sticky="nsew")

        options = ttk.LabelFrame(embed_tab, text="写入设置", padding=10)
        options.grid(row=4, column=0, columnspan=4, sticky="ew", pady=10)
        self._password_field(options, 0)

        ttk.Label(options, text="强度").grid(row=0, column=2, sticky="w", padx=(18, 4))
        ttk.Combobox(
            options,
            textvariable=self.strength,
            values=["subtle", "balanced", "strong"],
            state="readonly",
            width=12,
        ).grid(row=0, column=3, sticky="w")

        ttk.Label(options, text="策略").grid(row=0, column=4, sticky="w", padx=(18, 4))
        ttk.Combobox(
            options,
            textvariable=self.profile,
            values=["invisible", "balanced", "durable", "benchmark", "legacy"],
            state="readonly",
            width=14,
        ).grid(row=0, column=5, sticky="w")

        ttk.Label(options, text="PDF 模式").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Combobox(options, textvariable=self.pdf_mode, values=["keep", "strong", "both"], state="readonly", width=10).grid(
            row=1, column=1, sticky="w", pady=(8, 0)
        )

        ttk.Label(options, text="DOC/DOCX 模式").grid(row=1, column=2, sticky="w", padx=(18, 4), pady=(8, 0))
        ttk.Combobox(options, textvariable=self.doc_mode, values=["keep", "strong", "both"], state="readonly", width=10).grid(
            row=1, column=3, sticky="w", pady=(8, 0)
        )

        ttk.Label(
            embed_tab,
            text="支持：图片、PDF、DOCX、DOC、MP4、MOV、AVI、MKV、WEBM、M4V。DOC/DOCX strong 需要 LibreOffice；视频输出为 AVI/FFV1。",
            foreground="#475569",
        ).grid(row=5, column=0, columnspan=4, sticky="w", pady=(0, 8))

        self.embed_button = ttk.Button(embed_tab, text="开始添加水印", command=self._start_embed)
        self.embed_button.grid(row=6, column=0, sticky="w")

        self._read_row(read_tab, "待读取文件", self.read_path, 0)
        read_options = ttk.LabelFrame(read_tab, text="读取设置", padding=10)
        read_options.grid(row=1, column=0, columnspan=4, sticky="ew", pady=12)
        self._password_field(read_options, 0)
        ttk.Checkbutton(read_options, text="深度扫描（适合裁剪/截图后的图片，视频只深扫少量帧）", variable=self.deep_scan).grid(
            row=1, column=0, columnspan=4, sticky="w", pady=(8, 0)
        )
        self.read_button = ttk.Button(read_tab, text="读取/验证水印", command=self._start_read)
        self.read_button.grid(row=2, column=0, sticky="w", pady=8)

        output_frame = ttk.LabelFrame(self, text="处理日志", padding=8)
        output_frame.pack(fill="both", expand=False, padx=10, pady=(0, 10))
        self.progress = ttk.Progressbar(output_frame, mode="indeterminate")
        self.progress.pack(fill="x", pady=(0, 6))
        self.log = tk.Text(output_frame, height=12, wrap="word")
        self.log.pack(fill="both", expand=True)

        for tab in (embed_tab, read_tab):
            tab.columnconfigure(1, weight=1)
        embed_tab.rowconfigure(3, weight=1)

    def _password_field(self, parent: ttk.Frame, row: int) -> None:
        ttk.Label(parent, text="认证口令").grid(row=row, column=0, sticky="w")
        ttk.Entry(parent, textvariable=self.password, show="*", width=28).grid(row=row, column=1, sticky="w", padx=(6, 0))

    def _input_row(self, parent: ttk.Frame, label: str, var: tk.StringVar, row: int) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(parent, textvariable=var).grid(row=row, column=1, sticky="ew", padx=8)
        ttk.Button(parent, text="选择文件", command=self._pick_input_file).grid(row=row, column=2, sticky="e", padx=(0, 6))
        ttk.Button(parent, text="选择文件夹", command=self._pick_input_folder).grid(row=row, column=3, sticky="e")

    def _read_row(self, parent: ttk.Frame, label: str, var: tk.StringVar, row: int) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(parent, textvariable=var).grid(row=row, column=1, sticky="ew", padx=8)
        ttk.Button(parent, text="选择文件", command=self._pick_read_file).grid(row=row, column=2, sticky="e")

    def _output_row(self, parent: ttk.Frame, label: str, var: tk.StringVar, row: int) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(parent, textvariable=var).grid(row=row, column=1, sticky="ew", padx=8)
        ttk.Button(parent, text="选择目录", command=self._pick_output).grid(row=row, column=2, sticky="e")

    def _pick_input_file(self) -> None:
        path = filedialog.askopenfilename(filetypes=FILE_TYPES)
        if path:
            self.input_path.set(path)

    def _pick_input_folder(self) -> None:
        path = filedialog.askdirectory()
        if path:
            self.input_path.set(path)

    def _pick_output(self) -> None:
        path = filedialog.askdirectory()
        if path:
            self.output_path.set(path)

    def _pick_read_file(self) -> None:
        path = filedialog.askopenfilename(filetypes=FILE_TYPES)
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
            messagebox.showerror("缺少口令", "请输入水印认证口令。")
            return False
        return True

    def _start_embed(self) -> None:
        if not self._validate_common():
            return
        text = self.text_box.get("1.0", "end").strip()
        if not text:
            messagebox.showerror("缺少水印内容", "请输入要嵌入的水印内容。")
            return
        input_path = self.input_path.get().strip()
        if not input_path:
            messagebox.showerror("缺少输入", "请选择输入文件或文件夹。")
            return

        def worker() -> None:
            self._control("busy")
            self._log("开始添加水印...")
            try:
                inputs = collect_inputs(input_path)
                if not inputs:
                    self._log("没有找到支持的文件。")
                    return
                self._log(f"发现 {len(inputs)} 个支持文件。")
                results = embed_many(
                    inputs,
                    self.output_path.get().strip(),
                    text,
                    self.password.get(),
                    self.strength.get(),
                    self.profile.get(),
                    self.pdf_mode.get(),
                    self.doc_mode.get(),
                )
                for result in results:
                    self._log(self._format_process_result(result))
                self._log("处理完成。")
            except Exception as exc:
                self._log(f"[error] {exc}")
            finally:
                self._control("idle")

        threading.Thread(target=worker, daemon=True).start()

    def _format_process_result(self, result) -> str:
        detail = result.output_path or result.message
        metrics: list[str] = []
        if result.quality_psnr:
            metrics.append(f"PSNR {result.quality_psnr:.2f}")
        if result.quality_ssim:
            metrics.append(f"SSIM {result.quality_ssim:.4f}")
        if result.tiles_total:
            metrics.append(f"tiles {result.tiles_used}/{result.tiles_total}")
        if result.frames_total:
            metrics.append(f"frames {result.frames_marked}/{result.frames_total}")
        if result.mode:
            metrics.append(result.mode)
        suffix = f" | {', '.join(metrics)}" if metrics else ""
        return f"[{result.status}] {result.input_path} -> {detail}{suffix}"

    def _start_read(self) -> None:
        if not self._validate_common():
            return
        path = self.read_path.get().strip()
        if not path:
            messagebox.showerror("缺少文件", "请选择要读取的文件。")
            return

        def worker() -> None:
            self._control("busy")
            self._log("开始读取/验证水印...")
            try:
                result = read_file(path, self.password.get(), deep_scan=self.deep_scan.get())
                self._log(f"[{result.status}] {result.message}")
                if result.status == "ok":
                    self._log(f"ID: {result.watermark_id}")
                    self._log(f"模式: {result.mode}")
                    self._log(f"创建时间: {result.created_at or '--'}")
                    if result.tiles_checked:
                        self._log(f"tile 置信度: {result.confidence:.2f}，验证 tile: {result.tiles_verified}/{result.tiles_checked}")
                    if result.frames_checked:
                        self._log(f"视频帧验证: {result.frames_verified}/{result.frames_checked}")
                    self._log(f"核心文本: {result.core_text or '强水印层仅包含认证 ID；未找到同目录记录文本。'}")
            except Exception as exc:
                self._log(f"[error] {exc}")
            finally:
                self._control("idle")

        threading.Thread(target=worker, daemon=True).start()


def main() -> None:
    WatermarkApp().mainloop()


if __name__ == "__main__":
    main()
