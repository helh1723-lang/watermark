# 技术说明

## 目标

项目目标是本地认证型隐形水印：在尽量低可见痕迹的前提下，为图片、文档和视频提供可验证的水印 ID 与认证标签。项目不做内容加密，不承诺水印绝对不可清除。

## 核心算法

默认图片算法为 `IWM2 Local Robust`：

- 多 tile 局部嵌入，同一短认证消息分布到多个区域。
- DWT 频带分解结合 DCT 系数调制。
- Hamming(15,11) 纠错、bit interleaving 和 tile 投票恢复。
- 内容感知 tile 选择，优先纹理、边缘、灰度过渡区，避开大面积纯白/纯黑和透明区域。
- 读取结果返回 tile 数、认证 tile、误码估计和置信度。

强水印只嵌入短认证包：版本、watermark_id、created_at、HMAC tag 和 CRC。完整文本由 PDF metadata、DOCX customXml 或本地记录承载。

## 文档水印

PDF：

- keep：写入 metadata，不改变页面视觉。
- strong：把页面渲染成图片后写入 IWM2，再重新生成 PDF。

DOCX：

- keep：在 OOXML 包内写入 `customXml/invisibleWatermark.xml`。
- strong：通过 LibreOffice 转 PDF 后走 PDF strong。

DOC：

- 不直接改写老式二进制 OLE 文件。
- 通过 LibreOffice 转 DOCX/PDF 后写入，降低文件损坏风险。

## 视频水印

视频适配器采用关键帧帧级水印：

- 输入视频由 OpenCV 解码。
- 按开头优先 + 全片分布策略选择少量关键帧，将短认证包写入帧图像。
- 默认输出 MP4，并使用视频专用快速 DCT 认证水印提高处理速度和常见播放器兼容性。
- 读取时按秒抽帧，快速恢复认证；必要时对少量帧深度扫描。

局限：

- 低码率 MP4 二次转码会显著降低恢复率。
- 无 ffmpeg 时无法自动保留原音轨。
- 视频级抗转码水印仍可继续引入时域冗余、运动区域选择和频域强同步码。

## Web 架构

- 前端：Vite + React + TypeScript。
- 后端：Python `ThreadingHTTPServer`，本地 REST API。
- `/api/watermark`：multipart 上传并添加水印。
- `/api/read`：multipart 上传并读取验证。
- `/api/download/{token}`：下载处理结果。

服务只绑定 `127.0.0.1`，默认不暴露到局域网。

## 启动脚本

- `scripts/start_web.ps1`：启动 Python API 和 Vite 前端，写入 PID/端口文件，可自动打开浏览器。
- `scripts/stop_web.ps1`：按 PID 树和端口兜底关闭服务。
- `scripts/setup_env.ps1`：安装 Python/Web 依赖并做环境探测。
- `scripts/start_app.ps1` / `scripts/close_app.ps1`：桌面 GUI 启停。

## 评测体系

`watermark_app.benchmark` 会生成测试图片/PDF，并运行 JPEG、裁剪、遮挡、resize、亮度、模糊等攻击。报告包含：

- 恢复率
- PSNR
- SSIM
- 局部差异和纸面污染指标
- 分算法、分格式、分攻击排行

建议提交前至少运行：

```powershell
D:\Anaconda\python.exe tests\smoke_test.py
D:\Anaconda\python.exe tests\robust_test.py
D:\Anaconda\python.exe tests\video_smoke_test.py
cd web
npm.cmd run build
```
