# 使用文档

## 1. 环境配置

双击根目录的 `一键配置环境.bat`。脚本会安装 Python 依赖、Web 依赖，并检查 GUI 运行环境。

如果要强制使用项目内虚拟环境：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup_env.ps1 -UseVenv
```

## 2. 网页工作台

双击 `启动数字水印网页.bat`，浏览器会打开 `http://127.0.0.1:5173`。

网页有两个模式：

- 添加水印：上传文件、填写水印内容和认证口令，选择无感/均衡/持久档位，处理完成后下载输出文件。
- 读取水印：上传已加水印文件，输入同一认证口令，查看水印 ID、文本、创建时间和恢复置信度。

关闭网页服务请双击 `关闭数字水印网页.bat`。

## 3. 桌面 GUI

双击 `启动数字水印工具.bat` 打开桌面程序。关闭时可以直接关窗口，也可以双击 `关闭数字水印工具.bat`。

## 4. 支持格式

| 类型 | 输入 | 输出 | 说明 |
| --- | --- | --- | --- |
| 图片 | png/jpg/jpeg/webp/bmp/tif/tiff | 图片 | 默认使用 IWM2 |
| PDF | pdf | pdf | keep/strong 两层 |
| DOCX | docx | docx/pdf | keep 输出 DOCX，strong 输出 PDF |
| DOC | doc | docx/pdf | 需要 LibreOffice 转换 |
| 视频 | mp4/mov/avi/mkv/webm/m4v | mp4 | 默认输出 MP4，使用全帧快速认证水印 |

## 5. 口令与认证

水印口令用于生成认证标签。读取时必须输入同一口令，否则会认证失败。项目不加密文件内容，也不恢复忘记的口令。

## 6. 推荐档位

- 日常图片、截图：`balanced`。
- 白底文档、低纹理页面：`invisible`。
- 会被转发、裁剪或压缩的资料：`durable`。
- 公开演示或对比旧算法：`legacy`。

## 7. 常见问题

### DOC 或 DOCX strong 失败

安装 LibreOffice，并确保 `soffice` 在 PATH 中。

### PDF strong 失败

安装 Poppler，并确保 `pdftoppm` 在 PATH 中。

### 视频输出没有声音

安装 ffmpeg。没有 ffmpeg 时，程序仍会生成带水印画面的视频，但无法把原视频音轨合并回来。

### 读取失败

确认口令正确；图片/PDF 截图类文件可以勾选深度扫描。视频读取建议使用程序生成的 MP4 文件，不建议读取二次低码率压缩或平台再次转码后的文件。
