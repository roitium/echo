"""
将所有推文数据构建为 SFT 训练用 JSONL，直接输出供 SFTTrainer 使用。

数据来源（三路合并）：
  output/replies_matched.json    — 存档内能找到父推的回复
  output/replies_inferred.json   — Gemini 推断父推内容的孤立回复
  output/tweets_triggered.json   — Gemini 生成触发语境的独立推文
  data/identity.json             — 手写身份问答

产出：
  output/dataset/merged.jsonl
"""

import json
import random
import re

from constants import (
    DATASET_DIR,
    DEVELOPER,
    IDENTITY_PATH,
    MERGED_DATASET_PATH,
    NAME,
    REPLIES_INFERRED_PATH,
    REPLIES_MATCHED_PATH,
    SYSTEM_PROMPT,
    TWEETS_TRIGGERED_PATH,
)

SEED = 42


# ── 工具函数 ──────────────────────────────────────────────────────────────────
def clean_reply(text: str) -> str:
    return re.sub(r'^(@\S+\s*)+', '', text).strip()


def is_valid(text: str, min_len: int = 3) -> bool:
    return len(text) >= min_len and not re.fullmatch(r'(@\S+\s*)+', text.strip())


def make_msg(user: str, assistant: str) -> dict:
    return {"messages": [
        {"role": "system",    "content": SYSTEM_PROMPT},
        {"role": "user",      "content": user},
        {"role": "assistant", "content": assistant},
    ]}


# ── 数据源 1：父推在存档内的回复 ──────────────────────────────────────────────
def load_replies_matched() -> list[dict]:
    records = json.loads(REPLIES_MATCHED_PATH.read_text(encoding="utf-8"))
    samples, skipped = [], 0
    for r in records:
        parent = (r.get("parent_text") or "").strip()
        reply  = clean_reply((r.get("text") or "").strip())
        if not parent or not is_valid(reply):
            skipped += 1
            continue
        samples.append(make_msg(parent, reply))
    print(f"[replies_matched]   有效: {len(samples)}，跳过: {skipped}")
    return samples


# ── 数据源 2：Gemini 推断父推的孤立回复 ──────────────────────────────────────
def load_replies_inferred() -> list[dict]:
    records = json.loads(REPLIES_INFERRED_PATH.read_text(encoding="utf-8"))
    samples, skipped = [], 0
    for r in records:
        original = (r.get("inferred_original_tweet") or "").strip()
        reply    = clean_reply((r.get("reply_text") or "").strip())
        if not original or not is_valid(reply):
            skipped += 1
            continue
        samples.append(make_msg(original, reply))
    print(f"[replies_inferred]  有效: {len(samples)}，跳过: {skipped}")
    return samples


# ── 数据源 3：Gemini 生成触发语境的独立推文 ──────────────────────────────────
def load_tweets_triggered() -> list[dict]:
    records = json.loads(TWEETS_TRIGGERED_PATH.read_text(encoding="utf-8"))
    samples, skip_unmotivated, skip_invalid = [], 0, 0
    for r in records:
        if r.get("unmotivated", True):
            skip_unmotivated += 1
            continue
        trigger  = (r.get("inferred_trigger") or "").strip()
        tweet    = (r.get("tweet_text") or "").strip()
        if not trigger or not is_valid(tweet, min_len=5):
            skip_invalid += 1
            continue
        samples.append(make_msg(trigger, tweet))
    print(f"[tweets_triggered]  有效: {len(samples)}，跳过无动机: {skip_unmotivated}，无效: {skip_invalid}")
    return samples


# ── 数据源 4：手写身份问答 ────────────────────────────────────────────────────
def load_identity() -> list[dict]:
    identity = json.loads(IDENTITY_PATH.read_text(encoding="utf-8"))
    samples = []
    for item in identity:
        user = item["instruction"]
        if item.get("input"):
            user += "\n" + item["input"]
        output = item["output"].replace("<name>", NAME).replace("<developer>", DEVELOPER)
        samples.append(make_msg(user, output))
    print(f"[identity]          有效: {len(samples)}")
    return samples


# ── 主流程 ────────────────────────────────────────────────────────────────────
def main():
    DATASET_DIR.mkdir(parents=True, exist_ok=True)

    samples = (
        load_replies_matched()
        + load_replies_inferred()
        + load_tweets_triggered()
        + load_identity()
    )

    random.seed(SEED)
    random.shuffle(samples)
    print(f"\n[total]  合计样本: {len(samples)}")

    MERGED_DATASET_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(MERGED_DATASET_PATH, "w", encoding="utf-8") as f:
        for item in samples:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    print(f"已写入 → {MERGED_DATASET_PATH}")


if __name__ == "__main__":
    main()
