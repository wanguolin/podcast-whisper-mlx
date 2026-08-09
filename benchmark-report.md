# 双主播播客本地 MLX 全文转写测试

测试日期：2026-08-08
设备：Apple M4 Pro，64GB 统一内存，macOS 26.5.2
音频：38 分 24 秒，AAC 44.1kHz 双声道；测试前统一转为 16kHz 单声道 PCM
主方案：`MOSS-Transcribe-Diarize 0.9B` + `mlx-audio 0.4.6`
基线：`whisper-large-v3-turbo-asr-fp16` + `mlx-audio 0.4.6`

## 结论

MOSS 已经可以在这台 Mac 上一次完成 38 分钟中文播客的全文转写、时间戳和说话人标签，速度和内存都可接受。它适合生成可审校初稿，但不宜把原始结果直接发布：两位主播被短暂拆成三个标签，冷门英文/俄文专名仍会错，片头和片尾音乐会诱发时间戳偏移或短幻觉。

推荐实际流程是：**MOSS + 事先核实的热词表 + 已知主播数的标签合并 + 人工审校专名与头尾**。如果只需要文字、不需要说话人，Whisper 快约 3.7 倍；但本次 Whisper 在片头产生乱码幻觉和一条非法时间段，不能直接视为更可靠。

## 实测性能

| 项目 | MOSS 首跑 | MOSS 热词复跑 | Whisper 基线 |
|---|---:|---:|---:|
| 纯模型处理/本地复跑 | 314.09 秒 | 300.86 秒 | 84.85 秒 |
| 相对实时速度 | 7.34x | 7.66x | 27.15x |
| 实时因子 RTF | 0.136 | 0.131 | 0.037 |
| 模型报告峰值 MLX 内存 | 7.21GB | 未单独记录 | 3.26GB |
| swap | 0 | 0 | 0 |
| 输出片段 | 207 | 120 | 992 |
| 非法时间段 | 0 | 0 | 1 |

首次墙钟时间还包括模型下载：MOSS 389.84 秒，其中模型下载约 73 秒；Whisper 145.10 秒，其中模型下载约 58 秒。两个模型缓存合计约 3.2GB，隔离 Python 环境约 409MB。

## 识别质量与潜在错误率

这次没有人工逐字标注的参考稿，所以不能声称测得了真实 CER/WER。可复现的替代指标如下：

- MOSS 热词稿与 Whisper 正文稿在 Unicode 归一化、去标点、去说话人标签后，字符级一致度为 **92.8%**，模型间分歧为 **7.2%**。
- MOSS 官方模型卡报告其 Podcast 测试集 **CER 5.97%、cpCER 7.37%**。这不是本音频的实测值，只能作为先验参考。
- 综合官方 Podcast CER、本音频 7.2% 的双模型分歧和人工可判定样例，建议把这期节目的普通中文正文潜在 CER 预算放在 **约 4%–8%**；冷门英文/俄文专有名词的精确拼写错误率明显更高，保守按 **20%–40%** 预留人工审校量。
- 两次 MOSS 正文的一致度为 **98.9%**，说明主体内容稳定；热词主要改变专名拼写和分段，没有大幅改写正文。

可确认的错误例子：

| 类型 | 未加热词 MOSS | 热词 MOSS / 应为 |
|---|---|---|
| 工作室名 | `CILASVATA` | `Sila Sveta` |
| 艺人名 | `THEWEEKENDDRINK、BILLALISH` | `The Weeknd、Drake、Billie Eilish` |
| 创始人 | `ALEXANDERWUS、ALEXIRUSOV` | `Alexander Us、Alexey Rozov` |
| 固定成语 | `顺发于全母之中` | 应人工改为“舜发于畎亩之中” |
| 说唱组合 | `音色儿` | 语境中更可能是“阴三儿” |

热词并非越多越好。第一遍机器稿中若已有错误专名，再直接拿它生成热词，会把错误固化；热词表应该先查官方来源。

## 说话人区分

- 音频已知只有一位女主播和一位男主播。
- MOSS 两次都输出 `S01/S02/S03`；即便提示词明确要求只用两个标签，仍未遵守。
- 从开场自我介绍和对话轮次可判断：`S01 = 西卡`，`S02 = 万国`，5 个 `S03` 片段也是万国被短暂误拆。
- `S03` 只占约 **11.28 秒**，约为有声片段时长的 **0.5%**；去掉标签本身后，按转写字符计约 **0.6%**。这是可观察到的标签碎片率，不等于严格 DER。真正的 DER 仍需逐帧人工说话人标注。
- 合并 `S03 -> 万国` 后可作为两人播客审校稿；重叠讲话和极短应答仍建议人工听一遍。

## 时间轴可用性

- MOSS 热词稿 119 个正文片段全部满足 `start <= end`；Whisper 有 1 个非法片段（`22.68s -> 16.46s`）。
- MOSS 与 Whisper 的正文边界交叉核对：MOSS 边界到最近 Whisper 边界的中位差为 **0.085 秒**，86.0% 在 0.5 秒内，92.8% 在 1 秒内。
- 片头例外：MOSS 首跑把第一句定位在 7.54 秒，热词复跑却从 0 秒开始；片尾音乐中两次分别在 2204 秒和 2294 秒幻听出一个“嗯”。头尾必须人工修剪。
- MOSS 的片段较长，适合作为说话人段落或文本审校底稿；若直接做字幕，还需要按行宽与语速二次切分。

## 可用性判断

| 场景 | 判断 |
|---|---|
| 内部检索、内容摘要、剪辑定位 | 可用 |
| 两人播客全文初稿 | 可用，需自动合并标签和人工审校 |
| 直接发布逐字稿 | 不建议 |
| 直接生成成片字幕 | 不建议，需重新切句并审头尾 |
| 冷门专名密集的研究节目 | 需先做经核实的热词表 |

## 产物

- `outputs/moss-hotwords-full/transcript.json`：热词版 MOSS 原始输出
- `outputs/moss-full/transcript.json`：无完整热词版 MOSS 原始输出
- `outputs/whisper-full/transcript.json`：Whisper 对照输出
- `outputs/review/artistq_sila_sveta_review.json`：合并说话人、移除片尾幻觉后的审校 JSON
- `outputs/review/artistq_sila_sveta_review.srt`：带主播姓名的审校 SRT
- `outputs/review/artistq_sila_sveta_review.md`：便于阅读全文的 Markdown

## 复现环境与资料

- [MOSS-Transcribe-Diarize 官方模型卡](https://huggingface.co/OpenMOSS-Team/MOSS-Transcribe-Diarize)
- [MLX-Audio 官方仓库](https://github.com/Blaizzy/mlx-audio)
- [Sila Sveta 官方 About 页面](https://www.silasveta.com/about)
- Python 3.12.11；`mlx-audio 0.4.6`、`mlx 0.32.0`、`mlx-lm 0.31.3`、`transformers 5.12.1`

生成审校稿：

```bash
.venv/bin/python scripts/build_review_transcript.py \
  outputs/moss-hotwords-full/transcript.json \
  outputs/review/artistq_sila_sveta_review
```
