# 隐形数字水印认证工具

一个纯本地运行的隐形数字水印工具，面向“认证、溯源、持久性、低可见痕迹”场景。项目不做内容加密，不上传云端，支持桌面 GUI、网页工作台和命令行。

## 主要能力

- 图片：PNG、JPG、JPEG、WEBP、BMP、TIF、TIFF。
- PDF：支持 metadata 保留层和强水印图像层；去除 metadata 后仍可尝试通过强水印认证。
- DOCX：支持 OOXML 自定义 XML 保留层；强水印模式会经 LibreOffice 转 PDF 后写入。
- DOC：经 LibreOffice 转 DOCX/PDF 后写入水印，输出为转换后的 DOCX/PDF。
- 视频：支持 MP4、MOV、AVI、MKV、WEBM、M4V 输入；默认输出 MP4 关键帧水印文件，兼顾设备兼容性与认证信号保留。
- 网页：支持添加水印和读取验证水印。
- 评测：内置图片/PDF 攻击矩阵 benchmark，输出 JSON、Markdown、HTML 报告。

## 一键使用

首次使用先双击：

```text
一键配置环境.bat
```

启动网页工作台：

```text
启动数字水印网页.bat
```

关闭网页工作台：

```text
关闭数字水印网页.bat
```

如果 Windows 批处理对中文文件名显示异常，也可以使用英文备用入口：

```text
setup_env.bat
start_web.bat
stop_web.bat
```

启动桌面 GUI：

```text
启动数字水印工具.bat
```

关闭桌面 GUI：

```text
关闭数字水印工具.bat
```

默认网页地址：

- Web App: http://127.0.0.1:5173
- Local API: http://127.0.0.1:8765

## 命令行

添加水印：

```powershell
D:\Anaconda\python.exe -m watermark_app.cli embed .\input.png -o .\output -t "内部资料，仅授权本人使用" -p "your-password" --profile balanced
```

读取水印：

```powershell
D:\Anaconda\python.exe -m watermark_app.cli read .\output\input_wm.png -p "your-password" --deep-scan
```

Profile：

- `invisible`：无感优先，适合白底文档、截图、低纹理内容。
- `balanced`：默认档，兼顾不可见性与恢复率。
- `durable`：持久优先，适合会被压缩、裁剪、转发的文件。
- `legacy`：旧版 IWM1 DCT 算法，仅用于兼容和对照。

## 视频说明

视频水印使用“关键帧帧级快速 DCT 认证水印”。默认输出为 MP4，便于在常见设备和播放器中直接打开；程序会优先在开头和全片少量关键帧写入短认证包，减少大视频长时间无响应的问题。

- 如果系统安装了 ffmpeg，程序会尝试把原始音轨复制到输出文件。
- 如果没有 ffmpeg，输出视频可能没有原音轨，但画面水印认证仍可用。
- 若将输出再压成低码率 MP4、强滤镜、缩放或平台二次转码，视频强水印可能下降或丢失。

## 评测

标准评测：

```powershell
D:\Anaconda\python.exe -m watermark_app.benchmark run --output output\benchmark
```

完整攻击矩阵：

```powershell
D:\Anaconda\python.exe -m watermark_app.benchmark run --output output\benchmark_full --full
```

输出：

- `report.md`
- `report.html`
- `results.json`

## 测试

```powershell
D:\Anaconda\python.exe tests\smoke_test.py
D:\Anaconda\python.exe tests\robust_test.py
D:\Anaconda\python.exe tests\video_smoke_test.py
cd web
npm.cmd run build
```

## 依赖

Python：

- Pillow
- numpy
- pypdf
- python-docx
- reportlab
- opencv-python
- PyWavelets
- scikit-image

可选但推荐：

- LibreOffice：DOC/DOCX strong 和 DOC 转换需要。
- Poppler：PDF strong 渲染需要 `pdftoppm`。
- ffmpeg：视频输出保留原音轨需要。

## 文档

- [使用文档](docs/USER_GUIDE.md)
- [技术说明](docs/TECHNICAL_NOTES.md)

## 安全与边界

隐形水印不能保证绝对不可清除。重绘、重排版、强截图压缩、强滤镜、低码率视频转码等极端操作仍可能破坏强水印。对高价值文件建议同时使用 keep 层、strong 层、访问控制和外部审计记录。
