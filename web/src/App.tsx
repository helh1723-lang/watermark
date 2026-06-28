import { useEffect, useMemo, useRef, useState } from "react";
import {
  BadgeCheck,
  Download,
  Eye,
  FileArchive,
  FileText,
  Fingerprint,
  Image as ImageIcon,
  Loader2,
  LockKeyhole,
  SearchCheck,
  ShieldCheck,
  Sparkles,
  UploadCloud,
  Video
} from "lucide-react";

type Profile = "invisible" | "balanced" | "durable";
type Mode = "embed" | "read";

type EmbedResultItem = {
  input_path: string;
  status: string;
  message: string;
  output_path?: string;
  download_url?: string;
  watermark_id?: string;
  mode?: string;
  quality_psnr?: number;
  quality_ssim?: number;
  paper_diff?: number;
  tiles_used?: number;
  tiles_total?: number;
  frames_total?: number;
  frames_marked?: number;
};

type ReadResultItem = {
  input_path: string;
  status: string;
  message: string;
  watermark_id?: string;
  core_text?: string;
  created_at?: number;
  mode?: string;
  tiles_total?: number;
  tiles_checked?: number;
  tiles_verified?: number;
  bit_error_estimate?: number;
  confidence?: number;
  frames_checked?: number;
  frames_verified?: number;
};

const profiles: Array<{ id: Profile; label: string; note: string }> = [
  { id: "balanced", label: "均衡", note: "默认档，兼顾无感与认证恢复" },
  { id: "invisible", label: "无感优先", note: "降低视觉扰动，适合文档和截图" },
  { id: "durable", label: "持久优先", note: "增强抗压缩、裁剪和转码能力" }
];

function SpectrumBackground() {
  const ref = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    let frame = 0;
    let raf = 0;

    const resize = () => {
      const dpr = window.devicePixelRatio || 1;
      canvas.width = Math.floor(window.innerWidth * dpr);
      canvas.height = Math.floor(window.innerHeight * dpr);
      canvas.style.width = `${window.innerWidth}px`;
      canvas.style.height = `${window.innerHeight}px`;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };

    const draw = () => {
      frame += 0.006;
      const width = window.innerWidth;
      const height = window.innerHeight;
      ctx.clearRect(0, 0, width, height);
      ctx.fillStyle = "#f7f8fb";
      ctx.fillRect(0, 0, width, height);

      const grid = 34;
      for (let y = -grid; y < height + grid; y += grid) {
        for (let x = -grid; x < width + grid; x += grid) {
          const wave = Math.sin(x * 0.012 + frame * 8) + Math.cos(y * 0.016 - frame * 6);
          const alpha = 0.08 + Math.max(0, wave) * 0.035;
          ctx.fillStyle = `rgba(17, 24, 39, ${alpha})`;
          ctx.fillRect(x + Math.sin(frame + y) * 2, y, 1, 1);
        }
      }

      for (let i = 0; i < 8; i += 1) {
        const y = (height * (i + 1)) / 9;
        ctx.beginPath();
        for (let x = 0; x <= width; x += 18) {
          const offset = Math.sin(x * 0.009 + frame * 12 + i) * (10 + i * 1.2);
          if (x === 0) ctx.moveTo(x, y + offset);
          else ctx.lineTo(x, y + offset);
        }
        ctx.strokeStyle = `rgba(17, 24, 39, ${0.05 - i * 0.003})`;
        ctx.lineWidth = 1;
        ctx.stroke();
      }

      raf = requestAnimationFrame(draw);
    };

    resize();
    draw();
    window.addEventListener("resize", resize);
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", resize);
    };
  }, []);

  return <canvas className="spectrum" ref={ref} aria-hidden="true" />;
}

function fileIcon(file?: File | null) {
  const name = file?.name.toLowerCase() || "";
  if (name.match(/\.(png|jpg|jpeg|webp|bmp|tiff?)$/)) return <ImageIcon size={22} />;
  if (name.match(/\.(mp4|mov|avi|mkv|webm|m4v)$/)) return <Video size={22} />;
  if (name.match(/\.(pdf|docx?|doc)$/)) return <FileText size={22} />;
  return <FileArchive size={22} />;
}

function formatDate(seconds?: number) {
  if (!seconds) return "--";
  return new Date(seconds * 1000).toLocaleString();
}

function metric(value?: number, digits = 2) {
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? value.toFixed(digits) : "--";
}

export function App() {
  const [mode, setMode] = useState<Mode>("embed");
  const [file, setFile] = useState<File | null>(null);
  const [text, setText] = useState("内部资料，仅授权本人使用。");
  const [password, setPassword] = useState("");
  const [profile, setProfile] = useState<Profile>("balanced");
  const [deepScan, setDeepScan] = useState(true);
  const [busy, setBusy] = useState(false);
  const [embedResults, setEmbedResults] = useState<EmbedResultItem[]>([]);
  const [readResult, setReadResult] = useState<ReadResultItem | null>(null);
  const [error, setError] = useState("");
  const [sparks, setSparks] = useState<Array<{ id: number; x: number; y: number }>>([]);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const ready = Boolean(file && password.trim() && !busy && (mode === "read" || text.trim()));
  const stats = useMemo(() => {
    const ok = embedResults.filter((item) => item.status === "ok");
    const psnr = ok.find((item) => item.quality_psnr)?.quality_psnr;
    const confidence = readResult?.confidence;
    return { ok: ok.length, psnr, confidence };
  }, [embedResults, readResult]);

  const spark = (event: React.MouseEvent) => {
    const id = Date.now();
    setSparks((items) => [...items, { id, x: event.clientX, y: event.clientY }]);
    window.setTimeout(() => setSparks((items) => items.filter((item) => item.id !== id)), 650);
  };

  const resetOutput = () => {
    setError("");
    setEmbedResults([]);
    setReadResult(null);
  };

  const submit = async () => {
    if (!file) return;
    setBusy(true);
    resetOutput();
    const form = new FormData();
    form.set("file", file);
    form.set("password", password);
    try {
      if (mode === "embed") {
        form.set("text", text);
        form.set("profile", profile);
        form.set("pdfMode", "both");
        form.set("docxMode", "both");
        const response = await fetch("/api/watermark", { method: "POST", body: form });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "处理失败");
        setEmbedResults(data.results || []);
      } else {
        form.set("deepScan", String(deepScan));
        const response = await fetch("/api/read", { method: "POST", body: form });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "读取失败");
        setReadResult(data.result || null);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "处理失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="app" onClick={spark}>
      <SpectrumBackground />
      {sparks.map((item) => (
        <span key={item.id} className="spark" style={{ left: item.x, top: item.y }} />
      ))}

      <section className="workspace">
        <aside className="rail">
          <div className="brand">
            <span className="brand-mark"><Fingerprint size={22} /></span>
            <span>隐形水印控制台</span>
          </div>
          <div className="rail-stat">
            <ShieldCheck size={18} />
            <div>
              <strong>IWM2</strong>
              <span>本地鲁棒认证</span>
            </div>
          </div>
          <div className="rail-stat">
            <BadgeCheck size={18} />
            <div>
              <strong>{stats.ok || 0}</strong>
              <span>输出文件</span>
            </div>
          </div>
          <div className="rail-stat">
            <Sparkles size={18} />
            <div>
              <strong>{stats.confidence ? `${Math.round(stats.confidence * 100)}%` : stats.psnr ? `${stats.psnr.toFixed(1)} dB` : "--"}</strong>
              <span>{mode === "read" ? "读取置信度" : "质量评分"}</span>
            </div>
          </div>
        </aside>

        <section className="panel enter">
          <div className="panel-head">
            <div>
              <p className="eyebrow">认证水印工作台</p>
              <h1>{mode === "embed" ? "写入无感认证水印" : "读取并验证隐形水印"}</h1>
            </div>
            <span className="status-pill">Local only</span>
          </div>

          <div className="mode-tabs" role="tablist">
            <button className={mode === "embed" ? "active" : ""} type="button" onClick={() => { setMode("embed"); resetOutput(); }}>
              <ShieldCheck size={17} />
              添加水印
            </button>
            <button className={mode === "read" ? "active" : ""} type="button" onClick={() => { setMode("read"); resetOutput(); }}>
              <SearchCheck size={17} />
              读取水印
            </button>
          </div>

          <div
            className={`dropzone ${file ? "has-file" : ""}`}
            onClick={() => inputRef.current?.click()}
            onDragOver={(event) => event.preventDefault()}
            onDrop={(event) => {
              event.preventDefault();
              setFile(event.dataTransfer.files[0] || null);
              resetOutput();
            }}
          >
            <input
              ref={inputRef}
              type="file"
              hidden
              onChange={(event) => {
                setFile(event.target.files?.[0] || null);
                resetOutput();
              }}
            />
            <div className="file-orb">{file ? fileIcon(file) : <UploadCloud size={26} />}</div>
            <div>
              <strong>{file ? file.name : "拖入或选择文件"}</strong>
              <span>支持图片、PDF、DOCX、DOC、MP4、MOV、AVI、MKV、WEBM，本地处理后返回结果</span>
            </div>
          </div>

          {mode === "embed" && (
            <label className="field">
              <span>水印内容</span>
              <textarea value={text} onChange={(event) => setText(event.target.value)} />
            </label>
          )}

          <label className="field">
            <span>认证口令</span>
            <div className="password">
              <LockKeyhole size={18} />
              <input value={password} onChange={(event) => setPassword(event.target.value)} type="password" />
            </div>
          </label>

          {mode === "embed" ? (
            <div className="profiles">
              {profiles.map((item) => (
                <button
                  key={item.id}
                  className={profile === item.id ? "active" : ""}
                  onClick={() => setProfile(item.id)}
                  type="button"
                >
                  <strong>{item.label}</strong>
                  <span>{item.note}</span>
                </button>
              ))}
            </div>
          ) : (
            <label className="toggle">
              <input checked={deepScan} onChange={(event) => setDeepScan(event.target.checked)} type="checkbox" />
              <span>启用深度扫描</span>
            </label>
          )}

          <button className="primary" disabled={!ready} onClick={submit} type="button">
            {busy ? <Loader2 className="spin" size={19} /> : mode === "embed" ? <ShieldCheck size={19} /> : <Eye size={19} />}
            {busy ? "正在处理" : mode === "embed" ? "添加水印" : "读取水印"}
          </button>

          {error && <div className="error">{error}</div>}
        </section>

        <section className="panel results enter delay">
          <div className="panel-head compact">
            <div>
              <p className="eyebrow">输出</p>
              <h2>{mode === "embed" ? "处理结果" : "验证结果"}</h2>
            </div>
            <span className="status-pill">{busy ? "Running" : "Ready"}</span>
          </div>

          {!embedResults.length && !readResult && !busy && (
            <div className="empty">
              <Fingerprint size={34} />
              <span>{mode === "embed" ? "等待一次写入任务" : "等待一次读取验证"}</span>
            </div>
          )}

          {busy && (
            <div className="progress">
              <div />
            </div>
          )}

          {mode === "embed" && (
            <div className="result-list">
              {embedResults.map((item, index) => (
                <article className={`result ${item.status}`} key={`${item.output_path}-${index}`}>
                  <div>
                    <strong>{item.status === "ok" ? "写入成功" : "处理失败"}</strong>
                    <span>{item.message}</span>
                  </div>
                  <dl>
                    <div><dt>模式</dt><dd>{item.mode || "--"}</dd></div>
                    <div><dt>编号</dt><dd>{item.watermark_id || "--"}</dd></div>
                    <div><dt>PSNR</dt><dd>{metric(item.quality_psnr)}</dd></div>
                    <div><dt>SSIM</dt><dd>{metric(item.quality_ssim, 4)}</dd></div>
                    <div><dt>Tiles</dt><dd>{item.tiles_used || "--"} / {item.tiles_total || "--"}</dd></div>
                    <div><dt>Frames</dt><dd>{item.frames_marked || "--"} / {item.frames_total || "--"}</dd></div>
                  </dl>
                  {item.download_url && (
                    <a className="download" href={item.download_url}>
                      <Download size={18} />
                      下载文件
                    </a>
                  )}
                </article>
              ))}
            </div>
          )}

          {mode === "read" && readResult && (
            <article className={`result ${readResult.status}`}>
              <div>
                <strong>{readResult.status === "ok" ? "认证成功" : "认证失败"}</strong>
                <span>{readResult.message}</span>
              </div>
              <dl>
                <div><dt>模式</dt><dd>{readResult.mode || "--"}</dd></div>
                <div><dt>编号</dt><dd>{readResult.watermark_id || "--"}</dd></div>
                <div><dt>内容</dt><dd>{readResult.core_text || "--"}</dd></div>
                <div><dt>创建时间</dt><dd>{formatDate(readResult.created_at)}</dd></div>
                <div><dt>置信度</dt><dd>{readResult.confidence ? `${Math.round(readResult.confidence * 100)}%` : "--"}</dd></div>
                <div><dt>误码估计</dt><dd>{metric(readResult.bit_error_estimate, 4)}</dd></div>
                <div><dt>Tiles</dt><dd>{readResult.tiles_verified || "--"} / {readResult.tiles_checked || "--"}</dd></div>
                <div><dt>Frames</dt><dd>{readResult.frames_verified || "--"} / {readResult.frames_checked || "--"}</dd></div>
              </dl>
            </article>
          )}
        </section>
      </section>
    </main>
  );
}
