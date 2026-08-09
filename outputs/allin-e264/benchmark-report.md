# All-In E264 本地 MLX 全文转写测试

## 结论

这期文件并非约一小时，而是 **1:20:22（4822.152 秒）**。在这台 Apple Silicon Mac 上：

- **正文转写可用**：`whisper-large-v3-turbo-asr-fp16` 用 **150.18 秒**完成全期，约 **32.11× 实时速度**，峰值 MLX 内存约 **4.99 GB**。清晰英语对谈的文字质量较好，适合作为全文初稿。
- **MOSS 单次跑 80 分钟不可用**：虽然进程正常结束，但只输出到 **36:19**，覆盖 **45.19%**，并在中段出现重复和时间倒退。
- **MOSS 分块能救回正文覆盖，但救不回说话人身份**：两个约 40 分钟分块合计 **13:29.24**，完整到最后一句口播；然而模型把同一主播拆成多个名字，且跨块标签漂移，不能直接作为可靠的 speaker diarization。
- **Sortformer v1 不能整期离线运行**：模型尝试申请约 **232.3 GB** Metal 缓冲区，超过本机约 **41.7 GB** 单缓冲上限，在推理前失败。

因此，本期最实用的交付是：**Whisper 全文文字稿 + 末尾幻觉清理 + 人工复核专名和数字**。当前实测组合还不能稳定给这期四到五个声音生成跨 80 分钟一致的说话人标签。

## 音频与环境

| 项目 | 值 |
|---|---:|
| 文件 | `ALLIN-E264_V2_Ch.mp3` |
| 节目标题（文件标签） | Iran War, Oil Shock, Off Ramps, AI's Revenue Explosion and PR Nightmare |
| 时长 | 4822.157 秒（约 1:20:22） |
| 原文件 | 48 kHz、双声道、192 kbps、115,734,803 bytes |
| 测试输入 | 16 kHz、单声道、PCM WAV、4822.152 秒 |
| 原文件 SHA-256 | `ef1f240a6209cb4eec009219d58f77a2f0a118c81367237b587797df43ebe45f` |
| WAV SHA-256 | `2346cfb89ebfb8afe82ca8bf253d56b4507497cad328bb45a6780910bbaae353` |
| Python | 3.12.11 |
| mlx-audio / mlx / mlx-lm | 0.4.6 / 0.32.0 / 0.31.3 |
| transformers | 5.12.1 |

本地环境位于项目的 `.venv`。MOSS 使用官方模型 `OpenMOSS-Team/MOSS-Transcribe-Diarize`；Whisper 使用 `mlx-community/whisper-large-v3-turbo-asr-fp16`。

## 性能和可用性

| 方案 | 推理时间 | 速度 | 峰值 MLX 内存 | 覆盖 | 判断 |
|---|---:|---:|---:|---:|---|
| MOSS，80 分钟单次 | 887.17 秒 | 5.44× | 11.99 GB | 45.19%，止于 36:19 | **失败**：静默截断、局部重复 |
| MOSS，两个约 40 分钟块 | 809.24 秒 | 5.96× | 7.57 GB | 到 80:09.26，覆盖全部口播 | **正文可用，说话人不可用** |
| Whisper large-v3-turbo fp16 | 150.18 秒 | 32.11× | 4.99 GB | 全期 | **推荐做全文初稿** |
| Sortformer 4spk v1，整期离线 | 未进入有效推理 | — | — | 0% | **失败**：请求 232.3 GB Metal 缓冲 |

速度只统计模型推理；首次模型下载不计入。MOSS 两块之间有 30 秒重叠，第二块从全局 40:00 开始。

## 文字识别错误率：能确认什么

本次没有官方或人工逐字金标准，因此**不能严谨计算 WER**。我采用了一个可复现的替代指标：将 MOSS 分块正文和 Whisper 在相同时间窗内的英文单词转成小写、去掉标点，再计算词级 Levenshtein 距离。

| 区间 | MOSS 词数 | Whisper 词数 | 词编辑距离 | 模型分歧率 |
|---|---:|---:|---:|---:|
| 前半段 | 7,519 | 7,467 | 206 | 2.74% |
| 后半段 | 7,544 | 7,486 | 339 | 4.49% |
| 加权合计 | 15,063 | — | 545 | **3.62%** |

这里的 **3.62% 是两个模型之间的分歧，不是真实 WER**：两者可能同时听错，也可能只是缩写、断句或口语写法不同。结合抽听和模型分歧，我对清晰英文正文的操作性估计是 **约 3%–6% 的词需要改动**；这是推断，不是测量值。人名、机构名、缩写、数字和战争地名的错误风险明显高于普通句子，需要定点人工复核。

观察到的典型错误包括：

- `Friedberg` 被写成 `Freeburg`；
- `Sacks` 被写成 `Sax`；
- `PCE` 被写成 `PC`；
- `rearview mirror` 被拆成近似 `Riv You mirror`；
- MOSS 的说话人猜测出现 `David Mazzola`、`Altimeter`、`Sam Weitz` 等不可信标签。

## 说话人识别：为什么目前不可交付

### MOSS 单次长音频

单次输出有五个匿名标签，但只到 36:19。前半段凭上下文可以推测 `S01` 大致对应 Jason、`S02` 大致对应 Brad、`S03` 大致对应 Chamath，其他标签包含 Sacks 和插入的 Sam Altman 片段；这些只是上下文推断，没有人工标注，不能据此计算 DER。

更严重的是约 12:56–13:35 出现重复、回跳，最终在 36:19 静默结束。官方模型页标注支持最长 90 分钟音频，但这次实测表明“接口接受长音频”不等于“这台机器上一次生成能稳定覆盖长音频”。

### MOSS 两个 40 分钟块

分块后正文能到最后一句，但提示词中的人名被模型当成生成式说话人名称：

- 第一块 101 段，猜测名称包括 `Brad Gerstner` 36 段、`David Mazzola` 33 段、`David Friedberg` 24 段、`Chamath Palihapitiya` 5 段、`Sacks` 3 段；
- 第二块 139 段，猜测名称包括 `Chamath Palihapitiya` 51 段、`David Friedberg` 46 段、`Altimeter` 24 段、`Brad Gerstner` 17 段、`Sam Weitz` 1 段。

开场自称 Friedberg 缺席的主持人却被标成 `David Friedberg`；第二块开头是第一块末尾 Sacks 发言的重叠内容，却被标成 `Brad Gerstner`。这直接证明这些名称是**不可信的模型猜测**，不能作为真实 speaker ID。

### Sortformer v1

`mlx-community/diar_sortformer_4spk-v1-fp32` 的整期离线 `generate` 在推理前失败：

```text
RuntimeError: [metal::malloc] Attempting to allocate 232322072000 bytes
which is greater than the maximum allowed buffer size of 41747087360 bytes.
```

官方 MLX-Audio 文档同时提供 Sortformer v2.1 的流式路径，但当前需从 NVIDIA NeMo checkpoint 转换；本轮**没有测试 v2.1**，不能把它写成已验证方案。

## 时间轴和末尾幻觉

- MOSS 单次解析出的 134 段本身没有负时长，但正文中出现重复和时间回跳，且严重截断。
- Whisper 原始输出 1,492 段。原始条件统计中有 18 个空片段、19 个零或负时长片段（两者高度重叠），并有 28 段结束时间晚于人工确认的最后口播区间。
- 音频在约 80:09 后进入结尾音乐；Whisper 继续生成多次 `I'm going all in`，时间戳甚至到 80:40，超过文件时长。
- 审阅版按顺序排除空文本、零或负时长以及结束时间超过 4809.5 秒的片段，共保留 1,463 段；最后一句是 4807.74–4808.90 的 `We need to get merch.`。正文措辞没有自动改写。

## 推荐工作流

1. **全文底稿**：继续用 MLX Whisper large-v3-turbo fp16。它在本机速度、显存和覆盖率上明显最佳。
2. **自动清理**：删除空片段、负/零时长片段、超出音频长度的片段，并对长音乐尾声设置 VAD 或人工确认的 cutoff。
3. **人工复核**：重点查节目名、人名、机构、缩写、数字、战争地名和引用；不要平均用力逐字重听。
4. **说话人标签**：当前先不要发布自动标签。下一轮优先测试 Sortformer v2.1 流式；备选是 20–30 分钟匿名标签分块，再用声纹 embedding 做跨块聚类和人工映射。
5. **MOSS 的定位**：可作为第二模型做文字交叉检查。若继续试，应使用官方匿名标签格式，不要让提示词直接生成主播姓名；40 分钟块已验证能跑完，但跨块身份仍需额外算法。

## 产物

- `whisper-review/transcript.md`：便于阅读的机器审阅稿；
- `whisper-review/transcript.srt`：1,463 条字幕；
- `whisper-review/transcript.json`：清理后的结构化片段及过滤统计；
- `whisper-full/transcript.json`：Whisper 原始完整输出；
- `moss-full/transcript.json`：MOSS 单次长音频原始输出；
- `moss-chunked/chunk1/parsed.json`、`chunk2/parsed.json`：分块输出的自定义解析结果，名称字段明确标为不可信猜测。

## 参考

- [MOSS-Transcribe-Diarize 官方模型页](https://huggingface.co/OpenMOSS-Team/MOSS-Transcribe-Diarize)
- [MLX-Audio 官方仓库](https://github.com/Blaizzy/mlx-audio)
- [MLX-Audio Sortformer 文档](https://github.com/Blaizzy/mlx-audio/blob/main/mlx_audio/vad/models/sortformer/README.md)
