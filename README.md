# echo

用自己的 Twitter 存档数据微调 Qwen3.5-9B，让模型学会你的说话风格

本教程并不旨在让你学会（确信），只是提供一份能跑通的思路和基本正确的代码，具体要自己修改。

## 硬件需求

我们使用 unsloth 进行微调，而 unsloth 在文档中提到，不建议使用 QLora 的方式微调，这意味着我们必须要以 16-bit 模型直接微调。所以你的 GPU 显存必须大于 **20G**（我实测数据）才能成功进行下去。

## 流程概览

```
Twitter 存档
    │
    ▼
[Step 1] parse_archive.py          解析存档，分三路输出
    ▼
[Step 2] infer_reply_context.py    Gemini 推断孤立回复的父推内容
    ▼
[Step 3] infer_tweet_trigger.py    Gemini 为独立推文生成触发语境
    ▼
[Step 4] build_dataset.py          构建训练数据集，输出 merged.jsonl
    ▼
[Step 5] train.py                  unsloth LoRA 微调
    ▼
[Step 6] upload.py                 上传到 HuggingFace
    ▼
[Step 7] 部署推理服务              Ollama 或 llama-server
    ▼
[Step 8] tg_bot.py                 Telegram Bot 接入
```

## 目录结构

```
.
├── scripts/
│   ├── constants.py            # 所有共用常量（名称、system prompt、路径）
│   ├── parse_archive.py        # Step 1
│   ├── infer_reply_context.py  # Step 2
│   ├── infer_tweet_trigger.py  # Step 3
│   ├── build_dataset.py        # Step 4
│   ├── train.py                # Step 5
│   ├── upload.py               # Step 6
│   ├── build_modelfile.py      # Step 7（Ollama 用）
│   ├── run_server.sh           # Step 7（llama-server 用）
│   └── tg_bot.py               # Step 8
├── data/
│   └── identity.json           # 用于构建模型的自我认知
├── twitter_archive/            # Twitter 导出的存档
├── output/                     # 运行时产出
│   ├── tweets.json             # Step 1 产出：独立推文
│   ├── replies_matched.json    # Step 1 产出：父推在存档内的回复
│   ├── replies_unmatched.json  # Step 1 产出：父推不在存档内的回复
│   ├── replies_inferred.json   # Step 2 产出
│   ├── tweets_triggered.json   # Step 3 产出
│   ├── dataset/
│   │   └── merged.jsonl        # Step 4 产出（最终训练数据）
│   └── checkpoints/
│       └── lora_adapter_final/ # Step 5 产出
├── models/
│   └── *.gguf                  # 本地 GGUF（Ollama 需要手动下载到此处）
└── Modelfile                   # Step 7 生成（Ollama 用，不上传 git）
```

## 个性化配置

所有需要改的东西都在 `scripts/constants.py` 顶部：

```python
NAME      = "roitium"
DEVELOPER = "roitium 科技无限公司"
SYSTEM_PROMPT = (
    f"你是 {NAME}，一个喜欢技术、ACG 和分享日常的推特用户。"
    "请用简短自然的中文口语风格回复，就像在推特上随手发的消息一样。"
)
```

修改后，Modelfile 需要重新生成（Step 7）。

## 环境准备

```bash
pip install -e .
```

Gemini API（Steps 2/3）还需要二选一：
- **Google AI Studio**：[aistudio.google.com](https://aistudio.google.com) 获取 API key，传给 `--api-key`
- **Vertex AI**：`gcloud auth application-default login` 配置 ADC，传 `--backend vertex --project YOUR_PROJECT`，不需要 API key

## 准备 Twitter 存档

前往 [Twitter 数据导出页面](https://help.x.com/en/managing-your-account/how-to-download-your-x-archive) 申请数据导出，等邮件通知后下载并解压，将整个目录放到项目根目录下，重命名为 `twitter_archive/`。核心文件是 `twitter_archive/data/tweets.js`。

## Step 1：解析存档

从 `twitter_archive/data/tweets.js` 读取推文，过滤转推，在存档内解析父推关系，按三路输出：

```bash
python scripts/parse_archive.py
```

| 产出文件 | 内容 |
|---------|------|
| `output/tweets.json` | 独立推文（无父推） |
| `output/replies_matched.json` | 父推在存档内，含 `parent_text`，**直接可用** |
| `output/replies_unmatched.json` | 父推不在存档内，需 Step 2 推断 |

## Step 2：推断孤立回复的父推内容

对 `replies_unmatched.json` 中每条回复，Gemini 根据回复内容反推"它最可能在回复什么"，写入 `inferred_original_tweet` 字段。支持断点续跑。

```bash
# Google AI Studio
python scripts/infer_reply_context.py --api-key YOUR_GEMINI_API_KEY --workers 8

# Vertex AI（ADC，不需要 API key）
gcloud auth application-default login
python scripts/infer_reply_context.py --backend vertex --project YOUR_GCP_PROJECT --workers 8
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--model` | `gemini-3.0-flash-preview` | Gemini 模型 |
| `--workers` | `8` | 并发请求数 |
| `--limit` | `0`（全量） | 只处理前 N 条（调试用） |
| `--resume` | `true` | 断点续跑 |

产出：`output/replies_inferred.json`

## Step 3：为独立推文生成触发语境

独立推文直接用作 assistant 输出时缺少 user 侧内容。Gemini 判断每条推文是否有可推断的触发语境（看到了什么/发生了什么），有则生成 `inferred_trigger`，无则标记 `unmotivated: true` 跳过。

```bash
# Google AI Studio
python scripts/infer_tweet_trigger.py --api-key YOUR_GEMINI_API_KEY --workers 8

# Vertex AI（ADC，不需要 API key）
python scripts/infer_tweet_trigger.py --backend vertex --project YOUR_GCP_PROJECT --workers 8
```

产出：`output/tweets_triggered.json`

## Step 4：构建训练数据集

将三路推文数据与 `data/identity.json` 合并，直接输出 OpenAI messages 格式 JSONL：

```bash
python scripts/build_dataset.py
```

每条样本：
```json
{"messages": [
  {"role": "system",    "content": "你是 roitium..."},
  {"role": "user",      "content": "触发语境"},
  {"role": "assistant", "content": "推文内容"}
]}
```

产出：`output/dataset/merged.jsonl`

## Step 5：LoRA 微调

使用 unsloth + SFTTrainer 跑 LoRA 微调，需要 NVIDIA GPU（Linux/WSL）：

```bash
python scripts/train.py
```

默认超参（可在脚本顶部修改）：

| 超参 | 值 |
|------|----|
| Base model | `unsloth/Qwen3.5-9B`（你为什么要修改这个？） | 
| LoRA rank | 16 |
| Batch size | 1（梯度累积 ×8） |
| Epochs | 3 |
| Learning rate | 4e-4 |
| Scheduler | cosine |

产出：`output/checkpoints/lora_adapter_final/`

## Step 6：上传到 HuggingFace

（当然不上传是完全可以的，但是我懒得写不上传的步骤了，因为我自己没尝试过）

```bash
export HF_TOKEN=hf_xxx
export HF_USERNAME=your_username
export HF_REPO_NAME=echo

python scripts/upload.py --mode gguf       # 推荐：q4_k_m GGUF（约 5GB）
python scripts/upload.py --mode adapter    # 只上传 LoRA adapter（约 200MB）
python scripts/upload.py --mode gguf_f16   # f16 无损 GGUF（约 18GB）
python scripts/upload.py --mode merged     # 合并权重后上传完整模型
python scripts/upload.py --mode all_gguf   # 多种量化格式全上传
```

## Step 7：部署推理服务

两种后端可供选择：

### Ollama（不支持图片，需手动下载 GGUF）

Ollama 不能直接从 HuggingFace 拉取 GGUF，需先把模型文件下载到 `models/`，再生成 Modelfile 并创建模型：

```bash
# 1. 下载 GGUF 到 models/（例如从 HF 手动保存）
# 2. 生成 Modelfile（从 constants.py 读取 SYSTEM_PROMPT）
python scripts/build_modelfile.py
# 3. 创建并运行
ollama create roitium-echo -f Modelfile
ollama run roitium-echo
```

> 每次修改 `constants.py` 中的 `SYSTEM_PROMPT` 后，重新执行 `build_modelfile.py` + `ollama create`。

### llama-server（支持图片，可直接从 HF 拉取）

通过 `scripts/run_server.sh` 启动，所有参数通过环境变量控制：

```bash
brew install llama.cpp   # macOS，若未安装

# 直接从 HuggingFace 拉取
LLAMASERVER_HF_REPO=your_username/roitium-echo bash scripts/run_server.sh
```

## Step 8：Telegram Bot

```bash
export TG_TOKEN=your_bot_token   # 从 @BotFather 获取

# Ollama 后端（默认）
python scripts/tg_bot.py

# llama-server 后端
BACKEND=llamaserver python scripts/tg_bot.py
```

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `TG_TOKEN` | 必填 | Telegram Bot Token |
| `BACKEND` | `ollama` | `ollama` 或 `llamaserver` |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama 地址 |
| `OLLAMA_MODEL` | `roitium-echo` | 模型名 |
| `LLAMASERVER_HOST` | `http://localhost:8080` | llama-server 地址 |